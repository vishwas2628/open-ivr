"""Domain models for Asterisk ARI resources."""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable
from weakref import WeakSet

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from .connection import may_not_exist
from .exceptions import ChannelExit, StateError

if TYPE_CHECKING:
    from .client import Client
    from .events import EventMessage

log = logging.getLogger(__name__)


class EventUnsubscriber:
    """Handle to unsubscribe an event callback from an ARI domain object."""

    def __init__(self, obj: BaseObject, event_type: str, callback_tuple: tuple[Any, ...]) -> None:
        self._obj = obj
        self._event_type = event_type
        self._callback_tuple = callback_tuple

    def close(self) -> None:
        try:
            listeners = self._obj.event_listeners.get(self._event_type, [])
            if self._callback_tuple in listeners:
                listeners.remove(self._callback_tuple)
        except Exception:
            pass


class BaseObject:
    """Base class for all first-class ARI domain objects."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        self.client = client
        self.json: dict[str, Any] = dict(json or {})
        if id is not None:
            self.id = str(id)
        elif "id" in self.json:
            self.id = str(self.json["id"])
        elif "name" in self.json:
            self.id = str(self.json["name"])
        else:
            self.id = ""

        self.event_listeners: dict[str, list[tuple[Callable, tuple[Any, ...], dict[str, Any]]]] = {}
        self._changed = anyio.Event()
        self._stream_send: MemoryObjectSendStream[EventMessage] | None = None
        self._stream_recv: MemoryObjectReceiveStream[EventMessage] | None = None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id!r}>"

    def __getitem__(self, item: str) -> Any:
        return self.json[item]

    def __contains__(self, item: str) -> bool:
        return item in self.json

    def get(self, item: str, default: Any = None) -> Any:
        return self.json.get(item, default)

    def __getattr__(self, item: str) -> Any:
        if "json" in self.__dict__ and item in self.json:
            return self.json[item]
        raise AttributeError(f"{self.__class__.__name__!r} object has no attribute {item!r}")

    def on_event(self, event_type: str, fn: Callable, *args: Any, **kwargs: Any) -> EventUnsubscriber:
        """Register a callback for events relating to this domain object."""
        cb_tuple = (fn, args, kwargs)
        self.event_listeners.setdefault(event_type, []).append(cb_tuple)
        return EventUnsubscriber(self, event_type, cb_tuple)

    async def do_event(self, msg: EventMessage) -> None:
        """Deliver an event to all subscribers of this object."""
        callbacks = list(self.event_listeners.get(msg.type, [])) + list(self.event_listeners.get("*", []))
        for p, a, k in callbacks:
            try:
                res = p(msg, *a, **k)
                if inspect.iscoroutine(res):
                    await res
            except Exception as exc:
                log.exception("Error in %s event callback for %s: %s", self, msg.type, exc)

        if self._stream_send is not None:
            try:
                self._stream_send.send_nowait(msg)
            except (anyio.WouldBlock, anyio.ClosedResourceError):
                pass

        self._changed.set()
        self._changed = anyio.Event()

    async def wait_for(self, predicate: Callable[[], bool], timeout: float | None = None) -> None:
        """Wait until `predicate()` returns truthy, or timeout expires."""
        with anyio.fail_after(timeout):
            while not predicate():
                await self._changed.wait()

    def __aiter__(self) -> BaseObject:
        if self._stream_recv is None or self._stream_send is None:
            self._stream_send, self._stream_recv = anyio.create_memory_object_stream(50)
        return self

    async def __anext__(self) -> EventMessage:
        if self._stream_recv is None:
            self.__aiter__()
        assert self._stream_recv is not None
        try:
            return await self._stream_recv.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            raise StopAsyncIteration from None


class Channel(BaseObject):
    """Represents an active Asterisk ARI Channel."""

    active: WeakSet[Channel] = WeakSet()
    REASONS = ("congestion", "no_answer", "busy", "normal")

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", f"Channel/{self.id}"))
        self.state: str = str(self.json.get("state", "Down"))
        self.caller: dict[str, Any] = dict(self.json.get("caller", {}))
        self.reason: str = ""
        self._reason_seen: anyio.Event | None = None
        self._do_hangup: bool | None = None
        self.playbacks: set[Playback] = set()
        self.recordings: set[LiveRecording] = set()
        self.variables: dict[str, str] = {}
        self.vars = self.variables
        self.bridge: Bridge | None = None
        Channel.active.add(self)

    @property
    def caller_id_num(self) -> str:
        return str(self.caller.get("number", self.json.get("caller_id_num", "")))

    @property
    def caller_id_name(self) -> str:
        return str(self.caller.get("name", self.json.get("caller_id_name", "")))

    def set_reason(self, reason: str) -> None:
        """Set the hangup reason code."""
        if reason in self.REASONS:
            if not self.reason:
                self.reason = reason
            elif self.REASONS.index(reason) > self.REASONS.index(self.reason):
                self.reason = reason
        else:
            self.reason = reason

        if self._reason_seen is not None:
            self._reason_seen.set()

    async def answer(self) -> None:
        """Answer the channel."""
        await self.client.channels.answer(channelId=self.id)
        self.state = "Up"

    async def hangup(self, reason: str | None = None) -> None:
        """Hang up the channel immediately via REST API."""
        r = reason or self.reason or None
        await self.client.channels.hangup(channelId=self.id, reason=r)
        self.state = "Hangup"

    # Alias for compatibility with asyncari
    hang_up = hangup

    async def handle_exit(self, reason: str = "normal") -> None:
        """Ensure channel is hung up on state machine exit, ignoring 404."""
        try:
            with may_not_exist():
                await self.hangup(reason=self.reason or reason)
        finally:
            self.state = "Gone"
            self._changed.set()

    async def play(self, media: str, **kwargs: Any) -> Playback:
        """Play audio to the channel."""
        return await self.client.channels.play(channelId=self.id, media=media, **kwargs)

    async def playWithId(self, playbackId: str, media: str, **kwargs: Any) -> Playback:
        """Play audio with a specific playback ID."""
        return await self.client.channels.playWithId(
            channelId=self.id, playbackId=playbackId, media=media, **kwargs
        )

    async def record(self, name: str, format: str = "wav", **kwargs: Any) -> LiveRecording:
        """Record audio from the channel."""
        return await self.client.channels.record(
            channelId=self.id, name=name, format=format, **kwargs
        )

    async def continueInDialplan(self, **kwargs: Any) -> None:
        """Exit Stasis and continue in the Asterisk dialplan."""
        await self.client.channels.continueInDialplan(channelId=self.id, **kwargs)

    async def setChannelVar(self, variable: str, value: str) -> None:
        """Set a channel variable."""
        await self.client.channels.setChannelVar(channelId=self.id, variable=variable, value=value)
        self.variables[variable] = value

    async def getChannelVar(self, variable: str) -> str:
        """Retrieve a channel variable."""
        res = await self.client.channels.getChannelVar(channelId=self.id, variable=variable)
        if isinstance(res, dict):
            val = str(res.get("value", ""))
        else:
            val = str(res)
        self.variables[variable] = val
        return val

    async def ring(self) -> None:
        """Send ringing indication."""
        await self.client.channels.ring(channelId=self.id)

    async def ringStop(self) -> None:
        """Stop ringing indication."""
        await self.client.channels.ringStop(channelId=self.id)

    async def mute(self, direction: str = "both") -> None:
        """Mute channel."""
        await self.client.channels.mute(channelId=self.id, direction=direction)

    async def unmute(self, direction: str = "both") -> None:
        """Unmute channel."""
        await self.client.channels.unmute(channelId=self.id, direction=direction)

    async def hold(self) -> None:
        """Hold channel."""
        await self.client.channels.hold(channelId=self.id)

    async def stopHold(self) -> None:
        """Stop hold."""
        await self.client.channels.stopHold(channelId=self.id)

    async def startMoh(self, mohClass: str | None = None) -> None:
        """Start music on hold."""
        await self.client.channels.startMoh(channelId=self.id, mohClass=mohClass)

    async def stopMoh(self) -> None:
        """Stop music on hold."""
        await self.client.channels.stopMoh(channelId=self.id)

    async def startSilence(self) -> None:
        """Play silence."""
        await self.client.channels.startSilence(channelId=self.id)

    async def stopSilence(self) -> None:
        """Stop silence."""
        await self.client.channels.stopSilence(channelId=self.id)

    async def redirect(self, endpoint: str) -> None:
        """Redirect channel."""
        await self.client.channels.redirect(channelId=self.id, endpoint=endpoint)

    async def sendDTMF(self, dtmf: str, **kwargs: Any) -> None:
        """Send DTMF tones."""
        await self.client.channels.sendDTMF(channelId=self.id, dtmf=dtmf, **kwargs)

    async def dial(self, caller: str | None = None, timeout: int | None = None) -> None:
        """Dial created channel."""
        await self.client.channels.dial(channelId=self.id, caller=caller, timeout=timeout)

    async def snoopChannel(self, app: str, **kwargs: Any) -> Channel:
        """Snoop on this channel."""
        return await self.client.channels.snoopChannel(channelId=self.id, app=app, **kwargs)

    async def wait_up(self, timeout: float | None = None) -> None:
        """Wait until the channel reaches 'Up' state."""
        def chk() -> bool:
            if self.state == "Down":
                raise ChannelExit(self.state)
            return self.state == "Up"

        await self.wait_for(chk, timeout=timeout)

    async def wait_down(self, timeout: float | None = None) -> None:
        """Wait until the channel is no longer active."""
        await self.wait_for(lambda: self.state in ("Hangup", "Gone"), timeout=timeout)

    async def wait_not_playing(self, timeout: float | None = None) -> None:
        """Wait until all sound playbacks on this channel have finished."""
        await self.wait_for(lambda: len(self.playbacks) == 0, timeout=timeout)

    async def do_event(self, msg: EventMessage) -> None:
        """Update internal state and track active playbacks/recordings."""
        if msg.type == "StasisStart":
            Channel.active.add(self)
        elif msg.type in ("StasisEnd", "ChannelDestroyed"):
            Channel.active.discard(self)
            self.state = "Gone"
        elif msg.type == "ChannelStateChange":
            new_state = msg.get("channel", {}).get("state") or getattr(msg, "state", None)
            if new_state:
                self.state = str(new_state)
        elif msg.type == "ChannelVarset":
            var = str(msg.get("variable", ""))
            val = str(msg.get("value", ""))
            if var:
                self.variables[var] = val
        elif msg.type == "ChannelEnteredBridge":
            self.bridge = msg.bridge
        elif msg.type == "ChannelLeftBridge":
            if self.bridge is msg.bridge:
                self.bridge = None
        elif msg.type == "PlaybackStarted":
            if msg.playback is not None:
                self.playbacks.add(msg.playback)
        elif msg.type == "PlaybackFinished":
            if msg.playback is not None:
                self.playbacks.discard(msg.playback)
        elif msg.type == "RecordingStarted":
            if msg.recording is not None:
                self.recordings.add(msg.recording)
        elif msg.type == "RecordingFinished":
            if msg.recording is not None:
                self.recordings.discard(msg.recording)

        await super().do_event(msg)

    async def __anext__(self) -> EventMessage:
        evt = await super().__anext__()
        if evt.type in ("StasisEnd", "ChannelDestroyed"):
            raise StopAsyncIteration
        return evt


class Bridge(BaseObject):
    """Represents an Asterisk ARI Bridge."""

    active: WeakSet[Bridge] = WeakSet()

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", f"Bridge/{self.id}"))
        self.bridge_type: str = str(self.json.get("bridge_type", "mixing"))
        self.channels: set[str] = set(self.json.get("channels", []))
        Bridge.active.add(self)

    async def destroy(self) -> None:
        """Destroy the bridge."""
        await self.client.bridges.destroy(bridgeId=self.id)
        Bridge.active.discard(self)

    async def addChannel(self, channel: str | Channel, **kwargs: Any) -> None:
        """Add a channel to this bridge."""
        chan_id = channel.id if isinstance(channel, Channel) else str(channel)
        await self.client.bridges.addChannel(bridgeId=self.id, channel=chan_id, **kwargs)
        self.channels.add(chan_id)

    async def removeChannel(self, channel: str | Channel) -> None:
        """Remove a channel from this bridge."""
        chan_id = channel.id if isinstance(channel, Channel) else str(channel)
        await self.client.bridges.removeChannel(bridgeId=self.id, channel=chan_id)
        self.channels.discard(chan_id)

    async def play(self, media: str, **kwargs: Any) -> Playback:
        """Play audio into the bridge."""
        return await self.client.bridges.play(bridgeId=self.id, media=media, **kwargs)

    async def record(self, name: str, format: str = "wav", **kwargs: Any) -> LiveRecording:
        """Record audio from the bridge."""
        return await self.client.bridges.record(bridgeId=self.id, name=name, format=format, **kwargs)

    async def startMoh(self, mohClass: str | None = None) -> None:
        """Start music on hold on the bridge."""
        await self.client.bridges.startMoh(bridgeId=self.id, mohClass=mohClass)

    async def stopMoh(self) -> None:
        """Stop music on hold on the bridge."""
        await self.client.bridges.stopMoh(bridgeId=self.id)

    async def setVideoSource(self, channelId: str) -> None:
        """Set video source channel for the bridge."""
        await self.client.bridges.setVideoSource(bridgeId=self.id, channelId=channelId)

    async def clearVideoSource(self) -> None:
        """Clear video source for the bridge."""
        await self.client.bridges.clearVideoSource(bridgeId=self.id)

    async def do_event(self, msg: EventMessage) -> None:
        if msg.type == "ChannelEnteredBridge":
            if msg.channel is not None:
                self.channels.add(msg.channel.id)
        elif msg.type == "ChannelLeftBridge":
            if msg.channel is not None:
                self.channels.discard(msg.channel.id)
        elif msg.type == "BridgeDestroyed":
            Bridge.active.discard(self)
        await super().do_event(msg)


class Playback(BaseObject):
    """Represents an active Asterisk ARI Playback operation."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.media_uri: str = str(self.json.get("media_uri", ""))
        self.target_uri: str = str(self.json.get("target_uri", ""))
        self.state: str = str(self.json.get("state", "queued"))
        self._is_playing = anyio.Event()
        self._is_done = anyio.Event()

    async def stop(self) -> None:
        """Stop playback immediately."""
        with may_not_exist():
            await self.client.playbacks.stop(playbackId=self.id)
        self._is_done.set()

    async def control(self, operation: str) -> None:
        """Control playback (pause, unpause, restart, reverse, forward)."""
        await self.client.playbacks.control(playbackId=self.id, operation=operation)

    async def wait_playing(self, timeout: float | None = None) -> None:
        """Wait until playback starts."""
        with anyio.fail_after(timeout):
            await self._is_playing.wait()

    async def wait_done(self, timeout: float | None = None) -> None:
        """Wait until playback completes."""
        with anyio.fail_after(timeout):
            await self._is_done.wait()

    async def do_event(self, msg: EventMessage) -> None:
        if msg.type == "PlaybackStarted":
            self.state = "playing"
            self._is_playing.set()
        elif msg.type == "PlaybackFinished":
            self.state = "done"
            self._is_playing.set()
            self._is_done.set()
        await super().do_event(msg)


class LiveRecording(BaseObject):
    """Represents an active Asterisk ARI LiveRecording."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", self.id))
        self.format: str = str(self.json.get("format", "wav"))
        self.duration: int = int(self.json.get("duration", 0) or 0)
        self.state: str = str(self.json.get("state", "queued"))
        self._is_recording = anyio.Event()
        self._is_done = anyio.Event()

    async def stop(self) -> None:
        """Stop recording and save file."""
        with may_not_exist():
            await self.client.recordings.stop(recordingName=self.name)
        self._is_done.set()

    async def pause(self) -> None:
        """Pause recording."""
        await self.client.recordings.pause(recordingName=self.name)

    async def unpause(self) -> None:
        """Resume recording."""
        await self.client.recordings.unpause(recordingName=self.name)

    async def mute(self) -> None:
        """Mute recording audio."""
        await self.client.recordings.mute(recordingName=self.name)

    async def unmute(self) -> None:
        """Unmute recording audio."""
        await self.client.recordings.unmute(recordingName=self.name)

    async def cancel(self) -> None:
        """Cancel recording and discard file."""
        with may_not_exist():
            await self.client.recordings.cancel(recordingName=self.name)
        self._is_done.set()

    async def wait_recording(self, timeout: float | None = None) -> None:
        """Wait until recording begins."""
        with anyio.fail_after(timeout):
            await self._is_recording.wait()

    async def wait_done(self, timeout: float | None = None) -> None:
        """Wait until recording finishes."""
        with anyio.fail_after(timeout):
            await self._is_done.wait()

    async def do_event(self, msg: EventMessage) -> None:
        if msg.type == "RecordingStarted":
            self.state = "recording"
            self._is_recording.set()
        elif msg.type == "RecordingFinished":
            self.state = "done"
            if "duration" in msg:
                self.duration = int(msg["duration"] or 0)
            self._is_recording.set()
            self._is_done.set()
        await super().do_event(msg)


class StoredRecording(BaseObject):
    """Represents a completed recording stored on disk in Asterisk."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", self.id))
        self.format: str = str(self.json.get("format", "wav"))

    async def delete(self) -> None:
        """Delete this stored recording."""
        await self.client.recordings.deleteStored(recordingName=self.name)

    async def copy(self, destinationRecordingName: str) -> StoredRecording:
        """Copy this stored recording to a new name."""
        return await self.client.recordings.copyStored(
            recordingName=self.name, destinationRecordingName=destinationRecordingName
        )


class Sound(BaseObject):
    """Represents an Asterisk sound resource."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.text: str = str(self.json.get("text", ""))
        self.formats: list[dict[str, Any]] = list(self.json.get("formats", []))


class Endpoint(BaseObject):
    """Represents an Asterisk technology endpoint (e.g. PJSIP/1001)."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.technology: str = str(self.json.get("technology", ""))
        self.resource: str = str(self.json.get("resource", ""))
        self.state: str = str(self.json.get("state", "unknown"))


class DeviceState(BaseObject):
    """Represents an Asterisk device state."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", self.id))
        self.state: str = str(self.json.get("state", "UNKNOWN"))


class Mailbox(BaseObject):
    """Represents an Asterisk mailbox."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", self.id))
        self.old_messages: int = int(self.json.get("old_messages", 0) or 0)
        self.new_messages: int = int(self.json.get("new_messages", 0) or 0)


class Application(BaseObject):
    """Represents a registered Stasis application in Asterisk."""

    def __init__(self, client: Client, id: str | None = None, json: dict[str, Any] | None = None) -> None:
        super().__init__(client, id=id, json=json)
        self.name: str = str(self.json.get("name", self.id))
        self.channel_ids: list[str] = list(self.json.get("channel_ids", []))
        self.bridge_ids: list[str] = list(self.json.get("bridge_ids", []))
        self.endpoint_ids: list[str] = list(self.json.get("endpoint_ids", []))
        self.device_names: list[str] = list(self.json.get("device_names", []))


CLASS_MAP: dict[str, type[BaseObject]] = {
    "Channel": Channel,
    "Bridge": Bridge,
    "Playback": Playback,
    "LiveRecording": LiveRecording,
    "StoredRecording": StoredRecording,
    "Sound": Sound,
    "Endpoint": Endpoint,
    "DeviceState": DeviceState,
    "Mailbox": Mailbox,
    "Application": Application,
}
