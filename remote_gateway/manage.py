"""Local credentials management CLI for the Home Gateway.

    python -m remote_gateway.manage configure       # interactive (getpass)
    python -m remote_gateway.manage status
    python -m remote_gateway.manage clear
    python -m remote_gateway.manage import-legacy   # from ~/.siena/remote-gateway.json
    python -m remote_gateway.manage test            # one safe production connect+heartbeat

The gateway token is requested via getpass (never echoed), or read from the
SIENA_GATEWAY_ID / SIENA_GATEWAY_TOKEN environment variables (never stored
or printed). It is stored ONLY as a Windows-DPAPI-encrypted blob in
%LOCALAPPDATA%\\Siena_v2\\remote_gateway\\credentials.json. No plaintext
token ever reaches stdout, logs, or the repository.

`test` connects to the production Relay once and is deliberately NOT part
of pytest — run it manually after `configure`.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

# Allow `python -m remote_gateway.manage` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from remote_gateway import protocol  # noqa: E402
from remote_gateway.credentials import CredentialsStore  # noqa: E402

LEGACY_CREDENTIALS_PATH = Path.home() / ".siena" / "remote-gateway.json"


def _store() -> CredentialsStore:
    return CredentialsStore()


def cmd_configure() -> int:
    gateway_id = os.environ.get("SIENA_GATEWAY_ID") or input("Gateway ID (gw_...): ").strip()
    try:
        protocol.validate_gateway_id(gateway_id)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1

    token = os.environ.get("SIENA_GATEWAY_TOKEN") or getpass.getpass("Gateway token (input hidden): ").strip()
    if not token or len(token) > protocol.MAX_TOKEN_LENGTH:
        print("error: token is empty or too long")
        return 1

    store = _store()
    store.save(gateway_id, token)
    del token
    print("configured: true")
    print(f"gateway_id: {protocol.shorten_gateway_id(gateway_id)}")
    print(f"credentials: {store.path}")
    print("credentials_protected: true (Windows DPAPI, current user)")
    print("next: python -m remote_gateway.manage test")
    return 0


def cmd_status() -> int:
    store = _store()
    configured = store.is_configured()
    print(f"configured: {str(configured).lower()}")
    if configured:
        print(f"gateway_id: {protocol.shorten_gateway_id(store.gateway_id())}")
    print(f"relay_url: {config.REMOTE_GATEWAY_RELAY_URL}")
    print(f"credentials_path: {store.path}")
    print(f"credentials_protected: {str(configured).lower()}")
    return 0


def cmd_clear() -> int:
    store = _store()
    if not store.is_configured():
        print("nothing to clear")
        return 0
    # Best effort: ask a running backend to stop its agent first, so the
    # file isn't deleted from under a live connection.
    try:
        import urllib.request

        request = urllib.request.Request("http://127.0.0.1:8000/api/remote-gateway/disconnect", method="POST")
        urllib.request.urlopen(request, timeout=3)
        print("running backend agent: disconnected")
    except Exception:
        print("running backend agent: not reachable (ok if the backend is stopped)")
    removed = store.clear()
    print(f"local credentials removed: {str(removed).lower()}")
    print("note: the gateway registration on the Relay was NOT revoked — revoke it server-side if needed.")
    return 0


def cmd_import_legacy() -> int:
    """Imports the old temporary test client's plaintext credentials file
    into the DPAPI store. Never prints the token; never deletes the old
    file automatically."""
    if not LEGACY_CREDENTIALS_PATH.exists():
        print(f"no legacy file at {LEGACY_CREDENTIALS_PATH}")
        return 1
    try:
        data = json.loads(LEGACY_CREDENTIALS_PATH.read_text(encoding="utf-8-sig"))
        gateway_id = data["gateway_id"]
        token = data["token"]
    except Exception as exc:
        print(f"error: legacy file could not be parsed ({type(exc).__name__})")
        return 1
    try:
        protocol.validate_gateway_id(gateway_id)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1

    store = _store()
    store.save(gateway_id, token)
    del token, data
    print("imported: true")
    print(f"gateway_id: {protocol.shorten_gateway_id(gateway_id)}")
    print(f"encrypted credentials: {store.path}")
    print(f"you can now MANUALLY delete the old plaintext file: {LEGACY_CREDENTIALS_PATH}")
    return 0


async def _run_test() -> int:
    from remote_gateway.transport import TransportAuthError, TransportClosed, TransportTlsError, WebsocketsTransport

    store = _store()
    if not store.is_configured():
        print("error: not configured — run: python -m remote_gateway.manage configure")
        return 1

    gateway_id = store.gateway_id() or ""
    token = store.decrypt_token()
    if not token:
        print("error: credentials could not be decrypted (different Windows user?)")
        return 1

    relay_url = protocol.validate_relay_url(config.REMOTE_GATEWAY_RELAY_URL)
    url = protocol.gateway_ws_url(relay_url, gateway_id)
    transport = WebsocketsTransport()

    try:
        connection = await transport.connect(url, bearer_token=token, user_agent="Siena-Home-Gateway-Manage/0.2.3")
    except TransportAuthError as exc:
        print(f"Gateway connection: FAILED (authentication, HTTP {exc.status_code}) — check the gateway token")
        return 1
    except TransportTlsError as exc:
        print(f"Gateway connection: FAILED (TLS: {exc})")
        return 1
    finally:
        del token

    try:
        connected = protocol.parse_message(await asyncio.wait_for(connection.recv(), timeout=15))
        if connected["type"] != "gateway.connected":
            print(f"Gateway connection: unexpected first message type {connected['type']!r}")
            return 1

        request_id = protocol.new_request_id(1)
        await connection.send(protocol.build_heartbeat(request_id))
        ack = protocol.parse_message(await asyncio.wait_for(connection.recv(), timeout=15))
        heartbeat_ok = ack.get("type") == "heartbeat.ack" and ack.get("request_id") == request_id

        print("Gateway connection: successful")
        print(f"Gateway ID: {protocol.shorten_gateway_id(connected.get('gateway_id'))}")
        print(f"Relay: {protocol.PRODUCTION_RELAY_HOST}")
        print(f"Protocol version: {connected.get('protocol_version')}")
        print(f"Heartbeat: {'acknowledged' if heartbeat_ok else 'NOT acknowledged'}")
        return 0 if heartbeat_ok else 1
    except (TransportClosed, asyncio.TimeoutError) as exc:
        print(f"Gateway connection: FAILED ({type(exc).__name__})")
        return 1
    finally:
        await connection.close()


def cmd_test() -> int:
    return asyncio.run(_run_test())


COMMANDS = {
    "configure": cmd_configure,
    "status": cmd_status,
    "clear": cmd_clear,
    "import-legacy": cmd_import_legacy,
    "test": cmd_test,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in COMMANDS:
        print("usage: python -m remote_gateway.manage {configure|status|clear|import-legacy|test}")
        return 2
    return COMMANDS[args[0]]()


if __name__ == "__main__":
    sys.exit(main())
