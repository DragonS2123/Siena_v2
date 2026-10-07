"""Validated, versioned live settings snapshot for all Siena runtime services."""

from __future__ import annotations

import threading
import ipaddress
from urllib.parse import urlsplit
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

import config
from storage.settings_store import SettingsStore


class SettingsValidationError(ValueError):
    pass


ALIASES = {
    "provider": "inference_provider",
    "num_ctx": "context_size",
    "num_predict": "chat_output_tokens",
    "code_num_predict": "code_output_tokens",
    "auto_continue_on_length": "auto_continue_enabled",
    "max_auto_continuations": "auto_continue_max_rounds",
    "max_total_generation_tokens": "auto_continue_max_total_tokens",
    "continuation_overlap_window_chars": "auto_continue_overlap_window",
}

REVERSE_ALIASES = {value: key for key, value in ALIASES.items()}

DEFAULTS: dict[str, Any] = {
    "settings_revision": 0,
    "ollama_host": config.OLLAMA_HOST,
    "inference_provider": config.DEFAULT_INFERENCE_PROVIDER,
    "llama_cpp_url": config.LLAMA_CPP_URL,
    "llama_cpp_model": config.LLAMA_CPP_MODEL,
    "llama_cpp_profile": config.LLAMA_CPP_DEFAULT_PROFILE,
    "llama_cpp_managed": config.LLAMA_CPP_MANAGED,
    "llama_cpp_binary": str(config.LLAMA_CPP_BINARY),
    "llama_cpp_model_path": str(config.LLAMA_CPP_MODEL_PATH),
    "llama_cpp_host": config.LLAMA_CPP_HOST,
    "llama_cpp_port": config.LLAMA_CPP_PORT,
    "llama_cpp_device": config.LLAMA_CPP_DEVICE,
    "llama_cpp_startup_timeout": config.LLAMA_CPP_STARTUP_TIMEOUT,
    "llama_cpp_shutdown_timeout": config.LLAMA_CPP_SHUTDOWN_TIMEOUT,
    "llama_cpp_reasoning_budget_tokens": config.LLAMA_CPP_REASONING_BUDGET_TOKENS,
    "model_roles": config.inference_model_roles(config.DEFAULT_INFERENCE_PROVIDER),
    "max_context_messages": config.MAX_CONTEXT_MESSAGES,
    "context_size": config.LLAMA_CPP_PROFILES[config.LLAMA_CPP_DEFAULT_PROFILE] if config.DEFAULT_INFERENCE_PROVIDER == "llama_cpp" else config.OLLAMA_NUM_CTX,
    "chat_output_tokens": config.OLLAMA_NUM_PREDICT,
    "code_output_tokens": config.OLLAMA_CODE_NUM_PREDICT,
    "request_timeout_seconds": config.REQUEST_TIMEOUT_SECONDS,
    "code_request_timeout_seconds": config.CODE_REQUEST_TIMEOUT_SECONDS,
    "temperature": 0.8,
    "top_p": 0.9,
    "top_k": 40,
    "repeat_penalty": 1.1,
    "seed": None,
    "auto_continue_enabled": True,
    "auto_continue_max_rounds": 8,
    "auto_continue_max_total_tokens": 32768,
    "auto_continue_timeout_seconds": 1800,
    "auto_continue_overlap_window": config.CONTINUATION_OVERLAP_WINDOW_CHARS,
    "auto_continue_repair_rounds": 2,
    "auto_resume_interrupted": True,
    "thinking_display": "collapse_after_answer",
    "enable_ocr": True,
    "enable_image_understanding": True,
    "enable_translator": True,
    "enable_code_specialist_auto": True,
    "enable_reviewer_explicit": False,
    "stt_language": config.WHISPER_CPP_LANGUAGE,
    "tts_provider": config.TTS_PROVIDER,
    "interface_language": "en",
    "appearance_theme": "dark",
    "ui_font_size": "default",
    "ui_density": "comfortable",
    "show_message_timestamps": True,
    "show_typing_animation": True,
    "startup_page": "chat",
    "log_level": config.LOG_LEVEL,
}

LIVE_FIELDS = frozenset(DEFAULTS) - {"settings_revision", "tts_provider"}
RESTART_REQUIRED_FIELDS = frozenset({"tts_provider"})


@dataclass(frozen=True)
class RuntimeSettingsSnapshot:
    revision: int
    values: Mapping[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        canonical = ALIASES.get(key, key)
        return self.values.get(canonical, default)

    def as_dict(self, *, include_aliases: bool = True) -> dict[str, Any]:
        result = dict(self.values)
        result["model_roles"] = dict(result["model_roles"])
        result["settings_revision"] = self.revision
        if include_aliases:
            for alias, canonical in ALIASES.items():
                result[alias] = result[canonical]
        return result


@dataclass(frozen=True)
class SettingsUpdateResult:
    snapshot: RuntimeSettingsSnapshot
    applied: tuple[str, ...]
    restart_required: tuple[str, ...]
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": self.snapshot.as_dict(),
            "settings_revision": self.snapshot.revision,
            "applied": list(self.applied),
            "restart_required": list(self.restart_required),
            "errors": list(self.errors),
        }


class RuntimeSettingsService:
    """Owns the immutable effective snapshot and replaces it after atomic persistence."""

    def __init__(self, store: SettingsStore, logger: Any | None = None):
        self._store = store
        self._logger = logger
        self._lock = threading.RLock()
        self._listeners: list[Callable[[RuntimeSettingsSnapshot, tuple[str, ...]], None]] = []
        raw, error = store.load()
        if error:
            raise RuntimeError(f"failed to load settings: {error}")
        normalized = self._normalize(raw)
        self._snapshot = self._make_snapshot(normalized)

    def current(self) -> RuntimeSettingsSnapshot:
        with self._lock:
            return self._snapshot

    def subscribe(self, listener: Callable[[RuntimeSettingsSnapshot, tuple[str, ...]], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def save(self, patch: Mapping[str, Any]) -> SettingsUpdateResult:
        """Backward-compatible name for callers that previously wrote SettingsStore directly."""
        return self.update(patch)

    def update(self, patch: Mapping[str, Any]) -> SettingsUpdateResult:
        with self._lock:
            unknown = set(patch) - set(DEFAULTS) - set(ALIASES)
            if unknown:
                raise SettingsValidationError(f"unknown settings: {sorted(unknown)}")
            canonical_patch = self._canonical_patch(patch)
            current = self._snapshot.as_dict(include_aliases=False)
            current.pop("settings_revision", None)
            candidate = {**current, **canonical_patch}
            if {"llama_cpp_host", "llama_cpp_port"} & canonical_patch.keys() and "llama_cpp_url" not in canonical_patch:
                host = candidate["llama_cpp_host"]
                host = f"[{host}]" if isinstance(host, str) and ":" in host else host
                canonical_patch["llama_cpp_url"] = f"http://{host}:{candidate['llama_cpp_port']}"
                candidate["llama_cpp_url"] = canonical_patch["llama_cpp_url"]
            provider_changed = "inference_provider" in canonical_patch and candidate["inference_provider"] != current["inference_provider"]
            profile_changed = (candidate.get("inference_provider") == "llama_cpp"
                               and "llama_cpp_profile" in canonical_patch
                               and candidate["llama_cpp_profile"] != current["llama_cpp_profile"])
            if (provider_changed or profile_changed) and "context_size" not in canonical_patch:
                profile = candidate.get("llama_cpp_profile")
                context = (config.LLAMA_CPP_PROFILES.get(str(profile), 0)
                           if candidate.get("inference_provider") == "llama_cpp" else config.OLLAMA_NUM_CTX)
                canonical_patch["context_size"] = context
                candidate["context_size"] = context
            validated = self._validate(candidate)
            changed = tuple(sorted(key for key, value in canonical_patch.items() if current.get(key) != value))
            if not changed:
                return SettingsUpdateResult(self._snapshot, (), ())
            revision = self._snapshot.revision + 1
            persisted = {**validated, "settings_revision": revision}
            self._store.replace(persisted)
            next_snapshot = self._make_snapshot(persisted)
            self._snapshot = next_snapshot
            applied = tuple(key for key in changed if key in LIVE_FIELDS)
            restart_required = tuple(key for key in changed if key in RESTART_REQUIRED_FIELDS)
            if self._logger:
                self._logger.event(
                    "settings.updated",
                    settings_revision=revision,
                    changed=list(changed),
                )
                self._logger.event(
                    "settings.applied",
                    settings_revision=revision,
                    applied=list(applied),
                    restart_required=list(restart_required),
                )
            for listener in tuple(self._listeners):
                listener(next_snapshot, changed)
            return SettingsUpdateResult(next_snapshot, applied, restart_required)

    def _normalize(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(DEFAULTS)
        canonical = self._canonical_patch(raw)
        normalized.update(canonical)
        if {"llama_cpp_host", "llama_cpp_port"} & canonical.keys() and "llama_cpp_url" not in canonical:
            host = normalized["llama_cpp_host"]
            host = f"[{host}]" if isinstance(host, str) and ":" in host else host
            normalized["llama_cpp_url"] = f"http://{host}:{normalized['llama_cpp_port']}"
        # An existing pre-provider settings file keeps its legacy transport and
        # context. Switching providers is explicit; fresh Linux settings use Gemma.
        if "inference_provider" not in canonical and "ollama_host" in canonical:
            normalized["inference_provider"] = "ollama"
        provider = normalized.get("inference_provider")
        if "model_roles" not in canonical:
            normalized["model_roles"] = config.inference_model_roles(provider, normalized.get("llama_cpp_model"))
        if "context_size" not in canonical:
            normalized["context_size"] = config.LLAMA_CPP_PROFILES.get(str(normalized.get("llama_cpp_profile")), 0) if provider == "llama_cpp" else config.OLLAMA_NUM_CTX
        return self._validate(normalized)

    @staticmethod
    def _canonical_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in patch.items():
            canonical = ALIASES.get(key, key)
            if canonical in result and result[canonical] != value:
                raise SettingsValidationError(f"conflicting aliases for {canonical}")
            result[canonical] = value
        profile = result.get("llama_cpp_profile")
        if isinstance(profile, str):
            result["llama_cpp_profile"] = config.LLAMA_CPP_PROFILE_ALIASES.get(profile, profile)
        if "llama_cpp_url" in result:
            try:
                endpoint = urlsplit(result["llama_cpp_url"])
                host, port = endpoint.hostname, endpoint.port or (443 if endpoint.scheme == "https" else 80)
                if not host:
                    raise ValueError("missing host")
            except (ValueError, TypeError, AttributeError) as exc:
                raise SettingsValidationError("invalid llama_cpp_url") from exc
            for key, value in (("llama_cpp_host", host), ("llama_cpp_port", port)):
                if key in result and result[key] != value:
                    raise SettingsValidationError(f"llama_cpp_url conflicts with {key}")
                result[key] = value
        return result

    @staticmethod
    def _make_snapshot(values: Mapping[str, Any]) -> RuntimeSettingsSnapshot:
        revision = int(values.get("settings_revision", 0))
        copied = dict(values)
        copied.pop("settings_revision", None)
        copied["model_roles"] = MappingProxyType(dict(copied["model_roles"]))
        return RuntimeSettingsSnapshot(revision, MappingProxyType(copied))

    @staticmethod
    def _validate(values: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(values)
        revision = result.pop("settings_revision", 0)
        RuntimeSettingsService._integer(result, "max_context_messages", 1, 500)
        RuntimeSettingsService._integer(result, "context_size", 2048, 262144)
        RuntimeSettingsService._integer(result, "chat_output_tokens", 64, 65536)
        RuntimeSettingsService._integer(result, "code_output_tokens", 64, 65536)
        RuntimeSettingsService._integer(result, "llama_cpp_reasoning_budget_tokens", 0, 65536)
        RuntimeSettingsService._integer(result, "request_timeout_seconds", 5, 3600)
        RuntimeSettingsService._integer(result, "code_request_timeout_seconds", 5, 7200)
        RuntimeSettingsService._integer(result, "top_k", 0, 1000)
        RuntimeSettingsService._integer(result, "auto_continue_max_rounds", 1, 64)
        RuntimeSettingsService._integer(result, "auto_continue_max_total_tokens", 256, 262144)
        RuntimeSettingsService._integer(result, "auto_continue_timeout_seconds", 30, 14400)
        RuntimeSettingsService._integer(result, "auto_continue_overlap_window", 100, 20000)
        RuntimeSettingsService._integer(result, "auto_continue_repair_rounds", 0, 10)
        for key, low, high in (
            ("temperature", 0.0, 2.0),
            ("top_p", 0.0, 1.0),
            ("repeat_penalty", 0.0, 4.0),
        ):
            value = result.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= float(value) <= high:
                raise SettingsValidationError(f"{key} must be between {low} and {high}")
            result[key] = float(value)
        if result.get("seed") is not None:
            RuntimeSettingsService._integer(result, "seed", -1, 2**31 - 1)
        for key in (
            "auto_continue_enabled",
            "auto_resume_interrupted",
            "enable_ocr",
            "enable_image_understanding",
            "enable_translator",
            "enable_code_specialist_auto",
            "enable_reviewer_explicit",
            "show_message_timestamps",
            "show_typing_animation",
            "llama_cpp_managed",
        ):
            if not isinstance(result.get(key), bool):
                raise SettingsValidationError(f"{key} must be boolean")
        provider = result.get("inference_provider")
        if not isinstance(provider, str) or provider not in {"ollama", "llama_cpp"}:
            raise SettingsValidationError("inference_provider must be ollama or llama_cpp")
        profile = result.get("llama_cpp_profile")
        if not isinstance(profile, str) or profile not in config.LLAMA_CPP_PROFILES:
            raise SettingsValidationError("unknown llama_cpp_profile")
        endpoint = result.get("llama_cpp_url")
        try:
            parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
            valid_url = parsed and parsed.scheme in {"http", "https"} and parsed.hostname and parsed.port != 0 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        except ValueError:
            valid_url = False
        if not valid_url:
            raise SettingsValidationError("llama_cpp_url must be an http(s) server URL without credentials")
        result["llama_cpp_url"] = endpoint.rstrip("/")
        RuntimeSettingsService._integer(result, "llama_cpp_port", 1, 65535)
        if result["llama_cpp_port"] == 8080:
            raise SettingsValidationError("llama_cpp_port 8080 is reserved for TTS")
        for key in ("llama_cpp_startup_timeout", "llama_cpp_shutdown_timeout"):
            RuntimeSettingsService._integer(result, key, 1, 600)
        for key in ("llama_cpp_binary", "llama_cpp_model_path", "llama_cpp_device", "llama_cpp_host"):
            if not isinstance(result.get(key), str) or not result[key].strip():
                raise SettingsValidationError(f"{key} must not be empty")
        if provider == "llama_cpp" and result["llama_cpp_managed"]:
            try:
                local = result["llama_cpp_host"] == "localhost" or ipaddress.ip_address(result["llama_cpp_host"]).is_loopback
            except ValueError:
                local = False
            if not local or parsed.scheme != "http" or parsed.path not in {"", "/"}:
                raise SettingsValidationError("managed llama-server requires a loopback HTTP endpoint")
        model = result.get("llama_cpp_model")
        if not isinstance(model, str) or not model.strip():
            raise SettingsValidationError("llama_cpp_model must not be empty")
        result["llama_cpp_model"] = model.strip()
        if provider == "llama_cpp" and result["context_size"] != config.LLAMA_CPP_PROFILES[profile]:
            raise SettingsValidationError("context_size must match llama_cpp_profile; configure the external server separately")
        host = result.get("ollama_host")
        if not isinstance(host, str) or not host.startswith(("http://", "https://")):
            raise SettingsValidationError("ollama_host must be an http(s) URL")
        result["ollama_host"] = host.rstrip("/")
        roles = result.get("model_roles")
        if not isinstance(roles, Mapping):
            raise SettingsValidationError("model_roles must be an object")
        merged_roles = config.inference_model_roles(provider, result["llama_cpp_model"])
        for role, model in roles.items():
            if role not in config.MODEL_ROLES or not isinstance(model, str) or not model.strip():
                raise SettingsValidationError(f"invalid model role assignment: {role}")
            merged_roles[role] = model.strip()
        result["model_roles"] = merged_roles
        if result.get("stt_language") not in {"auto", "ru", "en"}:
            raise SettingsValidationError("stt_language must be auto, ru, or en")
        if result.get("thinking_display") not in {"always", "collapse_after_answer", "hidden"}:
            raise SettingsValidationError("invalid thinking_display")
        result["settings_revision"] = int(revision)
        return result

    @staticmethod
    def _integer(values: dict[str, Any], key: str, low: int, high: int) -> None:
        value = values.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise SettingsValidationError(f"{key} must be an integer between {low} and {high}")
