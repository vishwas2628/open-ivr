"""ARI connection loop: connect, dispatch StasisStart into IVRState, reconnect.

Kept deliberately small: the per-call logic lives in :mod:`openivr.state`,
this module only owns the websocket, the reconnect/backoff policy, the health
log and the "service down" e-mail alert.
"""

from __future__ import annotations

import logging
import signal
from typing import Any

import anyio
import asyncari

from .config import Config, load_config
from .context import RuntimeContext, build_context
from .notify import ALERT_SUBJECT, RECOVERED_SUBJECT, Notifier
from .originate import is_dialed_leg
from .state import IVRState

log = logging.getLogger(__name__)


#: how many consecutive failures before we e-mail "service down"
ALERT_AFTER_FAILURES = 3


class Service:
    """Owns the ARI websocket for the lifetime of the process."""

    def __init__(self, cfg: Config, ctx: RuntimeContext | None = None) -> None:
        self.cfg = cfg
        self.ctx = ctx or build_context(cfg)
        self.notifier: Notifier | None = self.ctx.notifier
        self._stop = anyio.Event()
        self._failures = 0
        self._alerted = False
        self._calls = 0

    def stop(self) -> None:
        self._stop.set()

    @property
    def calls_active(self) -> int:
        return self._calls

    # ------------------------------------------------------------------- main

    async def run(self) -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._signal_loop)
            await self._connect_loop()
            tg.cancel_scope.cancel()
        with anyio.move_on_after(5, shield=True):
            await self.ctx.cdr.close()

    async def _signal_loop(self) -> None:
        try:
            with anyio.open_signal_receiver(signal.SIGINT, signal.SIGTERM) as signals:
                async for sig in signals:
                    log.info("Received %s - shutting down", signal.Signals(sig).name)
                    self.stop()
                    return
        except NotImplementedError:  # pragma: no cover - non-POSIX
            await self._stop.wait()

    async def _connect_loop(self) -> None:
        cfg = self.cfg
        log.info(
            "Connecting to ARI %s as %s (app %s, dial mode %s, cdr %s)",
            cfg.ari.base_url,
            cfg.ari.username,
            cfg.app.stasis_app,
            cfg.dial.mode,
            cfg.cdr.backend,
        )
        self.ctx.warn_missing_prompts()
        backoff = float(cfg.ari.reconnect_initial)
        while not self._stop.is_set():
            try:
                async with asyncari.connect(
                    base_url=cfg.ari.base_url,
                    apps=[cfg.app.stasis_app],
                    username=cfg.ari.username,
                    password=cfg.require_ari_password(),
                ) as client:
                    log.info("ARI connected (%s)", cfg.ari.base_url)
                    if self._alerted:
                        await self._notify(
                            RECOVERED_SUBJECT,
                            f"ARI connection to {cfg.ari.base_url} restored",
                        )
                        self._alerted = False
                    self._failures = 0
                    backoff = float(cfg.ari.reconnect_initial)
                    await self._serve(client)
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:  # noqa: BLE001 - keep reconnecting
                self._failures += 1
                log.error("ARI connection failed (%d): %s", self._failures, exc)
                if self._failures >= ALERT_AFTER_FAILURES and not self._alerted:
                    self._alerted = True
                    await self._notify(
                        ALERT_SUBJECT,
                        f"Cannot reach ARI at {cfg.ari.base_url} after {self._failures} "
                        f"attempts: {exc!r}\nThe IVR cannot accept calls until this is fixed.",
                    )
                with anyio.move_on_after(backoff):
                    await self._stop.wait()
                backoff = min(backoff * 2, float(cfg.ari.reconnect_max))
        log.info("Service stopped")

    async def _notify(self, subject: str, body: str) -> None:
        if self.notifier is None:
            return
        with anyio.move_on_after(15):
            await self.notifier.send_alert(subject, body)

    # ----------------------------------------------------------------- serve

    async def _serve(self, client) -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._listen, client)
            tg.start_soon(self._health, client)
            await self._stop.wait()
            tg.cancel_scope.cancel()

    async def _listen(self, client) -> None:
        # asyncari only registers a listener in ``__aenter__``; iterating without
        # ``async with`` silently drops every event, so the IVR never sees calls.
        async with client.on_channel_event("StasisStart") as listener:
            async for objs, event in listener:
                channel = objs.get("channel") if isinstance(objs, dict) else objs
                if channel is None:
                    continue
                if is_dialed_leg(event):
                    log.debug("Ignoring originated leg %s (handled by originate.py)", channel.id)
                    continue
                args = [str(a) for a in (getattr(event, "args", None) or [])]
                client.taskgroup.start_soon(self._handle_call, channel, args)

    async def _handle_call(self, channel, args: list[str]) -> None:
        self._calls += 1
        try:
            state = IVRState(self.ctx, channel, args)
            log.debug("Starting IVR state for %s (args=%s)", channel.id, args)
            await state.start_task()
            await state
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as exc:  # noqa: BLE001 - never kill the ARI loop
            log.exception("Call on %s crashed: %s", channel.id, exc)
        finally:
            self._calls = max(0, self._calls - 1)
            log.debug("Call on %s finished", channel.id)

    async def _health(self, client) -> None:
        interval = float(self.cfg.health.interval)
        while True:
            await anyio.sleep(interval)
            try:
                with anyio.move_on_after(5, shield=True):
                    info = await client.asterisk.getInfo()
                    uptime = int(info.get("uptime", 0) or 0)
                    log.info(
                        "health: asterisk %s up %dh%02dm | ivr calls %d | cdr %s",
                        info.get("system", "?"),
                        uptime // 3600,
                        (uptime % 3600) // 60,
                        self._calls,
                        self.ctx.cdr.backend,
                    )
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("health check failed: %s", exc)


def build_service(cfg: Config, overrides: dict[str, Any] | None = None) -> Service:
    """Build a :class:`Service`, optionally re-loading the config with *overrides*."""
    if overrides:
        cfg = load_config(cfg.root, overrides=overrides)
    return Service(cfg)
