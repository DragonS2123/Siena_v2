"""Siena Remote Presence — Home Gateway Agent (Phase 1).

Keeps one outgoing, TLS-verified WSS connection to the Siena Relay
(wss://relay.sienaai.ru) for as long as the Siena_v2 backend runs, so the
Android app can show "Домашняя Siena: подключена" in real time. Phase 1 is
presence only: Bearer authentication, application heartbeat, reconnect with
backoff, and safe status reporting. No remote chat, no commands, no user
message forwarding, no PC control — those are explicitly later phases.

Security posture:
- the gateway token lives ONLY in a Windows-DPAPI-encrypted local file
  (remote_gateway/credentials.py), never in git/settings.json/localStorage/
  logs/trace/URLs;
- production connections are pinned to wss://relay.sienaai.ru with standard
  TLS verification (remote_gateway/protocol.py::validate_relay_url);
  localhost is allowed only when explicitly flagged for tests/dev;
- every log/trace/status payload this package emits is built from an
  explicit safe-field allowlist (see agent.snapshot()/protocol.redact) —
  raw protocol frames and headers are never logged.
"""

from remote_gateway.agent import GatewayState, HomeGatewayAgent
from remote_gateway.credentials import CredentialsStore, DpapiProtector, TokenProtector
from remote_gateway.service import RemoteGatewayService

__all__ = [
    "CredentialsStore",
    "DpapiProtector",
    "GatewayState",
    "HomeGatewayAgent",
    "RemoteGatewayService",
    "TokenProtector",
]
