from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.models.game_state import GameState


BASE = {
    "schema_version": "1.0",
    "session_id": "test-session",
    "sequence": 1,
    "captured_at": "2026-07-13T20:00:00Z",
    "game": {"running": True, "loaded": True, "paused": False},
    "player": {"health": 100, "max_health": 100, "in_combat": False, "in_vehicle": False, "position": {"x": 0, "y": 0, "z": 0}},
    "companion": {"present": True, "health": 100, "max_health": 100, "distance_to_player": 4, "current_intent": "follow", "moving": True},
    "environment": {"district": "Watson", "visible_hostiles": 0, "highest_threat_id": None},
}


@pytest.fixture
def state_factory():
    def factory(sequence: int = 1, **patches) -> GameState:
        data = deepcopy(BASE)
        data["sequence"] = sequence
        data["captured_at"] = datetime.now(timezone.utc).isoformat()
        for dotted, value in patches.items():
            target = data
            parts = dotted.split("__")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = value
        return GameState.model_validate(data)
    return factory
