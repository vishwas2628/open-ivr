"""DTMF collection helpers.

``DigitStream`` is a small async mailbox that the IVR state machine feeds from
``ChannelDtmfReceived`` events and the menu loop reads from. It also provides
barge-in (stop the current playback) and multi-digit collection with an
interdigit timeout.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field

import anyio

TERMINATORS = {"#", "*"}


@dataclass(slots=True)
class Collected:
    """Result of a multi-digit collection."""

    digits: str = ""
    terminator: str = ""
    timed_out: bool = False
    complete: bool = False
    trace: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.digits


class DigitStream:
    """Thread-safe (single event loop) mailbox of DTMF digits."""

    def __init__(self, max_buffer: int = 32) -> None:
        self._send, self._recv = anyio.create_memory_object_stream[str](max_buffer_size=max_buffer)
        self._closed = False

    def push(self, digit: str) -> None:
        """Non-blocking enqueue of a single digit."""
        if self._closed or not digit:
            return
        try:
            self._send.send_nowait(digit)
        except (anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass

    async def push_async(self, digit: str) -> None:
        if self._closed or not digit:
            return
        try:
            await self._send.send(digit)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass

    def drain(self) -> None:
        """Drop every buffered digit (used when (re)prompting)."""
        while True:
            try:
                self._recv.receive_nowait()
            except (anyio.WouldBlock, anyio.EndOfStream):
                return

    async def next_digit(self, timeout: float | None) -> str | None:
        """Wait up to *timeout* seconds for one digit; ``None`` on timeout/close."""
        if self._closed:
            return None
        if timeout is None:
            try:
                return await self._recv.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return None
        with anyio.move_on_after(timeout):
            try:
                return await self._recv.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                return None
        return None

    async def collect(
        self,
        *,
        min_len: int = 1,
        max_len: int = 4,
        first_timeout: float = 8.0,
        interdigit_timeout: float = 4.0,
        terminator: str | None = "#",
    ) -> Collected:
        """Collect ``min_len``..``max_len`` digits, honouring timeouts."""
        res = Collected()
        target = max(1, max_len)
        term = None if terminator is None else terminator
        while len(res.digits) < target:
            budget = first_timeout if not res.digits else interdigit_timeout
            if budget is not None and budget <= 0:
                res.timed_out = True
                break
            digit = await self.next_digit(budget)
            if digit is None:
                res.timed_out = True
                break
            res.trace.append(digit)
            if term and digit == term:
                res.terminator = digit
                res.complete = len(res.digits) >= min_len
                break
            res.digits += digit
            res.complete = len(res.digits) >= min_len
        if term is None:
            res.complete = len(res.digits) >= min_len
        return res

    async def aclose(self) -> None:
        self._closed = True
        try:
            await self._send.aclose()
            await self._recv.aclose()
        except Exception:  # noqa: BLE001 - closing must never raise
            pass


class RecordingDigitSink:
    """Utility: turn an ARI ChannelDtmfReceived event into a plain digit."""

    VALID = frozenset(string.digits + string.ascii_uppercase + "*#")

    @staticmethod
    def digit_of(event) -> str:
        return str(getattr(event, "digit", "") or "")

    @classmethod
    def is_digit(cls, event) -> bool:
        return cls.digit_of(event) in cls.VALID
