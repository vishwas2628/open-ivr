"""Modern, robust Asterisk ARI (REST & WebSocket) client library for Python 3.11+.

A lightweight, reliable library implementing the Asterisk REST Interface (ARI)
specifications with native AnyIO async concurrency, typed domain models, and
event-driven state machines.
"""

from __future__ import annotations

from .client import Client, connect
from .connection import may_not_exist, mayNotExist
from .events import EventListener, EventMessage
from .exceptions import (
    ARIError,
    BadRequest,
    BridgeExit,
    ChannelExit,
    Conflict,
    ConnectionError,
    DialFailed,
    EventTimeout,
    HTTPError,
    NotFound,
    OperationError,
    ResourceExit,
    StateError,
)
from .model import (
    Application,
    Bridge,
    Channel,
    DeviceState,
    Endpoint,
    LiveRecording,
    Mailbox,
    Playback,
    Sound,
    StoredRecording,
)
from .state import (
    BaseEvtHandler,
    BridgeState,
    ChannelState,
    DTMFHandler,
    ToplevelChannelState,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "connect",
    "Client",
    "may_not_exist",
    "mayNotExist",
    "EventListener",
    "EventMessage",
    "ARIError",
    "HTTPError",
    "BadRequest",
    "NotFound",
    "Conflict",
    "ConnectionError",
    "OperationError",
    "ResourceExit",
    "ChannelExit",
    "BridgeExit",
    "StateError",
    "EventTimeout",
    "DialFailed",
    "Channel",
    "Bridge",
    "Playback",
    "LiveRecording",
    "StoredRecording",
    "Sound",
    "Endpoint",
    "DeviceState",
    "Mailbox",
    "Application",
    "BaseEvtHandler",
    "ChannelState",
    "ToplevelChannelState",
    "BridgeState",
    "DTMFHandler",
]
