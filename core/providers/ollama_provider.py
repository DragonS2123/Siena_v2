"""Compatibility adapter around the unchanged legacy OllamaClient."""
from __future__ import annotations

from core.model_catalog import ModelCatalog
from core.ollama_client import OllamaClient


class OllamaProvider:
    def __init__(self, host: str, model: str, timeout: int, think: bool = False,
                 num_ctx: int | None = None, num_predict: int | None = None,
                 generation_options: dict | None = None):
        self._legacy = OllamaClient(host, model, timeout, think, num_ctx,
                                    num_predict, generation_options)
        self._host = host
        self._model = model  # Existing injected callers inspect this value.

    @property
    def num_ctx(self):
        return self._legacy.num_ctx

    @property
    def num_predict(self):
        return self._legacy.num_predict

    def diagnostics(self) -> dict:
        return {"provider": "ollama", "url": self._host, "model": self._model,
                "context_size": self.num_ctx, "output_tokens": self.num_predict,
                "managed": False, "legacy": True,
                "structured_json": False, "tool_choice_modes": ["auto"]}

    def catalog(self) -> dict:
        return ModelCatalog(self._host).refresh()

    def health(self) -> dict:
        catalog = self.catalog()
        return {"available": catalog["available"], "error": catalog["error"],
                **self.diagnostics()}

    @staticmethod
    def _check_options(response_format, tool_choice):
        # Preserve the legacy transport instead of silently ignoring new options.
        if response_format is not None or tool_choice != "auto":
            raise ValueError("legacy Ollama adapter exposes existing auto-tool chat only")

    def chat(self, messages, tools=None, model=None, *, response_format=None,
             tool_choice="auto"):
        self._check_options(response_format, tool_choice)
        return self._legacy.chat(messages, tools=tools, model=model)

    def generate(self, prompt, *, system=None, response_format=None):
        messages = ([{"role": "system", "content": system}] if system is not None else [])
        return self.chat([*messages, {"role": "user", "content": prompt}],
                         response_format=response_format)

    async def stream_chat(self, messages, tools=None, model=None, *,
                          response_format=None, tool_choice="auto"):
        self._check_options(response_format, tool_choice)
        source = self._legacy.stream_chat(messages, tools=tools, model=model)
        try:
            async for chunk in source:
                yield chunk
        finally:
            await source.aclose()
