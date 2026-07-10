from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_HOSTS = ["localhost", "[::1]", "127.0.0.1"]
DEFAULT_PORTS = [8785, 8786, 8787, 8080, 8000]
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "storage" / "game" / "nucleares_snapshot.json"
VARIABLE_RE = re.compile(r"variable=([A-Z0-9_]+)")

INTERESTING_KEYS = [
    "AMBIENT_TEMPERATURE",
    "CORE_TEMPERATURE",
    "CORE_PRESSURE",
    "PRESSURIZER_TEMPERATURE",
    "PRESSURIZER_PRESSURE",
    "CONDENSER_TEMPERATURE",
    "CONDENSER_PRESSURE",
    "STEAM_GENERATOR_1_TEMPERATURE",
    "STEAM_GENERATOR_1_PRESSURE",
    "ALARMS_ACTIVE",
    "AO_AGENT_STATUS",
    "AO_AGENT_DIAGNOSTICS_JSON",
]


def socket_host(host: str) -> str:
    return "::1" if host == "[::1]" else host


def base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def fetch_text(url: str, timeout: float) -> tuple[int | None, str, str | None]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/html, text/plain;q=0.9, */*;q=0.1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2 * 1024 * 1024)
            content_type = response.headers.get("content-type", "")
            return response.status, body.decode("utf-8", errors="replace"), content_type
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return exc.code, body, exc.headers.get("content-type") if exc.headers else None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, "", str(exc)


def tcp_reachable(host: str, port: int, timeout: float) -> tuple[bool, str | None]:
    try:
        with socket.create_connection((socket_host(host), port), timeout=timeout):
            return True, None
    except OSError as exc:
        return False, str(exc)


def discover_variables(root_text: str) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for match in VARIABLE_RE.finditer(root_text):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            variables.append(name)
    return variables


def read_variable(base: str, name: str, timeout: float) -> dict[str, str]:
    status, text, error = fetch_text(f"{base}/?variable={name}", timeout)
    raw = text.strip()
    if status == 200:
        return {"value": raw, "status": "ok", "raw": raw}
    return {
        "value": "",
        "status": "error",
        "raw": raw,
        "error": error or f"HTTP {status}",
    }


def probe_http(hosts: list[str], ports: list[int], limit: int, timeout: float) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    ipv4_localhost_failed = False

    for host in hosts:
        for port in ports:
            current_base = base_url(host, port)
            tcp_ok, tcp_error = tcp_reachable(host, port, timeout)
            status, root_text, content_type_or_error = fetch_text(current_base + "/", timeout) if tcp_ok else (None, "", tcp_error)
            attempt = {
                "host": host,
                "port": port,
                "base_url": current_base,
                "tcp_reachable": tcp_ok,
                "status": status,
                "content_type": content_type_or_error if status is not None else None,
                "error": None if status is not None else content_type_or_error,
            }
            attempts.append(attempt)

            if host == "127.0.0.1" and port == 8785 and not tcp_ok:
                ipv4_localhost_failed = True

            if status != 200:
                continue

            variables = discover_variables(root_text)
            bound_note = "IPv6 localhost if applicable" if host in ("localhost", "[::1]") else ""
            if not variables:
                return {
                    "connected": True,
                    "host": host,
                    "port": port,
                    "base_url": current_base,
                    "bound_note": bound_note,
                    "discovery": "root_reachable_no_variables",
                    "parameter_count": 0,
                    "sampled_count": 0,
                    "sample_keys": [],
                    "parameters": {},
                    "root_preview": root_text[:500],
                    "attempts": attempts,
                    "ipv4_localhost_failed": ipv4_localhost_failed,
                }

            sampled = variables[: max(0, limit)]
            parameters = {
                name: read_variable(current_base, name, timeout)
                for name in sampled
            }
            return {
                "connected": True,
                "host": host,
                "port": port,
                "base_url": current_base,
                "bound_note": bound_note,
                "discovery": "html_variable_links",
                "parameter_count": len(variables),
                "sampled_count": len(sampled),
                "sample_keys": variables[:20],
                "parameters": parameters,
                "attempts": attempts,
                "ipv4_localhost_failed": ipv4_localhost_failed,
            }

    return {
        "connected": False,
        "attempted": attempts,
        "message": "Nucleares not reachable",
        "ipv4_localhost_failed": ipv4_localhost_failed,
    }


def import_nucon() -> tuple[Any | None, str | None]:
    for module_name in ("nucon", "NuCon", "nucleares", "nucleares.nucon"):
        try:
            return importlib.import_module(module_name), None
        except ModuleNotFoundError:
            continue
        except Exception as exc:
            return None, f"{module_name} import failed: {exc}"
    return None, "NuCon is not installed. Install it in the active Python environment if you want the library probe path."


def candidate_client_factories(module: Any) -> list[Any]:
    candidates: list[Any] = []
    for name in ("NuCon", "Client", "Nucleares", "Connection", "API"):
        obj = getattr(module, name, None)
        if obj is not None and callable(obj):
            candidates.append(obj)
    for _, obj in inspect.getmembers(module):
        if inspect.isclass(obj) and obj not in candidates:
            lowered = obj.__name__.lower()
            if any(token in lowered for token in ("nucon", "client", "nucleares", "connection")):
                candidates.append(obj)
    return candidates


def instantiate_client(factory: Any, host: str, port: int) -> Any | None:
    plain_host = socket_host(host)
    url = base_url(host, port)
    attempts = [
        ((plain_host, port), {}),
        ((), {"host": plain_host, "port": port}),
        ((), {"url": url}),
        ((), {"base_url": url}),
        ((url,), {}),
        ((), {}),
    ]
    for args, kwargs in attempts:
        try:
            return factory(*args, **kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    return None


def call_read_method(client: Any) -> tuple[str, Any] | None:
    for name in (
        "get_all",
        "getAll",
        "read_all",
        "readAll",
        "get_all_parameters",
        "get_parameters",
        "parameters",
        "get_state",
        "state",
        "status",
    ):
        attr = getattr(client, name, None)
        if attr is None:
            continue
        try:
            value = attr() if callable(attr) else attr
        except TypeError:
            continue
        except Exception:
            continue
        if value is not None:
            return name, value
    return None


def probe_nucon(hosts: list[str], ports: list[int]) -> dict[str, Any]:
    module, import_message = import_nucon()
    if module is None:
        return {"available": False, "connected": False, "message": import_message}

    for factory in candidate_client_factories(module):
        for host in hosts:
            for port in ports:
                client = instantiate_client(factory, host, port)
                if client is None:
                    continue
                read = call_read_method(client)
                if read is None:
                    continue
                method_name, value = read
                return {
                    "available": True,
                    "connected": True,
                    "module": getattr(module, "__name__", "unknown"),
                    "client": getattr(factory, "__name__", repr(factory)),
                    "read_method": method_name,
                    "host": host,
                    "port": port,
                    "summary_type": type(value).__name__,
                }

    return {
        "available": True,
        "connected": False,
        "module": getattr(module, "__name__", "unknown"),
        "message": "NuCon imported, but no readable get_all/state/status-style method succeeded.",
    }


def localhost_ipv6_note(snapshot: dict[str, Any], hosts: list[str], ports: list[int], timeout: float) -> str:
    if not snapshot.get("connected"):
        return ""
    if snapshot.get("host") not in ("localhost", "[::1]"):
        return ""
    if 8785 not in ports:
        return ""
    ipv4_ok, _ = tcp_reachable("127.0.0.1", 8785, timeout)
    if not ipv4_ok:
        return "Nucleares is bound to IPv6 localhost (::1), not IPv4 127.0.0.1"
    return ""


def build_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    hosts = [args.host] if args.host else DEFAULT_HOSTS
    ports = [args.port] if args.port is not None else (args.ports or DEFAULT_PORTS)
    http = probe_http(hosts, ports, args.limit, args.timeout)
    nucon = probe_nucon(hosts, ports)
    timestamp = datetime.now(timezone.utc).isoformat()
    bound_note = localhost_ipv6_note(http, hosts, ports, args.timeout)

    if http.get("connected"):
        snapshot: dict[str, Any] = {
            "connected": True,
            "host": http["host"],
            "port": http["port"],
            "base_url": http["base_url"],
            "bound_note": bound_note or http.get("bound_note", ""),
            "discovery": http["discovery"],
            "parameter_count": http["parameter_count"],
            "sampled_count": http["sampled_count"],
            "sample_keys": http["sample_keys"],
            "parameters": http["parameters"],
            "writable_exposed": False,
            "timestamp": timestamp,
            "nucon": nucon,
            "attempts": http["attempts"],
        }
        if http["discovery"] == "root_reachable_no_variables":
            snapshot["root_preview"] = http.get("root_preview", "")
        return snapshot

    return {
        "connected": False,
        "host": None,
        "port": None,
        "base_url": None,
        "bound_note": bound_note,
        "discovery": "not_reachable",
        "parameter_count": 0,
        "sampled_count": 0,
        "sample_keys": [],
        "parameters": {},
        "writable_exposed": False,
        "timestamp": timestamp,
        "nucon": nucon,
        "attempted": http.get("attempted", []),
        "message": http.get("message", "Nucleares not reachable"),
    }


def save_snapshot(snapshot: dict[str, Any]) -> None:
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(snapshot: dict[str, Any]) -> None:
    print("Nucleares API probe (read-only)")
    print(f"connected: {str(snapshot['connected']).lower()}")
    if snapshot["connected"]:
        print(f"selected: {snapshot['base_url']}")
        if snapshot.get("bound_note"):
            print(snapshot["bound_note"])
    else:
        print(snapshot.get("message", "Nucleares not reachable"))
    print(f"discovery: {snapshot['discovery']}")
    if snapshot["discovery"] == "root_reachable_no_variables":
        print("webserver reachable, but variable list not recognized")
    print(f"parameter_count: {snapshot['parameter_count']}")
    print(f"sampled_count: {snapshot['sampled_count']}")

    if snapshot.get("sample_keys"):
        print("sample_keys:")
        for key in snapshot["sample_keys"][:20]:
            print(f"  - {key}")

    interesting = {
        key: snapshot.get("parameters", {}).get(key)
        for key in INTERESTING_KEYS
        if key in snapshot.get("parameters", {})
    }
    if interesting:
        print("interesting_values:")
        for key, item in list(interesting.items())[:10]:
            status = item.get("status", "?") if isinstance(item, dict) else "?"
            value = item.get("value", "") if isinstance(item, dict) else ""
            print(f"  - {key}: {value} ({status})")

    nucon = snapshot.get("nucon", {})
    if nucon.get("available"):
        if nucon.get("connected"):
            print(f"NuCon: readable via {nucon.get('client')}.{nucon.get('read_method')}")
        else:
            print(f"NuCon: {nucon.get('message', 'available, but no readable state discovered')}")
    else:
        print(f"NuCon: {nucon.get('message', 'missing')}")

    print(f"writable_exposed: {str(bool(snapshot['writable_exposed'])).lower()}")
    print(f"snapshot_path: {SNAPSHOT_PATH}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Nucleares local API probe.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum discovered variables to read, default 100")
    parser.add_argument("--host", default=None, help="Single host to probe. Defaults: localhost, [::1], 127.0.0.1")
    parser.add_argument("--port", type=int, default=None, help="Single port to probe. Defaults start with 8785")
    parser.add_argument("--ports", nargs="*", type=int, default=None, help="Fallback port list, ignored when --port is set")
    parser.add_argument("--timeout", type=float, default=2.0, help="Socket/HTTP timeout in seconds")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    snapshot = build_snapshot(args)
    save_snapshot(snapshot)
    print_summary(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
