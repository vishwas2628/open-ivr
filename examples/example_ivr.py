import json
import logging
import os
import uuid
from pathlib import Path

import anyio
import asyncari
from asyncari.state import ToplevelChannelState
from config import config

try:
    from manager.call_recording import is_voicemail_recording_enabled
    from manager.cdr_helper import update_cdr_json
except ImportError:
    from ariApp.manager.call_recording import is_voicemail_recording_enabled
    from ariApp.manager.cdr_helper import update_cdr_json

try:
    from apps.pushcall.router import PushCallRouter
    from apps.voicemail.core import VoicemailOptions, VoicemailSession
except ImportError:
    from ariApp.apps.pushcall.router import PushCallRouter
    from ariApp.apps.voicemail.core import VoicemailOptions, VoicemailSession


# Standalone IVR runner settings; normal multi-tenant runtime enters via handler.py.
ast_url = os.getenv("AST_URL", "http://127.0.0.1:8005")
ast_username = os.getenv("AST_USER", "hello_app")
ast_password = os.getenv("AST_PASS", "peekaboo")
ast_app = os.getenv("AST_APP", "hello_app")

log = logging.getLogger(__name__)


class IVRState(ToplevelChannelState):
    """
    Own one menu-driven IVR call, supporting multi-level named menus,
    configurable repeat/retry on invalid digit and timeout, and terminal
    fallback dispatch.

    Menu structure (from config JSON):
      - config["menus"]               : dict of named menu definitions
      - config["start_menu"]          : entry-point menu name (default "main")
      - config["prompt"]              : one-shot welcome prompt before menus start
      - config["repeat"]["max_attempts"] : global retry ceiling
      - config["repeat"]["fallback"]  : terminal action when budget exhausted

    Per-menu keys inside config["menus"][name]:
      - "prompt"   : audio prompt for this menu level
      - "options"  : digit → action mapping
      - "invalid"  : invalid-digit retry config
      - "timeout"  : timeout retry config

    The state object keeps playback, DTMF, timeout, and handoff decisions in one
    place so PushCall and voicemail transfers can share the original channel and
    call UUID.
    """

    # ------------------------------------------------------------------ #
    # Construction                                                         #
    # ------------------------------------------------------------------ #

    def __init__(self, channel, menu, client, worker=None, tenant_id=None, call_uuid=None):
        super().__init__(channel)
        # ``menu`` is the full JSON config dict; kept as ``config`` internally to
        # distinguish it clearly from the active per-level menu resolved via
        # ``current_menu``.
        self.config = menu
        self.client = client
        self.worker = worker
        self.tenant_id = tenant_id or "5"  # Fallback/Default
        self.current_playback = None
        self.dtmf_received = False
        self._hanging_up = False
        self.call_uuid = call_uuid or str(uuid.uuid4())

        # -- Multi-level menu navigation --
        # Flat configs (no "menus" key) are treated as a single-level menu whose
        # dict IS the current_menu; multi-level configs resolve via _menus lookup.
        self._menus = menu.get("menus", {})
        self._current_menu_name = menu.get("start_menu", "main")
        self._menu_stack: list = []  # breadcrumb for back-navigation

        # -- Retry state --
        # _attempt_count is reset each time we enter a new menu level so retries
        # are local to each level, not accumulated across the whole call.
        self._attempt_count = 0

        # -- Timeout & Concurrency lifecycle --
        # Held so replay_menu / navigate_to_menu can cancel the old timer before
        # spawning a fresh one, preventing ghost timeouts (zombie-call risk).
        self._timeout_cancel_scope: anyio.CancelScope | None = None
        # DTMF concurrency guard: set synchronously in on_dtmf before any await,
        # cleared after the action completes.  This prevents a rapid second DTMF
        # from stopping the new menu prompt or corrupting dtmf_received state.
        self._action_in_progress = False
        self._pending_digits: list[str] = []
        self._interaction_generation = 0
        self._terminal_action_in_progress = False

    # ------------------------------------------------------------------ #
    # Menu resolution                                                      #
    # ------------------------------------------------------------------ #

    @property
    def current_menu(self) -> dict:
        """Return the active menu dict.

        For named-menus configs, resolves from self._menus by current name.
        For flat (legacy-style) configs with no 'menus' key, the root config
        itself is the sole menu — enables existing tenant configs to work
        unchanged without any migration.
        """
        if self._menus:
            resolved = self._menus.get(self._current_menu_name)
            if resolved is None:
                log.warning(
                    "[%s] Menu '%s' not found in menus dict, falling back to empty",
                    self.tenant_id,
                    self._current_menu_name,
                )
                return {}
            return resolved
        # Flat config: treat root as the single menu.
        return self.config

    # ------------------------------------------------------------------ #
    # Playback helpers                                                     #
    # ------------------------------------------------------------------ #

    async def play_and_wait(self, media):
        """Play a prompt and block until the matching PlaybackFinished event arrives.

        On any ARI/audio failure the channel is torn down immediately via
        schedule_safe_hangup() instead of just setting the guard flag.  Setting
        _hanging_up alone would suppress all follow-up actions (timeouts, retries)
        while leaving the live channel open until the caller disconnects.
        """
        try:
            playback = await self.channel.play(media=media)
            self.current_playback = playback

            async with self.client.on_playback_event("PlaybackFinished") as listener:
                async for event_payload, _event in listener:
                    finished_playback = (
                        event_payload.get("playback")
                        if isinstance(event_payload, dict)
                        else event_payload
                    )
                    if getattr(finished_playback, "id", None) == playback.id:
                        break
        except Exception as ex:
            log.warning(
                "[%s] Playback failed: %s on %s: %s",
                self.tenant_id,
                media,
                self.channel.id,
                ex,
            )
            # FIX P1-B: schedule the actual hangup rather than just setting
            # the guard flag.  Bare flag-setting leaves the channel open while
            # suppressing every recovery path (timeouts, retries, fallbacks).
            self.schedule_safe_hangup(0.5)
        finally:
            self.current_playback = None

    async def stop_playback(self):
        """Stop current playback if still active."""
        if not self.current_playback:
            return
        try:
            await self.current_playback.stop()
        except Exception as exc:
            log.error("[%s] Playback stop error: %s", self.tenant_id, exc)
        finally:
            self.current_playback = None

    # ------------------------------------------------------------------ #
    # Hangup helpers                                                       #
    # ------------------------------------------------------------------ #

    async def safe_hangup(self, delay=0.0):
        """Stop active playback and hang up once, even if multiple paths request it."""
        self._hanging_up = True
        self._cancel_timeout()
        await self.stop_playback()
        if delay > 0:
            await anyio.sleep(delay)
        try:
            await self.channel.hangup()
            log.info("[%s] Hung up channel %s", self.tenant_id, self.channel.id)
        except Exception:
            pass

    def schedule_safe_hangup(self, delay=0.5):
        """Schedule hangup outside the current ARI callback to avoid blocking event handling."""
        if self._hanging_up:
            return True
        self._hanging_up = True
        return self.spawn_detached(self.safe_hangup, delay)

    def spawn_detached(self, func, *args):
        """Run a coroutine from whichever task-group context is available."""
        try:
            self.client.taskgroup.start_soon(func, *args)
            return True
        except Exception:
            try:
                anyio.lowlevel.spawn_system_task(func(*args))
                return True
            except Exception as exc:
                log.debug("[%s] Detached spawn failed: %s", self.tenant_id, exc)
                return False

    # ------------------------------------------------------------------ #
    # Timeout management                                                   #
    # ------------------------------------------------------------------ #

    def _cancel_timeout(self):
        """Cancel any running timeout task by closing its CancelScope.

        Safe to call repeatedly — no-op when no timeout is active.
        """
        scope = self._timeout_cancel_scope
        if scope is not None:
            self._timeout_cancel_scope = None
            scope.cancel()

    def _arm_timeout(self, generation=None):
        """Spawn a fresh timeout worker for the current menu level.

        Always cancels any previous timeout first to prevent double-armed timers.
        """
        self._cancel_timeout()
        timeout_cfg = self.current_menu.get("timeout", {})
        if not timeout_cfg:
            return
        self.spawn_detached(
            self.timeout_worker,
            int(timeout_cfg.get("seconds", 20)),
            timeout_cfg.get("prompt"),
            self._interaction_generation if generation is None else generation,
        )

    async def timeout_worker(self, seconds, no_input_prompt=None, generation=None):
        """Wait for DTMF; on expiry play no-input prompt then retry or fall back.

        The CancelScope stored on the instance allows replay_menu /
        navigate_to_menu to cancel this task cleanly when a digit arrives.

        FIX P1-A: replay_menu() calls enter_current_menu() which calls
        _cancel_timeout(), which would cancel *this* CancelScope while the
        worker is still executing it — aborting the replay before its prompt
        or timer can complete.  The fix: clear the CancelScope reference and
        exit the scope *before* delegating to replay_menu / execute_fallback
        so that those helpers can safely arm a brand-new timeout scope.
        """
        try:
            with anyio.CancelScope() as scope:
                self._timeout_cancel_scope = scope

                # Drain any lingering audio before the wait begins.
                await self.stop_playback()

                deadline = anyio.current_time() + seconds
                while anyio.current_time() < deadline:
                    if self._hanging_up or generation != self._interaction_generation:
                        return
                    await anyio.sleep(0.25)

                # Timed out — check again after the loop in case of a race
                # where dtmf_received was set in the final 0.25 s window.
                if self._hanging_up or generation != self._interaction_generation:
                    return

                await self.stop_playback()
                if no_input_prompt:
                    await self.play_and_wait(no_input_prompt)
                    await anyio.sleep(1.0)

                if self._hanging_up or generation != self._interaction_generation:
                    return

                timeout_cfg = self.current_menu.get("timeout", {})
                should_replay = timeout_cfg.get("repeat_prompt") and self.consume_retry(timeout_cfg)

            # ── The CancelScope has now exited. ───────────────────────────
            # Clear the reference so that enter_current_menu() → _cancel_timeout()
            # inside replay_menu() does NOT cancel a scope that has already
            # finished — it becomes a safe no-op instead.
            if self._timeout_cancel_scope is scope:
                self._timeout_cancel_scope = None

            # If the scope was cancelled via _cancel_timeout(), exit cleanly.
            if (
                scope.cancel_called
                or self._hanging_up
                or generation != self._interaction_generation
            ):
                log.debug(
                    "[%s] Timeout worker for %s cancelled cleanly",
                    self.tenant_id,
                    self.channel.id,
                )
                return

            if should_replay:
                await self.replay_menu()
            else:
                await self.execute_fallback(timeout_cfg.get("fail_action"))

        except anyio.get_cancelled_exc_class():
            # CancelScope was closed by _cancel_timeout — this is expected,
            # not an error; just exit silently.
            log.debug(
                "[%s] Timeout worker for %s cancelled cleanly",
                self.tenant_id,
                self.channel.id,
            )
        except Exception as exc:
            log.warning(
                "[%s] Timeout worker error (%s): %s",
                self.tenant_id,
                self.channel.id,
                exc,
            )

    # ------------------------------------------------------------------ #
    # Retry / repeat helpers                                               #
    # ------------------------------------------------------------------ #

    def has_attempts_remaining(self, trigger_config: dict | None = None) -> bool:
        """Return True if the retry budget for this menu level has not been exhausted.

        Priority: trigger-level ``max_retries`` → global ``repeat.max_attempts`` → 3.
        """
        global_max = int(self.config.get("repeat", {}).get("max_attempts", 3))
        if trigger_config and "max_retries" in trigger_config:
            max_retries = int(trigger_config["max_retries"])
        else:
            max_retries = global_max
        return self._attempt_count < max_retries

    def consume_retry(self, trigger_config: dict | None = None) -> bool:
        """Consume exactly one retry, only when one remains."""
        if not self.has_attempts_remaining(trigger_config):
            return False
        self._attempt_count += 1
        return True

    async def replay_menu(self):
        """Replay the current menu prompt and re-arm the timeout.

        Increments the per-level attempt counter and resets ``dtmf_received``
        so the DTMF handler fires correctly on the next keypress.
        """
        log.info(
            "[%s] Replaying menu '%s' (attempt %d)",
            self.tenant_id,
            self._current_menu_name,
            self._attempt_count,
        )
        await self.enter_current_menu()

    async def execute_fallback(self, fail_action: str | None = None):
        """Run the terminal action when the retry budget is exhausted.

        Resolves the action string from: argument → global repeat.fallback → "hangup".
        Supports: "hangup", "voicemail", "dial" (future-proofing).
        """
        if self._hanging_up:
            return
        action = fail_action or self.config.get("repeat", {}).get("fallback", "hangup")
        log.info(
            "[%s] Retry budget exhausted on menu '%s' → fallback=%s",
            self.tenant_id,
            self._current_menu_name,
            action,
        )
        if action == "voicemail":
            self._terminal_action_in_progress = True
            await self.handle_voicemail({"action": "voicemail", "mailbox": "default"})
        elif action == "dial":
            # Reserved for future fallback-dial config; hang up safely for now.
            self.schedule_safe_hangup(0.5)
        else:
            # Default: hangup
            self.schedule_safe_hangup(0.5)

    # ------------------------------------------------------------------ #
    # Menu navigation                                                      #
    # ------------------------------------------------------------------ #

    async def enter_current_menu(self):
        """Play this menu level's prompt and arm its timeout.

        Called on initial entry, on submenu navigation, and on every replay.
        Resets dtmf_received and cancels any old timeout before spawning a fresh
        one so there is never more than one timeout task active per call.
        """
        entry_generation = self._interaction_generation
        self.dtmf_received = False
        self._cancel_timeout()

        menu = self.current_menu
        if not menu:
            log.warning("[%s] Empty menu '%s', hanging up", self.tenant_id, self._current_menu_name)
            self.schedule_safe_hangup(0.5)
            return

        if menu.get("prompt"):
            await self.play_and_wait(menu["prompt"])

        if entry_generation != self._interaction_generation or self._hanging_up:
            return
        if not menu.get("options") and not menu.get("timeout"):
            log.info(
                "[%s] No interaction defined, scheduling hangup after playback.", self.tenant_id
            )
            self.schedule_safe_hangup(0.5)
            return
        self._arm_timeout(entry_generation)

    async def navigate_to_menu(self, target_name: str):
        """Switch to a named sub-menu, pushing the current name for back-navigation.

        Resets the per-level attempt counter because the new menu is a fresh context.
        """
        if not self._menus:
            log.warning(
                "[%s] navigate_to_menu called but config has no 'menus' dict",
                self.tenant_id,
            )
            self.schedule_safe_hangup(0.5)
            return

        if target_name == "back":
            if not self._menu_stack:
                await self.enter_current_menu()
                return
            self._current_menu_name = self._menu_stack.pop()
            self._attempt_count = 0
            await self.enter_current_menu()
            return

        if target_name not in self._menus:
            log.warning(
                "[%s] Submenu target '%s' not found in menus — treating as invalid "
                "selection and re-entering current menu",
                self.tenant_id,
                target_name,
            )
            # FIX P2: silently returning here leaves the caller parked with no
            # timeout and no prompt because DTMF already set dtmf_received=True
            # and cancelled the previous timer.  Re-enter the current menu so a
            # fresh prompt and timeout are armed, giving the caller another chance.
            if self.consume_retry():
                await self.enter_current_menu()
            else:
                await self.execute_fallback()
            return

        self._menu_stack.append(self._current_menu_name)
        self._current_menu_name = target_name
        self._attempt_count = 0  # Fresh retry budget for this menu level
        log.info(
            "[%s] Navigating to menu '%s' (stack depth=%d)",
            self.tenant_id,
            target_name,
            len(self._menu_stack),
        )
        await self.enter_current_menu()

    # ------------------------------------------------------------------ #
    # Entrypoint                                                           #
    # ------------------------------------------------------------------ #

    async def on_start(self):
        """Play the one-shot welcome prompt then enter the first named menu."""
        log.info("[%s] IVR Started for %s", self.tenant_id, self.channel.id)

        # Scope DTMF to this channel so simultaneous IVR calls do not consume
        # each other's digits.
        self.channel.on_event("ChannelDtmfReceived", self.on_dtmf)

        # Allow audio path to settle on the caller handset before audio playback starts.
        await anyio.sleep(1.5)

        # One-time welcome/greeting prompt (plays before any menu).
        if self.config.get("prompt"):
            await self.play_and_wait(self.config["prompt"])

        if self.dtmf_received or self._hanging_up:
            return

        # For flat configs with no "menus" key, enter_current_menu uses the
        # root config as the sole menu via the ``current_menu`` property.
        if self._menus:
            await self.enter_current_menu()
        else:
            # Flat config: replicate original two-step prompt → menu behaviour.
            if self.config.get("menu", {}).get("prompt"):
                await self.play_and_wait(self.config["menu"]["prompt"])
            if self.dtmf_received or self._hanging_up:
                return
            if self.config.get("timeout") or self.config.get("options"):
                self._arm_timeout()
            elif not self.config.get("options"):
                log.info(
                    "[%s] No interaction defined, scheduling hangup after playback.",
                    self.tenant_id,
                )
                await self.safe_hangup(0.5)

    # ------------------------------------------------------------------ #
    # DTMF handling                                                        #
    # ------------------------------------------------------------------ #

    async def on_dtmf(self, event):
        """Queue every DTMF event; stopping a prompt remains immediate barge-in."""
        if self._hanging_up or self._terminal_action_in_progress:
            return
        digit = getattr(event, "digit", None) or getattr(event, "key", None)
        self.dtmf_received = True
        self._interaction_generation += 1
        self._cancel_timeout()
        await self.stop_playback()
        self._pending_digits.append(digit)
        if not self._action_in_progress:
            self._action_in_progress = True
            if not self.spawn_detached(self.process_pending_dtmf):
                self._action_in_progress = False
                self._pending_digits.clear()
                self.schedule_safe_hangup(0.5)

    async def process_pending_dtmf(self):
        try:
            while (
                self._pending_digits
                and not self._hanging_up
                and not self._terminal_action_in_progress
            ):
                await self.process_dtmf_action(self._pending_digits.pop(0), manage_guard=False)
        finally:
            self._action_in_progress = False
            if (
                self._pending_digits
                and not self._hanging_up
                and not self._terminal_action_in_progress
            ):
                self._action_in_progress = True
                if not self.spawn_detached(self.process_pending_dtmf):
                    self._action_in_progress = False
                    self.schedule_safe_hangup(0.5)

    async def process_dtmf_action(self, digit, manage_guard=True):
        try:
            options = self.current_menu.get("options", {}) or {}
            action_cfg = options.get(digit)
            try:
                await update_cdr_json(
                    self.client,
                    self.channel.id,
                    {"ivr_digit": digit, "ivr_menu": self._current_menu_name},
                )
            except Exception:
                pass
            if not action_cfg:
                invalid_cfg = self.current_menu.get("invalid", {})
                await self.play_and_wait(invalid_cfg.get("prompt", "sound:invalid"))
                if self._hanging_up:
                    return
                if invalid_cfg.get("repeat_prompt") and self.consume_retry(invalid_cfg):
                    await self.replay_menu()
                else:
                    await self.execute_fallback(invalid_cfg.get("fail_action"))
                return
            if action_cfg.get("action") != "repeat":
                self._attempt_count = 0
            await self.handle_action(action_cfg)
        except Exception as exc:
            log.warning("[%s] DTMF action error: %s", self.tenant_id, exc)
            self.schedule_safe_hangup(0.5)
        finally:
            if manage_guard:
                self._action_in_progress = False

    # ------------------------------------------------------------------ #
    # Action dispatcher                                                    #
    # ------------------------------------------------------------------ #

    async def handle_action(self, action_config):
        """Dispatch all supported IVR action types."""
        action = action_config.get("action")

        if action == "dial":
            await self.handle_dial(action_config)
        elif action == "voicemail":
            self._terminal_action_in_progress = True
            await self.handle_voicemail(action_config)
        elif action == "repeat":
            await self.replay_menu()
        elif action == "submenu":
            # Navigate to a named menu level (or "back" when target == a parent).
            target = action_config.get("target")
            if target:
                await self.navigate_to_menu(target)
            else:
                # FIX P2: missing 'target' is a JSON config error.  Silently
                # returning here parks the caller because DTMF has already stopped
                # the previous playback and cancelled the timer.  Apply the same
                # recovery as navigate_to_menu() does for an unknown target name:
                # re-enter the current menu (retry) or execute the fallback when
                # the budget is exhausted.
                log.warning(
                    "[%s] 'submenu' action missing 'target' field — "
                    "re-entering current menu as recovery",
                    self.tenant_id,
                )
                if self.consume_retry():
                    await self.enter_current_menu()
                else:
                    await self.execute_fallback()
        elif action == "hangup":
            self.schedule_safe_hangup(0.5)
        else:
            log.warning("[%s] Unknown IVR action: %s", self.tenant_id, action)
            self.schedule_safe_hangup(0.5)

    # ------------------------------------------------------------------ #
    # Dial action                                                          #
    # ------------------------------------------------------------------ #

    async def handle_dial(self, action_config):
        """Hand the IVR caller to PushCall while preserving call UUID and CDR context."""
        endpoint = action_config.get("endpoint", "PJSIP/1002")
        callee = endpoint.replace("PJSIP/", "")

        try:
            await update_cdr_json(
                self.client,
                self.channel.id,
                {
                    "ivr_action": "dial",
                    "ivr_endpoint": callee,
                    "transfer_source": "IVR",
                },
            )
        except Exception:
            pass

        log.info("[%s] Dialing %s (PushCall) from %s", self.tenant_id, callee, self.channel.id)

        if isinstance(self.channel.caller, dict):
            caller = self.channel.caller.get("number", "unknown")
        else:
            caller = getattr(self.channel.caller, "number", "unknown")
        log.info("[%s] Caller: %s", self.tenant_id, caller)

        routing_config = []
        if self.worker and hasattr(self.worker, "load_routing_config"):
            routing_config = self.worker.load_routing_config()
        else:
            try:
                config_dir = Path(os.getenv("ROUTING_CONFIG_PATH") or config.ROUTING_CONFIG_PATH)
                cfg_path = config_dir / f"{self.tenant_id}.json"
                if cfg_path.exists():
                    routing_config = json.loads(cfg_path.read_text())
            except Exception as exc:
                log.error("[%s] Manually loading routing config failed: %s", self.tenant_id, exc)

        strategy, endpoints, timeout, strategy_options = PushCallRouter.parse_routing_config(
            routing_config, callee
        )

        router = PushCallRouter(
            channel=self.channel,
            client=self.client,
            worker=self.worker,
            caller=caller,
            endpoints=endpoints,
            strategy=strategy,
            config=self.config,
            timeout=timeout,
            route_key=callee,
            strategy_options=strategy_options,
            call_uuid=self.call_uuid,
            call_type="IVR_DIAL",
        )

        try:
            log.info("[%s] PushCall flow started: %s", self.tenant_id, router.call_uuid)
            await router.start_flow()
        except Exception as exc:
            log.exception("[%s] PushCall flow failed: %s", self.tenant_id, exc)
            self.schedule_safe_hangup(0.5)

    # ------------------------------------------------------------------ #
    # Voicemail action                                                     #
    # ------------------------------------------------------------------ #

    async def handle_voicemail(self, action_config):
        """Record voicemail from an IVR menu option using the shared voicemail runtime."""
        mailbox = action_config.get("mailbox", "default")
        try:
            if self.worker is not None:
                voicemail_enabled = self.worker.recording_policy.voicemail
            else:
                voicemail_enabled = is_voicemail_recording_enabled(tenant_id=self.tenant_id)

            if not voicemail_enabled:
                log.info(
                    "[%s] Voicemail action requested but voicemail is disabled by policy.",
                    self.tenant_id,
                )
                invalid_prompt = self.current_menu.get("invalid", {}).get("prompt", "sound:invalid")
                await self.play_and_wait(invalid_prompt)
                await anyio.sleep(2.5)
                self.schedule_safe_hangup(0.5)
                return

            if isinstance(self.channel.caller, dict):
                caller = self.channel.caller.get("number", "unknown")
            else:
                caller = getattr(self.channel.caller, "number", "unknown")

            session = VoicemailSession(
                client=self.client,
                channel=self.channel,
                tenant_id=self.tenant_id,
                caller=caller,
                call_uuid=self.call_uuid,
                options=VoicemailOptions(
                    source="ivr",
                    filename_prefix="ivr_vm",
                    filename_hint=mailbox,
                    notify_backend_enabled=True,
                    terminate_on="any",
                    callee=mailbox,
                ),
                worker=self.worker,
            )
            await session.run()
        except Exception as exc:
            log.exception("[%s] Voicemail failed: %s", self.tenant_id, exc)


# ------------------------------------------------------------------ #
# Standalone runner (direct module invocation only)                   #
# ------------------------------------------------------------------ #


async def ivr_worker(client, channel):
    """Standalone IVR worker used only when running this module directly."""
    log.info("Starting IVR for channel %s", channel.id)
    config_path = Path(config.IVR_CONFIG_PATH) / "default.json"
    try:
        standalone_config = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Unable to load standalone IVR config %s: %s", config_path, exc)
        standalone_config = {"start_menu": "main"}
    state = IVRState(channel, standalone_config, client)
    try:
        await channel.answer()
        await state.on_start()

        async with client.on_channel_event("StasisEnd") as end_listener:
            async for event_payload, _ in end_listener:
                ended_channel = (
                    event_payload.get("channel")
                    if isinstance(event_payload, dict)
                    else event_payload
                )
                if getattr(ended_channel, "id", None) == channel.id:
                    break
    except Exception as exc:
        log.warning("IVR worker exception: %s", exc)
    finally:
        if not getattr(state, "_hanging_up", False):
            try:
                await channel.hangup()
            except Exception:
                pass
        log.info("IVR ended for %s", channel.id)


async def on_start_listener(client):
    """Standalone StasisStart listener for direct module testing."""
    async with client.on_channel_event("StasisStart") as listener:
        async for event_payload, event in listener:
            if event.args == "outgoing":
                return
            channel = event_payload["channel"]
            client.taskgroup.start_soon(ivr_worker, client, channel)


async def main():
    """Run the legacy standalone IVR app outside the multi-tenant manager."""
    logging.basicConfig(level=logging.INFO)
    log.info("Connecting to ARI at %s (app=%s)", ast_url, ast_app)

    async with asyncari.connect(ast_url, [ast_app], ast_username, ast_password) as client:
        log.info("Connected to Asterisk ARI")

        client.taskgroup.start_soon(on_start_listener, client)

        async def monitor_channel_destruction():
            async with client.on_channel_event("ChannelDestroyed") as chd:
                async for event_payload, _event in chd:
                    destroyed_channel = (
                        event_payload.get("channel")
                        if isinstance(event_payload, dict)
                        else event_payload
                    )
                    log.debug("Channel destroyed: %s", getattr(destroyed_channel, "id", "<no-id>"))

        client.taskgroup.start_soon(monitor_channel_destruction)
        await anyio.sleep_forever()


# use uvloop is it more optimized
if __name__ == "__main__":
    try:
        anyio.run(main)
    except KeyboardInterrupt:
        log.info("Shutting down IVR app.")
