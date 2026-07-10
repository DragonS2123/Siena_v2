from __future__ import annotations

import json
import re
from typing import Any


_STRONG_INTENT_RE = re.compile(
    r"\b(nucleares|alarms|condenser|pressurizer|core|steam\s+generator)\b"
    r"|(?:нуклеарес|станци[яиюе]|реактор|энергоблок|турбин[ауы]?"
    r"|насос|насосы|авари[ияй])",
    re.IGNORECASE,
)
_GENERIC_TELEMETRY_RE = re.compile(r"\b(давлени[еяю]|температур[аыеу])\b", re.IGNORECASE)
_GAME_CONTEXT_RE = re.compile(
    r"\b(nucleares|condenser|pressurizer|core|steam\s+generator)\b"
    r"|(?:нуклеарес|станци[яиюе]|реактор|энергоблок|турбин[ауы]?|насос|насосы)",
    re.IGNORECASE,
)


def wants_nucleares_context(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _STRONG_INTENT_RE.search(text):
        return True
    return bool(_GENERIC_TELEMETRY_RE.search(text) and _GAME_CONTEXT_RE.search(text))


def nucleares_context_skip_reason(text: str) -> str | None:
    if not text or wants_nucleares_context(text):
        return None
    if _GENERIC_TELEMETRY_RE.search(text):
        return "generic_telemetry_without_game_context"
    return None


def _normalized_value(status: dict[str, Any], key: str) -> str | None:
    item = status.get("normalized", {}).get(key)
    if not isinstance(item, dict):
        return None
    value = item.get("value")
    if value is None:
        return None
    return str(value)


def _parse_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ao_status_summary(status: dict[str, Any]) -> str | None:
    parsed = _parse_json(_normalized_value(status, "ao_agent_status"))
    if not parsed:
        return None
    fields = []
    for key in ("runtime_state", "language", "response_mode", "llm_reachable", "heuristic_fallback"):
        if key in parsed:
            fields.append(f"{key}={parsed[key]}")
    return ", ".join(fields) if fields else None


def _diagnostics_summary(status: dict[str, Any]) -> dict[str, str]:
    parsed = _parse_json(_normalized_value(status, "ao_agent_diagnostics_json"))
    if not parsed:
        return {}

    result: dict[str, str] = {}
    overview = parsed.get("reactor_overview")
    if isinstance(overview, dict):
        if overview.get("operation_mode") is not None:
            result["operation_mode"] = str(overview["operation_mode"])
        if overview.get("core_temperature_c") is not None:
            result["core_temperature_c"] = str(overview["core_temperature_c"])

    alarms = parsed.get("active_alarms")
    if isinstance(alarms, dict):
        alarm_items = alarms.get("alarms")
        if isinstance(alarm_items, list):
            result["diagnostics_alarms"] = "none" if not alarm_items else ", ".join(str(item) for item in alarm_items[:5])

    warnings = parsed.get("warnings")
    if isinstance(warnings, list) and warnings:
        result["diagnostics_warnings"] = ", ".join(str(item) for item in warnings[:5])
    return result


def build_nucleares_context(status: dict[str, Any]) -> str:
    if not status.get("connected"):
        error = status.get("error") or status.get("message") or "Nucleares telemetry is unavailable"
        return "\n".join(
            [
                "[NUCLEARES_GAME_CONTEXT]",
                "This is telemetry from the game Nucleares simulation, not a real-world nuclear facility.",
                "Do not claim to control the game. Do not provide real-world nuclear operation instructions.",
                "connected: false",
                f"error: {error}",
                "[/NUCLEARES_GAME_CONTEXT]",
            ]
        )

    lines = [
        "[NUCLEARES_GAME_CONTEXT]",
        "This is telemetry from the game Nucleares simulation, not a real-world nuclear facility.",
        "Answer as game-simulation telemetry only. Do not claim to control the game.",
        "Do not provide real-world nuclear operation instructions; suggest safe in-game observations only.",
        "connected: true",
    ]
    if status.get("base_url"):
        lines.append(f"base_url: {status['base_url']}")
    if status.get("parameter_count") is not None:
        lines.append(f"parameter_count: {status['parameter_count']}")

    diagnostics = _diagnostics_summary(status)
    for key in ("operation_mode", "core_temperature_c", "diagnostics_alarms", "diagnostics_warnings"):
        if diagnostics.get(key):
            lines.append(f"{key}: {diagnostics[key]}")

    telemetry_keys = [
        "alarms_active",
        "ambient_temperature",
        "core_pressure",
        "pressurizer_temperature",
        "pressurizer_pressure",
        "condenser_temperature",
        "condenser_pressure",
        "condenser_circulation_pump_active",
        "condenser_circulation_pump_speed",
    ]
    for key in telemetry_keys:
        value = _normalized_value(status, key)
        if value is None:
            continue
        display = "none" if key == "alarms_active" and value == "" else value
        lines.append(f"{key}: {display}")

    ao_summary = _ao_status_summary(status)
    if ao_summary:
        lines.append(f"ao_agent_status_summary: {ao_summary}")
    if status.get("warnings"):
        lines.append(f"bridge_warnings: {'; '.join(str(item) for item in status['warnings'][:3])}")

    lines.append("[/NUCLEARES_GAME_CONTEXT]")
    return "\n".join(lines)
