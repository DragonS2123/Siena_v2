"""Vision — image scene/object understanding via Ollama qwen2.5vl.

Same technical-service role as ocr/glm_ocr_service.py and voice/ — this only
turns pixels into a visual description, it does not decide when to call it
or what the model does with the result afterward (see core/image_intent.py
for the OCR-vs-vision intent split and api/server.py::_run_image_vision for
the call site).

OCR and vision are deliberately separate services and separate models: OCR
(glm-ocr) reads TEXT, vision (qwen2.5vl) describes SCENE/OBJECTS. Neither is
a substitute for the other, and this service never touches OCR's code path.
"""

from __future__ import annotations

import time
from typing import Any, Protocol

import ollama
import requests


class _LoggerLike(Protocol):
    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None: ...
    def error(self, event_type: str, console_message: str, **fields: Any) -> None: ...


class VisionUnavailableError(Exception):
    """The vision call itself failed (Ollama reachable, model installed, but
    the inference call errored — timeout, bad response, etc.)."""


class VisionModelNotInstalledError(VisionUnavailableError):
    """config.IMAGE_UNDERSTANDING_MODEL is not present in Ollama's model
    list. A distinct subclass so api/server.py can surface a specific "model
    not installed" status instead of a generic vision failure."""


_DEFAULT_VISION_PROMPT = (
    "Describe what is visually shown in this image: the main subject, scene, "
    "setting, colors, style, and any other notable visible details. This is "
    "a scene/object description task, not text reading — do not just "
    "transcribe text you happen to see, describe what the image actually "
    "shows."
)


class QwenVisionService:
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
        """Cheap check: is self._model listed in what Ollama's /api/tags
        reports (same approach as GlmOcrService.is_available()) — does not
        run a real inference call just to check status."""
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=2)
            response.raise_for_status()
            names = {m.get("name") for m in response.json().get("models", [])}
        except Exception:
            return False
        return any(n == self._model or (n or "").startswith(f"{self._model}:") for n in names)

    def describe_image(self, image_base64: str, user_prompt: str = "") -> dict[str, Any]:
        """Runs one base64 image (no data:...;base64, prefix) through the
        vision model, optionally steered by the user's own question. Returns
        {"text": str, "elapsed_sec": float}.

        Raises VisionModelNotInstalledError if the model isn't in Ollama, or
        VisionUnavailableError if the call itself fails — same split as
        OcrModelNotInstalledError/OcrUnavailableError: infrastructure
        failures, not semantic ones. The caller (api/server.py) must catch
        these and surface an honest "vision unavailable/failed" note instead
        of crashing the chat turn."""
        if not self.is_available():
            raise VisionModelNotInstalledError(
                f"Vision model {self._model!r} not found in Ollama ({self._host}). "
                f"Run: ollama pull {self._model}"
            )

        prompt = _DEFAULT_VISION_PROMPT
        if user_prompt.strip():
            prompt = f"{_DEFAULT_VISION_PROMPT}\n\nUser's own question about the image: {user_prompt.strip()}"

        start = time.monotonic()
        try:
            response = self._client.chat(
                model=self._model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_base64],
                }],
                options={"temperature": 0.2},
            )
        except Exception as exc:
            raise VisionUnavailableError(f"qwen2.5vl call failed: {exc}") from exc

        elapsed_sec = round(time.monotonic() - start, 3)
        result = response.model_dump(exclude_none=True)
        text = (result.get("message") or {}).get("content", "") or ""
        return {"text": text.strip(), "elapsed_sec": elapsed_sec}
