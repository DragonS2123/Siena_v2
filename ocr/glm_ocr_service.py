"""OCR вЂ” РёР·РІР»РµС‡РµРЅРёРµ С‚РµРєСЃС‚Р° СЃ РёР·РѕР±СЂР°Р¶РµРЅРёР№ С‡РµСЂРµР· Ollama-РјРѕРґРµР»СЊ glm-ocr.

РўРµС…РЅРёС‡РµСЃРєРёР№ СЃРµСЂРІРёСЃ: РєР°Рє STT/TTS (СЃРј. voice/), РѕРЅ РўРћР›Р¬РљРћ РїСЂРµРІСЂР°С‰Р°РµС‚ РїРёРєСЃРµР»Рё
РІ С‚РµРєСЃС‚ вЂ” РЅРµ СЂРµС€Р°РµС‚, С‡С‚Рѕ РґРµР»Р°С‚СЊ СЃ СЌС‚РёРј С‚РµРєСЃС‚РѕРј РґР°Р»СЊС€Рµ. РР·РІР»РµС‡С‘РЅРЅС‹Р№ С‚РµРєСЃС‚
СѓС…РѕРґРёС‚ РІ /api/chat РєР°Рє РґРѕРїРѕР»РЅРёС‚РµР»СЊРЅС‹Р№ РєРѕРЅС‚РµРєСЃС‚ СЂРѕРІРЅРѕ С‚Р°Рє Р¶Рµ, РєР°Рє СЃРѕРґРµСЂР¶РёРјРѕРµ
Р»СЋР±РѕРіРѕ РґСЂСѓРіРѕРіРѕ С‚РµРєСЃС‚РѕРІРѕРіРѕ attachment (СЃРј. api/server.py, _build_attachment_context
Рё _run_image_ocr).

РњРѕРґРµР»СЊ РЅРµ РїСЂРѕРІРµСЂСЏРµС‚СЃСЏ РїСЂРё РёРјРїРѕСЂС‚Рµ/РєРѕРЅСЃС‚СЂСѓРёСЂРѕРІР°РЅРёРё вЂ” is_available() РґРµС€С‘РІРѕ
СЃРјРѕС‚СЂРёС‚, С‡РёСЃР»РёС‚СЃСЏ Р»Рё РѕРЅР° РІ Ollama (Р°РЅР°Р»РѕРі _ollama_status() РІ api/server.py),
Р±РµР· СЂРµР°Р»СЊРЅРѕРіРѕ OCR-РІС‹Р·РѕРІР°.
"""

from __future__ import annotations

import re
import time
from typing import Any, Protocol

import ollama
import requests


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class OcrUnavailableError(Exception):
    """The OCR call itself failed (Ollama reachable, model installed, but the
    inference call errored вЂ” timeout, bad response, etc.)."""


class OcrModelNotInstalledError(OcrUnavailableError):
    """glm-ocr is not present in Ollama's model list. A distinct subclass so
    api/server.py can surface a specific "glm-ocr not installed" UI status
    instead of a generic OCR failure."""


_RAMBLING_LINE_PATTERNS = (
    re.compile(r"^\s*(here is|here's|the extracted text is|extracted text:)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(i can(?:not|'t)|i'm sorry|sorry,|as an ai)\b", re.IGNORECASE),
    re.compile(r"^\s*(the image shows|this image shows|this image contains|there is no readable text)\b", re.IGNORECASE),
)


def clean_ocr_text(text: str) -> str:
    """Normalize glm-ocr output before it reaches chat context."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r"\n[ \t]+", "\n", normalized)
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)

    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if any(pattern.search(line) for pattern in _RAMBLING_LINE_PATTERNS):
            continue
        if line:
            key = line.casefold()
            if key in seen:
                continue
            seen.add(key)
        lines.append(line)

    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{4,}", " ", cleaned)
    return cleaned.strip()


def ocr_quality(text: str, cleaned_text: str, min_useful_chars: int) -> dict[str, Any]:
    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    raw_line_count = len(raw_lines) if raw_lines else 0
    raw_blank_count = sum(1 for line in raw_lines if not line.strip())
    raw_blank_ratio = (raw_blank_count / raw_line_count) if raw_line_count else 1.0

    non_empty = [line.strip().casefold() for line in raw_lines if line.strip()]
    repeat_ratio = 0.0
    if non_empty:
        repeat_ratio = 1.0 - (len(set(non_empty)) / len(non_empty))

    useful_chars = sum(1 for ch in (cleaned_text or "") if ch.isalnum())
    normalized = (cleaned_text or "").strip()
    low_quality = (
        not normalized
        or normalized.casefold() == "empty"
        or useful_chars < min_useful_chars
        or raw_blank_ratio > 0.75
        or repeat_ratio > 0.55
    )
    return {
        "quality": "low_quality" if low_quality else "ok",
        "useful_chars": useful_chars,
        "raw_blank_ratio": round(raw_blank_ratio, 3),
        "repeat_ratio": round(repeat_ratio, 3),
    }


class GlmOcrService:
    def __init__(self, host: str, model: str, timeout: int, logger: _LoggerLike | None = None):
        self._host = host
        self._model = model
        self._timeout = timeout
        self._logger = logger
        self._client = ollama.Client(host=host, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        """Р”РµС€С‘РІР°СЏ РїСЂРѕРІРµСЂРєР°: С‡РёСЃР»РёС‚СЃСЏ Р»Рё self._model СЃСЂРµРґРё РјРѕРґРµР»РµР№, РєРѕС‚РѕСЂС‹Рµ
        Ollama СЂРµР°Р»СЊРЅРѕ РІРёРґРёС‚ С‡РµСЂРµР· /api/tags (С‚РѕС‚ Р¶Рµ РїРѕРґС…РѕРґ, С‡С‚Рѕ
        _ollama_status() РІ api/server.py) вЂ” РЅРµ Р·Р°РїСѓСЃРєР°РµС‚ СЂРµР°Р»СЊРЅС‹Р№ OCR СЂР°РґРё
        РїСЂРѕРІРµСЂРєРё СЃС‚Р°С‚СѓСЃР°."""
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=2)
            response.raise_for_status()
            names = {m.get("name") for m in response.json().get("models", [])}
        except Exception:
            return False
        return any(n == self._model or (n or "").startswith(f"{self._model}:") for n in names)

    def extract_text(self, image_base64: str) -> dict[str, Any]:
        """РџСЂРѕРіРѕРЅСЏРµС‚ РѕРґРЅСѓ base64-РєР°СЂС‚РёРЅРєСѓ (Р±РµР· data:...;base64, РїСЂРµС„РёРєСЃР°)
        С‡РµСЂРµР· glm-ocr. Р’РѕР·РІСЂР°С‰Р°РµС‚ {"text": str, "elapsed_sec": float}.

        РџРѕРґРЅРёРјР°РµС‚ OcrModelNotInstalledError, РµСЃР»Рё РјРѕРґРµР»СЊ РЅРµ С‡РёСЃР»РёС‚СЃСЏ РІ Ollama,
        РёР»Рё OcrUnavailableError, РµСЃР»Рё СЃР°Рј РІС‹Р·РѕРІ СѓРїР°Р» вЂ” С‚РѕС‚ Р¶Рµ С‚РёРї СЃР±РѕСЏ, С‡С‚Рѕ
        STTUnavailableError/TTSUnavailableError (РёРЅС„СЂР°СЃС‚СЂСѓРєС‚СѓСЂРЅС‹Р№, РЅРµ
        СЃРјС‹СЃР»РѕРІРѕР№): РІС‹Р·С‹РІР°СЋС‰Р°СЏ СЃС‚РѕСЂРѕРЅР° (api/server.py) РѕР±СЏР·Р°РЅР° РїРѕР№РјР°С‚СЊ СЌС‚Рѕ Рё
        РќР• СЂРѕРЅСЏС‚СЊ chat, Р° РґРѕР±Р°РІРёС‚СЊ РїСЂРµРґСѓРїСЂРµР¶РґРµРЅРёРµ РІ РєРѕРЅС‚РµРєСЃС‚ (СЃРј. Р·Р°РґР°С‡Сѓ
        Phase 4B)."""
        if not self.is_available():
            raise OcrModelNotInstalledError(
                f"OCR-РјРѕРґРµР»СЊ {self._model!r} РЅРµ РЅР°Р№РґРµРЅР° РІ Ollama ({self._host}). "
                f"Р’С‹РїРѕР»РЅРёС‚Рµ: ollama pull {self._model}"
            )

        start = time.monotonic()
        try:
            response = self._client.chat(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract only visible text from the image. "
                        "Do not describe the image. "
                        "Do not repeat. "
                        "Do not invent text. "
                        "If no readable text is visible, return EMPTY. "
                        "Return only the extracted text."
                    ),
                    "images": [image_base64],
                }],
            )
        except Exception as exc:
            raise OcrUnavailableError(f"glm-ocr РІС‹Р·РѕРІ РЅРµ СѓРґР°Р»СЃСЏ: {exc}") from exc

        elapsed_sec = round(time.monotonic() - start, 3)
        result = response.model_dump(exclude_none=True)
        text = (result.get("message") or {}).get("content", "") or ""
        return {"text": text.strip(), "elapsed_sec": elapsed_sec}
