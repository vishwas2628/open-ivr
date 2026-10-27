"""HTTP and WebSocket connection abstractions for Asterisk ARI."""

from __future__ import annotations

import base64
import json
import logging
from contextlib import contextmanager
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urlsplit

import anyio
import httpx
from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection,
    CloseConnection,
    Ping,
    RejectConnection,
    Request,
    TextMessage,
)

from .exceptions import (
    BadRequest,
    Conflict,
    ConnectionError as ARIConnectionError,
    HTTPError,
    NotFound,
    OperationError,
)

log = logging.getLogger(__name__)


@contextmanager
def may_not_exist() -> Iterator[None]:
    """Context manager to suppress 404 Not Found errors during teardown/cleanup."""
    try:
        yield
    except NotFound:
        pass
    except HTTPError as exc:
        if exc.status_code == 404:
            pass
        else:
            raise


# Backwards compatibility alias
mayNotExist = may_not_exist


class HttpClient:
    """Async HTTP client for ARI REST API calls."""

    def __init__(
        self,
        base_url: str,
        username: str = "",
        password: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.raw_base_url = base_url.rstrip("/")
        # Ensure base URL has /ari prefix
        if not self.raw_base_url.endswith("/ari"):
            self.base_url = f"{self.raw_base_url}/ari"
        else:
            self.base_url = self.raw_base_url

        self.username = username
        self.password = password
        self.auth = httpx.BasicAuth(username, password) if (username or password) else None

        limits = httpx.Limits(max_keepalive_connections=5, max_connections=20)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            auth=self.auth,
            timeout=timeout,
            limits=limits,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute an HTTP request against the ARI REST API."""
        clean_path = path.lstrip("/")
        # Filter out None values in params
        clean_params = (
            {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items() if v is not None}
            if params
            else None
        )

        try:
            resp = await self._client.request(
                method=method.upper(),
                url=clean_path,
                params=clean_params,
                json=json_data,
                headers=headers,
            )
        except httpx.RequestError as exc:
            raise ARIConnectionError(f"HTTP request to {clean_path} failed: {exc}") from exc

        if not resp.is_success:
            err_msg = resp.text
            try:
                err_json = resp.json()
                if isinstance(err_json, dict) and "message" in err_json:
                    err_msg = err_json["message"]
            except Exception:
                pass

            match resp.status_code:
                case 400:
                    raise BadRequest(resp.status_code, err_msg, url=str(resp.url))
                case 404:
                    raise NotFound(resp.status_code, err_msg, url=str(resp.url))
                case 409:
                    raise Conflict(resp.status_code, err_msg, url=str(resp.url))
                case code:
                    raise HTTPError(code, err_msg, url=str(resp.url))

        if resp.status_code == 204 or not resp.content:
            return None

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return resp.json()
            except json.JSONDecodeError as exc:
                raise OperationError(f"Failed to decode ARI JSON response: {exc}") from exc
        return resp.content

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
    ) -> Any:
        return await self.request("POST", path, params=params, json_data=json_data)

    async def put(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: Any = None,
    ) -> Any:
        return await self.request("PUT", path, params=params, json_data=json_data)

    async def delete(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("DELETE", path, params=params)


class WebSocketClient:
    """WebSocket client for receiving events from Asterisk ARI."""

    def __init__(
        self,
        base_url: str,
        apps: list[str],
        username: str = "",
        password: str = "",
        subscribe_all: bool = False,
    ) -> None:
        self.raw_base_url = base_url.rstrip("/")
        self.apps = apps
        self.username = username
        self.password = password
        self.subscribe_all = subscribe_all
        self._sock: anyio.abc.ByteStream | None = None
        self._ws: WSConnection | None = None
        self._closed = False

    def _build_url_and_headers(self) -> tuple[str, int, str, bool, list[tuple[bytes, bytes]]]:
        parsed = urlsplit(self.raw_base_url)
        is_ssl = parsed.scheme in ("https", "wss")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if is_ssl else 80)

        # Build path with /ari/events
        base_path = parsed.path.rstrip("/")
        if not base_path.endswith("/ari"):
            base_path = f"{base_path}/ari"
        events_path = f"{base_path}/events"

        app_str = ",".join(self.apps)
        query = f"app={app_str}"
        if self.subscribe_all:
            query += "&subscribeAll=true"

        # Also support passing api_key in query parameter for Asterisk
        if self.username or self.password:
            query += f"&api_key={self.username}:{self.password}"

        full_path = f"{events_path}?{query}"

        headers: list[tuple[bytes, bytes]] = []

        if self.username or self.password:
            auth_val = base64.b64encode(f"{self.username}:{self.password}".encode("utf-8")).decode("ascii")
            headers.append((b"authorization", f"Basic {auth_val}".encode("ascii")))

        return host, port, full_path, is_ssl, headers

    async def connect(self) -> None:
        """Establish the WebSocket connection and perform handshake."""
        host, port, full_path, is_ssl, headers = self._build_url_and_headers()
        log.debug("Connecting WebSocket to %s:%d%s (ssl=%s)", host, port, full_path, is_ssl)

        try:
            if is_ssl:
                self._sock = await anyio.connect_tcp(
                    host, port, tls=True, tls_standard_compatible=False
                )
            else:
                self._sock = await anyio.connect_tcp(host, port)
        except Exception as exc:
            raise ARIConnectionError(f"Failed to connect TCP stream to {host}:{port}: {exc}") from exc

        self._ws = WSConnection(ConnectionType.CLIENT)
        handshake_data = self._ws.send(
            Request(
                host=f"{host}:{port}",
                target=full_path,
                extra_headers=headers,
            )
        )
        await self._sock.send(handshake_data)

        # Await handshake acceptance
        connected = False
        while not connected:
            try:
                data = await self._sock.receive(4096)
            except Exception as exc:
                await self.close()
                raise ARIConnectionError(f"WebSocket handshake failed (connection closed): {exc}") from exc

            if not data:
                await self.close()
                raise ARIConnectionError("WebSocket handshake failed: server closed connection")

            self._ws.receive_data(data)
            for event in self._ws.events():
                if isinstance(event, AcceptConnection):
                    connected = True
                    log.debug("WebSocket handshake accepted")
                    break
                elif isinstance(event, RejectConnection):
                    await self.close()
                    raise ARIConnectionError(
                        f"WebSocket connection rejected by server (status {event.status_code})"
                    )

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed JSON messages received over the WebSocket."""
        if self._sock is None or self._ws is None:
            raise ARIConnectionError("WebSocket is not connected")

        buffer_chunks: list[str] = []

        while not self._closed:
            try:
                data = await self._sock.receive(4096)
            except (anyio.ClosedResourceError, anyio.EndOfStream):
                break
            except Exception as exc:
                if not self._closed:
                    log.debug("WebSocket receive error: %s", exc)
                break

            if not data:
                break

            self._ws.receive_data(data)
            for event in self._ws.events():
                if isinstance(event, TextMessage):
                    buffer_chunks.append(event.data)
                    if event.message_finished:
                        complete_text = "".join(buffer_chunks)
                        buffer_chunks.clear()
                        try:
                            msg_json = json.loads(complete_text)
                            yield msg_json
                        except json.JSONDecodeError as exc:
                            log.warning("Invalid JSON received from ARI WebSocket: %s (%s)", complete_text, exc)
                elif isinstance(event, Ping):
                    try:
                        pong = self._ws.send(event.response())
                        await self._sock.send(pong)
                    except Exception:
                        pass
                elif isinstance(event, CloseConnection):
                    try:
                        pong = self._ws.send(event.response())
                        with anyio.move_on_after(0.5, shield=True):
                            await self._sock.send(pong)
                    except Exception:
                        pass
                    self._closed = True
                    return

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._closed:
            return
        self._closed = True
        if self._ws is not None and self._sock is not None:
            try:
                close_data = self._ws.send(CloseConnection(code=1000))
                with anyio.move_on_after(0.5, shield=True):
                    await self._sock.send(close_data)
            except Exception:
                pass
        if self._sock is not None:
            with anyio.move_on_after(0.5, shield=True):
                await self._sock.aclose()
            self._sock = None
