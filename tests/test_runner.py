"""The ARI event plumbing in ``Service``.

asyncari registers an event listener in ``_EventHandler.__aenter__`` - iterating
the handler without ``async with`` looks like it works but silently receives
nothing, which would mean the IVR never answers a single call.  The fake client
below reproduces that contract: events are only delivered to a handler that was
entered.
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from openivr.originate import DIALED_ARG, is_dialed_leg
from openivr.runner import Service

pytestmark = pytest.mark.anyio


class FakeEvent:
    def __init__(self, channel: FakeChannel, args: list[str] | None = None) -> None:
        self.channel = channel
        self.args = args or []


class FakeListener:
    def __init__(self, bus: FakeBus, kind: str) -> None:
        self.bus = bus
        self.kind = kind
        self._send, self._recv = anyio.create_memory_object_stream(max_buffer_size=10)
        self.registered = False

    async def __aenter__(self) -> FakeListener:
        self.registered = True
        self.bus.listeners.append(self)
        return self

    async def __aexit__(self, *tb: Any) -> None:
        self.bus.listeners.remove(self)

    def __aiter__(self) -> FakeListener:
        return self

    async def __anext__(self):
        event = await self._recv.receive()
        return {"channel": event.channel}, event


class FakeBus:
    def __init__(self) -> None:
        self.listeners: list[FakeListener] = []

    def emit(self, kind: str, event: Any) -> None:
        for listener in list(self.listeners):
            if listener.kind == kind:
                listener._send.send_nowait(event)

    def on_channel_event(self, kind: str) -> FakeListener:
        return FakeListener(self, kind)


class FakeChannel:
    def __init__(self, channel_id: str) -> None:
        self.id = channel_id
        self.name = f"PJSIP/{channel_id}@test"


class FakeClient:
    def __init__(self) -> None:
        self.bus = FakeBus()
        self.taskgroup = anyio.create_task_group  # replaced by the runner context

    def on_channel_event(self, kind: str) -> FakeListener:
        return self.bus.on_channel_event(kind)


def make_service(cfg) -> Service:
    service = Service(cfg)
    handled: list[tuple[str, list[str]]] = []

    async def fake_handle(channel, args: list[str]) -> None:
        handled.append((channel.id, args))

    service._handle_call = fake_handle  # type: ignore[method-assign]
    service._seen = handled  # type: ignore[attr-defined]
    return service


async def test_stasis_start_reaches_the_call_handler(cfg) -> None:
    """The regression: the listener must be entered, not just iterated."""
    service = make_service(cfg)
    client = FakeClient()

    async with anyio.create_task_group() as tg:
        client.taskgroup = tg
        tg.start_soon(service._listen, client)
        await anyio.sleep(0.05)
        client.bus.emit("StasisStart", FakeEvent(FakeChannel("1699.1")))
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    assert service._seen == [("1699.1", [])]


async def test_originated_legs_are_left_to_originate_module(cfg) -> None:
    service = make_service(cfg)
    client = FakeClient()

    async with anyio.create_task_group() as tg:
        client.taskgroup = tg
        tg.start_soon(service._listen, client)
        await anyio.sleep(0.05)
        event = FakeEvent(FakeChannel("1699.2"), [DIALED_ARG, "abc123"])
        assert is_dialed_leg(event)
        client.bus.emit("StasisStart", event)
        await anyio.sleep(0.05)
        tg.cancel_scope.cancel()

    assert service._seen == []


async def test_listener_is_closed_on_shutdown(cfg) -> None:
    """``__aexit__`` must unregister so a reconnect starts from a clean slate."""
    service = make_service(cfg)
    client = FakeClient()

    async def listen_then_stop() -> None:
        async with anyio.create_task_group() as tg:
            client.taskgroup = tg
            tg.start_soon(service._listen, client)
            await anyio.sleep(0.05)
            tg.cancel_scope.cancel()

    await listen_then_stop()
    assert client.bus.listeners == []
