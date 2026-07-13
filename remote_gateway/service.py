"""RemoteGatewayService — glues the HomeGatewayAgent to the Siena_v2
backend: settings (config.REMOTE_GATEWAY_ENABLED), the DPAPI credentials
store, diagnostic logging, and the trace hub. api/server.py owns exactly one
instance and drives it from the FastAPI lifespan + the settings apply block
+ the /api/remote-gateway/* endpoints.

Log/trace discipline (see remote_gateway/__init__.py): every event payload
is an agent snapshot (already allowlisted + redacted) — never raw frames,
never headers, never credentials material. Per-heartbeat noise is not
logged at all; only state CHANGES reach the JSONL log / trace stream.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from remote_gateway.agent import GatewayState, HomeGatewayAgent
from remote_gateway.credentials import CredentialsStore
from remote_gateway import protocol

# Events worth an INFO console line (everything still lands in JSONL).
_WARNING_EVENTS = {
    "remote_gateway_authentication_failed",
    "remote_gateway_connection_replaced",
    "remote_gateway_rate_limited",
    "remote_gateway_failed",
}
# High-churn intermediate states stay out of the console at INFO level.
_QUIET_EVENTS = {"remote_gateway_authenticating", "remote_gateway_unsupported_message"}


class RemoteGatewayService:
    def __init__(
        self,
        *,
        credentials_store: CredentialsStore,
        relay_url_provider: Callable[[], str],
        enabled_provider: Callable[[], bool],
        logger: Any,  # SienaLogger-compatible (event/error)
        broadcast: Callable[[dict[str, Any]], None] | None = None,
        transport: Any = None,
        allow_insecure_localhost: bool = False,
    ):
        self._credentials_store = credentials_store
        self._relay_url_provider = relay_url_provider
        self._enabled_provider = enabled_provider
        self._logger = logger
        self._broadcast = broadcast or (lambda _event: None)
        self._allow_insecure_localhost = allow_insecure_localhost

        if transport is None:
            from remote_gateway.transport import WebsocketsTransport

            transport = WebsocketsTransport()

        self._agent = HomeGatewayAgent(
            transport=transport,
            credentials_provider=self._load_credentials,
            relay_url=relay_url_provider(),
            allow_insecure_localhost=allow_insecure_localhost,
            on_event=self._handle_agent_event,
        )

    # ---- credentials ------------------------------------------------------

    def _load_credentials(self) -> tuple[str, str] | None:
        """Decrypts the token only at connect time (see credentials.py)."""
        gateway_id = self._credentials_store.gateway_id()
        if not gateway_id:
            return None
        token = self._credentials_store.decrypt_token()
        if not token:
            return None
        return gateway_id, token

    def is_configured(self) -> bool:
        return self._credentials_store.is_configured()

    def set_application_handler(self, handler: Any) -> None:
        """Registers the chat/tts application-message dispatcher — see
        HomeGatewayAgent.set_application_handler(). Called once from
        api/server.py's composition root, after the real chat/tts pipeline
        objects exist (RemoteChatService/RemoteTtsService)."""
        self._agent.set_application_handler(handler)

    @property
    def gateway_id(self) -> str | None:
        return self._agent.gateway_id

    # ---- agent events -> diagnostics ---------------------------------------

    def _handle_agent_event(self, event: str, fields: dict[str, Any]) -> None:
        safe_fields = protocol.redact(fields)
        if event in _QUIET_EVENTS:
            self._logger.event(event, **safe_fields)
        elif event in _WARNING_EVENTS:
            self._logger.error(event, console_message=f"[REMOTE-GW] {event.removeprefix('remote_gateway_')}", **safe_fields)
        else:
            self._logger.event(
                event,
                console_message=f"[REMOTE-GW] {event.removeprefix('remote_gateway_')}",
                **safe_fields,
            )
        try:
            self._broadcast({"event": event, **safe_fields})
        except Exception:
            pass  # trace visibility must never break the agent

    # ---- lifecycle ----------------------------------------------------------

    def start_if_enabled(self) -> str:
        """Called from the FastAPI lifespan startup and from the settings
        apply block. Never blocks backend startup — start() only spawns the
        background task."""
        if not self._enabled_provider():
            self._agent._state = GatewayState.DISABLED  # honest initial state for status API
            return "disabled"
        if not self.is_configured():
            self._agent._state = GatewayState.NOT_CONFIGURED
            self._logger.event(
                "remote_gateway_starting",
                configured=False,
                console_message="[REMOTE-GW] enabled but not configured — run: python -m remote_gateway.manage configure",
            )
            return "not_configured"
        self._logger.event("remote_gateway_starting", configured=True, console_message="[REMOTE-GW] starting")
        self._agent.start()
        return "started"

    async def stop(self) -> None:
        await self._agent.stop(final_state=GatewayState.DISABLED)

    async def apply_enabled(self, enabled: bool) -> str:
        """Live enable/disable from POST /api/settings — no restart needed."""
        if enabled:
            return self.start_if_enabled()
        await self.stop()
        return "stopped"

    # ---- manual controls (REST) ----------------------------------------------

    def connect(self) -> bool:
        if not self.is_configured():
            return False
        return self._agent.start()

    async def disconnect(self) -> None:
        """Stops the current session and stays Disabled until the next
        explicit connect (or a backend restart with the setting enabled) —
        auto-reconnect is unambiguous: off after a manual disconnect."""
        await self._agent.stop(final_state=GatewayState.DISABLED)

    async def reconnect(self) -> bool:
        if not self.is_configured():
            return False
        await self._agent.reconnect_now()
        return True

    # ---- status -----------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        snapshot = self._agent.snapshot()
        if snapshot.get("gateway_id") is None:
            # Before the agent's first run its snapshot has no gateway_id —
            # read the (shortened) identifier from the store so the status
            # API is useful even while disabled/not yet started.
            snapshot["gateway_id"] = protocol.shorten_gateway_id(self._credentials_store.gateway_id())
        return {
            "configured": self.is_configured(),
            "enabled": self._enabled_provider(),
            "running": self._agent.running,
            **snapshot,
        }
