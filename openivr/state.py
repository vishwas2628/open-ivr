"""The IVR state machine: welcome -> menus -> actions.

Design notes
------------
* Subclasses asyncari's :class:`ToplevelChannelState` (official handler) and the
  :class:`DTMFHandler` mix-in, so DTMF arrives through the normal event
  dispatch instead of ad-hoc websocket callbacks.
* ``on_start`` stays short - it only starts the call task.  Long running work
  lives in a child task of the state's task group, so ``StasisEnd`` /
  ``ChannelDestroyed`` (which call ``done()``) always cancel a call promptly.
* A :class:`openivr.dtmf.DigitStream` is the mailbox between the dispatcher
  and the menu loop, giving barge-in (a digit stops the current prompt) and
  multi-digit collection with an interdigit timeout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anyio
from asyncari.model import Channel
from asyncari.state import DTMFHandler, ToplevelChannelState

from .cdr import CallRecord
from .config import Config
from .context import RuntimeContext
from .dtmf import DigitStream
from .flow import Action, Flow, Menu
from .media import PLAYBACK_GUARD, resolve_media
from .originate import originate_and_bridge
from .spool import move_from_spool
from .voicemail import VoicemailBox

log = logging.getLogger(__name__)

PLAY_DONE = "done"
PLAY_STOPPED = "stopped"
PLAY_DEAD = "dead"
PLAY_SKIPPED = "skipped"

CONTINUE = "continue"
GOTO = "goto"
END = "end"

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(slots=True)
class Step:
    """What the menu loop should do next."""

    kind: str = CONTINUE
    menu: str = ""
    prompt: str | None = None


@dataclass(slots=True)
class Entry:
    """Where a call starts, plus an optional intro prompt."""

    menu: str
    prompt: str | None = None
    detail: str = ""


class IVRState(ToplevelChannelState, DTMFHandler):
    """One instance per inbound channel."""

    def __init__(
        self, ctx: RuntimeContext, channel: Channel, args: list[str] | None = None
    ) -> None:
        super().__init__(channel)
        self.ctx = ctx
        self.cfg: Config = ctx.cfg
        self.flow: Flow = ctx.flow
        self.args = [str(a) for a in (args or [])]
        self.digits = DigitStream()
        self.rec = CallRecord(channel_id=channel.id, caller=_caller_of(channel))
        self._playback = None
        self._barge_in = False
        self._recording = None
        self._recording_name = ""
        self._record_lock = anyio.Lock()
        self._dead = anyio.Event()
        self._stasis_end = anyio.Event()
        self._menu_name = self.flow.start_menu
        self._attempts: dict[tuple[str, str], int] = {}
        self._voicemail: VoicemailBox | None = ctx.voicemail
        self._direct_digits = ""
        self._answer_delay = self.cfg.app.answer_delay
        self._guard = self.cfg.dial.dial_timeout + 60
        self._menu_history: list[str] = []

    # ----------------------------------------------------------------- events

    async def on_start(self) -> None:
        """Keep this short: start the call task and return."""
        self.taskgroup.start_soon(self._call_task)

    async def on_dtmf(self, evt) -> None:
        """DTMFHandler mix-in hook - every digit lands here."""
        digit = str(getattr(evt, "digit", "") or "")
        if not digit:
            return
        self.rec.digits.append(digit)
        self.rec.event("dtmf", detail=digit, digit=digit, menu=self._menu_name)
        log.debug("[%s] DTMF %r in menu %s", self.rec.call_id, digit, self._menu_name)
        self.digits.push(digit)
        self._interrupt_playback()

    async def on_StasisEnd(self, evt) -> None:
        self._stasis_end.set()
        self._dead.set()
        self.rec.event("stasis_end", detail=str(getattr(evt, "channel_id", "") or ""))
        await super().on_StasisEnd(evt)

    async def on_ChannelDestroyed(self, evt) -> None:
        self._dead.set()
        await super().on_ChannelDestroyed(evt)

    async def on_ChannelHangupRequest(self, evt) -> None:
        self.rec.event("hangup_request", detail=str(getattr(evt, "cause", "") or ""))
        await super().on_ChannelHangupRequest(evt)

    async def on_DialResult(self, evt) -> None:
        """Log dial results (after a dialplan handoff) without killing the state."""
        status = str(getattr(evt, "dialstatus", "") or "")
        self.rec.event("dial_result", detail=status)
        log.info("[%s] dial result %s", self.rec.call_id, status or "?")

    # -------------------------------------------------------------- public API

    def seed_direct_entry(self, digits: str) -> None:
        """Pre-load the first digit (e.g. the extension the caller dialled)."""
        cleaned = "".join(c for c in (digits or "") if c.isdigit() or c in "*#")
        if cleaned:
            self._direct_digits = cleaned[:12]

    # -------------------------------------------------------------- call flow

    async def _call_task(self) -> None:
        try:
            await self._run()
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as exc:  # noqa: BLE001 - a crash must still log a CDR
            log.exception("[%s] call failed: %s", self.rec.call_id, exc)
            self.rec.outcome = self.rec.outcome or "error"
            self.rec.cause = str(exc)
        finally:
            with anyio.move_on_after(3, shield=True):
                await self._stop_recording()
            with anyio.move_on_after(2, shield=True):
                await self._finish()

    async def _run(self) -> None:
        ch = self.channel
        self.rec.event("stasis_start", detail=",".join(self.args))
        log.info("[%s] call from %s on %s", self.rec.call_id, self.rec.caller, ch.id)

        if self.cfg.record.ivr_leg:
            await self._start_recording()

        await ch.answer()
        self.rec.event("answered")
        self.digits.drain()  # digits pressed while ringing are not menu input
        if self._answer_delay:
            await anyio.sleep(self._answer_delay)

        if self._direct_digits:
            self.digits.push(self._direct_digits[0])
            self.rec.event("direct_entry", detail=self._direct_digits)
        elif self.flow.welcome:
            await self._play(self.flow.welcome)
            if self._dead.is_set():
                return

        entry = self._entry(self.flow.start_menu)
        await self._menu_loop(entry)

    async def _menu_loop(self, entry: Entry) -> None:
        menu_name = entry.menu
        if entry.prompt:
            await self._play(entry.prompt)
            if self._dead.is_set():
                return
        while not self._dead.is_set():
            if not self._menu_history or self._menu_history[-1] != menu_name:
                self._menu_history.append(menu_name)
            menu = self.flow.menus.get(menu_name)
            if menu is None:
                log.error("[%s] menu %r is not in the flow", self.rec.call_id, menu_name)
                await self._hangup("normal")
                return
            step = await self._run_menu(menu)
            if step.kind == GOTO and step.menu:
                if step.menu != menu_name:
                    self.digits.drain()  # digits queued for the old menu are stale
                    menu_name = step.menu
            elif step.kind == END:
                return

    # ----------------------------------------------------------- menu engine

    async def _run_menu(self, menu: Menu) -> Step:
        self._menu_name = menu.name
        self.rec.event("menu", detail=menu.name, menu=menu.name)

        played = await self._play(menu.prompt)
        if played == PLAY_DEAD:
            return Step(END)

        digit = await self.digits.next_digit(menu.timeout.seconds)
        if self._dead.is_set():
            return Step(END)

        if digit is None:
            self.rec.event("timeout", detail=f"menu={menu.name}", menu=menu.name)
            return await self._retry_or_fail(
                menu, "timeout", menu.timeout.prompt, menu.timeout.max_retries
            )

        option = menu.options.get(digit)
        if option is None:
            self.rec.event("invalid", detail=digit, digit=digit, menu=menu.name)
            log.info("[%s] invalid digit %r in menu %s", self.rec.call_id, digit, menu.name)
            return await self._retry_or_fail(
                menu, "invalid", menu.invalid.prompt, menu.invalid.max_retries
            )

        self._attempts.pop((menu.name, "timeout"), None)
        self._attempts.pop((menu.name, "invalid"), None)
        self.rec.event(
            "selection",
            detail=f"{digit} -> {option.action.describe()}",
            digit=digit,
            menu=menu.name,
        )
        log.info(
            "[%s] menu %s digit %s -> %s",
            self.rec.call_id,
            menu.name,
            digit,
            option.action.describe(),
        )
        return await self._run_action(option.action, menu)

    async def _retry_or_fail(self, menu: Menu, kind: str, prompt: str | None, retries: int) -> Step:
        """Play a retry prompt while attempts remain, else run the fail action."""
        key = (menu.name, kind)
        attempt = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempt
        if attempt <= max(0, retries) and prompt:
            self.digits.drain()  # forget the digit that triggered the retry
            await self._play(prompt)
            return Step(CONTINUE)
        fail = menu.timeout.fail_action or menu.on_exit or Action(type="hangup")
        log.info(
            "[%s] menu %s %s after %d attempt(s) -> %s",
            self.rec.call_id,
            menu.name,
            kind,
            attempt,
            fail.describe(),
        )
        self.rec.event(f"{kind}_failed", detail=fail.describe(), menu=menu.name)
        return await self._run_action(fail, menu)

    # --------------------------------------------------------------- actions

    async def _run_action(self, action: Action, menu: Menu) -> Step:
        if action.type == "hangup":
            await self._hangup("normal")
            return Step(END)
        if action.type in {"submenu", "goto"}:
            return Step(GOTO, menu=action.target)
        if action.type == "parent":
            return Step(GOTO, menu=action.target or self.flow.start_menu)
        if action.type == "repeat":
            return Step(CONTINUE)
        if action.type == "goback":
            if len(self._menu_history) > 1:
                self._menu_history.pop()  # current menu
                prev = self._menu_history.pop()  # target menu (will be re-appended in loop)
                return Step(GOTO, menu=prev)
            return Step(GOTO, menu=self.flow.start_menu)
        if action.type == "dial":
            return await self._do_dial(action)
        if action.type == "voicemail":
            return await self._do_voicemail(action, menu)
        if action.type == "collect":
            return await self._do_collect(action, menu)
        if action.type == "time_route":
            entry = self._route(action.business_menu, action.after_hours_menu, action.hours, "UTC")
            return Step(GOTO, menu=entry.menu, prompt=entry.prompt)
        log.warning("[%s] unhandled action type %r", self.rec.call_id, action.type)
        return Step(CONTINUE)

    async def _do_dial(self, action: Action) -> Step:
        endpoint = action.endpoint
        self.rec.event("dial", detail=endpoint)
        if self.cfg.dial.mode == "originate":
            res = await originate_and_bridge(
                self.client,
                self.channel,
                endpoint=endpoint,
                app=self.cfg.app.stasis_app,
                caller_id=action.label or None,
                timeout=self.cfg.dial.originate_timeout,
                variables=self._dial_variables(),
            )
            self.rec.event("dial_result", detail=f"originate {endpoint} ok={res.ok} {res.reason}")
            if not res.ok:
                await self._hangup("congestion")
            return Step(END)
        await self._handoff_to_dialplan(endpoint)
        return Step(END)

    def _dial_variables(self) -> dict[str, str]:
        return {
            "OPENIVR_CALL_ID": self.rec.call_id,
            "OPENIVR_DIGITS": ",".join(self.rec.digits),
            "OPENIVR_MENU": self._menu_name,
            "OPENIVR_ACCOUNTCODE": getattr(self.cfg.app, "company_id", "openivr"),
        }

    async def _handoff_to_dialplan(self, endpoint: str) -> None:
        """Plan default: let the dialplan run ``Dial()`` so the CDR stays clean."""
        ch = self.channel
        self.rec.event("dialplan_handoff", detail=endpoint)
        try:
            await ch.setChannelVar(variable="OPENIVR_DIAL_TARGET", value=endpoint)
            await ch.setChannelVar(variable="OPENIVR_CALL_ID", value=self.rec.call_id)
            await ch.setChannelVar(variable="OPENIVR_MENU", value=self._menu_name)
            await ch.setChannelVar(variable="OPENIVR_ACCOUNTCODE", value=getattr(self.cfg.app, "company_id", "openivr"))
            if self.rec.digits:
                await ch.setChannelVar(variable="OPENIVR_DIGITS", value=",".join(self.rec.digits))
            await ch.continueInDialplan(
                context=self.cfg.dial.context,
                extension=self.cfg.dial.extension,
                priority=int(self.cfg.dial.priority),
            )
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] dialplan handoff to %s failed: %s", self.rec.call_id, endpoint, exc)
            self.rec.cause = str(exc)
            await self._hangup("congestion")
            return
        with anyio.move_on_after(self._guard):
            await self._stasis_end.wait()

    async def _do_voicemail(self, action: Action, menu: Menu) -> Step:
        mailbox = action.mailbox or menu.name
        self.rec.event("voicemail", detail=mailbox)
        box = self._voicemail
        if box is None:
            log.warning("[%s] voicemail requested but not available", self.rec.call_id)
            return await self._run_action(action.after, menu) if action.after else Step(END)
        result = await box.run(
            self.channel,
            mailbox=mailbox,
            greeting=action.greeting or None,
            caller=self.rec.caller,
            prompt=action.prompt or None,
        )
        if result.path is not None:
            self.rec.voicemail_path = str(result.path)
        self.rec.event("voicemail_done", detail=f"ok={result.success} {result.reason}".strip())
        if action.after is not None:
            return await self._run_action(action.after, menu)
        if self._dead.is_set():
            return Step(END)
        return Step(CONTINUE)

    async def _do_collect(self, action: Action, menu: Menu) -> Step:
        prompt = action.prompt or menu.prompt
        if prompt:
            self.digits.drain()
            await self._play(prompt)
        result = await self.digits.collect(
            min_len=max(1, action.min_digits),
            max_len=max(action.min_digits, action.max_digits),
            first_timeout=menu.timeout.seconds or self.cfg.ivr.prompt_timeout,
            interdigit_timeout=self.cfg.ivr.interdigit_timeout,
            terminator="#",
        )
        name = action.variable or "collected"
        self.rec.variables[name] = result.digits
        self.rec.event("collected", detail=f"{name}={result.digits}", variable=name)
        log.info(
            "[%s] collected %s=%r terminator=%r timed_out=%s",
            self.rec.call_id,
            name,
            result.digits,
            result.terminator,
            result.timed_out,
        )
        if action.next is not None:
            return await self._run_action(action.next, menu)
        return Step(CONTINUE)

    # ------------------------------------------------------------------ media

    async def _play(self, prompt: str | None, *, guard: float = PLAYBACK_GUARD) -> str:
        if not prompt:
            return PLAY_SKIPPED
        if not self.ctx.prompt_exists(prompt):
            log.warning("[%s] no audio file for prompt %r", self.rec.call_id, prompt)
        try:
            media = resolve_media(prompt, self.cfg)
        except ValueError:
            log.error("[%s] unusable prompt name %r", self.rec.call_id, exc_info=True)
            return PLAY_SKIPPED

        await self._pause_recording()
        self._barge_in = False
        try:
            playback = await self.channel.play(media=media)
            self._playback = playback
            result = await self._await_playback(playback, guard)
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] play(%s) failed: %s", self.rec.call_id, media, exc)
            result = PLAY_DEAD
            self._dead.set()
        finally:
            self._playback = None
            await self._resume_recording()

        if result == PLAY_DONE:
            self.rec.event("played", detail=prompt)
        elif result == PLAY_STOPPED:
            self.rec.event("barge_in", detail=prompt)
        return result

    async def _await_playback(self, playback, guard: float) -> str:
        done = anyio.Event()

        async def waiter() -> None:
            await playback.wait_done()
            done.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(waiter)
            with anyio.move_on_after(guard):
                while not done.is_set() and not self._dead.is_set():
                    await anyio.sleep(0.1)
            tg.cancel_scope.cancel()
        if self._dead.is_set():
            return PLAY_DEAD
        if done.is_set():
            return PLAY_STOPPED if self._barge_in else PLAY_DONE
        return PLAY_STOPPED

    def _interrupt_playback(self) -> None:
        playback = self._playback
        if playback is None:
            return
        self._barge_in = True
        try:
            self.taskgroup.start_soon(self._stop_playback, playback)
        except RuntimeError:  # pragma: no cover - task group already closing
            pass

    async def _stop_playback(self, playback) -> None:
        with anyio.move_on_after(2, shield=True):
            try:
                await playback.stop()
            except Exception as exc:  # noqa: BLE001 - stop is best effort
                log.debug("playback stop failed: %s", exc)

    # ------------------------------------------------------------- recording

    async def _start_recording(self) -> None:
        name = f"openivr-leg-{self.rec.call_id}"
        self._recording_name = name
        try:
            rec = await self.channel.record(
                name=name,
                format=self.cfg.voicemail.format,
                maxDurationSeconds=int(self.cfg.app.max_call_seconds),
                ifExists="overwrite",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[%s] could not start call recording: %s", self.rec.call_id, exc)
            return
        self._recording = rec
        self.rec.event("recording_started", detail=name)

    async def _pause_recording(self) -> None:
        rec = self._recording
        if rec is None or self._dead.is_set():
            return
        async with self._record_lock:
            with anyio.move_on_after(2, shield=True):
                try:
                    await rec.pause()
                except Exception:  # noqa: BLE001 - pause/resume are advisory
                    pass

    async def _resume_recording(self) -> None:
        rec = self._recording
        if rec is None or self._dead.is_set():
            return
        async with self._record_lock:
            with anyio.move_on_after(2, shield=True):
                try:
                    await rec.resume()
                except Exception:  # noqa: BLE001
                    pass

    async def _stop_recording(self) -> None:
        rec = self._recording
        if rec is None:
            return
        self._recording = None
        with anyio.move_on_after(4, shield=True):
            try:
                await rec.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("recording stop failed: %s", exc)
        self.rec.event("recording_stopped")
        await self._archive_recording()

    async def _archive_recording(self) -> None:
        """Move the finished spool file into ``record.dir`` (configurable)."""
        name = self._recording_name
        if not name:
            return
        try:
            stored = await move_from_spool(
                self.cfg.paths.asterisk_spool,
                name,
                self.cfg.voicemail.format,
                self.cfg.record_dir,
            )
        except OSError as exc:  # pragma: no cover - defensive
            log.warning("[%s] could not archive recording: %s", self.rec.call_id, exc)
            return
        if stored is None:
            log.debug("[%s] recording %s not found in the spool", self.rec.call_id, name)
            return
        self.rec.recording_path = str(stored)
        self.rec.event("recording_archived", detail=str(stored))

    # ----------------------------------------------------------------- finish

    async def _hangup(self, reason: str = "normal") -> None:
        if self.flow.goodbye and not self._dead.is_set():
            await self._play(self.flow.goodbye, guard=20)
        self.rec.event("hangup", detail=reason)
        self.rec.outcome = self.rec.outcome or reason
        await self.hang_up(reason=reason)
        with anyio.move_on_after(self._guard):
            await self._stasis_end.wait()

    async def _finish(self) -> None:
        self.rec.outcome = self.rec.outcome or ("hangup" if self._dead.is_set() else "open")
        if not self.rec.cause:
            self.rec.cause = str(getattr(self, "last_cause", "") or "normal")
        try:
            await self.ctx.cdr.write(self.rec)
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] CDR write failed: %s", self.rec.call_id, exc)
        log.info(
            "[%s] finished outcome=%s cause=%s duration=%.1fs digits=%s",
            self.rec.call_id,
            self.rec.outcome,
            self.rec.cause,
            self.rec.duration,
            ",".join(self.rec.digits) or "-",
        )
        with anyio.move_on_after(1, shield=True):
            await self.digits.aclose()

    # ------------------------------------------------------------- time routes

    def _entry(self, name: str) -> Entry:
        route = self.flow.time_route
        if route is None or name != self.flow.start_menu:
            return Entry(menu=name)
        return self._route(
            route.business_menu,
            route.after_hours_menu,
            route.hours,
            route.timezone,
            prompt=route.prompt,
        )

    def _route(
        self,
        business: str,
        after_hours: str,
        hours: dict[str, Any],
        timezone_name: str,
        *,
        prompt: str | None = None,
    ) -> Entry:
        open_now, detail = in_business_hours(hours, timezone_name)
        self.rec.event("time_route", detail=f"{'open' if open_now else 'closed'} ({detail})")
        log.info(
            "[%s] business hours %s -> %s",
            self.rec.call_id,
            "open" if open_now else "closed",
            detail,
        )
        if open_now:
            return Entry(menu=business or self.flow.start_menu, detail=detail)
        if after_hours:
            return Entry(menu=after_hours, prompt=prompt, detail=detail)
        return Entry(menu=business or self.flow.start_menu, detail=detail)


def _caller_of(channel: Channel) -> str:
    data = getattr(channel, "json", None) or {}
    for key in ("caller_id_num", "name", "id"):
        value = data.get(key)
        if value:
            return str(value)
    return "unknown"


def in_business_hours(spec: dict[str, Any], timezone_name: str = "UTC") -> tuple[bool, str]:
    """Evaluate a ``{"mon": ["09:00-17:00"], "sat": [], ...}`` schedule.

    Returns ``(is_open, human readable detail)``.  Days missing from the spec -
    and days with an empty list - are closed.
    """
    if not spec:
        return True, "no schedule configured (always open)"
    tz = _zone(timezone_name)
    now = datetime.now(tz) if tz is not None else datetime.utcnow()
    day = DAYS[now.weekday()]
    windows = spec.get(day) or []
    if not windows:
        return False, f"{day} is closed"
    stamp = now.strftime("%H:%M")
    for window in windows:
        start, end = _window_bounds(window)
        if start is None or end is None:
            continue
        if start <= stamp <= end:
            zone = timezone_name if tz is not None else "UTC"
            return True, f"{day} {start}-{end} {zone}"
    return False, f"{day} {stamp} is outside {windows} ({timezone_name})"


def _window_bounds(window: Any) -> tuple[str | None, str | None]:
    if isinstance(window, (list, tuple)) and len(window) >= 2:
        return str(window[0])[:5], str(window[1])[:5]
    text = str(window)
    if "-" not in text:
        return None, None
    start, _, end = text.partition("-")
    return start.strip()[:5], end.strip()[:5]


def _zone(timezone_name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(timezone_name or "UTC")
    except Exception as exc:  # noqa: BLE001 - tzdata may be missing
        log.warning("Unknown timezone %r (%s); using UTC", timezone_name, exc)
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo("UTC")
        except Exception:  # noqa: BLE001
            return None
