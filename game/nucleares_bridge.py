from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_HOSTS = ["localhost", "[::1]", "127.0.0.1"]
DEFAULT_PORTS = [8785, 8786, 8787, 8080, 8000]
VARIABLE_RE = re.compile(r"variable=([A-Z0-9_]+)")
PLACEHOLDER_KEYS = {"VARNAME"}

NORMALIZED_KEYS = {
    "AMBIENT_TEMPERATURE": "ambient_temperature",
    "ALARMS_ACTIVE": "alarms_active",
    "AO_AGENT_STATUS": "ao_agent_status",
    "AO_AGENT_DIAGNOSTICS_JSON": "ao_agent_diagnostics_json",
    "CORE_TEMPERATURE": "core_temperature",
    "CORE_PRESSURE": "core_pressure",
    "PRESSURIZER_TEMPERATURE": "pressurizer_temperature",
    "PRESSURIZER_PRESSURE": "pressurizer_pressure",
    "CONDENSER_TEMPERATURE": "condenser_temperature",
    "CONDENSER_PRESSURE": "condenser_pressure",
    "CONDENSER_CIRCULATION_PUMP_SPEED": "condenser_circulation_pump_speed",
    "CONDENSER_CIRCULATION_PUMP_ACTIVE": "condenser_circulation_pump_active",
    "CONDENSER_COOLING_PUMP_SPEED": "condenser_cooling_pump_speed",
    "ELECTRIC_TURBINE_POWER": "electric_turbine_power",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def discover_variables(html: str) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for match in VARIABLE_RE.finditer(html):
        key = match.group(1)
        if key in PLACEHOLDER_KEYS or key in seen:
            continue
        seen.add(key)
        variables.append(key)
    return variables


def normalize_key(raw_key: str) -> str:
    return raw_key.lower()


def normalized_variable_names(variables: list[str]) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for key in variables:
        is_selected = (
            key in NORMALIZED_KEYS
            or (
                key.startswith("STEAM_GENERATOR_")
                and (key.endswith("_TEMPERATURE") or key.endswith("_PRESSURE"))
            )
        )
        if is_selected and key not in seen:
            seen.add(key)
            selected.append(key)
    return selected


class NuclearesBridgeClient:
    """Read-only client for the local Nucleares simulation webserver."""

    def __init__(
        self,
        base_url: str = "http://localhost:8785",
        hosts: list[str] | None = None,
        ports: list[int] | None = None,
        timeout: float = 2.0,
        raw_sample_limit: int = 20,
        snapshot_path: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.hosts = hosts or DEFAULT_HOSTS
        self.ports = ports or DEFAULT_PORTS
        self.timeout = timeout
        self.raw_sample_limit = raw_sample_limit
        self.snapshot_path = snapshot_path

    def status(self) -> dict[str, Any]:
        attempted: list[dict[str, Any]] = []
        selected_base_url, variables = self._discover(attempted)
        timestamp = _now_iso()

        if selected_base_url is None:
            return {
                "game": "nucleares",
                "connected": False,
                "timestamp": timestamp,
                "error": "Nucleares webserver not reachable or no variable list recognized",
                "attempted": attempted,
            }

        read_keys = normalized_variable_names(variables)
        raw_sample_keys = [key for key in variables if key not in read_keys][: self.raw_sample_limit]
        read_order = list(dict.fromkeys(read_keys + raw_sample_keys))
        values: dict[str, str] = {}
        warnings: list[str] = []
        for key in read_order:
            try:
                values[key] = self.read_variable(selected_base_url, key)
            except requests.RequestException as exc:
                warnings.append(f"{key}: {exc}")

        normalized = {
            NORMALIZED_KEYS.get(key, normalize_key(key)): {"value": values[key], "raw_key": key}
            for key in read_keys
            if key in values
        }
        raw_sample = {key: values[key] for key in raw_sample_keys if key in values}
        result = {
            "game": "nucleares",
            "connected": True,
            "base_url": selected_base_url,
            "timestamp": timestamp,
            "parameter_count": len(variables),
            "normalized": normalized,
            "raw_sample": raw_sample,
            "warnings": warnings,
        }
        self._write_snapshot(result, variables, values)
        return result

    def _discover(self, attempted: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
        preferred = self._discover_base_url(self.base_url, attempted)
        if preferred[0] is not None:
            return preferred

        for host in self.hosts:
            for port in self.ports:
                candidate = base_url(host, port)
                if candidate == self.base_url:
                    continue
                found = self._discover_base_url(candidate, attempted)
                if found[0] is not None:
                    return found
        return None, []

    def _discover_base_url(self, candidate_base_url: str, attempted: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
        try:
            response = requests.get(
                candidate_base_url.rstrip("/") + "/",
                timeout=self.timeout,
                headers={"Accept": "text/html, text/plain;q=0.9, */*;q=0.1"},
            )
        except requests.RequestException as exc:
            attempted.append({"base_url": candidate_base_url, "error": str(exc)})
            return None, []

        content_type = response.headers.get("content-type", "")
        variables = discover_variables(response.text if response.status_code == 200 else "")
        attempted.append(
            {
                "base_url": candidate_base_url,
                "status_code": response.status_code,
                "content_type": content_type,
                "variable_count": len(variables),
            }
        )
        if response.status_code == 200 and "text/html" in content_type.lower() and variables:
            return candidate_base_url.rstrip("/"), variables
        return None, []

    def read_variable(self, selected_base_url: str, key: str) -> str:
        response = requests.get(
            f"{selected_base_url.rstrip('/')}/?variable={quote(key, safe='')}",
            timeout=self.timeout,
            headers={"Accept": "text/plain, */*;q=0.1"},
        )
        response.raise_for_status()
        return response.text.strip()

    def _write_snapshot(self, result: dict[str, Any], variables: list[str], values: dict[str, str]) -> None:
        if self.snapshot_path is None:
            return
        snapshot = {
            "connected": True,
            "host": result["base_url"].removeprefix("http://").rsplit(":", 1)[0],
            "port": int(result["base_url"].rsplit(":", 1)[1]),
            "base_url": result["base_url"],
            "bound_note": "IPv6 localhost if applicable"
            if "localhost" in result["base_url"] or "[::1]" in result["base_url"]
            else "",
            "discovery": "html_variable_links",
            "parameter_count": len(variables),
            "sampled_count": len(values),
            "sample_keys": variables[:20],
            "parameters": {
                key: {"value": value, "status": "ok", "raw": value}
                for key, value in values.items()
            },
            "writable_exposed": False,
            "timestamp": result["timestamp"],
        }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
