"""DTMF mailbox behaviour (offline: no Asterisk involved)."""

from __future__ import annotations

import anyio
import pytest

from openivr.dtmf import Collected, DigitStream, RecordingDigitSink

pytestmark = pytest.mark.anyio


async def test_next_digit_returns_pushed_value() -> None:
    stream = DigitStream()
    async with anyio.create_task_group() as tg:
        tg.start_soon(stream.push_async, "5")
        assert await stream.next_digit(1) == "5"
        tg.cancel_scope.cancel()
    await stream.aclose()


async def test_next_digit_times_out() -> None:
    stream = DigitStream()
    assert await stream.next_digit(0.05) is None
    await stream.aclose()


async def test_next_digit_without_timeout_waits_for_push() -> None:
    stream = DigitStream()

    async def later() -> None:
        await anyio.sleep(0.05)
        stream.push("#")

    async with anyio.create_task_group() as tg:
        tg.start_soon(later)
        assert await stream.next_digit(None) == "#"
        tg.cancel_scope.cancel()
    await stream.aclose()


async def test_drain_drops_buffered_digits() -> None:
    stream = DigitStream()
    for digit in "123":
        stream.push(digit)
    stream.drain()
    assert await stream.next_digit(0.05) is None
    await stream.aclose()


async def test_collect_stops_at_max_length() -> None:
    stream = DigitStream()

    async def feed() -> None:
        for digit in "12345":
            await anyio.sleep(0)
            stream.push(digit)

    async with anyio.create_task_group() as tg:
        tg.start_soon(feed)
        result = await stream.collect(min_len=2, max_len=3, first_timeout=1, interdigit_timeout=1)
        tg.cancel_scope.cancel()
    assert result.digits == "123"
    assert result.complete is True
    assert result.terminator == ""
    assert result.timed_out is False
    assert result.trace == ["1", "2", "3"]
    await stream.aclose()


async def test_collect_terminator_and_min_length() -> None:
    stream = DigitStream()

    async def feed() -> None:
        for digit in ("1", "2", "#"):
            await anyio.sleep(0)
            stream.push(digit)

    async with anyio.create_task_group() as tg:
        tg.start_soon(feed)
        result = await stream.collect(min_len=2, max_len=6, first_timeout=1, interdigit_timeout=1)
        tg.cancel_scope.cancel()
    assert result.digits == "12"
    assert result.terminator == "#"
    assert result.complete is True
    await stream.aclose()


async def test_collect_terminator_below_min_length_is_incomplete() -> None:
    stream = DigitStream()

    async def feed() -> None:
        for digit in ("7", "#"):
            await anyio.sleep(0)
            stream.push(digit)

    async with anyio.create_task_group() as tg:
        tg.start_soon(feed)
        result = await stream.collect(min_len=3, max_len=6, first_timeout=1, interdigit_timeout=1)
        tg.cancel_scope.cancel()
    assert result.digits == "7"
    assert result.complete is False
    await stream.aclose()


async def test_collect_first_digit_timeout() -> None:
    stream = DigitStream()
    result = await stream.collect(min_len=1, max_len=4, first_timeout=0.05, interdigit_timeout=0.05)
    assert result.digits == ""
    assert result.timed_out is True
    assert result.complete is False
    await stream.aclose()


async def test_collect_interdigit_timeout_keeps_partial() -> None:
    stream = DigitStream()

    async def feed() -> None:
        stream.push("4")
        await anyio.sleep(0.4)

    async with anyio.create_task_group() as tg:
        tg.start_soon(feed)
        result = await stream.collect(
            min_len=1, max_len=6, first_timeout=0.1, interdigit_timeout=0.1
        )
        tg.cancel_scope.cancel()
    assert result.digits == "4"
    assert result.timed_out is True
    assert result.complete is True
    await stream.aclose()


async def test_collect_without_terminator_fills_max() -> None:
    stream = DigitStream()

    async def feed() -> None:
        for digit in "9876":
            await anyio.sleep(0)
            stream.push(digit)

    async with anyio.create_task_group() as tg:
        tg.start_soon(feed)
        result = await stream.collect(
            min_len=1, max_len=4, first_timeout=1, interdigit_timeout=1, terminator=None
        )
        tg.cancel_scope.cancel()
    assert result.digits == "9876"
    assert result.complete is True
    await stream.aclose()


async def test_push_after_close_is_harmless() -> None:
    stream = DigitStream()
    await stream.aclose()
    stream.push("1")
    await stream.push_async("2")
    assert await stream.next_digit(0.05) is None


async def test_buffer_overflow_is_dropped() -> None:
    stream = DigitStream(max_buffer=2)
    for digit in "12345":
        stream.push(digit)
    got = [await stream.next_digit(0.2) for _ in range(2)]
    assert got == ["1", "2"]
    await stream.aclose()


def test_recording_digit_sink() -> None:
    class Event:
        digit = "7"

    class Empty:
        digit = ""

    assert RecordingDigitSink.digit_of(Event()) == "7"
    assert RecordingDigitSink.digit_of(Empty()) == ""
    assert RecordingDigitSink.is_digit(Event()) is True
    assert RecordingDigitSink.is_digit(Empty()) is False
    assert RecordingDigitSink.is_digit(type("E", (), {"digit": "*"})()) is True


def test_collected_str() -> None:
    assert str(Collected(digits="42")) == "42"


@pytest.mark.parametrize("timeout", [None, 0.05, 1])
async def test_next_digit_never_raises(timeout: float | None) -> None:
    stream = DigitStream()
    with anyio.move_on_after(0.4):
        await stream.next_digit(timeout)
    await stream.aclose()
