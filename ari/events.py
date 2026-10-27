"""Event modeling, dispatching, and streaming for Asterisk ARI."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

if TYPE_CHECKING:
    from .client import Client

log = logging.getLogger(__name__)


class EventMessage:
    """Wraps an ARI JSON event with attribute access, object promotion, and routing."""

    def __init__(self, client: Client, data: dict[str, Any]) -> None:
        self._client = client
        self._data = dict(data)

        raw_type = str(self._data.get("type", ""))
        dialstatus = str(self._data.get("dialstatus", ""))

        # Asterisk Dial event subtypes using modern pattern matching
        match raw_type:
            case "Dial":
                match dialstatus:
                    case "":
                        self.type = "DialStart"
                    case "PROGRESS" | "RINGING":
                        self.type = "DialState"
                    case _:
                        self.type = "DialResult"
            case _:
                self.type = raw_type

        # Promote nested models
        self.channel = None
        self.bridge = None
        self.playback = None
        self.recording = None

        if "channel" in self._data and isinstance(self._data["channel"], dict):
            self.channel = client.get_channel(self._data["channel"].get("id", ""), self._data["channel"])
        elif "channel" in self._data and hasattr(self._data["channel"], "id"):
            self.channel = self._data["channel"]

        if "bridge" in self._data and isinstance(self._data["bridge"], dict):
            self.bridge = client.get_bridge(self._data["bridge"].get("id", ""), self._data["bridge"])
        elif "bridge" in self._data and hasattr(self._data["bridge"], "id"):
            self.bridge = self._data["bridge"]

        if "playback" in self._data and isinstance(self._data["playback"], dict):
            self.playback = client.get_playback(self._data["playback"].get("id", ""), self._data["playback"])
        elif "playback" in self._data and hasattr(self._data["playback"], "id"):
            self.playback = self._data["playback"]

        if "recording" in self._data and isinstance(self._data["recording"], dict):
            self.recording = client.get_recording(self._data["recording"].get("name", ""), self._data["recording"])
        elif "recording" in self._data and hasattr(self._data["recording"], "name"):
            self.recording = self._data["recording"]

    def __getitem__(self, item: str) -> Any:
        return self._data[item]

    def __contains__(self, item: str) -> bool:
        return item in self._data

    def get(self, item: str, default: Any = None) -> Any:
        return self._data.get(item, default)

    def _get(self, item: str, default: Any = None) -> Any:
        return self._data.get(item, default)

    def __getattr__(self, item: str) -> Any:
        if item in self._data:
            return self._data[item]
        raise AttributeError(f"'EventMessage' object has no attribute {item!r}")

    def __repr__(self) -> str:
        return f"<EventMessage {self.type}>"

    async def _send_event(self) -> None:
        """Deliver the event to any domain models referenced in the message."""
        models = [self.channel, self.bridge, self.playback, self.recording]
        for model in models:
            if model is not None and hasattr(model, "do_event"):
                await model.do_event(self)


class EventListener:
    """Async context manager and iterator for listening to ARI events."""

    def __init__(
        self,
        client: Client,
        event_type: str,
        *,
        mangler: Callable[[EventMessage], Any] | None = None,
        filter_fn: Callable[[EventMessage], bool] | None = None,
        max_buffer_size: int = 100,
    ) -> None:
        self.client = client
        self.event_type = event_type
        self.mangler = mangler
        self.filter_fn = filter_fn or (lambda _evt: True)
        self._send_stream: MemoryObjectSendStream[EventMessage]
        self._receive_stream: MemoryObjectReceiveStream[EventMessage]
        self._send_stream, self._receive_stream = anyio.create_memory_object_stream(max_buffer_size)
        self._open = False

    async def __call__(self, event: EventMessage) -> None:
        """Called by client's dispatcher when a matching event arrives."""
        if not self._open:
            return
        try:
            if self.filter_fn(event):
                self._send_stream.send_nowait(event)
        except anyio.WouldBlock:
            log.warning("EventListener queue full for %s, event dropped", self.event_type)
        except anyio.ClosedResourceError:
            pass

    def open(self) -> None:
        if not self._open:
            self._open = True
            self.client.add_listener(self.event_type, self)

    def close(self) -> None:
        if self._open:
            self._open = False
            self.client.remove_listener(self.event_type, self)
            try:
                self._send_stream.close()
            except Exception:
                pass

    async def __aenter__(self) -> EventListener:
        self.open()
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()

    def __aiter__(self) -> EventListener:
        return self

    async def __anext__(self) -> Any:
        while True:
            try:
                raw_event = await self._receive_stream.receive()
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                raise StopAsyncIteration from None

            if self.mangler is not None:
                mangled = self.mangler(raw_event)
                if mangled is None:
                    continue
                return mangled
            return raw_event
