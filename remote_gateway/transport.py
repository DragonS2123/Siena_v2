"""WSS transport for the Home Gateway Agent — a thin, injectable wrapper
around the `websockets` client (already a dependency via uvicorn[standard])
so the agent's state machine can be tested against an in-memory fake
without any network or TLS.

TLS: standard verification only. No trust-all contexts, no disabled
hostname checks — a certificate/hostname failure must surface as a normal
error (agent state Failed), never be "fixed" by weakening TLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class TransportConnection(Protocol):
    async def send(self, text: str) -> None: ...
    async def recv(self) -> str: ...
    async def close(self) -> None: ...


class TransportAuthError(Exception):
    """Handshake rejected with an auth-shaped HTTP status (401/403) —
    over uvicorn, a pre-accept close on the Relay side surfaces this way."""

    def __init__(self, status_code: int):
        super().__init__(f"handshake rejected with HTTP {status_code}")
        self.status_code = status_code


class TransportTlsError(Exception):
    """Certificate/hostname verification failure. Never retried by
    weakening TLS — reported as-is (without embedding header/URL secrets)."""


@dataclass
class TransportClosed(Exception):
    """Connection closed (cleanly or not). `code` is the WebSocket close
    code when one was received (e.g. Relay's 4001/4401/4408/4429)."""

    code: int | None = None
    reason: str = ""

    def __str__(self) -> str:  # pragma: no cover — repr convenience only
        return f"connection closed (code={self.code})"


class WebsocketsTransport:
    """Real transport. `connect()` returns an object with send/recv/close;
    recv raises TransportClosed with the server's close code."""

    def __init__(self, *, open_timeout: float = 15.0, close_timeout: float = 5.0):
        self._open_timeout = open_timeout
        self._close_timeout = close_timeout

    async def connect(self, url: str, *, bearer_token: str, user_agent: str) -> TransportConnection:
        import ssl

        import websockets
        from websockets.asyncio.client import connect as ws_connect

        try:
            protocol = await ws_connect(
                url,
                additional_headers={"Authorization": f"Bearer {bearer_token}"},
                user_agent_header=user_agent,
                open_timeout=self._open_timeout,
                close_timeout=self._close_timeout,
                max_size=8192,  # a little headroom above the Relay's 4096-byte frames
                # Protocol-level ping keeps NATs from silently dropping the
                # long-lived socket; it does NOT replace the application
                # heartbeat required by the Relay contract (agent.py).
                ping_interval=20,
                ping_timeout=20,
            )
        except ssl.SSLError as exc:
            # No header/URL details in the message — reason code only.
            raise TransportTlsError(getattr(exc, "reason", None) or "tls_verification_failed") from None
        except websockets.exceptions.InvalidStatus as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise TransportAuthError(status) from None
            raise
        return _WebsocketsConnection(protocol)


class _WebsocketsConnection:
    def __init__(self, protocol: Any):
        self._protocol = protocol

    async def send(self, text: str) -> None:
        import websockets

        try:
            await self._protocol.send(text)
        except websockets.exceptions.ConnectionClosed as exc:
            raise TransportClosed(code=getattr(exc.rcvd, "code", None) or getattr(exc.sent, "code", None)) from None

    async def recv(self) -> str:
        import websockets

        try:
            message = await self._protocol.recv()
        except websockets.exceptions.ConnectionClosed as exc:
            raise TransportClosed(code=getattr(exc.rcvd, "code", None) or getattr(exc.sent, "code", None)) from None
        return message if isinstance(message, str) else message.decode("utf-8", errors="replace")

    async def close(self) -> None:
        try:
            await self._protocol.close()
        except Exception:
            pass
