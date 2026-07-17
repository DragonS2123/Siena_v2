import json
from datetime import datetime

from app.models.game_event import GameEvent
from app.models.reaction import SienaReaction
from app.models.scene import ReactionFocus, SceneContext


class GameReactionPromptBuilder:
    def build(
        self,
        event: GameEvent,
        recent_events: list[GameEvent],
        language: str,
        session_started_at: datetime | None = None,
        recent_reactions: list[SienaReaction] | None = None,
        player_name: str = "",
        scene: SceneContext | None = None,
        focus: ReactionFocus | None = None,
        tactical_context: dict | None = None,
        related_event_types: list[str] | None = None,
    ) -> str:
        elapsed = max(0, round((event.created_at - session_started_at).total_seconds())) if session_started_at else None
        safe_data = {key: value for key, value in event.payload.items() if key in {
            "previous_health", "current_health", "max_health", "health_percent", "damage_amount",
            "total_damage", "hits", "healed_amount", "duration_seconds",
            "current_ram", "max_ram", "ram_percent", "ram_state",
            "active_weapon_record_id", "previous_weapon_record_id", "weapon_drawn",
            "status_effect_count", "previous_status_effect_count",
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
            "recent_siena_reactions": [
                {
                    "event_type": str(item.event_type),
                    **({"focus": str(item.focus)} if item.focus else {}),
                    "text": item.text,
                }
                for item in (recent_reactions or [])
                if item.session_id == event.session_id and (not scene or not item.scene_id or item.scene_id == scene.scene_id)
            ],
        }
        if scene:
            context["current_scene"] = {
                "phase": str(scene.phase),
                "previous_phase": str(scene.previous_phase) if scene.previous_phase else None,
                "severity": str(scene.severity),
                "peak_severity": str(scene.peak_severity),
                "health_current": scene.health_current,
                "health_max": scene.health_max,
                "health_maximum": scene.health_maximum,
                "health_percent": scene.health_percent,
                "health_state": scene.health_state,
                "health_trend": str(scene.health_trend),
                "ram_current": scene.ram_current,
                "ram_maximum": scene.ram_maximum,
                "ram_percent": scene.ram_percent,
                "ram_state": scene.ram_state,
                "active_weapon_record_id": scene.active_weapon_record_id,
                "weapon_drawn": scene.weapon_drawn,
                "status_effect_count": scene.status_effect_count,
                "level": scene.level,
                "street_cred": scene.street_cred,
                "armor": scene.armor,
                "build_awareness": {
                    "observed_tendency": scene.build_style,
                    "confidence": scene.build_confidence,
                    "dominant_quickhack_category": scene.quickhack_dominant_category,
                    "known_programs": scene.quickhack_known_count,
                    "unknown_programs": scene.quickhack_unknown_count,
                    "evidence": scene.build_evidence[:4],
                    "limitations": scene.build_limitations[:6],
                } if scene.build_style else None,
                "total_damage": scene.total_damage,
                "total_healing": scene.total_healing,
                "damage_hits": scene.damage_hits,
                "combat_state": scene.combat_state,
                "vehicle_state": scene.vehicle_state,
                "notable_facts": scene.notable_facts,
                "summary": scene.summary,
            }
            context["reaction_focus"] = str(focus) if focus else None
        if tactical_context:
            context["tactical_context"] = tactical_context
            context["related_events"] = (related_event_types or [])[:8]
        clean_player_name = player_name.strip()
        if clean_player_name:
            context["player_name"] = clean_player_name
        instruction = (
            "Ты — Сиена, игровой компаньон и наблюдатель. Игрок не является Сиеной. "
            "Никогда не называй игрока именем «Сиена» и не начинай реплику со своего имени. Обращайся к игроку на «ты». "
            "Не представляйся в каждой реплике и не говори «пользователь», «игрок», «персонаж» или «субъект». "
            "Не упоминай event type, телеметрию, backend, API или JSON. Не утверждай, что управляешь игрой, "
            "физически находишься внутри неё или управляешь персонажем. Ты только наблюдаешь и реагируешь. "
            "Не выдумывай предметы, врагов, район или состояние, которых нет во входных данных. "
            "Ответь естественной короткой репликой на русском языке: одна или две короткие фразы, обычно не более 240 символов. "
            "Без Markdown, списков, сценических ремарок и эмодзи. Не давай длинную тактическую инструкцию. "
            "При critical событии говори ясно и срочно, но без истерики; при medium/low — спокойно и кратко. "
            "При session_started достаточно кратко сообщить, что наблюдение началось. "
            "Не вызывай инструменты, web search или TTS. Не сохраняй событие в память. "
            "Не запускай другие модели и не повторяй дословно недавние реакции или одну и ту же рекомендацию. "
            "Реагируй на развитие текущей сцены, а не перечисляй события и технические поля. "
            "При завершении сцены не повторяй предупреждение, которое уже потеряло актуальность. "
            "Профиль сборки описывай только как наблюдаемую тенденцию, а не достоверный архетип. "
            "Не выдумывай атрибуты, перки, эффекты или стоимость программ; используй не более одного уместного факта профиля. "
            "Основным считай только event; related_events используй как краткий контекст одной ситуации. "
            "Не утверждай ничего о невидимых врагах, целях или точной причине урона. "
            "Решение говорить уже принято companion policy, поэтому всегда верни одну короткую реплику."
        )
        if clean_player_name:
            instruction += (
                f" Имя игрока — {clean_player_name}. Допускается редкое естественное обращение по имени, "
                "но не начинай каждую реплику с имени и не смешивай имя игрока с именем Сиены."
            )
        return f"{instruction}\n\nКонтекст события:\n{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
