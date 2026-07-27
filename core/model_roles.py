"""Validated, atomic model-role assignments."""

from __future__ import annotations

from typing import Any

import config
from storage.settings_store import SettingsStore


class ModelRoleError(ValueError):
    pass


class ModelRoles:
    def __init__(self, settings: SettingsStore):
        self._settings = settings

    def assignments(self) -> dict[str, str]:
        values, _ = self._settings.load()
        persisted = values.get("model_roles")
        result = dict(config.DEFAULT_MODEL_ROLES)
        if isinstance(persisted, dict):
            result.update(
                {role: model for role, model in persisted.items() if role in config.MODEL_ROLES and isinstance(model, str)}
            )
        return result

    def describe(self, catalog: dict[str, Any]) -> list[dict[str, Any]]:
        installed = {model["name"] for model in catalog.get("models", [])}
        return [
            {"role": role, "model": model, "missing": model not in installed}
            for role, model in self.assignments().items()
        ]

    def assign(self, role: str, model: str, catalog: dict[str, Any]) -> dict[str, str]:
        if role not in config.MODEL_ROLES:
            raise ModelRoleError(f"unknown model role: {role}")
        installed = {item["name"] for item in catalog.get("models", [])}
        if model not in installed:
            raise ModelRoleError(f"model is not installed in Ollama: {model}")
        assignments = self.assignments()
        assignments[role] = model
        self._settings.save({"model_roles": assignments})
        return assignments
