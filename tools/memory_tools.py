"""Existing memory tools with v1 provenance, explicit writes and bounded reads."""
from __future__ import annotations

import json
from core.errors import SienaToolError
from core.message import ToolResult
from memory.policy import authorize_write, origin, MAX_FACTS, RETRIEVAL_CHARS
from memory.policy import fact_key as infer_fact_key, terms
from tools.base import Tool


def _metadata(current):
    return {'conversation_id': current.conversation_id, 'message_id': current.message_id}


def _bounded(rows):
    selected = []
    for row in rows[:MAX_FACTS]:
        compact = {k: row[k] for k in ('id', 'text', 'source') if k in row}
        if len(json.dumps(selected + [compact], ensure_ascii=False)) <= RETRIEVAL_CHARS:
            selected.append(compact)
    return selected


class ShortMemorySaveTool(Tool):
    name = 'short_memory_save'
    description = 'Legacy temporary note, only on an explicit user request, only in this conversation. Recent dialog messages are already supplied; do not save reasoning, tools, web or secrets.'
    parameters = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}

    def __init__(self, store, logger): self._store, self._logger = store, logger

    def run(self, text):
        current = authorize_write(text)
        entry = self._store.save(text, conversation_id=current.conversation_id)
        self._logger.event('short_memory_saved', id=entry['id'])
        return ToolResult(True, entry)


class ShortMemorySearchTool(Tool):
    name = 'short_memory_search'
    description = 'Search legacy temporary notes from this conversation only. Recent messages are already in the short-term prompt.'
    parameters = {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}

    def __init__(self, store, logger): self._store, self._logger = store, logger

    def run(self, query):
        current = origin()
        if current is None: raise SienaToolError('current conversation is required')
        return ToolResult(True, _bounded(self._store.search(query, conversation_id=current.conversation_id)))


class ShortMemoryClearTool(Tool):
    name = 'short_memory_clear'
    description = 'Clear legacy temporary notes for this conversation, only when the user explicitly asks.'
    parameters = {'type': 'object', 'properties': {}, 'required': []}

    def __init__(self, store, logger): self._store, self._logger = store, logger

    def run(self):
        current = authorize_write()
        return ToolResult(True, {'cleared': self._store.clear(conversation_id=current.conversation_id)})


class LongMemorySaveTool(Tool):
    name = 'long_memory_save'
    description = (
        'Запомнить один устойчивый факт, предпочтение или решение только по явной просьбе '
        'пользователя в текущем сообщении. Не сохраняй секреты, reasoning, system/runtime/tool/web '
        'данные. Сохраняй формулировку близко к словам пользователя. Для обновления того же '
        'факта используй прежний fact_key или replaces_id; основной GPU заменяется автоматически. '
        'Поля provenance выставляет Siena, не модель.')
    parameters = {'type': 'object', 'properties': {
        'text': {'type': 'string', 'maxLength': 1000}, 'category': {'type': 'string'},
        'importance': {'type': 'string'},
        'fact_key': {'type': 'string', 'description': 'Stable subject key reused when changing the same fact.'},
        'replaces_id': {'type': 'integer', 'description': 'Existing memory id to replace in place.'}}, 'required': ['text']}

    def __init__(self, store, logger): self._store, self._logger = store, logger

    def run(self, text, category=None, importance=None, fact_key=None, replaces_id=None):
        current = authorize_write(text, category)
        inferred = infer_fact_key(text)
        if not inferred.startswith('text:'):
            fact_key = inferred
        if replaces_id is not None:
            previous = self._store.get(replaces_id)
            if previous and not (terms(previous['text']) & terms(text)):
                raise SienaToolError('replacement must refer to the same fact subject')
        source = 'explicit preference' if category in {'preference', 'предпочтение'} else 'user'
        entry = self._store.save(text, category, importance, source, _metadata(current), key=fact_key, replaces_id=replaces_id)
        self._logger.event('long_memory_saved', id=entry['id'], source=source,
                           conversation_id=current.conversation_id, fact_characters=len(entry['text']))
        return ToolResult(True, entry)


class LongMemorySearchTool(Tool):
    name = 'long_memory_search'
    description = 'Найти только релевантные активные долговременные факты. Лексический поиск, максимум 5 фактов / 2400 символов, с provenance.'
    parameters = {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}

    def __init__(self, store, logger): self._store, self._logger = store, logger

    def run(self, query):
        return ToolResult(True, _bounded(self._store.search(query)))


class LongMemoryListTool(Tool):
    name = 'long_memory_list'
    description = 'Показать небольшой список последних фактов, только по явной просьбе пользователя показать память; не использовать для обычных вопросов.'
    parameters = {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 5}}, 'required': []}

    def __init__(self, store, logger, default_limit=5): self._store, self._logger, self._default_limit = store, logger, default_limit

    def run(self, limit=None):
        current = origin()
        import re
        if current is None or not re.search(r'(покажи|список|перечисли|что.*(?:запомни|помни)|list|show).*', current.text, re.I):
            raise SienaToolError('listing memory requires an explicit user request')
        return ToolResult(True, _bounded(self._store.list_recent(min(limit or self._default_limit, MAX_FACTS))))


class LongMemoryDeactivateTool(Tool):
    name = 'long_memory_deactivate'
    description = 'Деактивировать устаревший факт по id только по явной просьбе пользователя забыть/удалить его. Сначала найди нужный факт через long_memory_search.'
    parameters = {'type': 'object', 'properties': {'id': {'type': 'integer'}}, 'required': ['id']}

    def __init__(self, store, logger): self._store, self._logger = store, logger

    def run(self, id):
        current = authorize_write()
        import re
        if not re.search(r'забудь|удали|forget|delete', current.text, re.I):
            raise SienaToolError('deactivation requires a forget/delete request')
        row = self._store.get(id)
        if not row or not row['active']: raise SienaToolError('active memory entry not found')
        from memory.policy import terms
        if not (terms(current.text) & terms(row['text'])):
            raise SienaToolError('requested fact does not match memory entry')
        changed = self._store.deactivate(id)
        self._logger.event('long_memory_deactivated', id=id, conversation_id=current.conversation_id)
        return ToolResult(True, {'id': id, 'active': False, 'changed': changed})
