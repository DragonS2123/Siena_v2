"""Local text extraction through the model assigned to the OCR role."""

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
    """The OCR inference call failed."""


class OcrModelNotInstalledError(OcrUnavailableError):
    """The assigned OCR model is not present in Ollama."""


_RAMBLING_LINE_PATTERNS = (
    re.compile(r"^\s*(here is|here's|the extracted text is|extracted text:)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(i can(?:not|'t)|i'm sorry|sorry,|as an ai)\b", re.IGNORECASE),
    re.compile(r"^\s*(the image shows|this image shows|this image contains|there is no readable text)\b", re.IGNORECASE),
)


def clean_ocr_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t\f\v]+", " ", normalized)
    normalized = re.sub(r"\n[ \t]+|[ \t]+\n", "\n", normalized)
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
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines).strip())


def ocr_quality(text: str, cleaned_text: str, min_useful_chars: int) -> dict[str, Any]:
    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blank_ratio = sum(1 for line in raw_lines if not line.strip()) / max(len(raw_lines), 1)
    non_empty = [line.strip().casefold() for line in raw_lines if line.strip()]
    repeat_ratio = 1.0 - len(set(non_empty)) / len(non_empty) if non_empty else 0.0
    useful_chars = sum(character.isalnum() for character in cleaned_text)
    low_quality = (
        not cleaned_text.strip()
        or cleaned_text.strip().casefold() == "empty"
        or useful_chars < min_useful_chars
        or blank_ratio > 0.75
        or repeat_ratio > 0.55
    )
    return {
        "quality": "low_quality" if low_quality else "ok",
        "useful_chars": useful_chars,
        "raw_blank_ratio": round(blank_ratio, 3),
        "repeat_ratio": round(repeat_ratio, 3),
    }


class GlmOcrService:
    def __init__(self, host: str, model: str, timeout: int, logger: _LoggerLike | None = None):
        self._host = host
        self._model = model
        self._logger = logger
        self._client = ollama.Client(host=host, timeout=timeout)

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=2)
            response.raise_for_status()
            names = {model.get("name") for model in response.json().get("models", [])}
        except Exception:
            return False
        return any(name == self._model or (name or "").startswith(f"{self._model}:") for name in names)

    def extract_text(self, image_base64: str) -> dict[str, Any]:
        if not self.is_available():
            raise OcrModelNotInstalledError(f"OCR model {self._model!r} is not installed in Ollama")
        started = time.monotonic()
        try:
            response = self._client.chat(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract only visible text from the image. Do not describe, repeat, "
                        "or invent text. Return EMPTY if no readable text is visible."
                    ),
                    "images": [image_base64],
                }],
            )
        except Exception as exc:
            raise OcrUnavailableError(f"OCR inference failed: {exc}") from exc
        result = response.model_dump(exclude_none=True)
        text = (result.get("message") or {}).get("content", "") or ""
        return {"text": text.strip(), "elapsed_sec": round(time.monotonic() - started, 3)}
