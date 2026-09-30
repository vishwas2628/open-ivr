"""``dial.mode: originate`` - originate, bridge, tear down.

The fakes below mimic the parts of ``asyncari.Client`` the module touches:
``channels.originate``, ``bridges.create`` and the *listener* context manager
that ``on_channel_event`` returns (which only registers on ``__aenter__``).
"""

from __future__ import annotations

from typing import Any

import anyio
import pytest

from openivr.originate import DIALED_ARG, originate_and_bridge

pytestmark = pytest.mark.anyio


class FakeEvent:
    def __init__(self, channel: FakeChannel, args: list[str] | None = None) -> None:
        self.channel = channel
        self.args = args or []


class FakeListener:
    """Registered on ``__aenter__``; feeds events injected by the test."""

    def __init__(self, bus: FakeBus, kind: str) -> None:
        self.bus = bus
        self.kind = kind
        self._send, self._recv = anyio.create_memory_object_stream(max_buffer_size=10)

    async def __aenter__(self) -> FakeListener:
        self.bus.open.append(self.kind)
        self.bus.listeners.append(self)
        return self

    async def __aexit__(self, *tb: Any) -> None:
        self.bus.closed.append(self.kind)
        self.bus.listeners.remove(self)

    def __aiter__(self) -> FakeListener:
        return self

    async def __anext__(self):
        return {}, await self._recv.receive()


class FakeBus:
    def __init__(self) -> None:
        self.listeners: list[FakeListener] = []
        self.open: list[str] = []
        self.closed: list[str] = []

    def emit(self, kind: str, event: FakeEvent) -> None:
        for listener in list(self.listeners):
            if listener.kind == kind:
                listener._send.send_nowait(event)

    def on_channel_event(self, kind: str) -> FakeListener:
        return FakeListener(self, kind)


class FakeChannel:
    def __init__(self, channel_id: str, caller: str = "") -> None:
        self.id = channel_id
        self.caller_id_num = caller
        self.answered = False
        self.hungup = False

    async def answer(self) -> None:
        self.answered = True

    async def hangup(self) -> None:
        self.hungup = True


class FakeBridge:
    def __init__(self, bridge_id: str) -> None:
        self.id = bridge_id
        self.channels: list[str] = []
        self.destroyed = False

    async def addChannel(self, channel: str) -> None:  # noqa: N802 - ARI name
        self.channels.append(channel)

    async def destroy(self) -> None:
        self.destroyed = True


class FakeChannels:
    def __init__(self, bus: FakeBus, channel: FakeChannel | None, fail: str = "") -> None:
        self.bus = bus
        self.channel = channel
        self.fail = fail
        self.kwargs: dict[str, Any] = {}

    async def originate(self, **kwargs: Any) -> FakeChannel:
        self.kwargs = kwargs
        if self.fail:
            raise RuntimeError(self.fail)
        token = kwargs["appArgs"][1]
        # Asterisk answers nothing yet; StasisStart arrives on the bus.
        self.bus.emit("StasisStart", FakeEvent(self.channel, [DIALED_ARG, token]))
        return self.channel


class FakeBridges:
    def __init__(self) -> None:
        self.bridges: list[FakeBridge] = []
        self.fail = ""

    async def create(self, **kwargs: Any) -> FakeBridge:
        if self.fail:
            raise RuntimeError(self.fail)
        bridge = FakeBridge(str(kwargs.get("bridgeId")))
        self.bridges.append(bridge)
        return bridge


class FakeClient:
    def __init__(
        self, channel: FakeChannel | None = None, *, fail: str = "", bridge_fail: str = ""
    ) -> None:
        self.bus = FakeBus()
        self.channels = FakeChannels(self.bus, channel, fail)
        self.bridges = FakeBridges()
        self.bridges.fail = bridge_fail

    def on_channel_event(self, kind: str) -> FakeListener:
        return self.bus.on_channel_event(kind)


async def test_bridge_and_teardown() -> None:
    incoming = FakeChannel("1699.1", caller="1001")
    outgoing = FakeChannel("1699.2")
    client = FakeClient(outgoing)

    async def end_the_call() -> None:
        with anyio.move_on_after(3):
            while not client.bridges.bridges:
                await anyio.sleep(0.01)
        token = client.channels.kwargs["appArgs"][1]
        await anyio.sleep(0.05)
        client.bus.emit("StasisEnd", FakeEvent(outgoing, [DIALED_ARG, token]))

    async with anyio.create_task_group() as tg:
        tg.start_soon(end_the_call)
        result = await originate_and_bridge(
            client, incoming, endpoint="PJSIP/1002", app="openivr", guard=30
        )
        tg.cancel_scope.cancel()

    assert result.ok
    assert result.channel_id == "1699.2"
    assert outgoing.answered, "the dialed leg must be answered on StasisStart"
    bridge = client.bridges.bridges[0]
    assert bridge.channels == ["1699.1", "1699.2"]
    assert bridge.destroyed
    assert outgoing.hungup and incoming.hungup
    assert sorted(client.bus.closed) == ["StasisEnd", "StasisStart"]


async def test_listeners_are_registered_before_originate() -> None:
    """A missed StasisStart leaves the leg ringing, a missed StasisEnd hangs the call."""
    incoming = FakeChannel("1699.1", caller="1001")
    outgoing = FakeChannel("1699.2")
    client = FakeClient(outgoing)
    order: list[str] = []

    original = client.channels.originate

    async def spy(**kwargs: Any):
        order.append("originate")
        return await original(**kwargs)

    client.channels.originate = spy  # type: ignore[method-assign]

    async with anyio.create_task_group() as tg:
        tg.start_soon(end_immediately, client)
        result = await originate_and_bridge(
            client, incoming, endpoint="PJSIP/1002", app="openivr", guard=30
        )
        tg.cancel_scope.cancel()
    assert order == ["originate"]
    assert result.ok
    assert client.bus.open[:2] == ["StasisStart", "StasisEnd"]


async def end_immediately(client: FakeClient) -> None:
    with anyio.move_on_after(2):
        while "originate" not in client.channels.kwargs:
            await anyio.sleep(0.01)
    token = client.channels.kwargs["appArgs"][1]
    client.bus.emit("StasisEnd", FakeEvent(client.channels.channel, [DIALED_ARG, token]))


async def test_originate_failure_is_reported() -> None:
    incoming = FakeChannel("1699.1", caller="1001")
    client = FakeClient(FakeChannel("1699.2"), fail="Originate failed: No such channel")
    result = await originate_and_bridge(client, incoming, endpoint="PJSIP/nope", app="openivr")
    assert not result.ok
    assert "No such channel" in result.reason
    assert incoming.hungup


async def test_bridge_failure_hangs_up_the_leg() -> None:
    incoming = FakeChannel("1699.1", caller="1001")
    outgoing = FakeChannel("1699.2")
    client = FakeClient(outgoing, bridge_fail="Bridge creation failed")
    result = await originate_and_bridge(
        client, incoming, endpoint="PJSIP/1002", app="openivr", guard=30
    )
    assert result.ok
    assert outgoing.hungup and incoming.hungup


async def test_caller_id_defaults_to_the_inbound_caller() -> None:
    incoming = FakeChannel("1699.1", caller="1001")
    client = FakeClient(FakeChannel("1699.2"))
    await originate_and_bridge(
        client, incoming, endpoint="PJSIP/1002", app="openivr", caller_id="", guard=1
    )
    assert client.channels.kwargs["callerId"] == "1001"


async def test_explicit_caller_id_wins() -> None:
    incoming = FakeChannel("1699.1", caller="1001")
    client = FakeClient(FakeChannel("1699.2"))
    await originate_and_bridge(
        client, incoming, endpoint="PJSIP/1002", app="openivr", caller_id="openivr", guard=1
    )
    assert client.channels.kwargs["callerId"] == "openivr"
