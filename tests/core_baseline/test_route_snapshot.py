from __future__ import annotations

from .inventory import load_snapshot, route_inventory


def test_fastapi_routes_match_core_baseline_snapshot(backend):
    assert route_inventory(backend.app) == load_snapshot("routes.json")


def test_required_addon_routes_are_classified_explicitly(backend):
    routes = route_inventory(backend.app)
    by_path = {item["path"]: item for item in routes}
    assert by_path["/api/external/game-reaction"]["owner"] == "cyberpunk"
    assert by_path["/api/external/cyberpunk/autonomous-turn/v1"]["owner"] == "cyberpunk"
    assert by_path["/api/game/nucleares/status"]["owner"] == "nucleares"
    assert by_path["/api/remote-gateway/status"]["owner"] == "remote_gateway"
