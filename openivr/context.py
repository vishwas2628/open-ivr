"""Shared runtime context handed to every call state machine."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .cdr import CdrWriter
from .config import Config
from .flow import Flow
from .media import available_sounds
from .notify import Notifier
from .voicemail import VoicemailBox

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeContext:
    """Everything a call needs: config, flow, CDR sink, mailer, voicemail."""

    cfg: Config
    flow: Flow
    cdr: CdrWriter
    notifier: Notifier | None = None
    voicemail: VoicemailBox | None = None
    sounds: set[str] = field(default_factory=set)

    def prompt_exists(self, name: str | None) -> bool:
        if not name or not self.sounds:
            return True
        return name in self.sounds

    def warn_missing_prompts(self) -> list[str]:
        missing = sorted(p for p in self.flow.prompts() if not self.prompt_exists(p))
        if missing:
            log.warning(
                "Flow references %d missing prompt file(s): %s", len(missing), ", ".join(missing)
            )
        return missing


def load_flow(cfg: Config) -> Flow:
    path: Path = cfg.flow_path
    flow = Flow.load(path)
    log.info("Loaded IVR flow from %s (%d menus)", path, len(flow.menus))
    return flow


def build_context(cfg: Config, flow: Flow | None = None) -> RuntimeContext:
    cfg.ensure_dirs()
    cdr = CdrWriter(cfg)
    notifier = Notifier(cfg.smtp)
    if notifier.enabled:
        log.info("SMTP notifications enabled (%s)", cfg.smtp.host)
    box = VoicemailBox(cfg, notify=notifier if notifier.enabled else None)
    sounds = available_sounds(cfg.sounds_dir)
    ctx = RuntimeContext(
        cfg=cfg,
        flow=flow or load_flow(cfg),
        cdr=cdr,
        notifier=notifier,
        voicemail=box,
        sounds=sounds,
    )
    if not sounds:
        log.warning("No audio files found in %s - prompts will not play", cfg.sounds_dir)
    return ctx
