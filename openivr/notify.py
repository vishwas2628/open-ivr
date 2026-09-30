"""SMTP notifications: service alerts and voicemail e-mail.

Uses only the standard library (``smtplib`` / ``email``) so the IVR has no
extra runtime dependency for mail.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import anyio

from .config import SmtpCfg

log = logging.getLogger(__name__)

ALERT_SUBJECT = "[openivr] ARI connection lost"
RECOVERED_SUBJECT = "[openivr] ARI connection restored"


class Notifier:
    """Sends alerts and voicemail notifications; silently no-ops if disabled."""

    def __init__(self, cfg: SmtpCfg) -> None:
        self.cfg = cfg

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.enabled and self.cfg.host)

    def recipients(self) -> list[str]:
        return [a for a in (self.cfg.alerts_to or []) if a]

    def _connect(self) -> smtplib.SMTP:
        if self.cfg.port == 465:
            ctx = ssl.create_default_context()
            return smtplib.SMTP_SSL(self.cfg.host, self.cfg.port, timeout=20, context=ctx)
        client = smtplib.SMTP(self.cfg.host, self.cfg.port, timeout=20)
        client.ehlo()
        if self.cfg.starttls:
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        return client

    def _auth_and_send(self, msg: EmailMessage) -> None:
        with self._connect() as client:
            if self.cfg.username:
                client.login(self.cfg.username, self.cfg.password or "")
            client.send_message(msg)

    def _base_message(self, subject: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.cfg.from_addr or self.cfg.username or "openivr@localhost"
        return msg

    async def send_alert(self, subject: str, body: str) -> bool:
        if not self.enabled or not self.recipients():
            log.debug("Alert suppressed (smtp disabled/no recipients): %s", subject)
            return False
        msg = self._base_message(subject)
        msg["To"] = ", ".join(self.recipients())
        msg.set_content(body)
        return await self._send(msg, subject)

    async def send_voicemail(
        self,
        *,
        mailbox: str,
        caller: str,
        path: Path | None,
        duration: int,
    ) -> bool:
        if not self.enabled or not self.recipients():
            return False
        subject = f"[openivr] Voicemail for {mailbox} from {caller}"
        msg = self._base_message(subject)
        msg["To"] = ", ".join(self.recipients())
        msg.set_content(
            f"New voicemail for mailbox {mailbox}\n"
            f"From: {caller}\n"
            f"Duration: {duration}s\n"
            f"File: {path}\n"
        )
        if path is not None and path.exists():
            data = await anyio.to_thread.run_sync(path.read_bytes)
            maintype, _, subtype = (path.suffix.lstrip(".") or "wav").partition(";")
            msg.add_attachment(
                data,
                maintype=maintype or "audio",
                subtype=subtype or "wav",
                filename=path.name,
            )
        return await self._send(msg, subject)

    async def _send(self, msg: EmailMessage, subject: str) -> bool:
        def do_send() -> None:
            self._auth_and_send(msg)

        try:
            await anyio.to_thread.run_sync(do_send)
        except Exception as exc:  # noqa: BLE001 - mail must never break the IVR
            log.error("SMTP send failed (%s): %s", subject, exc)
            return False
        log.info("SMTP sent: %s", subject)
        return True

    def test(self) -> tuple[bool, str]:
        """Blocking connectivity check used by the builder / CLI."""
        if not self.enabled:
            return False, "SMTP disabled (set host + enabled)"
        try:
            with self._connect() as client:
                if self.cfg.username:
                    client.login(self.cfg.username, self.cfg.password or "")
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)
        return True, f"connected to {self.cfg.host}:{self.cfg.port}"
