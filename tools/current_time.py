"""get_current_time() — точные текущие дата/время сервера.

Это чисто техническая функция: Ollama-модель не имеет доступа к системным
часам и не может "знать" текущее время сама — Runtime лишь сообщает
объективный факт, решение о том, когда его запросить, всё равно принимает
модель (см. ARCHITECTURE.md, философия tools).
"""

from __future__ import annotations

from datetime import datetime

from core.message import ToolResult
from tools.base import Tool


class GetCurrentTimeTool(Tool):
    name = "get_current_time"
    description = (
        "Получить точную текущую дату и время сервера. Используй, когда пользователь "
        "спрашивает который час, какое сегодня число, какой день недели — не отвечай "
        "на такие вопросы без вызова этого инструмента."
    )
    parameters = {"type": "object", "properties": {}, "required": []}

    def run(self) -> ToolResult:
        now = datetime.now().astimezone()
        return ToolResult(
            ok=True,
            content={
                "iso": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "weekday": now.strftime("%A"),
                "utc_offset": now.strftime("%z"),
            },
        )
