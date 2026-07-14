import json
from datetime import datetime

from app.models.game_event import GameEvent


class GameReactionPromptBuilder:
    def build(self, event: GameEvent, recent_events: list[GameEvent], language: str, session_started_at: datetime | None = None) -> str:
        elapsed = max(0, round((event.created_at - session_started_at).total_seconds())) if session_started_at else None
        safe_data = {key: value for key, value in event.payload.items() if key in {
            "previous_health", "current_health", "max_health", "health_percent", "damage_amount",
            "total_damage", "hits", "healed_amount", "duration_seconds",
        }}
        history = [
            {"type": str(item.event_type), "severity": str(item.severity), "summary": item.summary}
            for item in recent_events[-6:] if item.event_id != event.event_id
        ]
        context = {
            "game": "Cyberpunk 2077", "observer_mode": "read_only", "language": language,
            "session_elapsed_seconds": elapsed,
            "event": {"type": str(event.event_type), "severity": str(event.severity), "summary": event.summary, "data": safe_data},
            "recent_meaningful_events": history,
        }
        instruction = (
            "Ты получаешь игровое событие из Cyberpunk 2077. Ты наблюдатель и компаньон, но не управляешь игрой. "
            "Ответь естественной короткой репликой на русском языке: одна или две короткие фразы. "
            "Не используй markdown и не описывай JSON. Не упоминай API, телеметрию, event type или backend. "
            "Не вызывай инструменты, web search или TTS. Не сохраняй событие в память. "
            "Не запускай другие модели и не повторяй дословно предыдущие реакции. "
            "Не выдавай игровые команды от имени системы и не утверждай то, чего нет во входном событии."
        )
        return f"{instruction}\n\nКонтекст события:\n{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
