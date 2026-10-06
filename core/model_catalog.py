"""Live catalog of models reported by the local Ollama daemon."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Callable
from typing import Any

import requests


class ModelCatalog:
    def __init__(self, host: str | Callable[[], str], timeout: float = 3.0, session: requests.Session | None = None):
        self._host = host
        self._timeout = timeout
        self._session = session or requests.Session()

    @property
    def host(self) -> str:
        value = self._host() if callable(self._host) else self._host
        return value.rstrip("/")

    def refresh(self) -> dict[str, Any]:
        host = self.host
        try:
            tags = self._session.get(f"{host}/api/tags", timeout=self._timeout)
            tags.raise_for_status()
            tag_models = tags.json().get("models", [])
            try:
                loaded_response = self._session.get(f"{host}/api/ps", timeout=self._timeout)
                loaded_response.raise_for_status()
                loaded = {
                    item.get("name") or item.get("model")
                    for item in loaded_response.json().get("models", [])
                }
            except Exception:
                loaded = set()
        except Exception as exc:
            return {
                "available": False,
                "models": [],
                "error": str(exc),
                "refreshed_at": datetime.now().astimezone().isoformat(),
            }

        models = [self._normalize(item, loaded) for item in tag_models if isinstance(item, dict)]
        models.sort(key=lambda item: item["name"].casefold())
        return {
            "available": True,
            "models": models,
            "error": None,
            "refreshed_at": datetime.now().astimezone().isoformat(),
        }

    @staticmethod
    def _normalize(raw: dict[str, Any], loaded: set[str | None]) -> dict[str, Any]:
        name = str(raw.get("name") or raw.get("model") or "")
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        tag = name.rsplit(":", 1)[1] if ":" in name else "latest"
        capabilities = raw.get("capabilities")
        return {
            "name": name,
            "tag": tag,
            "family": details.get("family") or details.get("families", [None])[0],
            "parameter_size": details.get("parameter_size"),
            "quantization": details.get("quantization_level"),
            "size": raw.get("size"),
            "modified_at": raw.get("modified_at"),
            "digest": raw.get("digest"),
            "capabilities": capabilities if isinstance(capabilities, list) else [],
            "loaded": name in loaded,
        }


