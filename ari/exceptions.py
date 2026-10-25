"""Exception hierarchy for the ARI library."""

from __future__ import annotations

from typing import Any


class ARIError(Exception):
    """Base exception for all ARI client and state machine errors."""


class OperationError(ARIError):
    """Raised when an ARI REST or Swagger operation fails."""


class HTTPError(ARIError):
    """Raised when an HTTP operation returns a non-2xx status code."""

    def __init__(
        self,
        status_code: int,
        message: str = "",
        *,
        url: str = "",
        data: Any = None,
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.url = url
        self.data = data
        super().__init__(f"HTTP {status_code} on {url}: {message}" if url else f"HTTP {status_code}: {message}")


class BadRequest(HTTPError):
    """HTTP 400 Bad Request."""


class NotFound(HTTPError):
    """HTTP 404 Not Found."""


class Conflict(HTTPError):
    """HTTP 409 Conflict."""


class ConnectionError(ARIError):
    """Raised when connection to Asterisk ARI (HTTP or WebSocket) fails."""


class StateError(ARIError):
    """The expected or waited-for state machine condition did not occur."""


class EventTimeout(ARIError):
    """There were no events received past the timeout."""


class ResourceExit(ARIError):
    """The underlying ARI resource no longer exists."""


class ChannelExit(ResourceExit):
    """The channel has hung up or terminated."""


class BridgeExit(ResourceExit):
    """The bridge has terminated or been destroyed."""


class DialFailed(ARIError):
    """Raised when an outbound dial fails."""

    def __init__(self, dialstatus: str, cause: Any = None) -> None:
        self.dialstatus = dialstatus
        self.cause = cause
        super().__init__(f"Dial failed with status {dialstatus} (cause: {cause})")
