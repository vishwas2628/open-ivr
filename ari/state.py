"""State machine framework for Asterisk ARI channels and bridges."""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

import anyio
from anyio.abc import TaskGroup, TaskStatus
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from .connection import may_not_exist
from .exceptions import ChannelExit, DialFailed, StateError

if TYPE_CHECKING:
    from .client import Client
    from .model import Bridge, Channel

log = logging.getLogger(__name__)


class DTMFHandler:
    """Mix-in that routes incoming DTMF tones to specific method hooks.

    Calls:
    - ``on_dtmf_{0-9,A-D,Star,Pound}`` (e.g. ``on_dtmf_1``, ``on_dtmf_Star``)
    - ``on_dtmf_digit`` if digit in '0'-'9'
    - ``on_dtmf_letter`` if digit in 'A'-'D'
    - ``on_dtmf`` fallback
    """

    async def on_ChannelDtmfReceived(self, evt: Any) -> Any:
        digit = str(getattr(evt, "digit", "") or "")
        match digit:
            case "#":
                normalized = "Pound"
            case "*":
                normalized = "Star"
            case _:
                normalized = digit

        proc = getattr(self, f"on_dtmf_{normalized}", None)
        if proc is None and "0" <= digit <= "9":
            proc = getattr(self, "on_dtmf_digit", None)
        if proc is None and "A" <= digit <= "D":
            proc = getattr(self, "on_dtmf_letter", None)
        if proc is None:
            proc = getattr(self, "on_dtmf", None)

        if proc is None:
            log.debug("Unhandled DTMF %s on %s", digit, self)
            return False

        res = proc(evt)
        if inspect.iscoroutine(res):
            res = await res
        return res


class BaseEvtHandler:
    """Core asynchronous event-driven state machine."""

    def __init__(
        self,
        client: Client | None,
        taskgroup: TaskGroup | None = None,
        ready: anyio.Event | None = None,
    ) -> None:
        self.client = client
        self._base_tg = taskgroup or (client.taskgroup if client else None)
        self._ready = ready
        self._done: anyio.Event | None = None
        self._tg: TaskGroup | None = None
        self._qw: MemoryObjectSendStream[Any] | None = None
        self._qr: MemoryObjectReceiveStream[Any] | None = None
        self._sub: BaseEvtHandler | None = None
        self._running = False

    @property
    def taskgroup(self) -> TaskGroup:
        """Return the active task group for this state machine."""
        if self._tg is not None:
            return self._tg
        if self._base_tg is not None:
            return self._base_tg
        raise RuntimeError("No taskgroup available on state handler")

    async def start_task(self) -> None:
        """Start the state machine as a background task in the base task group."""
        if self._base_tg is None:
            raise RuntimeError("Cannot start_task without a base taskgroup")
        await self._base_tg.start(self._run_ctx)

    async def _run_ctx(self, *, task_status: TaskStatus[None]) -> None:
        if self._done is None:
            self._done = anyio.Event()
        self._qw, self._qr = anyio.create_memory_object_stream(100)
        self._running = True

        try:
            async with anyio.create_task_group() as tg:
                self._tg = tg
                await tg.start(self.run)
                task_status.started()
                await self._done.wait()
                tg.cancel_scope.cancel()
        finally:
            self._running = False
            self._tg = None
            if self._qw is not None:
                try:
                    self._qw.close()
                except Exception:
                    pass

    async def run(self, *, task_status: TaskStatus[None] | None = None) -> None:
        """Execute the event loop: calls on_start() then processes queued events."""
        if task_status is not None:
            task_status.started()

        await self.on_start()

        if self._ready is not None:
            self._ready.set()

        if self._qr is None:
            self._qw, self._qr = anyio.create_memory_object_stream(100)

        while True:
            try:
                evt = await self._qr.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                break
            try:
                await self._dispatch(evt)
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:
                log.exception("Error dispatching event %s in %s: %s", getattr(evt, "type", evt), self, exc)

    async def on_start(self) -> None:
        """Hook called when the state machine starts."""

    async def handle(self, evt: Any) -> None:
        """Feed an event into this state handler or its active sub-handler."""
        if self._sub is not None:
            await self._sub.handle(evt)
        else:
            if self._qw is not None:
                try:
                    self._qw.send_nowait(evt)
                except anyio.WouldBlock:
                    await self._qw.send(evt)
                except anyio.ClosedResourceError:
                    pass

    async def _dispatch(self, evt: Any) -> Any:
        typ = str(getattr(evt, "type", evt))
        handler = getattr(self, f"on_{typ}", None)
        if handler is None:
            return False
        res = handler(evt)
        if inspect.iscoroutine(res):
            res = await res
        return res

    async def done(self, result: Any = None) -> None:
        """Signal that this state machine has finished."""
        if self._done is not None and not self._done.is_set():
            self._done.set()
        if self._tg is not None:
            self._tg.cancel_scope.cancel()

    def __await__(self) -> Any:
        if self._done is None:
            self._done = anyio.Event()
        return self._done.wait().__await__()


class ChannelState(BaseEvtHandler):
    """Generic state machine for an inbound or outbound channel."""

    def __init__(self, channel: Channel, **kwargs: Any) -> None:
        self.channel = channel
        self.last_cause: Any = None
        self._channel_unsub: Any = None
        super().__init__(channel.client, **kwargs)

    async def run(self, *, task_status: TaskStatus[None] | None = None) -> None:
        self._channel_unsub = self.channel.on_event("*", self.handle)
        try:
            await super().run(task_status=task_status)
        finally:
            if self._channel_unsub is not None:
                self._channel_unsub.close()
                self._channel_unsub = None

    async def on_DialResult(self, evt: Any) -> None:
        dialstatus = str(getattr(evt, "dialstatus", "") or "")
        if dialstatus != "ANSWER":
            raise DialFailed(dialstatus, self.last_cause)

    async def on_ChannelHangupRequest(self, evt: Any) -> None:
        cause = getattr(evt, "cause", None)
        if cause is not None:
            self.last_cause = cause

    async def on_ChannelDestroyed(self, evt: Any) -> None:
        await self.done()

    async def on_StasisEnd(self, evt: Any) -> None:
        await self.done()


class ToplevelChannelState(ChannelState):
    """Channel state machine that unconditionally hangs up its channel on exit."""

    async def run(self, *, task_status: TaskStatus[None] | None = None) -> None:
        try:
            await super().run(task_status=task_status)
        except (ChannelExit, StateError):
            pass
        finally:
            with anyio.move_on_after(2, shield=True):
                await self.channel.handle_exit()

    async def hang_up(self, reason: str = "normal") -> None:
        """Set reason and hang up channel."""
        self.channel.set_reason(reason)
        await self.channel.hang_up()

    hangup = hang_up

    async def done(self, result: Any = None) -> None:
        with anyio.move_on_after(1, shield=True):
            await self.channel.hang_up()
        await super().done(result=result)


class BridgeState(BaseEvtHandler):
    """Generic state machine for a bridge; auto-destroys the bridge on exit."""

    def __init__(self, bridge: Bridge, **kwargs: Any) -> None:
        self.bridge = bridge
        self._bridge_unsub: Any = None
        super().__init__(bridge.client, **kwargs)

    async def run(self, *, task_status: TaskStatus[None] | None = None) -> None:
        self._bridge_unsub = self.bridge.on_event("*", self.handle)
        try:
            await super().run(task_status=task_status)
        finally:
            if self._bridge_unsub is not None:
                self._bridge_unsub.close()
                self._bridge_unsub = None
            with anyio.move_on_after(2, shield=True):
                with may_not_exist():
                    await self.bridge.destroy()
