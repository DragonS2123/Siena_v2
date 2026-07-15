import logging

from app.models.game_event import EventType, GameEvent
from app.models.scene import NormalizedSceneEvent

logger = logging.getLogger("siena_observer.scene")


SEMANTIC_ALIASES: dict[EventType, EventType] = {
    EventType.PLAYER_HEALTH_BELOW_50: EventType.HEALTH_LOW,
    EventType.PLAYER_HEALTH_CRITICAL: EventType.HEALTH_CRITICAL,
    EventType.PLAYER_RECOVERED: EventType.PLAYER_HEALED,
    EventType.PLAYER_ENTERED_VEHICLE: EventType.VEHICLE_ENTERED,
    EventType.PLAYER_EXITED_VEHICLE: EventType.VEHICLE_EXITED,
}

DIAGNOSTIC_ONLY = {
    EventType.GAME_STARTED,
    EventType.GAME_STOPPED,
    EventType.GAME_LOADED,
    EventType.GAME_PAUSED,
    EventType.GAME_RESUMED,
    EventType.ENEMY_COUNT_CHANGED,
    EventType.COMPANION_INTENT_CHANGED,
}


class SceneEventNormalizer:
    def normalize(self, event: GameEvent) -> NormalizedSceneEvent:
        try:
            event_type = EventType(event.event_type)
        except ValueError:
            return NormalizedSceneEvent(event=event, semantic_type=str(event.event_type), diagnostic_only=True)
        semantic = SEMANTIC_ALIASES.get(event_type, event_type)
        normalized = NormalizedSceneEvent(
            event=event,
            semantic_type=semantic.value,
            legacy_alias=event_type in SEMANTIC_ALIASES,
            diagnostic_only=event_type in DIAGNOSTIC_ONLY,
        )
        logger.debug(
            "scene_event_normalized event_id=%s event_type=%s semantic_type=%s legacy_alias=%s",
            event.event_id,
            event_type,
            semantic,
            normalized.legacy_alias,
        )
        return normalized
