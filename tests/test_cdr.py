"""IVR event log: shape, CSV output, UNIQUEID keying."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from openivr.cdr import CSV_COLUMNS, CallRecord, CdrWriter

pytestmark = pytest.mark.anyio


def make_record(channel_id: str = "1699.1") -> CallRecord:
    record = CallRecord(channel_id=channel_id, caller="1001")
    record.event("stasis_start", menu="main")
    record.event("menu", menu="main", digit="1", detail="Sales")
    record.event("collected", menu="main", detail="acct=1234")
    record.digits.extend(["1", "2"])
    record.variables["acct"] = "1234"
    record.outcome = "hangup"
    record.cause = "normal"
    return record


def test_rows_are_sequential_and_typed() -> None:
    rows = make_record().rows()
    assert [row["seq"] for row in rows] == [0, 1, 2]
    assert rows[1]["digit"] == "1"
    assert rows[1]["menu"] == "main"
    assert rows[2]["detail"] == "acct=1234"
    assert all(row["channel_id"] == "1699.1" for row in rows)
    assert all(row["caller"] == "1001" for row in rows)
    assert all(isinstance(row["duration"], float) for row in rows)


def test_summary_includes_duration() -> None:
    summary = make_record().summary()
    assert summary["outcome"] == "hangup"
    assert summary["variables"] == {"acct": "1234"}
    assert "duration" in summary


def test_empty_record_has_no_rows() -> None:
    assert CallRecord().rows() == []


def test_call_id_is_uniquedid_not_uuid(cfg) -> None:
    """CallRecord.call_id should be set from Asterisk UNIQUEID (passed in)."""
    record = CallRecord(call_id="test-uniqueid-123", channel_id="test-uniqueid-123")
    assert record.call_id == "test-uniqueid-123"
    assert record.channel_id == "test-uniqueid-123"
    # Not a uuid4 hex
    assert len(record.call_id) != 16 or "-" in record.call_id


def test_writer_creates_header_once(cfg) -> None:
    cfg.cdr.backend = "csv"
    cfg.cdr.csv_file = "data/logs/cdr.csv"
    writer = CdrWriter(cfg)
    path = Path(cfg.path("data/logs/cdr.csv"))
    assert path.read_text(encoding="utf-8").splitlines()[0] == ",".join(CSV_COLUMNS)

    CdrWriter(cfg)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert writer.backend == "csv"


async def test_csv_rows_are_appended(cfg) -> None:
    cfg.cdr.backend = "csv"
    cfg.cdr.csv_file = "data/logs/cdr.csv"
    await CdrWriter(cfg).write(make_record())
    await CdrWriter(cfg).write(make_record("1699.2"))

    path = Path(cfg.path("data/logs/cdr.csv"))
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert len(rows) == 6
    assert [row["event"] for row in rows[:3]] == ["stasis_start", "menu", "collected"]
    assert rows[3]["channel_id"] == "1699.2"


async def test_csv_escapes_broken_detail(cfg) -> None:
    cfg.cdr.backend = "csv"
    cfg.cdr.csv_file = "data/logs/cdr.csv"
    record = CallRecord(channel_id="1.1", caller='"weird",caller')
    record.event("collected", detail='a,b"c\nd')
    await CdrWriter(cfg).write(record)

    with Path(cfg.path("data/logs/cdr.csv")).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["detail"] == 'a,b"c\nd'
    assert rows[0]["caller"] == '"weird",caller'


async def test_disabled_backend_writes_nothing(cfg) -> None:
    cfg.cdr.enabled = False
    cfg.cdr.backend = "csv"
    writer = CdrWriter(cfg)
    await writer.write(make_record())
    assert writer.backend == "none"
    assert not Path(cfg.path("data/logs/cdr.csv")).exists()


async def test_none_backend(cfg) -> None:
    cfg.cdr.backend = "none"
    writer = CdrWriter(cfg)
    await writer.write(make_record())
    assert writer.backend == "none"


async def test_invalid_backend_defaults_to_csv(cfg) -> None:
    """postgres/both are removed - should fall back to csv."""
    cfg.cdr.backend = "postgres"
    writer = CdrWriter(cfg)
    assert writer.backend == "csv"

    cfg.cdr.backend = "both"
    writer = CdrWriter(cfg)
    assert writer.backend == "csv"

    cfg.cdr.backend = "unknown"
    writer = CdrWriter(cfg)
    assert writer.backend == "csv"


async def test_write_skips_empty_records(cfg) -> None:
    cfg.cdr.backend = "csv"
    cfg.cdr.csv_file = "data/logs/cdr.csv"
    await CdrWriter(cfg).write(CallRecord())
    text = Path(cfg.path("data/logs/cdr.csv")).read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1