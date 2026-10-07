"""Select an inference transport from one immutable runtime settings snapshot."""
from __future__ import annotations

from datetime import datetime

import config
from core.errors import SienaInfraError
from core.model_provider import ModelProvider
from core.providers.llama_cpp_provider import LlamaCppConfig, LlamaCppProvider


def create_provider(snapshot, model: str | None = None, timeout: int | None = None,
                    think: bool = False, num_ctx: int | None = None,
                    num_predict: int | None = None, generation_options: dict | None = None) -> ModelProvider:
    kind = snapshot.get("inference_provider")
    context = num_ctx if num_ctx is not None else int(snapshot.get("context_size"))
    output = num_predict if num_predict is not None else int(snapshot.get("chat_output_tokens"))
    timeout = timeout if timeout is not None else int(snapshot.get("request_timeout_seconds"))
    if kind == "ollama":
        # Import the SDK only when legacy inference is explicitly selected.
        from core.providers.ollama_provider import OllamaProvider
        return OllamaProvider(str(snapshot.get("ollama_host")), model or snapshot.get("model_roles")["chat"],
                              timeout, think, context, output, generation_options)
    if kind != "llama_cpp":
        raise ValueError(f"unknown inference provider: {kind}")
    profile = str(snapshot.get("llama_cpp_profile"))
    expected = config.LLAMA_CPP_PROFILES[profile]
    if context != expected:
        raise ValueError("context_size must match the selected llama.cpp profile")
    return LlamaCppProvider(LlamaCppConfig(
        url=str(snapshot.get("llama_cpp_url")), model=model or str(snapshot.get("llama_cpp_model")),
        profile=profile, context_size=context, output_tokens=output, timeout=timeout,
        connect_timeout=config.INFERENCE_CONNECT_TIMEOUT_SECONDS,
        stream_idle_timeout=config.INFERENCE_STREAM_IDLE_TIMEOUT_SECONDS,
        generation_options=dict(generation_options or {}), reasoning=config.LLAMA_CPP_REASONING,
        reasoning_budget_tokens=int(snapshot.get("llama_cpp_reasoning_budget_tokens")),
        model_path=str(snapshot.get("llama_cpp_model_path")),
        managed=bool(snapshot.get("llama_cpp_managed")),
    ))


class ProviderCatalog:
    """Keep the existing catalog payload while querying the active provider."""
    def __init__(self, settings, manager=None):
        self._settings = settings
        self._manager = manager

    def refresh(self):
        provider = create_provider(self._settings.current())
        if self._manager is not None and self._manager.active:
            health = self._manager.health()
            if not health["available"]:
                return {"available": False, "models": [], "error": health["error"],
                        "provider": "llama_cpp", "diagnostics": self._manager.diagnostics(),
                        "refreshed_at": datetime.now().astimezone().isoformat()}
        try:
            result = provider.catalog()
        except SienaInfraError as exc:
            result = {"available": False, "models": [], "error": str(exc)}
        return {**result, "provider": provider.diagnostics()["provider"],
                "diagnostics": {**provider.diagnostics(), **(self._manager.diagnostics() if self._manager is not None and self._manager.active else {})},
                "refreshed_at": datetime.now().astimezone().isoformat()}
