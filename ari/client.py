"""High-level Asterisk ARI Client and connection manager."""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import anyio
from anyio.abc import TaskGroup

from .connection import HttpClient, WebSocketClient
from .events import EventListener, EventMessage
from .model import (
    Bridge,
    Channel,
    LiveRecording,
    Playback,
    StoredRecording,
)
from .repository import (
    ApplicationsRepository,
    AsteriskRepository,
    BridgesRepository,
    ChannelsRepository,
    DeviceStatesRepository,
    EndpointsRepository,
    EventsRepository,
    MailboxesRepository,
    PlaybacksRepository,
    RecordingsRepository,
    SoundsRepository,
)

log = logging.getLogger(__name__)


class Client:
    """Asterisk ARI async client managing REST endpoints and WebSocket events."""

    def __init__(
        self,
        taskgroup: TaskGroup,
        base_url: str,
        apps: str | list[str],
        username: str = "",
        password: str = "",
        http_client: HttpClient | None = None,
    ) -> None:
        self.taskgroup = taskgroup
        self.base_url = base_url.rstrip("/")
        self.apps = [apps] if isinstance(apps, str) else list(apps)
        self.username = username
        self.password = password

        self.http = http_client or HttpClient(base_url, username=username, password=password)
        self._owned_http = http_client is None
        self.ws = WebSocketClient(base_url, self.apps, username=username, password=password)

        # Repositories
        self.channels = ChannelsRepository(self)
        self.bridges = BridgesRepository(self)
        self.playbacks = PlaybacksRepository(self)
        self.recordings = RecordingsRepository(self)
        self.asterisk = AsteriskRepository(self)
        self.sounds = SoundsRepository(self)
        self.applications = ApplicationsRepository(self)
        self.endpoints = EndpointsRepository(self)
        self.deviceStates = DeviceStatesRepository(self)
        self.mailboxes = MailboxesRepository(self)
        self.events = EventsRepository(self)

        self.repositories: dict[str, Any] = {
            "channels": self.channels,
            "bridges": self.bridges,
            "playbacks": self.playbacks,
            "recordings": self.recordings,
            "asterisk": self.asterisk,
            "sounds": self.sounds,
            "applications": self.applications,
            "endpoints": self.endpoints,
            "deviceStates": self.deviceStates,
            "mailboxes": self.mailboxes,
            "events": self.events,
        }

        # Model instance caches
        self._channel_cache: dict[str, Channel] = {}
        self._bridge_cache: dict[str, Bridge] = {}
        self._playback_cache: dict[str, Playback] = {}
        self._recording_cache: dict[str, LiveRecording] = {}
        self._stored_recording_cache: dict[str, StoredRecording] = {}

        # Event listeners: event_type -> list of EventListener instances
        self.event_listeners: dict[str, list[EventListener]] = {}
        self._id_seq = 0
        self._id_prefix = f"ARI.{os.getpid()}.{int(time.time() * 1000)}"

    def generate_id(self, typ: str = "") -> str:
        """Generate a unique ID for a channel, bridge, or playback."""
        self._id_seq += 1
        return f"{self._id_prefix}.{typ}{self._id_seq}"

    def get_repo(self, name: str) -> Any:
        return self.repositories.get(name)

    # Factory methods returning cached or newly created domain model instances
    def get_channel(self, channel_id: str, data: dict[str, Any] | None = None) -> Channel:
        if not channel_id:
            return Channel(self, id="", json=data)
        chan = self._channel_cache.get(channel_id)
        if chan is None:
            chan = Channel(self, id=channel_id, json=data)
            self._channel_cache[channel_id] = chan
        elif data:
            chan.json.update(data)
            if "state" in data:
                chan.state = str(data["state"])
        return chan

    def get_bridge(self, bridge_id: str, data: dict[str, Any] | None = None) -> Bridge:
        if not bridge_id:
            return Bridge(self, id="", json=data)
        bridge = self._bridge_cache.get(bridge_id)
        if bridge is None:
            bridge = Bridge(self, id=bridge_id, json=data)
            self._bridge_cache[bridge_id] = bridge
        elif data:
            bridge.json.update(data)
        return bridge

    def get_playback(self, playback_id: str, data: dict[str, Any] | None = None) -> Playback:
        if not playback_id:
            return Playback(self, id="", json=data)
        pb = self._playback_cache.get(playback_id)
        if pb is None:
            pb = Playback(self, id=playback_id, json=data)
            self._playback_cache[playback_id] = pb
        elif data:
            pb.json.update(data)
            if "state" in data:
                pb.state = str(data["state"])
        return pb

    def get_recording(self, recording_name: str, data: dict[str, Any] | None = None) -> LiveRecording:
        if not recording_name:
            return LiveRecording(self, id="", json=data)
        rec = self._recording_cache.get(recording_name)
        if rec is None:
            rec = LiveRecording(self, id=recording_name, json=data)
            self._recording_cache[recording_name] = rec
        elif data:
            rec.json.update(data)
            if "state" in data:
                rec.state = str(data["state"])
        return rec

    def get_stored_recording(self, recording_name: str, data: dict[str, Any] | None = None) -> StoredRecording:
        if not recording_name:
            return StoredRecording(self, id="", json=data)
        rec = self._stored_recording_cache.get(recording_name)
        if rec is None:
            rec = StoredRecording(self, id=recording_name, json=data)
            self._stored_recording_cache[recording_name] = rec
        elif data:
            rec.json.update(data)
        return rec

    # Event registration
    def add_listener(self, event_type: str, listener: EventListener) -> None:
        self.event_listeners.setdefault(event_type, []).append(listener)

    def remove_listener(self, event_type: str, listener: EventListener) -> None:
        listeners = self.event_listeners.get(event_type, [])
        if listener in listeners:
            listeners.remove(listener)

    def on_event(
        self,
        event_type: str,
        *,
        mangler: Callable[[EventMessage], Any] | None = None,
        filter: Callable[[EventMessage], bool] | None = None,
    ) -> EventListener:
        """Create an event listener for `event_type`."""
        return EventListener(self, event_type, mangler=mangler, filter_fn=filter)

    def on_channel_event(
        self,
        event_type: str,
        filter: Callable[[EventMessage], bool] | None = None,
    ) -> EventListener:
        """Create an event listener that extracts `({"channel": Channel}, event)`."""
        def extract(event: EventMessage) -> tuple[dict[str, Channel | None], EventMessage]:
            return ({"channel": event.channel}, event)

        return EventListener(self, event_type, mangler=extract, filter_fn=filter)

    def on_object_event(
        self,
        event_type: str,
        factory_fn: Callable[..., Any],
        model_id: str,
        filter: Callable[[EventMessage], bool] | None = None,
    ) -> EventListener:
        """Create an event listener for objects of `model_id`."""
        key = model_id.lower()

        def extract(event: EventMessage) -> tuple[dict[str, Any] | Any, EventMessage]:
            val = getattr(event, key, None)
            return ({key: val}, event)

        return EventListener(self, event_type, mangler=extract, filter_fn=filter)

    async def process_ws(self, msg_json: dict[str, Any]) -> None:
        """Process an incoming event dictionary from WebSocket."""
        event = EventMessage(self, msg_json)

        # 1. Deliver to traditional listeners
        listeners = list(self.event_listeners.get(event.type, [])) + list(self.event_listeners.get("*", []))
        for listener in listeners:
            try:
                await listener(event)
            except Exception as exc:
                log.exception("Error in listener %s for %s: %s", listener, event.type, exc)

        # 2. Dispatch to domain models referenced in event
        await event._send_event()

    async def _ws_loop(self) -> None:
        """Background task reading and dispatching WebSocket events."""
        try:
            async for msg_json in self.ws.messages():
                await self.process_ws(msg_json)
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as exc:
            log.warning("WebSocket loop ended: %s", exc)

    async def __aenter__(self) -> Client:
        await self.ws.connect()
        self.taskgroup.start_soon(self._ws_loop)
        return self

    async def __aexit__(self, *tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close WebSocket connection and HTTP client."""
        await self.ws.close()
        if self._owned_http:
            await self.http.close()


@asynccontextmanager
async def connect(
    base_url: str,
    apps: str | list[str],
    username: str = "",
    password: str = "",
) -> AsyncIterator[Client]:
    """Helper method for connecting to Asterisk ARI.

    :param base_url: Base URL for Asterisk HTTP server (e.g. http://127.0.0.1:8088/)
    :param apps: Stasis application name(s) to register for
    :param username: ARI username
    :param password: ARI password
    """
    app_list = [apps] if isinstance(apps, str) else list(apps)
    http_client = HttpClient(base_url, username=username, password=password)
    try:
        async with anyio.create_task_group() as tg:
            client = Client(
                taskgroup=tg,
                base_url=base_url,
                apps=app_list,
                username=username,
                password=password,
                http_client=http_client,
            )
            async with client:
                try:
                    yield client
                finally:
                    tg.cancel_scope.cancel()
    finally:
        await http_client.close()
