"""HomeGatewayAgent — the Home Gateway state machine: authenticate, hold the
socket, heartbeat per the Relay contract, reconnect with backoff, and expose
a safe status snapshot. One asyncio task owns everything (connect + recv +
heartbeat scheduling live in the same loop), so heartbeat/reconnect tasks
can never duplicate, and a generation counter makes stale connections unable
to mutate the state of a newer one.

Never logs or exposes: the gateway token, Authorization headers, raw
protocol frames, or credentials material. Everything emitted goes through
explicit safe fields + protocol.redact() as a final net.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from enum import Enum
from typing import Any, Awaitable, Callable

from remote_gateway import protocol
from remote_gateway.transport import (
    TransportAuthError,
    TransportClosed,
    TransportConnection,
    TransportTlsError,
)

USER_AGENT = "Siena-Home-Gateway/0.2.3"

BACKOFF_SCHEDULE_SECONDS = (1, 2, 5, 10, 30)
RATE_LIMIT_MIN_DELAY_SECONDS = 60
CONNECT_HANDSHAKE_TIMEOUT_SECONDS = 15


class GatewayState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTHENTICATION_FAILED = "authentication_failed"
    CONNECTION_REPLACED = "connection_replaced"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    STOPPING = "stopping"


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class _HeartbeatTimeout(Exception):
    pass


class HomeGatewayAgent:
    def __init__(
        self,
        *,
        transport: Any,
        credentials_provider: Callable[[], tuple[str, str] | None],
        relay_url: str,
        allow_insecure_localhost: bool = False,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self._transport = transport
        self._credentials_provider = credentials_provider
        self._relay_url = relay_url
        self._allow_insecure_localhost = allow_insecure_localhost
        self._on_event = on_event or (lambda _event, _fields: None)

        # Application-protocol messages (chat/tts) are dispatched to this
        # handler when set — see set_application_handler(). None in Phase 1
        # deployments where only heartbeat is spoken.
        self._application_handler: (
            Callable[[dict[str, Any], Callable[[dict[str, Any]], Awaitable[None]], str], Awaitable[None]] | None
        ) = None
        # Serializes ALL outbound frames (heartbeat + any application-layer
        # sends spawned from _dispatch_application_message) — websockets
        # connections are not safe for concurrent send() from multiple tasks.
        self._send_lock = asyncio.Lock()

        self._task: asyncio.Task | None = None
        self._generation = 0

        self._state = GatewayState.DISCONNECTED
        self._gateway_id: str | None = None
        self._connected_at: float | None = None
        self._last_server_contact_at: float | None = None
        self._reconnect_attempt = 0
        self._next_reconnect_at: float | None = None
        self._last_close_code: int | None = None
        self._safe_error_code: str | None = None

    # ---- state -----------------------------------------------------------

    @property
    def state(self) -> GatewayState:
        return self._state

    @property
    def gateway_id(self) -> str | None:
        return self._gateway_id

    def set_application_handler(
        self,
        handler: Callable[[dict[str, Any], Callable[[dict[str, Any]], Awaitable[None]], str], Awaitable[None]] | None,
    ) -> None:
        """Registers the callback invoked for any inbound message type other
        than heartbeat.ack/error.unsupported_message (chat.request,
        chat.cancel, tts.request in Siena Remote 0.6.0). Called with
        (message, send, gateway_id) — send() writes a JSON frame back over
        the CURRENT live connection (safe to call even after this connection
        generation ends; the sender captured by a stale connection is simply
        pointed at a closed socket and raises, which callers must tolerate).
        Dispatched as a fire-and-forget task so a slow/failing chat turn can
        never block the heartbeat loop or a concurrent turn for another
        Device."""
        self._application_handler = handler

    def snapshot(self) -> dict[str, Any]:
        """Safe status only — no token, no headers, no raw frames, no
        credentials material (see module docstring)."""
        return protocol.redact({
            "state": self._state.value,
            "gateway_id": protocol.shorten_gateway_id(self._gateway_id),
            "relay_host": protocol.PRODUCTION_RELAY_HOST if not self._allow_insecure_localhost else self._relay_url,
            "connected_at": _iso(self._connected_at),
            "last_server_contact_at": _iso(self._last_server_contact_at),
            "reconnect_attempt": self._reconnect_attempt,
            "next_reconnect_at": _iso(self._next_reconnect_at),
            "last_close_code": self._last_close_code,
            "safe_error_code": self._safe_error_code,
        })

    def _set_state(self, generation: int, state: GatewayState, *, error_code: str | None = None, close_code: int | None = None) -> None:
        # A stale connection's callbacks must never overwrite the state of a
        # newer run — the generation check enforces that structurally.
        if generation != self._generation:
            return
        self._state = state
        if error_code is not None:
            self._safe_error_code = error_code
        if close_code is not None:
            self._last_close_code = close_code
        self._on_event(f"remote_gateway_{state.value}", self.snapshot())

    # ---- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Starts the single background task. Idempotent — returns False if
        already running."""
        if self._task is not None and not self._task.done():
            return False
        self._generation += 1
        self._safe_error_code = None
        self._task = asyncio.create_task(self._run(self._generation), name="remote-gateway-agent")
        return True

    async def stop(self, final_state: GatewayState = GatewayState.DISABLED) -> None:
        """Cancels the task and waits briefly — shutdown must not hang."""
        task = self._task
        self._task = None
        self._generation += 1  # anything still running is now stale
        if task is not None and not task.done():
            self._state = GatewayState.STOPPING
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        self._state = final_state
        self._connected_at = None
        self._next_reconnect_at = None
        self._on_event("remote_gateway_stopped", self.snapshot())

    async def reconnect_now(self) -> None:
        """Closes the current socket/run and starts a fresh one — never
        creates parallel tasks (stop() before start())."""
        await self.stop(final_state=GatewayState.DISCONNECTED)
        self.start()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- main loop ------------------------------------------------------

    async def _run(self, generation: int) -> None:
        attempt = 0
        while True:
            credentials = self._credentials_provider()
            if credentials is None:
                self._set_state(generation, GatewayState.NOT_CONFIGURED, error_code="credentials_missing")
                return

            gateway_id, token = credentials
            try:
                protocol.validate_gateway_id(gateway_id)
                relay_url = protocol.validate_relay_url(self._relay_url, allow_insecure_localhost=self._allow_insecure_localhost)
            except ValueError as exc:
                self._set_state(generation, GatewayState.FAILED, error_code=f"config:{type(exc).__name__}")
                return

            self._gateway_id = gateway_id
            url = protocol.gateway_ws_url(relay_url, gateway_id)
            self._reconnect_attempt = attempt
            self._set_state(generation, GatewayState.CONNECTING)

            connection: TransportConnection | None = None
            try:
                connection = await self._transport.connect(url, bearer_token=token, user_agent=USER_AGENT)
            except TransportAuthError as exc:
                self._set_state(generation, GatewayState.AUTHENTICATION_FAILED, error_code=f"auth_http_{exc.status_code}")
                return  # no auto-retry: the token needs human attention
            except TransportTlsError as exc:
                self._set_state(generation, GatewayState.FAILED, error_code=f"tls:{exc}")
                return  # never retried by weakening TLS
            except asyncio.CancelledError:
                raise
            except Exception:
                attempt = await self._sleep_backoff(generation, attempt, reason="connect_failed")
                continue
            finally:
                del token  # no extra plaintext copies once the handshake owns it

            try:
                self._set_state(generation, GatewayState.AUTHENTICATING)
                heartbeat_interval, heartbeat_timeout = await self._await_gateway_connected(connection)
                self._connected_at = time.time()
                self._last_server_contact_at = self._connected_at
                attempt = 0  # successful gateway.connected resets backoff
                self._reconnect_attempt = 0
                self._next_reconnect_at = None
                self._set_state(generation, GatewayState.CONNECTED)
                await self._connected_loop(connection, heartbeat_interval, heartbeat_timeout)
            except TransportClosed as exc:
                self._connected_at = None
                self._last_close_code = exc.code
                if exc.code == protocol.CLOSE_CONNECTION_REPLACED:
                    self._set_state(generation, GatewayState.CONNECTION_REPLACED, close_code=exc.code)
                    return  # another Home Gateway took over — no reconnect race
                if exc.code in (protocol.CLOSE_UNAUTHORIZED, protocol.CLOSE_REVOKED):
                    self._set_state(generation, GatewayState.AUTHENTICATION_FAILED, error_code=f"close_{exc.code}", close_code=exc.code)
                    return
                if exc.code == protocol.CLOSE_RATE_LIMITED:
                    self._set_state(generation, GatewayState.RATE_LIMITED, close_code=exc.code)
                    attempt = await self._sleep_backoff(generation, attempt, reason="rate_limited", minimum=RATE_LIMIT_MIN_DELAY_SECONDS)
                    continue
                attempt = await self._sleep_backoff(generation, attempt, reason=f"closed_{exc.code}")
                continue
            except _HeartbeatTimeout:
                self._connected_at = None
                attempt = await self._sleep_backoff(generation, attempt, reason="heartbeat_timeout")
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected_at = None
                attempt = await self._sleep_backoff(generation, attempt, reason=f"error_{type(exc).__name__}")
                continue
            finally:
                if connection is not None:
                    await connection.close()

    async def _await_gateway_connected(self, connection: TransportConnection) -> tuple[float, float]:
        first = await asyncio.wait_for(connection.recv(), timeout=CONNECT_HANDSHAKE_TIMEOUT_SECONDS)
        message = protocol.parse_message(first)
        if message["type"] != "gateway.connected":
            raise ValueError(f"expected gateway.connected, got {message['type']!r}")
        interval = float(message.get("heartbeat_interval_seconds") or protocol.DEFAULT_HEARTBEAT_INTERVAL_SECONDS)
        timeout = float(message.get("heartbeat_timeout_seconds") or protocol.DEFAULT_HEARTBEAT_TIMEOUT_SECONDS)
        return interval, timeout

    async def _connected_loop(self, connection: TransportConnection, heartbeat_interval: float, heartbeat_timeout: float) -> None:
        """Sends the application heartbeat on the server-provided interval
        and processes inbound frames — all in this one task, so nothing can
        duplicate. A heartbeat whose ack doesn't arrive within the server's
        own timeout closes the socket and lets _run() reconnect."""
        counter = 0
        next_heartbeat_at = time.monotonic()  # first heartbeat immediately
        pending_acks: dict[str, float] = {}  # request_id -> deadline (monotonic)

        while True:
            now = time.monotonic()

            overdue = [rid for rid, deadline in pending_acks.items() if now >= deadline]
            if overdue:
                raise _HeartbeatTimeout()

            if now >= next_heartbeat_at:
                counter += 1
                request_id = protocol.new_request_id(counter)
                async with self._send_lock:
                    await connection.send(protocol.build_heartbeat(request_id))
                pending_acks[request_id] = now + heartbeat_timeout
                next_heartbeat_at = now + heartbeat_interval

            deadlines = [next_heartbeat_at, *pending_acks.values()]
            wait_for = max(0.05, min(deadlines) - time.monotonic())

            try:
                text = await asyncio.wait_for(connection.recv(), timeout=wait_for)
            except asyncio.TimeoutError:
                continue  # time to send the next heartbeat / check ack deadlines

            try:
                message = protocol.parse_message(text)
            except ValueError:
                # Malformed inbound frame — per contract discipline we drop
                # the connection rather than guess. Payload is never logged.
                raise TransportClosed(code=None, reason="invalid_inbound_message")

            self._last_server_contact_at = time.time()
            message_type = message["type"]

            if message_type == "heartbeat.ack":
                pending_acks.pop(message.get("request_id"), None)
            elif message_type == "error.unsupported_message":
                # Getting this back for heartbeat would mean contract drift;
                # for an application message it just means Relay is on an
                # older/different protocol version than this handler expects.
                self._on_event("remote_gateway_unsupported_message", {"request_id": message.get("request_id")})
            elif self._application_handler is not None:
                # chat.request / chat.cancel / tts.request (Siena Remote
                # 0.6.0) — dispatched as a fire-and-forget task so a slow or
                # failing chat/tts turn can never block this loop's heartbeat
                # scheduling or a concurrent turn for another Device.
                sender = self._make_sender(connection)
                asyncio.create_task(self._dispatch_application_message(message, sender))
            # Any other message type with no handler registered is safely
            # ignored — never logged raw.

    def _make_sender(self, connection: TransportConnection) -> Callable[[dict[str, Any]], Awaitable[None]]:
        async def send(payload: dict[str, Any]) -> None:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            async with self._send_lock:
                await connection.send(text)

        return send

    async def _dispatch_application_message(
        self, message: dict[str, Any], sender: Callable[[dict[str, Any]], Awaitable[None]]
    ) -> None:
        handler = self._application_handler
        if handler is None:
            return
        try:
            await handler(message, sender, self._gateway_id or "")
        except asyncio.CancelledError:
            raise
        except Exception:
            # A chat/tts bridge failure must never take down the connection
            # loop — the bridge itself is responsible for sending the phone
            # a chat.failed/tts.failed on its own errors; this is a last
            # resort for bugs in the bridge itself. Never logged raw (may
            # contain no message content by construction, but the handler
            # itself owns all privacy-sensitive logging).
            self._on_event("remote_gateway_application_handler_error", {"message_type": message.get("type")})

    async def _sleep_backoff(self, generation: int, attempt: int, *, reason: str, minimum: float = 0) -> int:
        base = BACKOFF_SCHEDULE_SECONDS[min(attempt, len(BACKOFF_SCHEDULE_SECONDS) - 1)]
        delay = max(minimum, base * random.uniform(0.85, 1.15))
        self._next_reconnect_at = time.time() + delay
        self._reconnect_attempt = attempt + 1
        if self._state != GatewayState.RATE_LIMITED:
            self._set_state(generation, GatewayState.RECONNECTING, error_code=reason)
        await asyncio.sleep(delay)
        return attempt + 1
