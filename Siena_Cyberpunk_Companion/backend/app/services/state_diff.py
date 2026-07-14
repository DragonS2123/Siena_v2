from dataclasses import dataclass
from typing import Any

from app.models.game_state import GameState


@dataclass(frozen=True, slots=True)
class StateChange:
    path: str
    before: Any
    after: Any


def diff_states(previous: GameState | None, current: GameState) -> list[StateChange]:
    if previous is None or previous.session_id != current.session_id:
        return [StateChange("session", None, current.session_id)]

    before = previous.model_dump(mode="json")
    after = current.model_dump(mode="json")
    changes: list[StateChange] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if key in {"sequence", "captured_at"} and not path:
                    continue
                walk(left.get(key), right.get(key), f"{path}.{key}" if path else key)
        elif left != right:
            changes.append(StateChange(path, left, right))

    walk(before, after, "")
    return changes
