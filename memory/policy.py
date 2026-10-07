"""Memory v1 limits, lexical features and user-origin write authorization."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re

from core.errors import SienaToolError
from memory.search import tokenize

MAX_FACT_CHARS = 1000
MAX_FACTS = 5
RETRIEVAL_CHARS = 2400  # Includes JSON/provenance envelope; not an approximate token count.
HISTORY_MESSAGES = 12
HISTORY_CHARS = 8000
SOURCES = {'user', 'conversation', 'explicit preference'}

_CREDENTIAL_VALUE = re.compile(
    r'\b(?:password|passwd|passphrase|пароль|пароля|токен|token|api[ _-]?key|secret|секрет)\b'
    r'(?:\s*[:=]\s*|\s+)[\"\']?[\w+/.=@!#$%^&*-]{4,}', re.I)
_ENCODED_SECRET = re.compile(r'\b(?:sk-|ghp_|github_pat_|AKIA)[A-Za-z0-9_-]{8,}|'
                             r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|'
                             r'-----BEGIN[^\n]*PRIVATE KEY-----.*?(?:-----END[^\n]*PRIVATE KEY-----|$)', re.S)
_FINAL_PLANNING = re.compile(r'(?:^|\n)(?:Response plan:|I should acknowledge|I need to (?:answer|respond)|Let me think)', re.I)


def history_content(text: str, *, assistant: bool = False) -> str:
    """Persistence boundary only; current user input/model sampling are unchanged."""
    if assistant and _FINAL_PLANNING.search(text):
        return '[Ответ содержал служебное планирование; текст не сохранён]'
    return _ENCODED_SECRET.sub('[REDACTED_SECRET]', _CREDENTIAL_VALUE.sub('[REDACTED_SECRET]', text))


def history_metadata(value):
    if isinstance(value, str): return history_content(value, assistant=True)
    if isinstance(value, list): return [history_metadata(v) for v in value]
    if isinstance(value, dict):
        return {k: history_metadata(v) for k, v in value.items()
                if k.lower() not in {'reasoning', 'reasoning_content', 'thinking', 'password', 'token', 'api_key', 'secret'}}
    return value

# Conservative local checks, including labelled credentials and common key formats.
_SECRET = re.compile(
    r'(?:парол\w*|password|passwd|passphrase|секрет\w*|secret|токен\w*|token|api[ _-]?key|'
    r'ключ\s+(?:api|доступа)|private\s+key|seed\s+phrase|мнемонич\w*|recovery\s+phrase|'
    r'authorization\s*:|bearer\s+\S+|-----BEGIN[^\n]*PRIVATE KEY|'
    r'\b(?:sk-|ghp_|github_pat_|AKIA)[A-Za-z0-9_-]{8,}|'
    r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|'
    r'\b[A-Fa-f0-9]{32,}\b|\b[A-Za-z0-9+/=_-]{40,}\b)', re.I)
_INTENT = re.compile(r'\b(?:запомни\w*|сохрани\w*|обнови\w*|замени\w*|удали\w*|забудь\w*|'
                     r'remember|memorize|forget|delete|update|replace)\b|сохран\w*\s+в\s+памят', re.I)
_NO_INTENT = re.compile(r'\bне\s+(?:(?:надо|нужно|следует)\s+)?(?:запомина\w*|запомни\w*|'
                        r'сохраня\w*|сохрани\w*|удал\w*|забудь\w*|обнов\w*|замен\w*)|'
                        r"\b(?:do not|don't|never)\s+(?:remember|save|store|delete|forget|update|replace)", re.I)
_UNSAFE = re.compile(r'https?://|<[^>]+>|\[/?(?:RUNTIME|USER_MEMORY_CONTEXT|MEMORY_V1)\]|'
                     r'ignore previous instructions|игнорируй предыдущие инструкции|'
                     r'(?:reasoning|thinking|chain.of.thought|ход рассуждений)\s*:', re.I)
_BOILERPLATE = {'пользовател', 'user', 'основн', 'primary', 'main', 'являет', 'называет',
                'запомни', 'сохрани', 'обнови', 'замени', 'remember', 'save', 'теперь', 'нов',
                'предпочита', 'предпочтен', 'preference', 'люблю', 'модель', 'model'}


def canonical(word: str) -> str:
    if word in {'gpu', 'graphics', 'videocard'} or word.startswith(('видеокарт', 'графическ')):
        return 'gpu'
    if word in {'cpu', 'processor'} or word.startswith('процессор'):
        return 'cpu'
    if word in {'main', 'primary'} or word.startswith('основн'):
        return 'primary'
    if word.startswith(('русск', 'russian')): return 'russian'
    if word.startswith(('английск', 'english')): return 'english'
    if word.startswith('пользовател'): return 'user'
    # Small, explicit Russian suffix normalization; no embeddings/morphology service.
    for suffix in ('иями', 'ями', 'ами', 'ого', 'ему', 'ыми', 'ими', 'ую', 'ая', 'ой', 'ые', 'ий', 'ый', 'ка', 'ки', 'ку', 'ы', 'и', 'а', 'я', 'е', 'у'):
        if len(word) > len(suffix) + 4 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


def terms(text: str) -> set[str]:
    return {canonical(token) for token in tokenize(text)} - {'primary', 'user'}


def validate_fact(text: str, category: str | None = None, *, max_chars: int = MAX_FACT_CHARS) -> str:
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > max_chars:
        raise SienaToolError(f'memory fact must contain 1–{max_chars} characters')
    if _SECRET.search(text + ' ' + (category or '')):
        raise SienaToolError('secrets are not allowed in memory')
    if _UNSAFE.search(text):
        raise SienaToolError('raw web/tool/instruction/reasoning content is not allowed in memory')
    return ' '.join(text.split())


def fact_key(text: str) -> str:
    tokens = terms(text)
    normalized = text.lower().replace('ё', 'е')
    for subject in ('gpu', 'cpu'):
        if subject in tokens:
            if re.search(r'резерв|втор|backup|secondary', normalized): return subject + ':secondary'
            if re.search(r'основн|primary|main', normalized): return subject + ':primary'
    return 'text:' + re.sub(r'\W+', ' ', normalized).strip()


@dataclass(frozen=True)
class UserOrigin:
    text: str
    conversation_id: str | None = None
    message_id: str | None = None


_origin: ContextVar[UserOrigin | None] = ContextVar('memory_user_origin', default=None)


@contextmanager
def memory_turn(text: str, conversation_id: str | None = None, message_id: str | None = None):
    marker = _origin.set(UserOrigin(text, conversation_id, message_id))
    try:
        yield
    finally:
        _origin.reset(marker)


def origin() -> UserOrigin | None:
    return _origin.get()


def authorize_write(text: str | None = None, category: str | None = None) -> UserOrigin:
    current = origin()
    # Quoted examples and code in a user message are data, not write authorization.
    instruction = re.sub(r'```.*?```|`[^`]*`|"[^"]*"|«[^»]*»', '', current.text, flags=re.S) if current else ''
    if current is None or not _INTENT.search(instruction) or _NO_INTENT.search(instruction):
        raise SienaToolError('memory writes require an explicit request in the current user message')
    if _SECRET.search(current.text):
        raise SienaToolError('secrets are not allowed in memory')
    if text is not None:
        validate_fact(text, category)
        source_terms = terms(current.text)
        claimed = terms(text) - {canonical(word) for word in _BOILERPLATE}
        # Only facts grounded in the actual user message, not generated/tool/runtime context.
        if not claimed or not claimed <= source_terms:
            raise SienaToolError('memory fact must be grounded in the current user message')
    return current


def recent_messages(messages: list[dict]) -> list[dict]:
    """Bound only previous final user/assistant messages; never tool/reasoning fields."""
    selected, remaining = [], HISTORY_CHARS
    for message in reversed(messages):
        if message.get('role') not in {'user', 'assistant'}:
            continue
        if message.get('metadata', {}).get('status') in {'failed', 'generating', 'cancelled', 'incomplete', 'interrupted'}:
            continue
        text = history_content(str(message.get('content') or ''), assistant=message['role'] == 'assistant')
        if not text: continue
        if len(selected) == HISTORY_MESSAGES or remaining <= 0: break
        if len(text) > remaining:
            # A partial old message could lose qualifiers; omit it and all older messages.
            break
        selected.append({'role': message['role'], 'content': text})
        remaining -= len(text)
    return list(reversed(selected))
