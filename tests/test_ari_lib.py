"""Comprehensive test suite for the new standalone 'ari' library."""

from __future__ import annotations

import anyio
import pytest

import ari
from ari.connection import HttpClient, may_not_exist
from ari.events import EventListener, EventMessage
from ari.exceptions import (
    BadRequest,
    ChannelExit,
    Conflict,
    DialFailed,
    HTTPError,
    NotFound,
)
from ari.model import Bridge, Channel, LiveRecording, Playback
from ari.state import BaseEvtHandler, DTMFHandler, ToplevelChannelState

pytestmark = pytest.mark.anyio


class MockHttpClient:
    """Mock HTTP client recording requests and returning canned responses."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict | None]] = []
        self.responses: dict[tuple[str, str], Any] = {}

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_data: Any = None,
        headers: dict | None = None,
    ) -> Any:
        clean_path = path.lstrip("/")
        self.calls.append((method.upper(), clean_path, params or {}, json_data))
        key = (method.upper(), clean_path)
        if key in self.responses:
            resp = self.responses[key]
            if isinstance(resp, Exception):
                raise resp
            return resp
        return None

    async def get(self, path: str, *, params: dict | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, *, params: dict | None = None, json_data: Any = None) -> Any:
        return await self.request("POST", path, params=params, json_data=json_data)

    async def put(self, path: str, *, params: dict | None = None, json_data: Any = None) -> Any:
        return await self.request("PUT", path, params=params, json_data=json_data)

    async def delete(self, path: str, *, params: dict | None = None) -> Any:
        return await self.request("DELETE", path, params=params)

    async def close(self) -> None:
        pass


def make_test_client(mock_http: MockHttpClient | None = None) -> ari.Client:
    http = mock_http or MockHttpClient()
    client = ari.Client(
        taskgroup=None,  # type: ignore
        base_url="http://127.0.0.1:8088",
        apps=["test_app"],
        username="user",
        password="pass",
        http_client=http,  # type: ignore
    )
    return client


async def test_client_initialization() -> None:
    client = make_test_client()
    assert client.base_url == "http://127.0.0.1:8088"
    assert client.apps == ["test_app"]
    assert client.channels is not None
    assert client.bridges is not None
    assert client.playbacks is not None
    assert client.recordings is not None
    assert client.asterisk is not None
    assert client.sounds is not None
    assert client.generate_id("C").startswith("ARI.")


async def test_channels_repository() -> None:
    http = MockHttpClient()
    http.responses[("POST", "channels")] = {"id": "chan123", "name": "PJSIP/1001-0001", "state": "Down"}
    http.responses[("GET", "channels")] = [{"id": "chan123", "name": "PJSIP/1001-0001", "state": "Up"}]
    http.responses[("GET", "channels/chan123")] = {"id": "chan123", "name": "PJSIP/1001-0001", "state": "Up"}
    http.responses[("POST", "channels/chan123/play")] = {"id": "play456", "state": "queued"}
    http.responses[("POST", "channels/chan123/record")] = {"name": "rec789", "format": "wav"}
    http.responses[("GET", "channels/chan123/variable")] = {"value": "world"}
    http.responses[("POST", "channels/externalMedia")] = {"id": "ext1", "name": "UnicastRTP/127.0.0.1"}

    client = make_test_client(http)

    chan = await client.channels.originate(endpoint="PJSIP/1001", app="test_app")
    assert isinstance(chan, Channel)
    assert chan.id == "chan123"

    chans = await client.channels.list()
    assert len(chans) == 1
    assert chans[0].id == "chan123"

    fetched = await client.channels.get("chan123")
    assert fetched.id == "chan123"

    playback = await client.channels.play("chan123", media="sound:beep")
    assert isinstance(playback, Playback)
    assert playback.id == "play456"

    recording = await client.channels.record("chan123", name="rec789", format="wav")
    assert isinstance(recording, LiveRecording)
    assert recording.name == "rec789"

    ext_chan = await client.channels.externalMedia(app="test_app", external_host="127.0.0.1:5000")
    assert isinstance(ext_chan, Channel)
    assert ext_chan.id == "ext1"

    var_val = await client.channels.getChannelVar("chan123", "hello")
    assert var_val == {"value": "world"}

    await client.channels.answer("chan123")
    await client.channels.hangup("chan123", reason="normal")
    await client.channels.setChannelVar("chan123", "foo", "bar")
    await client.channels.continueInDialplan("chan123", context="default", extension="100")
    await client.channels.ring("chan123")
    await client.channels.ringStop("chan123")
    await client.channels.mute("chan123")
    await client.channels.unmute("chan123")
    await client.channels.hold("chan123")
    await client.channels.stopHold("chan123")
    await client.channels.startMoh("chan123")
    await client.channels.stopMoh("chan123")
    await client.channels.startSilence("chan123")
    await client.channels.stopSilence("chan123")
    await client.channels.sendDTMF("chan123", dtmf="123")


async def test_bridges_repository() -> None:
    http = MockHttpClient()
    http.responses[("POST", "bridges")] = {"id": "br123", "name": "test_bridge"}
    http.responses[("GET", "bridges")] = [{"id": "br123", "name": "test_bridge"}]
    http.responses[("GET", "bridges/br123")] = {"id": "br123", "name": "test_bridge"}

    client = make_test_client(http)
    bridge = await client.bridges.create(type="mixing", name="test_bridge")
    assert isinstance(bridge, Bridge)
    assert bridge.id == "br123"

    bridges = await client.bridges.list()
    assert len(bridges) == 1

    await client.bridges.addChannel("br123", "chan1")
    await client.bridges.removeChannel("br123", "chan1")
    await client.bridges.destroy("br123")


async def test_asterisk_repository() -> None:
    http = MockHttpClient()
    http.responses[("GET", "asterisk/info")] = {"system": {"version": "20.5.0"}}
    http.responses[("GET", "asterisk/ping")] = {"ping": "pong"}

    client = make_test_client(http)
    info = await client.asterisk.getInfo()
    assert info["system"]["version"] == "20.5.0"
    ping = await client.asterisk.ping()
    assert ping["ping"] == "pong"


async def test_channel_model_methods() -> None:
    http = MockHttpClient()
    http.responses[("POST", "channels/chan1/answer")] = None
    http.responses[("DELETE", "channels/chan1")] = None
    http.responses[("POST", "channels/chan1/play")] = {"id": "p1", "media_uri": "sound:hello"}
    http.responses[("POST", "channels/chan1/record")] = {"name": "r1", "format": "wav"}
    http.responses[("GET", "channels/chan1/variable")] = {"value": "123"}
    http.responses[("POST", "channels/chan1/variable")] = None

    client = make_test_client(http)
    chan = client.get_channel("chan1", {"caller": {"number": "1001", "name": "Alice"}})

    assert chan.caller_id_num == "1001"
    assert chan.caller_id_name == "Alice"

    await chan.answer()
    assert chan.state == "Up"

    pb = await chan.play(media="sound:hello")
    assert isinstance(pb, Playback)
    assert pb.id == "p1"

    rec = await chan.record(name="r1")
    assert isinstance(rec, LiveRecording)
    assert rec.name == "r1"

    chan.set_reason("busy")
    assert chan.reason == "busy"

    val = await chan.getChannelVar("foo")
    assert val == "123"

    await chan.setChannelVar("foo", "456")
    assert chan.variables["foo"] == "456"

    await chan.hangup()
    assert chan.state == "Hangup"


async def test_playback_and_recording_wait_done() -> None:
    client = make_test_client()
    pb = client.get_playback("pb1")
    rec = client.get_recording("rec1")

    async with anyio.create_task_group() as tg:
        async def finish_pb():
            await anyio.sleep(0.05)
            await pb.do_event(EventMessage(client, {"type": "PlaybackFinished", "playback": {"id": "pb1"}}))

        async def finish_rec():
            await anyio.sleep(0.05)
            await rec.do_event(EventMessage(client, {"type": "RecordingFinished", "recording": {"name": "rec1"}, "duration": 5}))

        tg.start_soon(finish_pb)
        tg.start_soon(finish_rec)
        await pb.wait_done()
        await rec.wait_done()

    assert pb.state == "done"
    assert rec.state == "done"
    assert rec.duration == 5


async def test_dtmf_handler_routing() -> None:
    class MyHandler(DTMFHandler):
        def __init__(self) -> None:
            self.digits: list[str] = []

        async def on_dtmf_1(self, evt) -> None:
            self.digits.append("ONE")

        async def on_dtmf_Star(self, evt) -> None:
            self.digits.append("STAR")

        async def on_dtmf_digit(self, evt) -> None:
            self.digits.append(f"DIGIT_{evt.digit}")

        async def on_dtmf_letter(self, evt) -> None:
            self.digits.append(f"LETTER_{evt.digit}")

        async def on_dtmf(self, evt) -> None:
            self.digits.append(f"OTHER_{evt.digit}")

    handler = MyHandler()

    class FakeEvt:
        def __init__(self, digit: str) -> None:
            self.digit = digit

    await handler.on_ChannelDtmfReceived(FakeEvt("1"))
    await handler.on_ChannelDtmfReceived(FakeEvt("*"))
    await handler.on_ChannelDtmfReceived(FakeEvt("9"))
    await handler.on_ChannelDtmfReceived(FakeEvt("A"))
    await handler.on_ChannelDtmfReceived(FakeEvt("#"))

    assert handler.digits == ["ONE", "STAR", "DIGIT_9", "LETTER_A", "OTHER_#"]


async def test_toplevel_channel_state_lifecycle() -> None:
    http = MockHttpClient()
    http.responses[("DELETE", "channels/chan1")] = None
    client = make_test_client(http)
    chan = client.get_channel("chan1")

    events_seen: list[str] = []

    class MyState(ToplevelChannelState):
        async def on_start(self) -> None:
            events_seen.append("started")

        async def on_CustomEvent(self, evt) -> None:
            events_seen.append("custom")
            await self.done()

    async with anyio.create_task_group() as tg:
        state = MyState(chan, taskgroup=tg)
        await state.start_task()
        await anyio.sleep(0.02)
        assert "started" in events_seen

        await chan.do_event(EventMessage(client, {"type": "CustomEvent", "channel": {"id": "chan1"}}))
        await state

    assert "custom" in events_seen
    assert chan.state in ("Hangup", "Gone")


async def test_event_listener_filtering_and_mangling() -> None:
    client = make_test_client()

    async with client.on_channel_event("StasisStart") as listener:
        # Deliver event
        evt_dict = {
            "type": "StasisStart",
            "channel": {"id": "c1", "name": "PJSIP/1001"},
            "args": ["inbound"],
        }
        await client.process_ws(evt_dict)

        with anyio.fail_after(1):
            async for objs, evt in listener:
                assert objs["channel"].id == "c1"
                assert evt.args == ["inbound"]
                break


def test_may_not_exist() -> None:
    with may_not_exist():
        raise NotFound(404, "Channel not found")

    with pytest.raises(Conflict):
        with may_not_exist():
            raise Conflict(409, "Channel in wrong state")


def test_dial_event_classification() -> None:
    client = make_test_client()

    ev1 = EventMessage(client, {"type": "Dial", "dialstatus": ""})
    assert ev1.type == "DialStart"

    ev2 = EventMessage(client, {"type": "Dial", "dialstatus": "PROGRESS"})
    assert ev2.type == "DialState"

    ev3 = EventMessage(client, {"type": "Dial", "dialstatus": "ANSWER"})
    assert ev3.type == "DialResult"
