"""Keyword + fuzzy ranking для поиска по памяти. Store не решает, что "релевантно" —
он только считает совпадения токенов и сортирует по счёту (это техническая
операция, не смысловое решение).

Это сознательная точка расширения: когда понадобится embedding/vector search,
меняется только tokenize()/rank() здесь — сигнатура search() в
long_memory_store.py/short_memory_store.py и tool-контракт long_memory_search/
short_memory_search остаются прежними (см. ARCHITECTURE.md, раздел 9).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_FUZZY_THRESHOLD = 0.82

_STOPWORDS_RU = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то",
    "все", "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за",
    "бы", "по", "только", "ее", "её", "мне", "было", "вот", "от", "меня",
    "еще", "ещё", "нет", "о", "из", "ему", "теперь", "когда", "даже", "ну",
    "вдруг", "ли", "если", "уже", "или", "ни", "быть", "был", "него", "до",
    "вас", "нибудь", "опять", "уж", "вам", "ведь", "там", "потом", "себя",
    "ничего", "ей", "может", "они", "тут", "где", "есть", "надо", "ней",
    "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без", "будто",
    "чего", "раз", "тоже", "себе", "под", "будет", "ж", "тогда", "кто",
    "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь",
    "этом", "один", "почти", "мой", "тем", "чтобы", "нее", "неё", "кажется",
    "сейчас", "были", "куда", "зачем", "всех", "никогда", "можно", "при",
    "наконец", "два", "об", "другой", "хоть", "после", "над", "больше",
    "тот", "через", "эти", "нас", "про", "всего", "них", "какая", "много",
    "разве", "три", "эту", "моя", "впрочем", "хорошо", "свою", "этой",
    "перед", "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им",
    "более", "всегда", "конечно", "всю", "между", "ты", "твой", "твоя",
    "твое", "твоё",
}

_STOPWORDS_EN = {
    "a", "an", "the", "is", "are", "was", "were", "to", "of", "and", "in",
    "on", "for", "that", "this", "it", "i", "you", "my", "your", "what",
    "who", "does", "do", "did", "with", "as", "at", "by", "be", "or", "if",
    "me", "we", "us",
}

STOPWORDS = _STOPWORDS_RU | _STOPWORDS_EN


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokenize(text: str) -> list[str]:
    tokens = _TOKEN_RE.findall(_normalize(text))
    return [t for t in tokens if t not in STOPWORDS and len(t) > 1]


def _tokens_match(a: str, b: str) -> bool:
    if a in b or b in a:
        return True
    return SequenceMatcher(None, a, b).ratio() >= _FUZZY_THRESHOLD


def rank(query: str, rows: list[dict], fields: list[str], limit: int) -> list[dict]:
    """Токенизирует query, убирает стоп-слова, считает для каждой строки,
    сколько ключевых слов запроса нашли совпадение (точное подстрочное в obe
    стороны или похожее по SequenceMatcher) среди токенов полей fields.
    Возвращает top-N строк по убыванию счёта (при равенстве — по recency,
    предполагая, что rows уже отсортированы по created_at DESC при равном score).
    """
    keywords = tokenize(query)
    if not keywords:
        return rows[:limit]

    scored: list[tuple[int, dict]] = []
    for row in rows:
        haystack_tokens = tokenize(" ".join(str(row.get(f) or "") for f in fields))
        score = sum(1 for qt in keywords if any(_tokens_match(qt, ht) for ht in haystack_tokens))
        if score:
            scored.append((score, row))

    scored.sort(key=lambda pair: (pair[0], pair[1].get("created_at", "")), reverse=True)
    return [row for _, row in scored[:limit]]
