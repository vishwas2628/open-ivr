"""Common type aliases and protocols for ARI."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Protocol, TypeVar

JSONDict = dict[str, Any]
EventCallback = Callable[..., Coroutine[Any, Any, Any] | Any]

T = TypeVar("T")


class Closable(Protocol):
    """Protocol for objects that can be closed."""

    def close(self) -> None: ...
