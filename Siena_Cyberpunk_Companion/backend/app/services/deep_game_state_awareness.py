import math
import re
from datetime import datetime

from app.domain.priorities import EventPriority
from app.models.game_event import EventType, GameEvent
from app.models.game_state import GameState


HEALTH_LOW_PERCENT = 25.0
HEALTH_CRITICAL_PERCENT = 12.0
HEALTH_RECOVERED_PERCENT = 40.0
RAM_LOW_PERCENT = 25.0
RAM_EXHAUSTED_PERCENT = 10.0
RAM_RECOVERED_PERCENT = 60.0


class DeepGameStateAwareness:
    """Session-scoped state machines over the already-normalized v0.8.1 fields."""

    def __init__(self) -> None:
        self._active_session_by_source: dict[str, str] = {}
        self._health_state: dict[tuple[str, str], str] = {}
        self._ram_state: dict[tuple[str, str], str] = {}
        self._weapon: dict[tuple[str, str], tuple[bool, str | None]] = {}

    @staticmethod
    def _event(
        state: GameState,
        event_type: EventType,
        priority: EventPriority,
        payload: dict,
        now: datetime,
    ) -> GameEvent:
        return GameEvent(
            session_id=state.session_id,
            event_type=event_type,
            priority=priority,
            created_at=now,
            source=state.source,
            sequence=state.sequence,
            summary={
                EventType.PLAYER_HEALTH_LOW: "Player health entered the low range",
                EventType.PLAYER_HEALTH_CRITICAL: "Player health entered the critical range",
                EventType.PLAYER_HEALTH_RECOVERED: "Player health recovered from danger",
                EventType.PLAYER_RAM_LOW: "Player RAM entered the low range",
                EventType.PLAYER_RAM_EXHAUSTED: "Player RAM is nearly exhausted",
                EventType.PLAYER_RAM_RECOVERED: "Player RAM recovered",
                EventType.WEAPON_DRAWN: "Player drew a weapon",
                EventType.WEAPON_HOLSTERED: "Player holstered the active weapon",
                EventType.WEAPON_CHANGED: "Player weapon identity changed",
                EventType.STATUS_EFFECTS_INCREASED: "Applied status effect count increased",
                EventType.STATUS_EFFECTS_DECREASED: "Applied status effect count decreased",
            }[event_type],
            deduplication_key=f"{state.session_id}:{event_type.value}",
            payload=payload,
        )

    @staticmethod
    def player_ready(state: GameState) -> bool:
        deep = state.deep_game_state
        player = deep.player if deep else None
        return bool(
            state.game.loaded
            and player
            and player.entity_available is True
            and player.session_available is True
            and player.is_pre_game is False
        )

    @staticmethod
    def _resource(value: float | None, maximum: float | None) -> tuple[float, float, float] | None:
        if value is None or maximum is None:
            return None
        if not math.isfinite(value) or not math.isfinite(maximum) or value < 0 or maximum <= 0:
            return None
        return value, maximum, value / maximum * 100.0

    @classmethod
    def health_resource(cls, state: GameState) -> tuple[float, float, float] | None:
        pools = state.deep_game_state.stat_pools if state.deep_game_state else None
        return cls._resource(pools.current_health, pools.maximum_health) if pools else None

    @classmethod
    def ram_resource(cls, state: GameState) -> tuple[float, float, float] | None:
        pools = state.deep_game_state.stat_pools if state.deep_game_state else None
        return cls._resource(pools.current_memory, pools.maximum_memory) if pools else None

    @staticmethod
    def health_label(percent: float) -> str:
        if percent <= HEALTH_CRITICAL_PERCENT:
            return "critical"
        if percent <= HEALTH_LOW_PERCENT:
            return "low"
        return "normal"

    @staticmethod
    def ram_label(percent: float) -> str:
        if percent <= RAM_EXHAUSTED_PERCENT:
            return "exhausted"
        if percent <= RAM_LOW_PERCENT:
            return "low"
        return "normal"

    @staticmethod
    def stable_weapon_id(value: str | None) -> str | None:
        if not value:
            return None
        cleaned = value.strip()
        if not cleaned or "userdata:" in cleaned.casefold():
            return None
        embedded = re.search(r"--\[\[\s*(.*?)\s*--\]\]", cleaned)
        return embedded.group(1).strip() if embedded else cleaned

    def _reset_source_session(self, state: GameState) -> bool:
        previous = self._active_session_by_source.get(state.source)
        changed = previous is not None and previous != state.session_id
        if previous and changed:
            key = (state.source, previous)
            self._health_state.pop(key, None)
            self._ram_state.pop(key, None)
            self._weapon.pop(key, None)
        self._active_session_by_source[state.source] = state.session_id
        return changed

    def synthesize(self, previous: GameState | None, current: GameState, now: datetime) -> list[GameEvent]:
        new_session = self._reset_source_session(current) or previous is None or previous.session_id != current.session_id
        key = (current.source, current.session_id)
        if not self.player_ready(current):
            return []

        common = {
            "player_available": True,
            "session_available": True,
            "is_pre_game": False,
        }
        events: list[GameEvent] = []
        health = self.health_resource(current)
        if health:
            value, maximum, percent = health
            current_label = self.health_label(percent)
            previous_label = self._health_state.get(key)
            if previous_label is None and not new_session and previous and self.player_ready(previous):
                previous_health = self.health_resource(previous)
                if previous_health:
                    previous_label = self.health_label(previous_health[2])
            if new_session or previous_label is None:
                self._health_state[key] = current_label
            elif previous_label == "normal" and current_label == "low":
                self._health_state[key] = "low"
                events.append(self._event(current, EventType.PLAYER_HEALTH_LOW, EventPriority.P1_HIGH, {
                    **common, "current_health": value, "max_health": maximum,
                    "health_percent": percent, "health_state": "low",
                }, now))
            elif previous_label in {"normal", "low"} and current_label == "critical":
                self._health_state[key] = "critical"
                events.append(self._event(current, EventType.PLAYER_HEALTH_CRITICAL, EventPriority.P0_CRITICAL, {
                    **common, "current_health": value, "max_health": maximum,
                    "health_percent": percent, "health_state": "critical",
                }, now))
            elif previous_label in {"low", "critical"} and percent >= HEALTH_RECOVERED_PERCENT:
                self._health_state[key] = "normal"
                events.append(self._event(current, EventType.PLAYER_HEALTH_RECOVERED, EventPriority.P2_MEDIUM, {
                    **common, "current_health": value, "max_health": maximum,
                    "health_percent": percent, "health_state": "normal",
                }, now))

        ram = self.ram_resource(current)
        if ram:
            value, maximum, percent = ram
            current_label = self.ram_label(percent)
            previous_label = self._ram_state.get(key)
            if previous_label is None and not new_session and previous and self.player_ready(previous):
                previous_ram = self.ram_resource(previous)
                if previous_ram:
                    previous_label = self.ram_label(previous_ram[2])
            if new_session or previous_label is None:
                self._ram_state[key] = current_label
            elif previous_label == "normal" and current_label == "low":
                self._ram_state[key] = "low"
                events.append(self._event(current, EventType.PLAYER_RAM_LOW, EventPriority.P2_MEDIUM, {
                    **common, "current_ram": value, "max_ram": maximum,
                    "ram_percent": percent, "ram_state": "low",
                }, now))
            elif previous_label in {"normal", "low"} and current_label == "exhausted":
                self._ram_state[key] = "exhausted"
                events.append(self._event(current, EventType.PLAYER_RAM_EXHAUSTED, EventPriority.P1_HIGH, {
                    **common, "current_ram": value, "max_ram": maximum,
                    "ram_percent": percent, "ram_state": "exhausted",
                }, now))
            elif previous_label in {"low", "exhausted"} and percent >= RAM_RECOVERED_PERCENT:
                self._ram_state[key] = "normal"
                events.append(self._event(current, EventType.PLAYER_RAM_RECOVERED, EventPriority.P2_MEDIUM, {
                    **common, "current_ram": value, "max_ram": maximum,
                    "ram_percent": percent, "ram_state": "normal",
                }, now))

        weapon = current.deep_game_state.weapon if current.deep_game_state else None
        if weapon and weapon.drawn is not None:
            current_weapon = (weapon.drawn, self.stable_weapon_id(weapon.record_id))
            previous_weapon = self._weapon.get(key)
            if previous_weapon is None and not new_session and previous and self.player_ready(previous):
                old_weapon = previous.deep_game_state.weapon if previous.deep_game_state else None
                if old_weapon and old_weapon.drawn is not None:
                    previous_weapon = (old_weapon.drawn, self.stable_weapon_id(old_weapon.record_id))
            self._weapon[key] = current_weapon
            if not new_session and previous_weapon is not None:
                was_drawn, previous_id = previous_weapon
                is_drawn, current_id = current_weapon
                payload = {
                    **common, "previous_weapon_record_id": previous_id,
                    "active_weapon_record_id": current_id,
                    "weapon_drawn": is_drawn,
                }
                if not was_drawn and is_drawn:
                    events.append(self._event(current, EventType.WEAPON_DRAWN, EventPriority.P3_LOW, payload, now))
                elif was_drawn and not is_drawn:
                    events.append(self._event(current, EventType.WEAPON_HOLSTERED, EventPriority.P3_LOW, payload, now))
                elif previous_id and current_id and previous_id != current_id:
                    events.append(self._event(current, EventType.WEAPON_CHANGED, EventPriority.P3_LOW, payload, now))

        if not new_session and previous and previous.session_id == current.session_id:
            before_effects = previous.deep_game_state.status_effects if previous.deep_game_state else None
            after_effects = current.deep_game_state.status_effects if current.deep_game_state else None
            before_count = before_effects.observed_count if before_effects else None
            after_count = after_effects.observed_count if after_effects else None
            if before_count is not None and after_count is not None and before_count != after_count:
                event_type = EventType.STATUS_EFFECTS_INCREASED if after_count > before_count else EventType.STATUS_EFFECTS_DECREASED
                events.append(self._event(current, event_type, EventPriority.P3_LOW, {
                    **common, "previous_status_effect_count": before_count,
                    "status_effect_count": after_count,
                }, now))

        return events
