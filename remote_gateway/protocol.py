"""Gateway WebSocket protocol constants + message helpers, synchronized
with Relay 0.5.0 (G:\\SienaRelay\\app\\gateway_ws.py — read-only reference).
Do not change these unilaterally: the Relay side is the contract owner.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any
from urllib.parse import urlsplit

PROTOCOL_VERSION = 1

# Server-side defaults (Relay sends the live values inside gateway.connected;
# these are only the synchronized fallbacks when a field is absent).
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 20
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 55
MAX_MESSAGE_BYTES = 4096

# Relay validation patterns (must match gateway_ws.py exactly).
GATEWAY_ID_PATTERN = re.compile(r"^gw_[A-Za-z0-9_-]{8,64}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_TOKEN_LENGTH = 256

# Relay close codes (gateway_ws.py / connections.py).
CLOSE_CONNECTION_REPLACED = 4001
CLOSE_INVALID_MESSAGE = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_REVOKED = 4403
CLOSE_HEARTBEAT_TIMEOUT = 4408
CLOSE_MESSAGE_TOO_LARGE = 4409
CLOSE_RATE_LIMITED = 4429
CLOSE_INTERNAL_ERROR = 1011

PRODUCTION_RELAY_HOST = "relay.sienaai.ru"

# Fields that must never appear in any log/trace/status payload this
# package produces — applied as a final safety net on top of the explicit
# safe-field allowlists used everywhere (see agent.snapshot()).
_SENSITIVE_FIELD_NAMES = {"authorization", "token", "gateway_token", "encrypted_token", "secret"}


class RelayUrlError(ValueError):
    pass


def validate_relay_url(url: str, *, allow_insecure_localhost: bool = False) -> str:
    """Production is pinned to wss://relay.sienaai.ru with standard TLS
    verification. ws:// and other hosts are allowed ONLY when the caller
    explicitly opts into localhost dev/test mode — and even then only for
    loopback hosts, so a config typo can never silently point the token at
    an arbitrary server."""
    parts = urlsplit(url)
    host = parts.hostname or ""

    if parts.scheme == "wss" and host == PRODUCTION_RELAY_HOST:
        return url.rstrip("/")

    if allow_insecure_localhost and parts.scheme in ("ws", "wss") and host in ("127.0.0.1", "localhost", "::1"):
        return url.rstrip("/")

    raise RelayUrlError(
        f"relay URL must be wss://{PRODUCTION_RELAY_HOST} (got scheme={parts.scheme!r}, host={host!r})"
    )


def validate_gateway_id(gateway_id: str) -> str:
    if not GATEWAY_ID_PATTERN.fullmatch(gateway_id or ""):
        raise ValueError("gateway_id must match gw_[A-Za-z0-9_-]{8,64}")
    return gateway_id


def gateway_ws_url(relay_url: str, gateway_id: str) -> str:
    return f"{relay_url.rstrip('/')}/v1/ws/gateway/{gateway_id}"


def shorten_gateway_id(gateway_id: str | None) -> str | None:
    """gw_AbCdEfGh... -> gw_…fGh — enough to recognize, useless to guess."""
    if not gateway_id:
        return None
    return f"gw_…{gateway_id[-5:]}" if len(gateway_id) > 8 else gateway_id


def build_heartbeat(request_id: str) -> str:
    return json.dumps({"type": "heartbeat", "request_id": request_id}, separators=(",", ":"))


def new_request_id(counter: int) -> str:
    return f"hb-{counter}-{secrets.token_hex(4)}"


def parse_message(text: str) -> dict[str, Any]:
    """Strict inbound parsing per the Relay contract: JSON object only,
    size-capped, string `type`. Raises ValueError on anything else — the
    caller decides whether that warrants a disconnect. Never logs the
    payload itself."""
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds max_message_bytes")
    message = json.loads(text)
    if not isinstance(message, dict):
        raise ValueError("message is not a JSON object")
    if not isinstance(message.get("type"), str):
        raise ValueError("message has no string 'type'")
    return message


def redact(fields: dict[str, Any]) -> dict[str, Any]:
    """Final safety net for log/trace payloads: drops any key whose name
    looks like a credential, recursively. The code never intentionally puts
    secrets into these dicts — this exists so a future mistake fails safe."""
    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key.lower() in _SENSITIVE_FIELD_NAMES:
            clean[key] = "[redacted]"
        elif isinstance(value, dict):
            clean[key] = redact(value)
        else:
            clean[key] = value
    return clean
