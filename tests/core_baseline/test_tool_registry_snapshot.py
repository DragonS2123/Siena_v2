from __future__ import annotations

from .inventory import load_snapshot, tool_inventory


def test_tool_registry_matches_core_baseline_snapshot(backend):
    assert tool_inventory(backend.registry) == load_snapshot("tools.json")


def test_cyberpunk_tools_are_currently_in_the_shared_default_registry(backend):
    inventory = tool_inventory(backend.registry)
    cyberpunk = [item for item in inventory if item["owner"] == "cyberpunk"]
    assert backend.config.CYBERPUNK_SIENA_BODY_TOOLS_ENABLED is True
    assert len(cyberpunk) == 14
    assert all(item["registered_by_default"] for item in cyberpunk)
    assert all(not item["required_for_core"] for item in cyberpunk)
