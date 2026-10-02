"""IVR event log (NOT the call record).

The call record is written by Asterisk's native CDR engine via cdr.conf
(csv backend). This module is a lightweight per-call event trace for IVR
debugging: menu navigation, DTMF digits, per-step timings.

Configuration: cdr.backend: csv | none (default: csv). The 'postgres' and
'both' backends were removed - Asterisk's cdr.conf is the authoritative
call log source.

Output: a flat CSV at data/logs/cdr.csv with one row per IVR event,
keyed by Asterisk's ${UNIQUEID} (exposed as OPENIVR_CALL_ID / channel_id).
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from .config import Config

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    "call_id",        # Asterisk ${UNIQUEID}
    "seq",            # event sequence within the call
    "ts",             # ISO timestamp of the event
    "event",          # event name (menu_enter, digit, dial, etc.)
    "channel_id",     # Asterisk channel ID (same as call_id for our usage)
    "caller",         # caller ID number
    "menu",           # current IVR menu name
    "digit",          # DTMF digit pressed (if any)
    "detail",         # free-form detail
    "duration",       # seconds since call start
    "cause",          # termination cause (if terminal event)
]


@dataclass(slots=True)
class CallRecord:
    """IVR event trace for a single call. Keyed by Asterisk ${UNIQUEID}."""
    call_id: str = ""                     # set from ARI channel.id / UNIQUEID
    channel_id: str = ""                  # same as call_id
    caller: str = "unknown"
    started: float = field(default_factory=time.time)
    menu: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    digits: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    outcome: str = ""
    cause: str = ""
    voicemail_path: str = ""
    recording_path: str = ""

    @property
    def duration(self) -> float:
        return round(time.time() - self.started, 3)

    def event(self, event: str, detail: str = "", **extra: Any) -> None:
        entry = {"event": event, "detail": detail}
        entry.update(extra)
        self.events.append(entry)

    def rows(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for seq, entry in enumerate(self.events):
            out.append(
                {
                    "call_id": self.call_id,
                    "seq": seq,
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "event": entry.get("event", ""),
                    "channel_id": self.channel_id,
                    "caller": self.caller,
                    "menu": entry.get("menu", self.menu),
                    "digit": entry.get("digit", ""),
                    "detail": entry.get("detail", ""),
                    "duration": self.duration,
                    "cause": entry.get("cause", ""),
                }
            )
        return out

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["duration"] = self.duration
        return data


class CdrWriter:
    """CSV-only IVR event log sink."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        # Only csv or none are valid now. postgres/both removed.
        self.backend = cfg.cdr.backend if cfg.cdr.backend in {"csv", "none"} else "csv"
        self.csv_path: Path | None = None
        self._lock = threading.Lock()
        if not cfg.cdr.enabled or self.backend == "none":
            self.backend = "none"
            return
        self.csv_path = cfg.path(cfg.cdr.csv_file)
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        if self.csv_path is None:
            return
        with self._lock:
            exists = self.csv_path.exists() and self.csv_path.stat().st_size > 0
            with self.csv_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                if not exists:
                    writer.writeheader()

    async def write(self, record: CallRecord) -> None:
        if self.backend == "none":
            return
        rows = record.rows()
        if not rows:
            return
        if self.csv_path is not None:
            await anyio.to_thread.run_sync(self._write_csv, rows)

    def _write_csv(self, rows: list[dict[str, Any]]) -> None:
        if self.csv_path is None:
            return
        try:
            with self._lock, self.csv_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                writer.writerows(rows)
        except OSError as exc:
            log.error("Cannot write IVR event log CSV %s: %s", self.csv_path, exc)

    async def close(self) -> None:
        log.info("IVR event log backend '%s' closed", self.backend)