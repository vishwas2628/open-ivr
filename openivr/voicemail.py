"""Voicemail: play a greeting, record the caller's message, keep the file.

Recording uses the ARI ``POST /channels/{id}/record`` operation
(``channel.record`` in asyncari). Asterisk writes the file into its recording
spool (``/var/spool/asterisk/recording`` by default); afterwards we move it
into the project so it survives Asterisk spool cleaning and can be e-mailed.

Note: the ARI ``LiveRecording`` model has no ``filename`` property, so the
spool path is derived from the recording name + format.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import anyio

from .config import Config
from .spool import move_from_spool

log = logging.getLogger(__name__)

PROMPT_SILENCE = "please-leave-a-message-after-the-tone"


@dataclass(slots=True)
class VoicemailResult:
    mailbox: str
    recording_name: str
    path: Path | None
    duration: int = 0
    success: bool = False
    reason: str = ""


def sanitise_mailbox(name: str) -> str:
    """Make *name* safe for a filename and for an Asterisk mailbox id."""
    cleaned = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in (name or "default"))
    cleaned = cleaned.strip("-.") or "default"
    return cleaned[:48]


class VoicemailBox:
    """Records one voicemail for a channel."""

    def __init__(self, cfg: Config, notify=None) -> None:
        self.cfg = cfg
        self.notify = notify

    def new_recording_name(self, mailbox: str) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        unique = uuid.uuid4().hex[:8]
        return f"openivr-{sanitise_mailbox(mailbox)}-{stamp}-{unique}"

    async def run(
        self,
        channel,
        *,
        mailbox: str = "default",
        greeting: str | None = None,
        caller: str = "unknown",
        prompt: str | None = None,
    ) -> VoicemailResult:
        vm_cfg = self.cfg.voicemail
        name = self.new_recording_name(mailbox)
        result = VoicemailResult(mailbox=sanitise_mailbox(mailbox), recording_name=name, path=None)

        if not vm_cfg.enabled:
            result.reason = "voicemail disabled"
            log.warning("Voicemail requested but disabled in config: %s", result.reason)
            return result

        try:
            if prompt:
                await self._play(channel, prompt)
            elif greeting:
                await self._play(channel, greeting)
            else:
                await self._play(channel, vm_cfg.greeting_prompt or PROMPT_SILENCE)
            await anyio.sleep(0.5)

            rec = await channel.record(
                name=name,
                format=vm_cfg.format,
                maxDurationSeconds=int(vm_cfg.max_duration),
                maxSilenceSeconds=int(vm_cfg.silence_timeout),
                beep=bool(vm_cfg.beep),
                terminateOn=vm_cfg.terminate_on,
                ifExists="overwrite",
            )
            await rec.wait_done()
            duration = int(getattr(rec, "duration", 0) or 0)
            result.duration = duration

            if duration < int(vm_cfg.min_duration):
                log.info("Voicemail %s too short (%ss), discarding", name, duration)
                self._cleanup_spool(name, vm_cfg.format)
                result.reason = "message too short"
                return result

            stored = await self._move_from_spool(name, vm_cfg.format)
            if stored is None:
                result.reason = "recording file not found in Asterisk spool"
                log.error("Voicemail %s: %s", name, result.reason)
                return result

            result.path = stored
            result.success = True
            log.info("Voicemail for %s saved: %s (%ss)", caller, stored, duration)

            if self.notify is not None and vm_cfg.notify:
                await self._notify(result, caller)
        except Exception as exc:  # noqa: BLE001 - never let voicemail kill the call
            log.exception("Voicemail failed for %s: %s", mailbox, exc)
            result.reason = str(exc)
        return result

    async def _play(self, channel, prompt: str) -> None:
        from .media import play_prompt

        await play_prompt(channel, prompt, self.cfg)

    def _cleanup_spool(self, name: str, fmt: str) -> None:
        spool = Path(self.cfg.paths.asterisk_spool) / f"{name}.{fmt}"
        try:
            spool.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - best effort
            pass

    async def _move_from_spool(self, name: str, fmt: str) -> Path | None:
        """Asterisk writes ``<spool>/<name>.<fmt>``; move it into the project."""
        return await move_from_spool(
            self.cfg.paths.asterisk_spool, name, fmt, self.cfg.voicemail_dir
        )

    async def _notify(self, result: VoicemailResult, caller: str) -> None:
        try:
            await self.notify.send_voicemail(
                mailbox=result.mailbox,
                caller=caller,
                path=result.path,
                duration=result.duration,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Voicemail e-mail failed: %s", exc)
