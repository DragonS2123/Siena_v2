"""Validated live model-role assignments backed by RuntimeSettingsService."""

from __future__ import annotations

from typing import Any

import config
from core.runtime_settings import RuntimeSettingsService


class ModelRoleError(ValueError):
    pass


class ModelRoles:
    def __init__(self, settings: RuntimeSettingsService):
        self._settings = settings

    def assignments(self) -> dict[str, str]:
        roles = self._settings.current().get("model_roles", config.DEFAULT_MODEL_ROLES)
        return dict(roles)

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
            raise ModelRoleError(f"model is unavailable from inference provider: {model}")
        assignments = self.assignments()
        assignments[role] = model
        self._settings.update({"model_roles": assignments})
        return assignments
