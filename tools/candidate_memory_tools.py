"""Когнитивный цикл Siena: Observation → Insight → Reflection → Candidate Memory.

ВАЖНО: только CandidateMemoryCreateTool зарегистрирован как tool модели.
agent_loop отправляет модели ПОЛНЫЙ список tools на каждой итерации
(core/agent_loop.py) — если бы promote/reject/later/delete тоже были tools,
Siena могла бы сама подтверждать свои кандидаты в long-term memory без
участия человека, что убивает смысл human-in-the-loop в этой фиче.

promote_candidate() ниже — обычная Python-функция, не Tool: её вызывает
только REST-эндпоинт /api/insights/{id}/promote (api/server.py) в ответ на
явное действие человека в интерфейсе. reject/later не нуждаются в отдельном
хелпере — это прямой store.set_status(...) в самом эндпоинте.
"""

from __future__ import annotations

from core.errors import SienaToolError
from core.message import ToolResult
from logging_.logger import SienaLogger
from memory.candidate_memory_store import CandidateMemoryStore
from memory.long_memory_store import LongMemoryStore
from tools.base import Tool

_MAX_OBSERVATION_LEN = 2000
_MAX_INSIGHT_LEN = 2000
_MAX_REFLECTION_LEN = 3000
_MAX_PROPOSED_MEMORY_LEN = 2000


class CandidateMemoryCreateTool(Tool):
    name = "candidate_memory_create"
    description = (
        "Предложить кандидата в долговременную память на основе собственного вывода, "
        "к которому ты пришла в разговоре (НЕ по явной просьбе пользователя — для явной "
        "просьбы используй long_memory_save). Это не сохраняет факт: кандидат ждёт "
        "подтверждения человеком в интерфейсе Insights."
    )
    parameters = {
        "type": "object",
        "properties": {
            "observation": {"type": "string", "description": "Что именно ты заметила"},
            "insight": {"type": "string", "description": "Что это значит"},
            "reflection": {
                "type": "string",
                "description": "Действительно ли это долгосрочно важно, или просто мысль вслух",
            },
            "proposed_memory": {
                "type": "string",
                "description": "Краткая, независимая от диалога формулировка факта для long-term memory",
            },
            "confidence": {"type": "number", "description": "Уверенность модели, 0..1"},
            "category": {"type": "string", "description": "Категория факта (например: decision, preference)"},
        },
        "required": ["observation", "insight", "reflection", "proposed_memory"],
    }

    def __init__(self, store: CandidateMemoryStore, logger: SienaLogger):
        self._store = store
        self._logger = logger

    def run(
        self,
        observation: str,
        insight: str,
        reflection: str,
        proposed_memory: str,
        confidence: float | None = None,
        category: str | None = None,
    ) -> ToolResult:
        # Валидация здесь — только формат (тип/непустота/длина/диапазон), не
        # смысл. Runtime не оценивает, "важно" ли это — это по-прежнему решает
        # модель; он лишь отказывается принять заведомо некорректный вызов
        # (см. ARCHITECTURE.md, раздел 7.2 — формат vs семантика).
        text_fields = {
            "observation": (observation, _MAX_OBSERVATION_LEN),
            "insight": (insight, _MAX_INSIGHT_LEN),
            "reflection": (reflection, _MAX_REFLECTION_LEN),
            "proposed_memory": (proposed_memory, _MAX_PROPOSED_MEMORY_LEN),
        }
        cleaned: dict[str, str] = {}
        for field_name, (value, max_len) in text_fields.items():
            if not isinstance(value, str):
                return ToolResult(ok=False, error=f"{field_name} must be a string")
            stripped = value.strip()
            if not stripped:
                return ToolResult(ok=False, error=f"{field_name} must not be empty")
            if len(stripped) > max_len:
                return ToolResult(ok=False, error=f"{field_name} exceeds max length {max_len}")
            cleaned[field_name] = stripped

        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                return ToolResult(ok=False, error="confidence must be a number")
            if not (0.0 <= confidence <= 1.0):
                return ToolResult(ok=False, error="confidence must be between 0.0 and 1.0")

        if category is not None:
            if not isinstance(category, str):
                return ToolResult(ok=False, error="category must be a string")
            category = category.strip() or None

        self._logger.event("observation_created", observation=cleaned["observation"])
        self._logger.event("insight_created", insight=cleaned["insight"])
        self._logger.event("reflection_created", reflection=cleaned["reflection"])

        entry = self._store.create(
            observation=cleaned["observation"],
            insight=cleaned["insight"],
            reflection=cleaned["reflection"],
            proposed_memory=cleaned["proposed_memory"],
            confidence=confidence,
            category=category,
        )

        self._logger.event(
            "candidate_memory_created",
            id=entry["id"],
            proposed_memory=cleaned["proposed_memory"],
            confidence=confidence,
            category=category,
            console_message=f"[MEMORY][CANDIDATE][CREATE] #{entry['id']} {cleaned['proposed_memory']!r}",
        )
        return ToolResult(ok=True, content=entry)


def promote_candidate(candidate_store: CandidateMemoryStore, long_store: LongMemoryStore, candidate_id: int) -> dict:
    """Вызывается только из REST /api/insights/{id}/promote — то есть только
    после явного нажатия "Сохранить" человеком в интерфейсе. Runtime здесь не
    решает, стоит ли доверять кандидату — это решение уже принял человек,
    кликнув кнопку; Runtime лишь переносит уже одобренный текст в long_memory.
    Логирование candidate_memory_promoted/long_memory_saved — на вызывающей
    стороне (api/server.py), т.к. у этой функции нет своего logger-контекста.
    """
    candidate = candidate_store.get(candidate_id)
    if candidate is None or candidate["status"] != "pending":
        raise SienaToolError(f"Candidate {candidate_id} not found or already resolved")

    long_entry = long_store.save(
        text=candidate["proposed_memory"],
        category=candidate["category"],
        importance=None,  # Runtime не оценивает важность — это данные, которые уже дала модель
        source=f"candidate_memory:{candidate_id}",
        metadata={"confidence": candidate["confidence"], "candidate_memory_id": candidate_id},
    )
    candidate_store.set_status(candidate_id, "promoted")
    return {"candidate_id": candidate_id, "long_memory_entry": long_entry}
