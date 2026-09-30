"""Call detail records: CSV by default, optional PostgreSQL backend.

The IVR writes a flat CSV (``cdr.backend: csv``) so it stays dependency free;
``cdr.backend: postgres|both`` additionally inserts into the ``cdr`` table
created by ``system/steps/60-database.sh``.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from .config import Config

log = logging.getLogger(__name__)

CSV_COLUMNS = [
    "call_id",
    "seq",
    "ts",
    "event",
    "channel_id",
    "caller",
    "menu",
    "digit",
    "detail",
    "duration",
    "cause",
]


@dataclass(slots=True)
class CallRecord:
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    channel_id: str = ""
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
    """Fan-out CDR sink (CSV and/or PostgreSQL)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.backend = cfg.cdr.backend
        self.csv_path: Path | None = None
        self._lock = threading.Lock()
        self._pg_pool = None
        if not cfg.cdr.enabled or self.backend == "none":
            self.backend = "none"
            return
        if self.backend in {"csv", "both"}:
            self.csv_path = cfg.path(cfg.cdr.csv_file)
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_header()
        if self.backend in {"postgres", "both"}:
            self._init_postgres()

    def _ensure_header(self) -> None:
        if self.csv_path is None:
            return
        with self._lock:
            exists = self.csv_path.exists() and self.csv_path.stat().st_size > 0
            with self.csv_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                if not exists:
                    writer.writeheader()

    def _init_postgres(self) -> None:
        try:
            import psycopg  # noqa: PLC0415
            from psycopg.types.json import Jsonb  # noqa: PLC0415
        except ImportError:
            log.error(
                "cdr.backend=%s but psycopg is not installed "
                "(pip install 'openivr[postgres]' or psycopg[binary])",
                self.backend,
            )
            self.backend = "csv" if self.backend == "both" else "none"
            if self.backend == "csv" and self.csv_path is None:
                self.csv_path = self.cfg.path(self.cfg.cdr.csv_file)
                self.csv_path.parent.mkdir(parents=True, exist_ok=True)
                self._ensure_header()
            return
        pg = self.cfg.cdr.postgres
        dsn = os.environ.get(
            "OPENIVR_DB_DSN",
            f"host={pg.host} port={pg.port} dbname={pg.dbname} user={pg.user} "
            f"password={pg.password or ''}",
        )
        self._pg_pool = (psycopg, dsn, Jsonb)
        log.info("PostgreSQL CDR backend enabled for %s/%s", pg.host, pg.dbname)

    async def write(self, record: CallRecord) -> None:
        if self.backend == "none":
            return
        rows = record.rows()
        if not rows:
            return
        if self.csv_path is not None:
            await anyio.to_thread.run_sync(self._write_csv, rows)
        if self._pg_pool is not None:
            await anyio.to_thread.run_sync(self._write_pg, record)

    def _write_csv(self, rows: list[dict[str, Any]]) -> None:
        if self.csv_path is None:
            return
        try:
            with self._lock, self.csv_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
                writer.writerows(rows)
        except OSError as exc:
            log.error("Cannot write CDR CSV %s: %s", self.csv_path, exc)

    def _write_pg(self, record: CallRecord) -> None:
        if self._pg_pool is None:
            return
        psycopg, dsn, jsonb = self._pg_pool
        try:
            with psycopg.connect(dsn, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO cdr
                            (call_id, channel_id, caller, started_at, ended_at,
                             duration, outcome, cause, digits, variables, events)
                        VALUES (%s, %s, %s, to_timestamp(%s), now(), %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            record.call_id,
                            record.channel_id,
                            record.caller,
                            record.started,
                            record.duration,
                            record.outcome or "unknown",
                            record.cause or "normal",
                            ",".join(record.digits),
                            jsonb(record.variables),
                            jsonb(record.events),
                        ),
                    )
        except Exception as exc:  # noqa: BLE001
            log.error("Cannot write CDR to PostgreSQL: %s", exc)

    async def close(self) -> None:
        log.info("CDR backend '%s' closed", self.backend)
