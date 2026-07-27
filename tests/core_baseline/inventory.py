from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"

_ROUTE_ADDONS = {
    "/api/external/game-reaction": "cyberpunk",
    "/api/external/cyberpunk/autonomous-turn/v1": "cyberpunk",
    "/api/game/nucleares/status": "nucleares",
}

_CORE_TOOL_SOURCES = {
    "web_search": "tools.web_search.WebSearchTool",
    "open_url": "tools.open_url.OpenUrlTool",
    "get_current_time": "tools.current_time.GetCurrentTimeTool",
    "short_memory_save": "tools.memory_tools.ShortMemorySaveTool",
    "short_memory_search": "tools.memory_tools.ShortMemorySearchTool",
    "short_memory_clear": "tools.memory_tools.ShortMemoryClearTool",
    "long_memory_save": "tools.memory_tools.LongMemorySaveTool",
    "long_memory_search": "tools.memory_tools.LongMemorySearchTool",
    "long_memory_list": "tools.memory_tools.LongMemoryListTool",
    "candidate_memory_create": "tools.candidate_memory_tools.CandidateMemoryCreateTool",
    "delegate_model": "tools.delegate_model.DelegateModelTool",
    "translate_text": "tools.translate_tool.TranslateTextTool",
}


def _route_owner(path: str) -> tuple[str, str]:
    if path in _ROUTE_ADDONS:
        return "addon", _ROUTE_ADDONS[path]
    if path.startswith("/api/remote-gateway"):
        return "addon", "remote_gateway"
    return "core", "core"


def route_inventory(app: Any) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for route in app.routes:
        path = str(route.path)
        category, owner = _route_owner(path)
        methods = sorted(route.methods) if getattr(route, "methods", None) else ["WEBSOCKET"]
        for method in methods:
            inventory.append(
                {
                    "method": method,
                    "path": path,
                    "name": getattr(route, "name", None),
                    "category": category,
                    "owner": owner,
                }
            )
    return sorted(inventory, key=lambda item: (item["path"], item["method"], item["name"] or ""))


def tool_inventory(registry: Any) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for name in sorted(registry.names()):
        if name.startswith("cyberpunk_"):
            category = "addon"
            owner = "cyberpunk"
            source = "tools.cyberpunk_siena.CyberpunkSienaBodyTool"
            required_for_core = False
        else:
            category = "core"
            owner = "core"
            source = _CORE_TOOL_SOURCES.get(name, "unknown")
            required_for_core = name != "translate_text"
        inventory.append(
            {
                "name": name,
                "source": source,
                "category": category,
                "owner": owner,
                "required_for_core": required_for_core,
                "registered_by_default": True,
            }
        )
    return inventory


def load_snapshot(name: str) -> Any:
    return json.loads((SNAPSHOT_DIR / name).read_text(encoding="utf-8"))
