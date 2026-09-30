"""Voicemail: recording names, spool handling and the happy path with a fake channel."""

from __future__ import annotations

from pathlib import Path

import pytest

from openivr.voicemail import VoicemailBox, VoicemailResult, sanitise_mailbox

pytestmark = pytest.mark.anyio


class FakeRecording:
    def __init__(self, name: str, duration: int = 7) -> None:
        self.name = name
        self.duration = duration
        self.stopped = False

    async def wait_done(self) -> None:
        return None


class FakeChannel:
    """Minimal stand-in for an asyncari Channel."""

    def __init__(self, cfg, *, duration: int = 7, write_spool: bool = True) -> None:
        self.cfg = cfg
        self.duration = duration
        self.played: list[str] = []
        self.recorded: dict[str, object] = {}
        self.hung_up = False
        self._write_spool = write_spool

    async def play(self, media: str, **_):
        self.played.append(media)

        class Playback:
            async def wait_done(self) -> None:
                return None

        return Playback()

    async def record(self, **kwargs):
        self.recorded = kwargs
        if self._write_spool:
            spool = Path(self.cfg.paths.asterisk_spool)
            spool.mkdir(parents=True, exist_ok=True)
            (spool / f"{kwargs['name']}.{kwargs['format']}").write_bytes(b"RIFFfake")
        return FakeRecording(str(kwargs["name"]), self.duration)


def _spool(project: Path) -> Path:
    spool = project / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    return spool


@pytest.fixture()
def vm_cfg(project: Path, cfg):
    cfg.paths.asterisk_spool = str(_spool(project))
    cfg.voicemail.enabled = True
    cfg.voicemail.notify = False
    return cfg


def test_sanitise_mailbox() -> None:
    assert sanitise_mailbox("support") == "support"
    assert sanitise_mailbox("a b/c") == "a-b-c"
    assert sanitise_mailbox("../../etc/passwd") == "etc-passwd"
    assert sanitise_mailbox("") == "default"
    assert sanitise_mailbox("---") == "default"
    assert len(sanitise_mailbox("x" * 200)) == 48


def test_recording_name_shape(vm_cfg) -> None:
    box = VoicemailBox(vm_cfg)
    name = box.new_recording_name("Support Team")
    assert name.startswith("openivr-Support-Team-")
    assert name.endswith(name.split("-")[-1])
    assert box.new_recording_name("x") != box.new_recording_name("x")


async def test_voicemail_stores_the_file(vm_cfg) -> None:
    channel = FakeChannel(vm_cfg)
    box = VoicemailBox(vm_cfg)
    result = await box.run(channel, mailbox="support", greeting="vm-support", caller="1001")

    assert isinstance(result, VoicemailResult)
    assert result.success is True
    assert result.path is not None and result.path.exists()
    assert result.path.parent == vm_cfg.voicemail_dir
    assert result.duration == 7
    assert channel.played == ["sound:custom/vm-support"]
    assert channel.recorded["format"] == "wav"
    assert channel.recorded["ifExists"] == "overwrite"
    assert channel.recorded["maxSilenceSeconds"] == vm_cfg.voicemail.silence_timeout
    assert not (Path(vm_cfg.paths.asterisk_spool) / f"{result.recording_name}.wav").exists()


async def test_greeting_falls_back_to_config_prompt(vm_cfg) -> None:
    vm_cfg.voicemail.greeting_prompt = "vm-default"
    channel = FakeChannel(vm_cfg)
    result = await VoicemailBox(vm_cfg).run(channel, mailbox="m", caller="1001")
    assert result.success is True
    assert channel.played == ["sound:custom/vm-default"]


async def test_explicit_prompt_wins(vm_cfg) -> None:
    channel = FakeChannel(vm_cfg)
    await VoicemailBox(vm_cfg).run(channel, mailbox="m", greeting="vm-a", prompt="vm-b", caller="1")
    assert channel.played == ["sound:custom/vm-b"]


async def test_media_uri_is_passed_through(vm_cfg) -> None:
    channel = FakeChannel(vm_cfg)
    await VoicemailBox(vm_cfg).run(channel, mailbox="m", greeting="sound:custom/x.wav", caller="1")
    assert channel.played == ["sound:custom/x.wav"]


async def test_disabled_voicemail_is_reported(vm_cfg) -> None:
    vm_cfg.voicemail.enabled = False
    channel = FakeChannel(vm_cfg)
    result = await VoicemailBox(vm_cfg).run(channel, mailbox="m", caller="1")
    assert result.success is False
    assert "disabled" in result.reason
    assert channel.played == []
    assert channel.recorded == {}


async def test_too_short_message_is_discarded(vm_cfg) -> None:
    vm_cfg.voicemail.min_duration = 10
    channel = FakeChannel(vm_cfg, duration=2)
    result = await VoicemailBox(vm_cfg).run(channel, mailbox="m", caller="1")
    assert result.success is False
    assert result.reason == "message too short"
    assert list(vm_cfg.voicemail_dir.glob("*.wav")) == []


async def test_missing_spool_file_is_graceful(vm_cfg) -> None:
    channel = FakeChannel(vm_cfg, write_spool=False)
    result = await VoicemailBox(vm_cfg).run(channel, mailbox="m", caller="1")
    assert result.success is False
    assert "spool" in result.reason


async def test_playback_failure_does_not_raise(vm_cfg) -> None:
    class Broken(FakeChannel):
        async def play(self, media: str, **_):
            raise RuntimeError("no such file")

    result = await VoicemailBox(vm_cfg).run(Broken(vm_cfg), mailbox="m", caller="1")
    assert result.success is False
    assert "no such file" in result.reason


async def test_notification_is_called(vm_cfg) -> None:
    sent: list[dict] = []

    class Notifier:
        async def send_voicemail(self, **kwargs) -> None:
            sent.append(kwargs)

    vm_cfg.voicemail.notify = True
    result = await VoicemailBox(vm_cfg, notify=Notifier()).run(
        FakeChannel(vm_cfg), mailbox="sales", caller="1002"
    )
    assert result.success is True
    assert sent and sent[0]["mailbox"] == "sales"
    assert sent[0]["path"] == result.path


async def test_notification_failure_is_swallowed(vm_cfg) -> None:
    class Broken:
        async def send_voicemail(self, **_):
            raise RuntimeError("smtp down")

    vm_cfg.voicemail.notify = True
    result = await VoicemailBox(vm_cfg, notify=Broken()).run(FakeChannel(vm_cfg), mailbox="m")
    assert result.success is True
