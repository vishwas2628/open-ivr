"""IVR state machine behaviour, driven by a fake asyncari channel.

No Asterisk, no websocket: the fake channel records what the state asked Asterisk
to do (``play``/``record``/``setChannelVar``/``continueInDialplan``/``hangup``),
and DTMF is injected by calling ``state.on_DtmfReceived`` from another task,
exactly as the real event dispatcher would.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import anyio
import pytest

from openivr.context import build_context
from openivr.flow import Flow
from openivr.state import IVRState, in_business_hours

pytestmark = pytest.mark.anyio


class FakePlayback:
    """Mimics a prompt: runs for ``duration`` seconds unless stopped (barge-in)."""

    def __init__(self, media: str, duration: float = 0.15) -> None:
        self.media = media
        self.duration = duration
        self.stopped = False
        self.finished = False
        self._done = anyio.Event()

    async def wait_done(self) -> None:
        with anyio.move_on_after(self.duration):
            await self._done.wait()
        self.finished = True

    async def stop(self) -> None:
        self.stopped = True
        self._done.set()


class FakeRecording:
    def __init__(self, name: str) -> None:
        self.name = name
        self.duration = 9
        self.paused = 0
        self.resumed = 0
        self.stopped = False

    async def wait_done(self) -> None:
        await anyio.sleep(0)

    async def pause(self) -> None:
        self.paused += 1

    async def resume(self) -> None:
        self.resumed += 1

    async def stop(self) -> None:
        self.stopped = True


class FakeClient:
    """Stand-in for ``asyncari.Client``; the handler only reads ``.taskgroup``."""

    def __init__(self) -> None:
        self.taskgroup = None


class FakeChannel:
    def __init__(self, caller: str = "1001") -> None:
        self.id = "1699.1"
        self.name = "PJSIP/1001-00000001"
        self.json = {"caller_id_num": caller, "name": "Tester"}
        self.client = FakeClient()
        self.state = "Down"
        self.played: list[str] = []
        self.playbacks: list[FakePlayback] = []
        self.recorded: list[str] = []
        self.recordings: list[FakeRecording] = []
        self.variables: dict[str, str] = {}
        self.continued: dict[str, Any] = {}
        self.hangups: list[str] = []
        self.reason = ""

    async def answer(self) -> None:
        self.state = "Up"

    async def play(self, media: str, **_):
        self.played.append(media)
        playback = FakePlayback(media)
        self.playbacks.append(playback)
        return playback

    async def record(self, **kwargs):
        name = str(kwargs.get("name"))
        self.recorded.append(name)
        recording = FakeRecording(name)
        self.recordings.append(recording)
        return recording

    async def setChannelVar(self, variable: str, value: str) -> None:
        self.variables[variable] = value

    async def continueInDialplan(self, **kwargs) -> None:
        self.continued = kwargs

    async def hang_up(self) -> None:
        """asyncari's ToplevelChannelState sets the reason before calling this."""
        self.hangups.append(self.reason or "normal")
        self.state = "Hangup"

    async def handle_exit(self) -> None:
        return None

    def set_reason(self, reason: str) -> None:
        self.reason = reason

    async def __aiter__(self):  # pragma: no cover - unused in these tests
        return self


def add_sound(cfg, name: str = "present.wav") -> Path:
    """Create a real file so ``available_sounds`` finds the sounds directory."""
    sounds = Path(cfg.sounds_dir)
    sounds.mkdir(parents=True, exist_ok=True)
    path = sounds / name
    path.write_bytes(b"RIFF")
    return path


def make_state(cfg, flow_dict: dict[str, Any] | None = None, *, caller: str = "1001"):
    flow = Flow.from_dict(flow_dict) if flow_dict is not None else Flow.load(cfg.flow_path)
    ctx = build_context(cfg, flow)
    channel = FakeChannel(caller=caller)
    return IVRState(ctx, channel, [caller]), channel, flow


def one_option_flow(
    action: dict[str, Any], *, timeout: float = 0.3, retries: int = 0
) -> dict[str, Any]:
    """A single-menu flow whose timeout/invalid paths run ``action``."""
    return {
        "version": 1,
        "start_menu": "main",
        "goodbye": None,
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {
                    "seconds": timeout,
                    "max_retries": retries,
                    "prompt": None,
                    "fail_action": action,
                },
                "invalid": {"max_retries": retries, "prompt": None, "fail_action": action},
                "options": {},
            }
        },
    }


async def feed_digits(state: IVRState, digits: str, delay: float = 0.02) -> None:
    """Deliver DTMF the way asyncari's dispatcher would."""
    await anyio.sleep(delay)
    for digit in digits:
        await state.on_ChannelDtmfReceived(type("Evt", (), {"digit": digit})())


async def drive(
    state: IVRState,
    channel: FakeChannel,
    *,
    digits: str = "",
    wait_for: str = "hangup",
    timeout: float = 6.0,
    delay: float = 0.02,
    task: str = "_run",
) -> list[dict[str, Any]]:
    """Run the call to completion (or ``timeout``), feeding ``digits`` first."""
    state._stasis_end.set()
    async with anyio.create_task_group() as tg:
        state._base_tg = tg  # asyncari exposes the task group read-only
        if digits:
            tg.start_soon(feed_digits, state, digits, delay)
        tg.start_soon(getattr(state, task))
        with anyio.move_on_after(timeout):
            while wait_for not in [event["event"] for event in state.rec.events]:
                await anyio.sleep(0.02)
            await anyio.sleep(0.1)
        state._dead.set()
        tg.cancel_scope.cancel()
    return list(state.rec.events)


async def test_digit_dispatches_action(cfg) -> None:
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 2},
                "options": {"7": {"action": "hangup", "label": "bye"}},
            }
        },
    }
    state, channel, _ = make_state(cfg, flow_dict)
    state._stasis_end.set()
    async with anyio.create_task_group() as tg:
        tg.start_soon(feed_digits, state, "7")
        tg.start_soon(state._run)
        with anyio.move_on_after(4):
            await anyio.sleep(0.5)
        state._dead.set()
        state._stasis_end.set()
        tg.cancel_scope.cancel()

    events = [e["event"] for e in state.rec.events]
    assert "stasis_start" in events and "answered" in events
    assert "selection" in events
    assert "hangup" in events
    assert channel.hangups == ["normal"]


async def test_dialplan_handoff_sets_variables(cfg) -> None:
    cfg.dial.mode = "dialplan"
    cfg.dial.context = "openivr-dial"
    cfg.dial.extension = "s"
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 2},
                "options": {"1": {"action": "dial", "endpoint": "PJSIP/1001"}},
            }
        },
    }
    state, channel, _ = make_state(cfg, flow_dict)
    await drive(state, channel, digits="1", timeout=4)

    assert channel.variables["OPENIVR_DIAL_TARGET"] == "PJSIP/1001"
    assert "OPENIVR_CALL_ID" in channel.variables
    assert channel.continued == {"context": "openivr-dial", "extension": "s", "priority": 1}
    assert "dialplan_handoff" in [e["event"] for e in state.rec.events]


async def test_submenu_switches_menu(cfg) -> None:
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 2, "max_retries": 0, "fail_action": {"action": "hangup"}},
                "options": {"2": {"action": "submenu", "target": "sales"}},
            },
            "sales": {
                "prompt": None,
                "timeout": {"seconds": 0.3, "max_retries": 0, "fail_action": {"action": "hangup"}},
                "options": {},
            },
        },
    }
    state, channel, _ = make_state(cfg, flow_dict)
    await drive(state, channel, digits="2", timeout=5)

    menus = [e.get("detail") for e in state.rec.events if e["event"] == "menu"]
    assert menus[:2] == ["main", "sales"]


async def test_timeout_runs_fail_action(cfg) -> None:
    flow_dict = one_option_flow({"action": "hangup"}, timeout=0.1)
    state, channel, _ = make_state(cfg, flow_dict)
    await drive(state, channel, timeout=4)

    events = [e["event"] for e in state.rec.events]
    assert "timeout" in events and "timeout_failed" in events and "hangup" in events
    assert channel.hangups


async def test_invalid_digit_retries_then_fails(cfg) -> None:
    flow_dict = one_option_flow({"action": "hangup"}, timeout=1.0, retries=1)
    state, channel, _ = make_state(cfg, flow_dict)
    await drive(state, channel, digits="zzz9", timeout=5)

    events = [e["event"] for e in state.rec.events]
    assert "invalid" in events
    assert "invalid_failed" in events


async def test_collect_stores_variable_and_continues(cfg) -> None:
    cfg.ivr.interdigit_timeout = 0.3
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 2},
                "options": {
                    "3": {
                        "action": "collect",
                        "min_digits": 2,
                        "max_digits": 4,
                        "variable": "acct",
                        "next": {"action": "hangup"},
                    }
                },
            }
        },
    }
    state, channel, _ = make_state(cfg, flow_dict)
    await drive(state, channel, digits="31234#", timeout=6)

    assert state.rec.variables["acct"] == "1234"
    assert "collected" in [e["event"] for e in state.rec.events]


async def test_voicemail_action_records(cfg, project: Path) -> None:
    spool = project / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    cfg.paths.asterisk_spool = str(spool)
    cfg.voicemail.notify = False

    class SpoolChannel(FakeChannel):
        async def record(self, **kwargs):
            name = str(kwargs["name"])
            (spool / f"{name}.{kwargs['format']}").write_bytes(b"RIFF")
            self.recorded.append(name)
            recording = FakeRecording(name)
            self.recordings.append(recording)
            return recording

    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 2},
                "options": {
                    "2": {
                        "action": "voicemail",
                        "mailbox": "sales",
                        "greeting": "vm-sales",
                        "after": {"action": "hangup"},
                    }
                },
            }
        },
    }
    ctx = build_context(cfg, Flow.from_dict(flow_dict))
    channel = SpoolChannel()
    state = IVRState(ctx, channel, ["1001"])
    await drive(state, channel, digits="2", timeout=8)

    events = [e["event"] for e in state.rec.events]
    assert "voicemail" in events and "voicemail_done" in events
    assert "sound:custom/vm-sales" in channel.played
    assert state.rec.voicemail_path.endswith(".wav")
    assert Path(state.rec.voicemail_path).exists()


async def test_missing_prompt_is_logged_not_fatal(cfg, caplog) -> None:
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "welcome": "does-not-exist",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 0.1, "max_retries": 0, "fail_action": {"action": "hangup"}},
                "options": {},
            }
        },
    }
    add_sound(cfg)
    state, channel, _ = make_state(cfg, flow_dict)
    with caplog.at_level("WARNING", logger="openivr.state"):
        await drive(state, channel, timeout=4)
    assert channel.played == ["sound:custom/does-not-exist"]
    assert any("no audio file" in r.message for r in caplog.records)


async def test_ivr_leg_recording_pauses_during_playback(cfg) -> None:
    """The IVR leg is recorded, but paused while prompts play (and on barge-in)."""
    cfg.record.ivr_leg = True
    cfg.app.answer_delay = 0
    add_sound(cfg)
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "welcome": "welcome",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 1, "max_retries": 0, "fail_action": {"action": "hangup"}},
                "options": {"5": {"action": "hangup"}},
            }
        },
    }
    state, channel, _ = make_state(cfg, flow_dict)
    await drive(state, channel, digits="5", delay=0.05, timeout=6, task="_call_task")

    assert channel.recorded and channel.recorded[0].startswith("openivr-leg-")
    recording = channel.recordings[0]
    assert recording.paused >= 1 and recording.resumed >= 1
    assert recording.stopped
    events = [event["event"] for event in state.rec.events]
    assert "recording_started" in events and "recording_stopped" in events
    assert "barge_in" in events


async def test_direct_entry_skips_welcome(cfg) -> None:
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "welcome": "welcome",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 2},
                "options": {"5": {"action": "hangup"}},
            }
        },
    }
    state, channel, _ = make_state(cfg, flow_dict)
    state.seed_direct_entry("55")
    await drive(state, channel, timeout=4)

    assert "sound:custom/welcome" not in channel.played
    assert "direct_entry" in [e["event"] for e in state.rec.events]


def test_business_hours_helper(caplog) -> None:
    today = datetime.now(ZoneInfo("UTC")).strftime("%a").lower()

    open_now, detail = in_business_hours({}, "UTC")
    assert open_now is True and "always open" in detail

    open_now, detail = in_business_hours({today: []}, "UTC")
    assert open_now is False and "closed" in detail

    other_day = "mon" if today != "mon" else "tue"
    assert in_business_hours({other_day: ["00:00-23:59"]}, "UTC")[0] is False

    assert in_business_hours({today: ["00:00-23:59"]}, "UTC")[0] is True
    assert in_business_hours({today: [["00:00", "23:59"]]}, "UTC")[0] is True
    assert in_business_hours({today: ["nonsense"]}, "UTC")[0] is False
    with caplog.at_level("WARNING", logger="openivr.state"):
        assert in_business_hours({today: ["00:00-23:59"]}, "Not/AZone")[0] is True
    assert any("Unknown timezone" in record.message for record in caplog.records)
    assert in_business_hours({"mon": ["09:00-18:00"]}, "Not/AZone")[1]


def test_time_route_picks_menu(cfg) -> None:
    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "time_route": {
            "timezone": "UTC",
            "business_menu": "sales",
            "after_hours_menu": "closed",
            "hours": {"mon": ["09:00-18:00"]},
        },
        "menus": {
            "main": {"prompt": None, "timeout": {"seconds": 1}, "options": {}},
            "sales": {"prompt": None, "timeout": {"seconds": 1}, "options": {}},
            "closed": {"prompt": None, "timeout": {"seconds": 1}, "options": {}},
        },
    }
    state, _channel, flow = make_state(cfg, flow_dict)
    entry = state._entry("main")
    assert entry.menu in {"sales", "closed"}
    assert entry.detail

    non_start = state._entry("sales")
    assert non_start.menu == "sales"


async def test_cdr_is_written_on_finish(cfg) -> None:
    cfg.cdr.backend = "csv"
    cfg.cdr.csv_file = "data/logs/cdr.csv"
    flow_dict = one_option_flow({"action": "hangup"}, timeout=0.1)
    state, _channel, _ = make_state(cfg, flow_dict)
    state._stasis_end.set()
    await state._run()
    await state._finish()

    path = Path(cfg.path("data/logs/cdr.csv"))
    assert path.exists()
    rows = path.read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("call_id,seq")
    assert "hangup" in rows[-1]


async def test_call_task_crash_still_writes_cdr(cfg) -> None:
    cfg.cdr.backend = "csv"
    cfg.cdr.csv_file = "data/logs/cdr.csv"
    state, _channel, _ = make_state(cfg)
    state._stasis_end.set()

    async def boom() -> None:
        raise RuntimeError("synthetic failure")

    state._run = boom  # type: ignore[method-assign]
    await state._call_task()

    assert state.rec.outcome == "error"
    assert "synthetic failure" in state.rec.cause
    text = Path(cfg.path("data/logs/cdr.csv")).read_text(encoding="utf-8")
    assert "stasis_start" not in text  # _run never ran


def test_caller_fallbacks() -> None:
    from openivr.state import _caller_of

    class Chan:
        json = {}

    assert _caller_of(Chan()) == "unknown"
    Chan.json = {"name": "Alice"}
    assert _caller_of(Chan()) == "Alice"
    Chan.json = {"caller_id_num": 1001}
    assert _caller_of(Chan()) == "1001"


async def test_ivr_leg_recording_is_archived_out_of_the_spool(cfg, project: Path) -> None:
    """Asterisk writes to its spool; the finished file must land in record.dir."""
    cfg.record.ivr_leg = True
    cfg.record.dir = "data/recordings/ivr"
    cfg.app.answer_delay = 0
    spool = project / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    cfg.paths.asterisk_spool = str(spool)
    add_sound(cfg)

    class SpoolChannel(FakeChannel):
        async def record(self, **kwargs):
            name = str(kwargs["name"])
            (spool / f"{name}.{kwargs['format']}").write_bytes(b"RIFF-ivr-leg")
            self.recorded.append(name)
            recording = FakeRecording(name)
            self.recordings.append(recording)
            return recording

    flow_dict = {
        "version": 1,
        "start_menu": "main",
        "menus": {
            "main": {
                "prompt": None,
                "timeout": {"seconds": 0.6, "max_retries": 0, "fail_action": {"action": "hangup"}},
                "options": {},
            }
        },
    }
    ctx = build_context(cfg, Flow.from_dict(flow_dict))
    channel = SpoolChannel()
    state = IVRState(ctx, channel, ["1001"])
    await drive(state, channel, timeout=6, task="_call_task", wait_for="recording_archived")

    events = [event["event"] for event in state.rec.events]
    assert "recording_archived" in events
    stored = Path(state.rec.recording_path)
    assert stored.exists() and stored.read_bytes() == b"RIFF-ivr-leg"
    assert stored.parent == Path(cfg.record_dir).resolve() or stored.parent == cfg.record_dir
    assert not list(spool.glob("openivr-leg-*")), "the spool copy must be moved, not copied"
    assert oct(stored.stat().st_mode)[-3:] == "640"
