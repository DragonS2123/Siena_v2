"""История диалога текущего запуска. Session не принимает решений — только хранит messages[]."""

from __future__ import annotations

from html import escape
import re
from core.message import system_message, tool_message, user_message
from core.message import ToolResult


class Session:
    def __init__(self, system_prompt: str):
        self.messages: list[dict] = [system_message(system_prompt)]
        self.web_sources: list[dict] = []

    def add_user(self, content: str) -> None:
        self.messages.append(user_message(content))

    def add_assistant_raw(self, message: dict) -> None:
        """Добавляет сырое сообщение ассистента, как его вернул Ollama (включая tool_calls)."""
        self.messages.append(message)

    def add_tool_result(self, name: str, result: ToolResult, args: dict | None = None, tool_call_id: str | None = None) -> None:
        self.messages.append(tool_message(name, result, args, tool_call_id=tool_call_id))
        if result.ok and name in {'web_search', 'web_read'}:
            rows = result.content if isinstance(result.content, list) else [result.content]
            for row in rows:
                if isinstance(row, dict) and row.get('url'):
                    source = {'url': row['url'], 'title': str(row.get('title') or '')[:500], 'tool': name}
                    if source in self.web_sources:
                        continue
                    if len(self.web_sources) >= 15 and name == 'web_read':
                        victim = next((s for s in self.web_sources if s['tool'] == 'web_search'), None)
                        if victim is not None:
                            self.web_sources.remove(victim)
                    if len(self.web_sources) < 15:
                        self.web_sources.append(source)

    def get_messages(self) -> list[dict]:
        return self.messages

    def citation_suffix(self, content: str) -> str:
        """Format references only; no model call, relevance decision or fetch."""
        read = [source for source in self.web_sources if source['tool'] == 'web_read']
        sources = read or self.web_sources
        linked = set(re.findall(r'\[(?:\\.|[^\]\\\n])+\]\((https?://[^\s)]+)\)', content))
        if not read and any(str(s['url']).replace('(', '%28').replace(')', '%29') in linked for s in sources):
            return '\n\nПримечание: использованы поисковые сниппеты; страницы не прочитаны.'
        rows, seen = [], set()
        for source in sources:
            url = str(source['url']).replace('(', '%28').replace(')', '%29')
            if url in linked or url in seen:
                continue
            seen.add(url)
            title = escape(' '.join(str(source.get('title') or source['url']).split()), quote=False)
            title = title.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
            rows.append(f'- [{title}]({url})')
            if not read and len(rows) == 5:
                break
        if not rows:
            return ''
        heading = 'Источники' if read else 'Поисковые источники (страницы не прочитаны)'
        return '\n\n' + heading + ':\n' + '\n'.join(rows)

    def get_context_messages(self, max_messages: int) -> list[dict]:
        """Технический срез для отправки модели: system prompt (всегда) + последние
        `max_messages` сообщений истории. Полная история в `self.messages` НЕ
        изменяется и не укорачивается — это только то, что физически уезжает в
        Ollama на этот вызов.

        Никакой смысловой фильтрации или суммаризации здесь нет — чистая обрезка
        по позиции (см. DIAGNOSIS_CONTEXT_OVERFLOW.md, раздел 10: Runtime не решает,
        что важно, он лишь ограничивает технически допустимый объём).
        """
        if not self.messages:
            return []
        system = self.messages[0]
        rest = self.messages[1:]
        tail = rest[-max_messages:] if max_messages > 0 else []
        return [system] + tail
