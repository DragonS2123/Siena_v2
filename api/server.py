from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import re
import tempfile
import time
import uuid
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import requests
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config
from core import model_router
from core.image_intent import decide_vision
from core.memory_intent import wants_long_memory_save
from core.research_intent import wants_grounded_research
from core.agent_loop import MaxIterationsReached, run as run_agent_loop
from core.errors import SienaInfraError, SienaToolError
from core.ollama_client import OllamaClient
from core.resource_manager import (
    find_tts_server_processes,
    ollama_process_status,
    resolve_unload_targets,
    tts_server_status,
    whisper_cli_status,
)
from core.session import Session
from core.system_metrics import cpu_ram_metrics, vram_metrics
from game.nucleares_bridge import NuclearesBridgeClient
from game.nucleares_context import build_nucleares_context, nucleares_context_skip_reason, wants_nucleares_context
from logging_.logger import SienaLogger
from main import build_registry
from memory.long_memory_store import LongMemoryStore
from memory.short_memory_store import ShortMemoryStore
from ocr.glm_ocr_service import (
    GlmOcrService,
    OcrModelNotInstalledError,
    OcrUnavailableError,
    clean_ocr_text,
    ocr_quality,
)
from storage.conversation_store import ConversationStore
from storage.settings_store import PERSISTABLE_FIELDS, SettingsStore
from tools.candidate_memory_tools import promote_candidate
from translator.translator_service import (
    TranslatorCallFailedError,
    TranslatorModelNotInstalledError,
    TranslatorService,
)
from voice.faster_qwen_tts import FasterQwen3TTSProvider
from voice.qwen_tts import Qwen3TTSProvider
from voice.qwen_tts_ggml_vulkan import QwenTTSGgmlVulkanProvider
from voice.stt import STTUnavailableError, WhisperSTTProvider
from voice.tts import SileroTTSProvider, TTSUnavailableError
from voice.voice_profiles import VoiceProfile, VoiceProfileStore
from voice.voice_service import VoiceService
from voice.whisper_cpp_stt import (
    WhisperCppEmptyResultError,
    WhisperCppSTTProvider,
    WhisperCppTimeoutError,
    WhisperCppTranscriptionError,
    WhisperCppUnavailableError,
)
from vision.qwen_vision_service import (
    QwenVisionService,
    VisionModelNotInstalledError,
    VisionUnavailableError,
)


class ChatAttachment(BaseModel):
    name: str
    type: str
    size: str | None = None
    lang: str | None = None
    mime: str | None = None
    content: str | None = None  # text/code/markdown/json/log content
    data_url: str | None = None  # image attachments only вЂ” base64 data URL, OCR'd server-side (Phase 4B)
    # Phase 4C вЂ” explicit opt-in only; /api/chat never auto-translates. When
    # true, the text content (or OCR result, for images) is translated before
    # being injected into the model's context. target_lang defaults to
    # config.TRANSLATOR_DEFAULT_TARGET when unset.
    translate: bool = False
    target_lang: str | None = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    attachments: list[ChatAttachment] = []


class ClientTraceEventRequest(BaseModel):
    event: str
    fields: dict[str, Any] = {}


class SetActiveChatModelRequest(BaseModel):
    model: str


class ModelLifecycleUnloadRequest(BaseModel):
    target: str  # "tool_models" | "all_non_chat" | "specific" — validated in the handler
    model: str | None = None  # required only when target == "specific"


class TranslateRequest(BaseModel):
    text: str
    source_lang: str = config.TRANSLATOR_DEFAULT_SOURCE
    target_lang: str = config.TRANSLATOR_DEFAULT_TARGET
    preserve_formatting: bool = True
    # Accepted for API-shape compatibility (see Phase 4C spec) but primary/
    # fallback model selection stays server-side (config.TRANSLATOR_MODEL /
    # TRANSLATOR_FALLBACK_MODEL) вЂ” a client-picked provider could bypass the
    # fallback safety net entirely, so this is intentionally not honored yet.
    provider: str | None = None


class MemoryLongSaveRequest(BaseModel):
    """Feedback row 'Save-to-memory' (explicit human action, not a model
    decision) — see POST /api/memory/long below."""
    text: str
    source: str = "feedback_row"
    conversation_id: str | None = None
    message_id: str | None = None


class VoiceSynthesizeRequest(BaseModel):
    text: str
    voice: str | None = None


class VoiceTtsTestRequest(BaseModel):
    text: str = "Привет, это тестовый запрос синтеза речи."


class VoiceTtsStreamRequest(BaseModel):
    """POST /api/voice/tts/stream — experimental (Phase 2, HANDOFF_v2.md)
    backend-only PCM streaming, qwen3_tts_ggml_vulkan-only. `language` is
    accepted for shape-completeness but not actually forwarded per-request
    — see QwenTTSGgmlVulkanProvider.stream_pcm()'s docstring for why."""
    text: str
    voice: str | None = None
    language: str | None = None


class VoiceProfileCreateRequest(BaseModel):
    id: str
    name: str
    speaker: str
    language: str = "Russian"
    model_repo: str | None = None
    instruct: str = ""
    provider: str = "qwen3_tts"


class VoiceProfileUpdateRequest(BaseModel):
    name: str | None = None
    speaker: str | None = None
    language: str | None = None
    model_repo: str | None = None
    instruct: str | None = None
    provider: str | None = None


class VoiceProfileActivateRequest(BaseModel):
    profile_id: str


class ConversationCreateRequest(BaseModel):
    title: str | None = None


class ConversationRenameRequest(BaseModel):
    title: str


class SettingsUpdate(BaseModel):
    primary_model: str | None = None
    code_model: str | None = None
    ollama_host: str | None = None
    max_iterations: int | None = None
    request_timeout_seconds: int | None = None
    delegate_timeout_seconds: int | None = None
    num_ctx: int | None = None
    num_predict: int | None = None
    max_context_messages: int | None = None
    log_level: str | None = None
    # Settings unfreeze pass (HANDOFF_v2.md) — real, persisted, applied live.
    enable_ocr: bool | None = None
    enable_image_understanding: bool | None = None
    enable_translator: bool | None = None
    enable_code_specialist_auto: bool | None = None
    enable_reviewer_explicit: bool | None = None
    stt_language: str | None = None
    # Settings Pass 2 — pure frontend UI/display preferences (no backend
    # behavioral effect at all; see config.py's own comment on these).
    appearance_theme: str | None = None
    accent_color: str | None = None
    ui_font_size: str | None = None
    ui_density: str | None = None
    show_message_timestamps: bool | None = None
    show_typing_animation: bool | None = None
    copy_before_clear_chat: bool | None = None
    startup_page: str | None = None
    code_font_size: str | None = None
    code_line_wrap: bool | None = None
    # Settings Pass 3 — remaining code-display visibility toggles, the
    # experimental Stream-button visibility toggle (both pure frontend), and
    # the one real addition: a soft chat-prompt language preference.
    code_syntax_highlighting: bool | None = None
    code_show_line_numbers: bool | None = None
    code_show_language_badge: bool | None = None
    code_show_copy_button: bool | None = None
    code_show_collapse_button: bool | None = None
    code_show_save_button: bool | None = None
    show_experimental_stream_button: bool | None = None
    preferred_response_language: str | None = None
    # Real UI localization pass — application UI language only, separate
    # from stt_language/preferred_response_language above.
    interface_language: str | None = None


class TraceHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._recent: deque[dict[str, Any]] = deque(maxlen=500)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        for event in self._recent:
            await websocket.send_json(event)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, event: dict[str, Any]) -> None:
        self._recent.append(event)
        stale: list[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_json(event)
            except RuntimeError:
                stale.append(client)
        for client in stale:
            self.disconnect(client)


class BroadcastLogger:
    """Р›РѕРіРёСЂСѓРµС‚ РІ JSONL (С‡РµСЂРµР· SienaLogger) + С€Р»С‘С‚ РІ WebSocket-С‚СЂР°СЃСЃРёСЂРѕРІРєСѓ +,
    РµСЃР»Рё РїРµСЂРµРґР°РЅ conversation_id, РґСѓР±Р»РёСЂСѓРµС‚ РєР°Р¶РґРѕРµ СЃРѕР±С‹С‚РёРµ РІ
    conversation_events (ConversationStore) вЂ” СЌС‚Рѕ С‚Рѕ, С‡С‚Рѕ РґРµР»Р°РµС‚ РёСЃС‚РѕСЂРёСЋ С‡Р°С‚Р°
    РІРѕСЃСЃС‚Р°РЅРѕРІРёРјРѕР№ РїРѕСЃР»Рµ РїРµСЂРµР·Р°РїСѓСЃРєР° backend'Р° (СЃРј. DONEARCHITECTURE.md,
    Conversation History). Runtime РЅРµ СЂРµС€Р°РµС‚, РєР°РєРёРµ СЃРѕР±С‹С‚РёСЏ "СЃС‚РѕРёС‚" СЃРѕС…СЂР°РЅРёС‚СЊ вЂ”
    СЃРѕС…СЂР°РЅСЏСЋС‚СЃСЏ Р±СѓРєРІР°Р»СЊРЅРѕ РІСЃРµ СЃРѕР±С‹С‚РёСЏ СЌС‚РѕРіРѕ Р·Р°РїСЂРѕСЃР°, Р±РµР· С„РёР»СЊС‚СЂР°С†РёРё РїРѕ СЃРјС‹СЃР»Сѓ.
    """

    def __init__(
        self,
        logger: SienaLogger,
        hub: TraceHub,
        loop: asyncio.AbstractEventLoop,
        conversation_store: ConversationStore | None = None,
        conversation_id: str | None = None,
    ):
        self._logger = logger
        self._hub = hub
        self._loop = loop
        self._conversation_store = conversation_store
        self._conversation_id = conversation_id

    def _publish(self, event_type: str, level: str, fields: dict[str, Any]) -> None:
        event = {"ts": fields.get("ts"), "event": event_type, "level": level, **fields}
        if event["ts"] is None:
            event.pop("ts", None)
        self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._hub.broadcast(event)))

    def _persist(self, event_type: str, fields: dict[str, Any]) -> None:
        if self._conversation_store is not None and self._conversation_id is not None:
            self._conversation_store.append_event(self._conversation_id, event_type, fields)

    def event(self, event_type: str, console_message: str | None = None, **fields: Any) -> None:
        self._logger.event(event_type, console_message=console_message, **fields)
        self._publish(event_type, "info", fields)
        self._persist(event_type, fields)

    def error(self, event_type: str, console_message: str, **fields: Any) -> None:
        self._logger.error(event_type, console_message=console_message, **fields)
        self._publish(event_type, "error", fields)
        self._persist(event_type, fields)


class SessionStore:
    """Р”РµСЂР¶РёС‚ Р РћР’РќРћ РћР”РРќ Р¶РёРІРѕР№ Session вЂ” С‚РѕС‚, С‡С‚Рѕ РёСЃРїРѕР»СЊР·СѓРµС‚ agent_loop РїСЂСЏРјРѕ
    СЃРµР№С‡Р°СЃ вЂ” РїР»СЋСЃ Р·РЅР°РµС‚, РєР°РєРѕРјСѓ conversation_id (РІ ConversationStore) РѕРЅ
    СЃРѕРѕС‚РІРµС‚СЃС‚РІСѓРµС‚. Р­С‚Рѕ РЅРµ С…СЂР°РЅРёР»РёС‰Рµ РёСЃС‚РѕСЂРёРё: РїРѕР»РЅР°СЏ РїРµСЂРµРїРёСЃРєР° Р¶РёРІС‘С‚ РІ
    ConversationStore (SQLite, РїРµСЂРµР¶РёРІР°РµС‚ РїРµСЂРµР·Р°РїСѓСЃРє); Session вЂ” СЌС‚Рѕ С‚РѕР»СЊРєРѕ
    "СЂР°Р±РѕС‡РёР№" РѕР±СЉРµРєС‚ agent_loop РґР»СЏ РўР•РљРЈР©Р•Р“Рћ СЂР°Р·РіРѕРІРѕСЂР° (СЃРј.
    DIAGNOSIS_CONTEXT_OVERFLOW.md СЂР°Р·РґРµР» 7Р± Рё DONEARCHITECTURE.md Conversation
    History).

    Runtime Р·РґРµСЃСЊ РїРѕ-РїСЂРµР¶РЅРµРјСѓ РЅРёС‡РµРіРѕ РЅРµ СЂРµС€Р°РµС‚ РїРѕ СЃРѕРґРµСЂР¶Р°РЅРёСЋ вЂ” С‚РѕР»СЊРєРѕ РєР°РєРѕР№
    Session РёСЃРїРѕР»СЊР·РѕРІР°С‚СЊ РґР»СЏ С‚РµРєСѓС‰РµРіРѕ Р·Р°РїСЂРѕСЃР°; СЃРѕР·РґР°РЅРёРµ/РїРµСЂРµРєР»СЋС‡РµРЅРёРµ
    СЂР°Р·РіРѕРІРѕСЂР° вЂ” СЏРІРЅРѕРµ РґРµР№СЃС‚РІРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ (РєРЅРѕРїРєР° "New Chat" / РєР»РёРє РїРѕ
    СЃС‚Р°СЂРѕРјСѓ С‡Р°С‚Сѓ), РЅРµ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРѕРµ СЂРµС€РµРЅРёРµ.
    """

    def __init__(self, system_prompt: str, conversation_store: ConversationStore):
        self._system_prompt = system_prompt
        self._conversation_store = conversation_store
        self._current_conversation_id: str | None = None
        self._current_session: Session = Session(system_prompt)

    def new_conversation(self, title: str | None = None) -> str:
        conversation_id = self._conversation_store.create_conversation(title)
        self._current_conversation_id = conversation_id
        self._current_session = Session(self._system_prompt)
        return conversation_id

    def new_session(self) -> str:
        """РЎРѕРІРјРµСЃС‚РёРјРѕСЃС‚СЊ СЃ РїСЂРµР¶РЅРёРј /api/session/new вЂ” С‚РµРїРµСЂСЊ СЌС‚Рѕ РїСЂРѕСЃС‚Рѕ alias
        РґР»СЏ new_conversation()."""
        return self.new_conversation()

    def activate(self, conversation_id: str) -> None:
        """РџРµСЂРµСЃРѕР±РёСЂР°РµС‚ Session РёР· СЃРѕС…СЂР°РЅС‘РЅРЅРѕР№ РёСЃС‚РѕСЂРёРё conversation_id Рё РґРµР»Р°РµС‚
        РµС‘ С‚РµРєСѓС‰РµР№. Р’РѕСЃСЃС‚Р°РЅР°РІР»РёРІР°СЋС‚СЃСЏ С‚РѕР»СЊРєРѕ role user/assistant СЃРѕРѕР±С‰РµРЅРёСЏ вЂ”
        РЅРµ tool messages (MVP-СЂРµС€РµРЅРёРµ, С‡С‚РѕР±С‹ РЅРµ СЂР°Р·РґСѓРІР°С‚СЊ РєРѕРЅС‚РµРєСЃС‚ СЃС‚Р°СЂС‹Рј
        tool-С‚СЂРµР№СЃРѕРј; РїРѕР»РЅС‹Р№ trace РІСЃС‘ СЂР°РІРЅРѕ РґРѕСЃС‚СѓРїРµРЅ РІ conversation_events
        РґР»СЏ UI, РїСЂРѕСЃС‚Рѕ РЅРµ РѕС‚РїСЂР°РІР»СЏРµС‚СЃСЏ РїРѕРІС‚РѕСЂРЅРѕ РјРѕРґРµР»Рё)."""
        conv = self._conversation_store.get_conversation(conversation_id)
        if conv is None:
            raise KeyError(conversation_id)

        session = Session(self._system_prompt)
        for message in conv["messages"]:
            if message["role"] == "user":
                session.add_user(message["content"] or "")
            elif message["role"] == "assistant":
                session.add_assistant_raw({"role": "assistant", "content": message["content"] or ""})

        self._current_conversation_id = conversation_id
        self._current_session = session

    def build_session(self, conversation_id: str) -> Session:
        conv = self._conversation_store.get_conversation(conversation_id)
        if conv is None:
            raise KeyError(conversation_id)

        session = Session(self._system_prompt)
        for message in conv["messages"]:
            if message["role"] == "user":
                session.add_user(message["content"] or "")
            elif message["role"] == "assistant":
                session.add_assistant_raw({"role": "assistant", "content": message["content"] or ""})
        return session

    @property
    def current_id(self) -> str | None:
        return self._current_conversation_id

    def current(self) -> Session:
        if self._current_conversation_id is None:
            raise KeyError("no active conversation")
        return self._current_session

    def clear_active(self) -> None:
        self._current_conversation_id = None
        self._current_session = Session(self._system_prompt)


app = FastAPI(title="Siena v2 Control Panel API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

trace_hub = TraceHub()

# Settings persistence (Settings > Model section only, HANDOFF_v2.md §6) —
# reload whatever was last explicitly saved via POST /api/settings (see
# update_settings() below, which writes through settings_store.save()).
# Applied BEFORE base_logger/build_registry/ollama_client below so
# log_level/primary_model/code_model actually take effect at construction
# time, not just on the next settings change. A missing or corrupt
# settings.json never blocks startup — settings_store.load() only ever
# returns an empty dict plus an error string, never raises.
settings_store = SettingsStore(config.SETTINGS_STORE_PATH)
_persisted_settings, _settings_load_error = settings_store.load()
if "primary_model" in _persisted_settings:
    config.PRIMARY_MODEL = _persisted_settings["primary_model"]
if "code_model" in _persisted_settings:
    _old_code_model = config.CODE_MODEL
    config.CODE_MODEL = _persisted_settings["code_model"]
    _code_model_description = config.DELEGATE_MODELS.pop(
        _old_code_model,
        "Специализированная модель программирования (код, рефакторинг, анализ, объяснение кода).",
    )
    config.DELEGATE_MODELS[config.CODE_MODEL] = _code_model_description
if "max_context_messages" in _persisted_settings:
    config.MAX_CONTEXT_MESSAGES = _persisted_settings["max_context_messages"]
if "num_ctx" in _persisted_settings:
    config.OLLAMA_NUM_CTX = _persisted_settings["num_ctx"]
if "num_predict" in _persisted_settings:
    config.OLLAMA_NUM_PREDICT = _persisted_settings["num_predict"]
if "request_timeout_seconds" in _persisted_settings:
    config.REQUEST_TIMEOUT_SECONDS = _persisted_settings["request_timeout_seconds"]
if "log_level" in _persisted_settings:
    config.LOG_LEVEL = _persisted_settings["log_level"]
if "enable_ocr" in _persisted_settings:
    config.ENABLE_OCR = _persisted_settings["enable_ocr"]
if "enable_image_understanding" in _persisted_settings:
    config.ENABLE_IMAGE_UNDERSTANDING = _persisted_settings["enable_image_understanding"]
if "enable_translator" in _persisted_settings:
    config.ENABLE_TRANSLATOR = _persisted_settings["enable_translator"]
if "enable_code_specialist_auto" in _persisted_settings:
    config.ENABLE_CODE_SPECIALIST_AUTO = _persisted_settings["enable_code_specialist_auto"]
if "enable_reviewer_explicit" in _persisted_settings:
    config.ENABLE_REVIEWER_EXPLICIT = _persisted_settings["enable_reviewer_explicit"]
if "stt_language" in _persisted_settings:
    config.WHISPER_CPP_LANGUAGE = _persisted_settings["stt_language"]
if "appearance_theme" in _persisted_settings:
    config.APPEARANCE_THEME = _persisted_settings["appearance_theme"]
if "accent_color" in _persisted_settings:
    config.ACCENT_COLOR = _persisted_settings["accent_color"]
if "ui_font_size" in _persisted_settings:
    config.UI_FONT_SIZE = _persisted_settings["ui_font_size"]
if "ui_density" in _persisted_settings:
    config.UI_DENSITY = _persisted_settings["ui_density"]
if "show_message_timestamps" in _persisted_settings:
    config.SHOW_MESSAGE_TIMESTAMPS = _persisted_settings["show_message_timestamps"]
if "show_typing_animation" in _persisted_settings:
    config.SHOW_TYPING_ANIMATION = _persisted_settings["show_typing_animation"]
if "copy_before_clear_chat" in _persisted_settings:
    config.COPY_BEFORE_CLEAR_CHAT = _persisted_settings["copy_before_clear_chat"]
if "startup_page" in _persisted_settings:
    config.STARTUP_PAGE = _persisted_settings["startup_page"]
if "code_font_size" in _persisted_settings:
    config.CODE_FONT_SIZE = _persisted_settings["code_font_size"]
if "code_line_wrap" in _persisted_settings:
    config.CODE_LINE_WRAP = _persisted_settings["code_line_wrap"]
if "code_syntax_highlighting" in _persisted_settings:
    config.CODE_SYNTAX_HIGHLIGHTING = _persisted_settings["code_syntax_highlighting"]
if "code_show_line_numbers" in _persisted_settings:
    config.CODE_SHOW_LINE_NUMBERS = _persisted_settings["code_show_line_numbers"]
if "code_show_language_badge" in _persisted_settings:
    config.CODE_SHOW_LANGUAGE_BADGE = _persisted_settings["code_show_language_badge"]
if "code_show_copy_button" in _persisted_settings:
    config.CODE_SHOW_COPY_BUTTON = _persisted_settings["code_show_copy_button"]
if "code_show_collapse_button" in _persisted_settings:
    config.CODE_SHOW_COLLAPSE_BUTTON = _persisted_settings["code_show_collapse_button"]
if "code_show_save_button" in _persisted_settings:
    config.CODE_SHOW_SAVE_BUTTON = _persisted_settings["code_show_save_button"]
if "show_experimental_stream_button" in _persisted_settings:
    config.SHOW_EXPERIMENTAL_STREAM_BUTTON = _persisted_settings["show_experimental_stream_button"]
if "preferred_response_language" in _persisted_settings:
    config.PREFERRED_RESPONSE_LANGUAGE = _persisted_settings["preferred_response_language"]
if "interface_language" in _persisted_settings:
    config.INTERFACE_LANGUAGE = _persisted_settings["interface_language"]

base_logger = SienaLogger(config.LOG_DIR, config.LOG_LEVEL)
if _settings_load_error:
    base_logger.error(
        "settings_load_failed",
        console_message=f"[SETTINGS] storage/settings.json повреждён/недоступен: {_settings_load_error}",
        error=_settings_load_error,
    )
else:
    base_logger.event(
        "settings_loaded",
        loaded_fields=list(_persisted_settings.keys()),
        console_message=(
            f"[SETTINGS] загружено из storage/settings.json: {', '.join(_persisted_settings.keys())}"
            if _persisted_settings
            else "[SETTINGS] storage/settings.json отсутствует — используются config.py defaults"
        ),
    )

registry, short_store, long_store, candidate_store = build_registry(base_logger)

# Runtime view CPU/RAM/VRAM meters (core/system_metrics.py) — VRAM support is
# probed once here and logged once, NOT on every /api/runtime/status poll
# (that endpoint is polled every 5s by RuntimeStatusProvider and must stay
# silent — see HANDOFF_v2.md §5/§6).
_vram_probe = vram_metrics()
if _vram_probe["vram_supported"]:
    base_logger.event(
        "runtime_vram_probe",
        vram_supported=True,
        vram_total_gb=_vram_probe["vram_total_gb"],
        console_message=f"[RUNTIME] VRAM метрики доступны (nvidia-smi): {_vram_probe['vram_total_gb']} GB",
    )
else:
    base_logger.event(
        "runtime_vram_probe",
        vram_supported=False,
        vram_reason=_vram_probe["vram_reason"],
        console_message=f"[RUNTIME] VRAM метрики недоступны: {_vram_probe['vram_reason']}",
    )

conversation_store = ConversationStore(config.CONVERSATIONS_DB_PATH, config.CONVERSATION_EVENTS_DEFAULT_LIMIT)
session_store = SessionStore(config.SYSTEM_PROMPT, conversation_store)
ollama_client = OllamaClient(
    host=config.OLLAMA_HOST,
    model=config.PRIMARY_MODEL,
    timeout=config.REQUEST_TIMEOUT_SECONDS,
    think=config.OLLAMA_THINK,
    num_ctx=config.OLLAMA_NUM_CTX,
    num_predict=config.OLLAMA_NUM_PREDICT,
)
chat_lock = asyncio.Lock()
nucleares_client = NuclearesBridgeClient(snapshot_path=config.BASE_DIR / "storage" / "game" / "nucleares_snapshot.json")

# Last routing decision (Phase 4D) вЂ” purely informational, read by
# /api/models and /api/runtime/status for "last used model" in the UI.
# Never influences routing itself (core/model_router.py doesn't read this).
_last_used_model: str | None = None
_last_used_role: str | None = None

# Manually-selected "normal chat" model (Phase 4E) вЂ” runtime-only, NOT
# persisted (resets to config.MAIN_CHAT_MODEL on restart). Only ever mutated
# by POST /api/models/active below, which validates against
# config.ALLOWED_MANUAL_CHAT_MODELS + live Ollama install status before
# touching this. core/model_router.py additionally never trusts this value
# blindly either (re-validates against the same allowlist).
_active_chat_model: str = config.MAIN_CHAT_MODEL


def _build_routed_client(model: str) -> OllamaClient:
    """Per-request OllamaClient for a specialist turn (code_specialist /
    reviewer_critic) вЂ” mirrors the module-level `ollama_client` singleton's
    settings but targets a different model. Cheap to construct (no model
    loading happens here, same as request_registry being rebuilt per request
    below) вЂ” never reused across requests, never mutates the singleton."""
    return OllamaClient(
        host=config.OLLAMA_HOST,
        model=model,
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        think=config.OLLAMA_THINK,
        num_ctx=config.OLLAMA_NUM_CTX,
        num_predict=config.OLLAMA_NUM_PREDICT,
    )


# Voice Layer (STT/TTS) вЂ” СЃРµСЂРІРёСЃС‹ РёРЅС‚РµСЂС„РµР№СЃР° РІРІРѕРґР°/РІС‹РІРѕРґР°, РЅРµ tools РјРѕРґРµР»Рё.
# РњРѕРґРµР»Рё РќР• РіСЂСѓР·СЏС‚СЃСЏ Р·РґРµСЃСЊ вЂ” С‚РѕР»СЊРєРѕ Р»РµРЅРёРІРѕ РЅР° РїРµСЂРІС‹Р№ СЂРµР°Р»СЊРЅС‹Р№ РІС‹Р·РѕРІ (СЃРј.
# voice/stt.py, voice/tts.py, voice/qwen_tts.py). РљРѕРЅСЃС‚СЂСѓРёСЂРѕРІР°РЅРёРµ СЌС‚РёС…
# РѕР±СЉРµРєС‚РѕРІ РЅРёРєРѕРіРґР° РЅРµ СЂРѕРЅСЏРµС‚ backend, РґР°Р¶Рµ РµСЃР»Рё faster-whisper/torch/qwen-tts
# РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅС‹ РёР»Рё CUDA РЅРµРґРѕСЃС‚СѓРїРЅР°.
voice_profile_store = VoiceProfileStore(config.VOICE_PROFILES_PATH, logger=base_logger)

_silero_tts = SileroTTSProvider(
    language=config.TTS_LANGUAGE,
    model_id=config.TTS_MODEL_ID,
    speaker=config.TTS_SPEAKER,
    device=config.TTS_DEVICE,
    output_dir=config.TTS_OUTPUT_DIR,
    sample_rate=config.TTS_SAMPLE_RATE,
    models_dir=config.TTS_MODELS_DIR,
    strip_all_numbers=config.TTS_STRIP_ALL_NUMBERS,
    logger=base_logger,
)

# config.TTS_PROVIDER вЂ” СЏРІРЅС‹Р№ РІС‹Р±РѕСЂ С‡РµР»РѕРІРµРєР° (СЃРј. config.py). Р•СЃР»Рё РІС‹Р±СЂР°РЅ
# "qwen3_tts"/"faster_qwen3_tts", Silero РІСЃС‘ СЂР°РІРЅРѕ РєРѕРЅСЃС‚СЂСѓРёСЂСѓРµС‚СЃСЏ Рё РґРµСЂР¶РёС‚СЃСЏ
# РєР°Рє fallback_tts вЂ” VoiceService.synthesize() РїРµСЂРµРєР»СЋС‡РёС‚СЃСЏ РЅР° РЅРµРіРѕ, РµСЃР»Рё
# РѕСЃРЅРѕРІРЅРѕР№ provider РЅРµРґРѕСЃС‚СѓРїРµРЅ РёР»Рё СѓРїР°РґС‘С‚ РїСЂРё СЃРёРЅС‚РµР·Рµ (РёРЅР¶РµРЅРµСЂРЅР°СЏ Р·Р°С‰РёС‚Р°,
# РЅРµ СЃРјС‹СЃР»РѕРІРѕРµ СЂРµС€РµРЅРёРµ).
if config.TTS_PROVIDER == "faster_qwen3_tts":
    _primary_tts = FasterQwen3TTSProvider(
        model_repo=config.FASTER_QWEN_TTS_MODEL_REPO,
        language=config.FASTER_QWEN_TTS_LANGUAGE,
        speaker=config.FASTER_QWEN_TTS_SPEAKER,
        instruct=config.FASTER_QWEN_TTS_INSTRUCT,
        device=config.FASTER_QWEN_TTS_DEVICE,
        dtype=config.FASTER_QWEN_TTS_DTYPE,
        output_dir=config.TTS_OUTPUT_DIR,
        sample_rate=config.TTS_SAMPLE_RATE,
        use_chunking=config.FASTER_QWEN_TTS_USE_CHUNKING,
        strip_all_numbers=config.TTS_STRIP_ALL_NUMBERS,
        voice_profile_store=voice_profile_store,
        logger=base_logger,
    )
    _fallback_tts = _silero_tts
elif config.TTS_PROVIDER == "qwen3_tts":
    _primary_tts = Qwen3TTSProvider(
        model_repo=config.QWEN_TTS_MODEL_REPO,
        language=config.QWEN_TTS_LANGUAGE,
        speaker=config.QWEN_TTS_SPEAKER,
        instruct=config.QWEN_TTS_INSTRUCT,
        device=config.QWEN_TTS_DEVICE,
        output_dir=config.TTS_OUTPUT_DIR,
        sample_rate=config.TTS_SAMPLE_RATE,
        strip_all_numbers=config.TTS_STRIP_ALL_NUMBERS,
        voice_profile_store=voice_profile_store,
        logger=base_logger,
    )
    _fallback_tts = _silero_tts
elif config.TTS_PROVIDER == "qwen3_tts_ggml_vulkan":
    _primary_tts = QwenTTSGgmlVulkanProvider(
        server_url=config.QWEN_TTS_SERVER_URL,
        exe_path=config.QWEN_TTS_EXE,
        model_path=config.QWEN_TTS_MODEL_PATH,
        codec_path=config.QWEN_TTS_CODEC_PATH,
        default_language=config.QWEN_TTS_DEFAULT_LANGUAGE,
        default_speaker=config.QWEN_TTS_DEFAULT_SPEAKER,
        timeout=config.QWEN_TTS_TIMEOUT_SECONDS,
        output_dir=config.TTS_OUTPUT_DIR,
        auto_start=config.QWEN_TTS_AUTO_START,
        logger=base_logger,
    )
    _fallback_tts = _silero_tts
else:
    _primary_tts = _silero_tts
    _fallback_tts = None

if config.TTS_PROVIDER == "qwen3_tts_ggml_vulkan" and config.QWEN_TTS_KEEP_SERVER_WARM:
    # Явный выбор человека (config.QWEN_TTS_KEEP_SERVER_WARM=True) — поднять
    # tts-server.exe при старте backend'а, а не лениво на первый запрос.
    # Никогда не роняет запуск backend'а: если сервер не поднялся, синтез
    # просто откатится на Silero при первом реальном вызове (как обычно).
    try:
        _primary_tts.ensure_server_running()
    except Exception as exc:  # noqa: BLE001 — best-effort прогрев, не критично для старта backend'а
        base_logger.error(
            "tts_unavailable",
            console_message=f"[VOICE][TTS][qwen_ggml_vulkan] прогрев при старте не удался: {exc}",
            error=str(exc),
        )

voice_service = VoiceService(
    stt=WhisperSTTProvider(
        model_name=config.STT_MODEL,
        device=config.STT_DEVICE,
        compute_type=config.STT_COMPUTE_TYPE,
        download_root=config.STT_MODELS_DIR,
        logger=base_logger,
    ),
    tts=_primary_tts,
    fallback_tts=_fallback_tts,
    logger=base_logger,
)

# STT via whisper.cpp (Phase 1, HANDOFF_v2.md) — a separate, standalone
# service from voice_service.stt above (faster-whisper, still unavailable —
# the package isn't installed). Not wired into voice_service/VoiceService at
# all in this pass, and not used by any mic UI — only the new
# POST /api/voice/stt/transcribe endpoint below calls this directly.
whisper_cpp_stt_service = WhisperCppSTTProvider(
    exe_path=config.WHISPER_CPP_EXE_PATH,
    model_path=config.WHISPER_CPP_MODEL_PATH,
    timeout=config.WHISPER_CPP_TIMEOUT_SECONDS,
    beam_size=config.WHISPER_CPP_BEAM_SIZE,
    best_of=config.WHISPER_CPP_BEST_OF,
    use_vulkan=config.WHISPER_CPP_USE_VULKAN,
    cpu_fallback=config.WHISPER_CPP_CPU_FALLBACK,
    logger=base_logger,
)

# OCR (glm-ocr, Phase 4B) вЂ” same technical-service role as voice_service
# above: only turns image attachments into text, never decides what happens
# with it. Constructing this never loads a model or touches the network вЂ”
# just holds an ollama.Client + config (see ocr/glm_ocr_service.py).
ocr_service = GlmOcrService(
    host=config.OLLAMA_HOST,
    model=config.OCR_MODEL,
    timeout=config.OCR_TIMEOUT_SECONDS,
    logger=base_logger,
)

# Vision (qwen2.5vl) — separate technical service from ocr_service above: OCR
# reads text, this describes scene/objects. Never called unconditionally on
# every image attachment — only when core/image_intent.py detects the user
# actually asked what the image shows (see _run_image_vision).
vision_service = QwenVisionService(
    host=config.OLLAMA_HOST,
    model=config.IMAGE_UNDERSTANDING_MODEL,
    timeout=config.IMAGE_UNDERSTANDING_TIMEOUT_SECONDS,
    logger=base_logger,
)

# Translator (translategemma:4b, Phase 4C) вЂ” same technical-service role as
# ocr_service/voice_service above. Constructed once with the primary model;
# fallback to config.TRANSLATOR_FALLBACK_MODEL is an explicit per-call
# override (see _translate_text below), not a second service instance.
translator_service = TranslatorService(
    host=config.OLLAMA_HOST,
    model=config.TRANSLATOR_MODEL,
    timeout=config.TRANSLATOR_TIMEOUT_SECONDS,
    logger=base_logger,
)


def _latest_log_path() -> Path | None:
    paths = sorted(config.LOG_DIR.glob("siena_*.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return paths[-1] if paths else None


def _read_recent_jsonl(limit: int) -> list[dict[str, Any]]:
    path = _latest_log_path()
    if path is None or not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"event": "unparseable_log_line", "level": "ERROR", "raw": line})
    return records


def _ollama_status(host: str | None = None) -> dict[str, Any]:
    target = host or config.OLLAMA_HOST
    try:
        response = requests.get(f"{target}/api/tags", timeout=2)
        response.raise_for_status()
        models = [m.get("name") for m in response.json().get("models", [])]
        return {"connected": True, "models": [m for m in models if m]}
    except Exception as exc:
        return {"connected": False, "models": [], "error": str(exc)}


def _rebuild_ollama_client() -> None:
    """РџРµСЂРµСЃРѕР±РёСЂР°РµС‚ module-level ollama_client РёР· С‚РµРєСѓС‰РёС… config.* Р·РЅР°С‡РµРЅРёР№.

    РќСѓР¶РЅРѕ С‚РѕР»СЊРєРѕ РґР»СЏ СЌС‚РѕРіРѕ singleton'Р°: request_registry (Рё РµРіРѕ delegate-РєР»РёРµРЅС‚)
    Рё С‚Р°Рє РїРµСЂРµСЃРѕР±РёСЂР°РµС‚СЃСЏ Р·Р°РЅРѕРІРѕ РЅР° РєР°Р¶РґС‹Р№ /api/chat С‡РµСЂРµР· build_registry() вЂ”
    СЃР»РµРґСѓСЋС‰РёР№ Р·Р°РїСЂРѕСЃ РїРѕРґС…РІР°С‚РёС‚ РЅРѕРІС‹Рµ config.CODE_MODEL/DELEGATE_MODELS/
    DELEGATE_TIMEOUT_SECONDS Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё, Р±РµР· СЏРІРЅРѕР№ РїРµСЂРµСЃР±РѕСЂРєРё Р·РґРµСЃСЊ.
    """
    global ollama_client
    ollama_client = OllamaClient(
        host=config.OLLAMA_HOST,
        model=config.PRIMARY_MODEL,
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        think=config.OLLAMA_THINK,
        num_ctx=config.OLLAMA_NUM_CTX,
        num_predict=config.OLLAMA_NUM_PREDICT,
    )


def _settings_payload() -> dict[str, Any]:
    return {
        "primary_model": config.PRIMARY_MODEL,
        "code_model": config.CODE_MODEL,
        "ollama_host": config.OLLAMA_HOST,
        "max_iterations": config.MAX_ITERATIONS,
        "request_timeout_seconds": config.REQUEST_TIMEOUT_SECONDS,
        "delegate_timeout_seconds": config.DELEGATE_TIMEOUT_SECONDS,
        "num_ctx": config.OLLAMA_NUM_CTX,
        "num_predict": config.OLLAMA_NUM_PREDICT,
        "max_context_messages": config.MAX_CONTEXT_MESSAGES,
        "log_level": config.LOG_LEVEL,
        "enable_ocr": config.ENABLE_OCR,
        "enable_image_understanding": config.ENABLE_IMAGE_UNDERSTANDING,
        "enable_translator": config.ENABLE_TRANSLATOR,
        "enable_code_specialist_auto": config.ENABLE_CODE_SPECIALIST_AUTO,
        "enable_reviewer_explicit": config.ENABLE_REVIEWER_EXPLICIT,
        "stt_language": config.WHISPER_CPP_LANGUAGE,
        "appearance_theme": config.APPEARANCE_THEME,
        "accent_color": config.ACCENT_COLOR,
        "ui_font_size": config.UI_FONT_SIZE,
        "ui_density": config.UI_DENSITY,
        "show_message_timestamps": config.SHOW_MESSAGE_TIMESTAMPS,
        "show_typing_animation": config.SHOW_TYPING_ANIMATION,
        "copy_before_clear_chat": config.COPY_BEFORE_CLEAR_CHAT,
        "startup_page": config.STARTUP_PAGE,
        "code_font_size": config.CODE_FONT_SIZE,
        "code_line_wrap": config.CODE_LINE_WRAP,
        "code_syntax_highlighting": config.CODE_SYNTAX_HIGHLIGHTING,
        "code_show_line_numbers": config.CODE_SHOW_LINE_NUMBERS,
        "code_show_language_badge": config.CODE_SHOW_LANGUAGE_BADGE,
        "code_show_copy_button": config.CODE_SHOW_COPY_BUTTON,
        "code_show_collapse_button": config.CODE_SHOW_COLLAPSE_BUTTON,
        "code_show_save_button": config.CODE_SHOW_SAVE_BUTTON,
        "show_experimental_stream_button": config.SHOW_EXPERIMENTAL_STREAM_BUTTON,
        "preferred_response_language": config.PREFERRED_RESPONSE_LANGUAGE,
        "interface_language": config.INTERFACE_LANGUAGE,
    }


def _runtime_payload() -> dict[str, Any]:
    ollama = _ollama_status()
    tools = [{"name": name, "schema": schema} for name, schema in zip(registry.names(), registry.schemas())]
    latest_log = _latest_log_path()
    return {
        "primary_model": config.PRIMARY_MODEL,
        "code_model": config.CODE_MODEL,
        "delegate_models": config.DELEGATE_MODELS,
        "ollama_host": config.OLLAMA_HOST,
        "ollama_status": ollama,
        "registered_tools": tools,
        "max_iterations": config.MAX_ITERATIONS,
        "request_timeout_seconds": config.REQUEST_TIMEOUT_SECONDS,
        "delegate_timeout_seconds": config.DELEGATE_TIMEOUT_SECONDS,
        "memory_paths": {
            "short": str(config.SHORT_MEMORY_PATH),
            "long": str(config.LONG_MEMORY_DB_PATH),
        },
        "log_path": str(latest_log or config.LOG_DIR),
        "web_search_provider": "ddgs",
        "log_level": config.LOG_LEVEL,
        "max_context_messages": config.MAX_CONTEXT_MESSAGES,
        "num_ctx": config.OLLAMA_NUM_CTX,
        "num_predict": config.OLLAMA_NUM_PREDICT,
        "last_used_model": _last_used_model,
        "last_used_role": _last_used_role,
        "active_chat_model": _active_chat_model,
        # Settings unfreeze pass (HANDOFF_v2.md) — reflects the current live
        # value of feature flags toggled in Settings > Tools/Code, so the
        # Runtime view can honestly show what's actually enabled right now.
        "enable_ocr": config.ENABLE_OCR,
        "enable_image_understanding": config.ENABLE_IMAGE_UNDERSTANDING,
        "enable_translator": config.ENABLE_TRANSLATOR,
        "enable_code_specialist_auto": config.ENABLE_CODE_SPECIALIST_AUTO,
        "enable_reviewer_explicit": config.ENABLE_REVIEWER_EXPLICIT,
        **cpu_ram_metrics(),
        **vram_metrics(),
    }


_TEXT_ATTACHMENT_TYPES = {"text", "code", "markdown", "json", "log"}
_ATTACHMENT_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SAFE_EXT_RE = re.compile(r"^[a-z0-9]{1,12}$")
_IMAGE_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
}
_TEXT_TYPE_EXT = {
    "text": "txt",
    "code": "txt",
    "markdown": "md",
    "json": "json",
    "log": "log",
}


def _attachment_fence_lang(att: ChatAttachment) -> str:
    if att.lang:
        return att.lang
    if att.type in ("json", "markdown"):
        return att.type
    return "text"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _attachment_kind(att: ChatAttachment) -> str:
    if att.type == "image":
        return "image"
    if att.type in _TEXT_ATTACHMENT_TYPES:
        return "text"
    return "unknown"


def _safe_original_name(name: str) -> str:
    candidate = Path(name or "attachment").name.strip().replace("\x00", "")
    return candidate[:180] or "attachment"


def _safe_attachment_ext(att: ChatAttachment) -> str:
    original = _safe_original_name(att.name)
    ext = Path(original).suffix.lower().lstrip(".")
    if att.type == "image":
        mime_ext = _IMAGE_MIME_EXT.get((att.mime or "").lower())
        if mime_ext:
            return mime_ext
    elif att.type in _TEXT_TYPE_EXT:
        fallback = _TEXT_TYPE_EXT[att.type]
        if ext and _SAFE_EXT_RE.fullmatch(ext):
            return ext
        return fallback
    guessed = mimetypes.guess_extension(att.mime or "")
    if guessed:
        ext = guessed.lower().lstrip(".")
    if ext and _SAFE_EXT_RE.fullmatch(ext):
        return ext
    return "bin"


def _attachment_bytes(att: ChatAttachment) -> bytes:
    if att.type == "image" and att.data_url:
        return _decode_image_bytes(att.data_url)
    if att.type in _TEXT_ATTACHMENT_TYPES:
        return (att.content or "").encode("utf-8")
    return b""


def _resolve_attachment_path(stored_relative_path: str) -> Path:
    root = config.ATTACHMENTS_STORAGE_ROOT.resolve()
    candidate = (config.BASE_DIR / stored_relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise HTTPException(status_code=404, detail="attachment not found")
    return candidate


def _persist_uploaded_attachments(
    conversation_id: str,
    message_id: str,
    attachments: list[ChatAttachment],
) -> list[dict[str, Any]]:
    uploaded_root = config.ATTACHMENTS_STORAGE_ROOT / "uploaded"
    (config.ATTACHMENTS_STORAGE_ROOT / "generated").mkdir(parents=True, exist_ok=True)
    persisted: list[dict[str, Any]] = []
    for att in attachments:
        data = _attachment_bytes(att)
        attachment_id = str(uuid.uuid4())
        ext = _safe_attachment_ext(att)
        stored_filename = f"{attachment_id}.{ext}"
        target_dir = uploaded_root / conversation_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = (target_dir / stored_filename).resolve()
        expected_root = uploaded_root.resolve()
        if expected_root != target_path and expected_root not in target_path.parents:
            raise HTTPException(status_code=400, detail="invalid attachment path")
        target_path.write_bytes(data)
        relative_path = target_path.relative_to(config.BASE_DIR).as_posix()
        metadata = {
            "client_type": att.type,
            "lang": att.lang,
            "size_label": att.size,
        }
        row = {
            "id": attachment_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "kind": _attachment_kind(att),
            "source": "uploaded",
            "original_name": _safe_original_name(att.name),
            "stored_filename": stored_filename,
            "stored_relative_path": relative_path,
            "mime_type": att.mime or mimetypes.guess_type(att.name)[0] or "application/octet-stream",
            "size_bytes": len(data),
            "created_at": _now_iso(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "metadata": {k: v for k, v in metadata.items() if v is not None},
        }
        persisted.append(conversation_store.add_attachment(row))
    return persisted


def _public_attachment(att: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": att["id"],
        "kind": att["kind"],
        "source": att["source"],
        "original_name": att["original_name"],
        "stored_filename": att["stored_filename"],
        "stored_relative_path": att["stored_relative_path"],
        "mime_type": att["mime_type"],
        "size_bytes": att["size_bytes"],
        "created_at": att["created_at"],
        "sha256": att.get("sha256"),
        "url": f"/api/attachments/{att['id']}/content",
        "client_type": att.get("client_type"),
        "lang": att.get("lang"),
        "size_label": att.get("size_label"),
        "ocr_status": att.get("ocr_status"),
        "ocr_preview": att.get("ocr_preview"),
        "ocr_quality": att.get("ocr_quality"),
        "vision_status": att.get("vision_status"),
        "vision_preview": att.get("vision_preview"),
    }


def _persist_attachment_processing_results(
    persisted_attachments: list[dict[str, Any]],
    ocr_results: list[dict[str, Any]],
    vision_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name = {att["original_name"]: att for att in persisted_attachments}
    updated: list[dict[str, Any]] = []
    for result in ocr_results:
        att = by_name.get(result.get("name"))
        if not att:
            continue
        patch = {
            "ocr_status": result.get("status"),
            "ocr_preview": result.get("preview"),
            "ocr_quality": result.get("quality"),
        }
        patch = {k: v for k, v in patch.items() if v is not None}
        merged = conversation_store.merge_attachment_metadata(att["id"], patch)
        att.update(merged)
        updated.append(att)
    for result in vision_results:
        att = by_name.get(result.get("name"))
        if not att:
            continue
        patch = {
            "vision_status": result.get("status"),
            "vision_preview": result.get("preview"),
        }
        patch = {k: v for k, v in patch.items() if v is not None}
        merged = conversation_store.merge_attachment_metadata(att["id"], patch)
        att.update(merged)
        if att not in updated:
            updated.append(att)
    return persisted_attachments


def _translate_text(
    text: str,
    source_lang: str,
    target_lang: str,
    preserve_formatting: bool,
    logger: BroadcastLogger,
) -> dict[str, Any]:
    """Runs translation with primary (config.TRANSLATOR_MODEL) -> fallback
    (config.TRANSLATOR_FALLBACK_MODEL) logic. Never raises вЂ” returns
    {"ok": bool, ...} so callers (POST /api/translate, attachment/OCR
    context injection) can decide what to do on failure themselves without
    duplicating the fallback dance. Fallback is only attempted when the
    primary model isn't installed (TranslatorModelNotInstalledError) вЂ” a
    real call failure (timeout, bad response) fails immediately, same as OCR."""
    if not config.ENABLE_TRANSLATOR:
        return {"ok": False, "error": "Translator is disabled (ENABLE_TRANSLATOR=false)", "fallback_used": False, "duration_ms": 0}

    logger.event(
        "translator_started",
        source_lang=source_lang,
        target_lang=target_lang,
        chars=len(text),
        console_message=f"[TRANSLATOR] Р·Р°РїСѓСЃРє ({source_lang}->{target_lang}), {len(text)} СЃРёРјРІРѕР»РѕРІ",
    )
    start = time.monotonic()
    candidates = [(config.TRANSLATOR_MODEL, False), (config.TRANSLATOR_FALLBACK_MODEL, True)]
    last_error: Exception | None = None
    for model_name, is_fallback in candidates:
        if is_fallback:
            logger.event(
                "translator_fallback",
                primary=config.TRANSLATOR_MODEL,
                fallback=config.TRANSLATOR_FALLBACK_MODEL,
                reason=str(last_error),
                console_message=f"[TRANSLATOR] {config.TRANSLATOR_MODEL} РЅРµРґРѕСЃС‚СѓРїРЅР°, РїСЂРѕР±СѓСЋ {config.TRANSLATOR_FALLBACK_MODEL}",
            )
        try:
            result = translator_service.translate(
                text, source_lang=source_lang, target_lang=target_lang,
                preserve_formatting=preserve_formatting, model=model_name,
            )
        except TranslatorModelNotInstalledError as exc:
            last_error = exc
            continue
        except TranslatorCallFailedError as exc:
            duration_ms = round((time.monotonic() - start) * 1000)
            logger.error(
                "translator_failed",
                console_message=f"[TRANSLATOR] СЃР±РѕР№ РІС‹Р·РѕРІР° ({model_name}): {exc}",
                error=str(exc),
                provider=model_name,
                duration_ms=duration_ms,
            )
            return {"ok": False, "error": str(exc), "fallback_used": is_fallback, "duration_ms": duration_ms}

        duration_ms = round((time.monotonic() - start) * 1000)
        translated_text = result["translated_text"]
        if len(translated_text) > config.TRANSLATOR_MAX_OUTPUT_CHARS:
            translated_text = translated_text[: config.TRANSLATOR_MAX_OUTPUT_CHARS] + "\nвЂ¦(truncated)"
        logger.event(
            "translator_completed",
            provider=model_name,
            source_lang=source_lang,
            target_lang=target_lang,
            chars=len(translated_text),
            duration_ms=duration_ms,
            console_message=f"[TRANSLATOR] РіРѕС‚РѕРІРѕ Р·Р° {duration_ms}РјСЃ ({model_name})",
        )
        return {"ok": True, "provider": model_name, "translated_text": translated_text, "fallback_used": is_fallback, "duration_ms": duration_ms}

    duration_ms = round((time.monotonic() - start) * 1000)
    logger.error(
        "translator_failed",
        console_message=f"[TRANSLATOR] РЅРё РѕСЃРЅРѕРІРЅР°СЏ, РЅРё fallback РјРѕРґРµР»СЊ РЅРµРґРѕСЃС‚СѓРїРЅС‹: {last_error}",
        error=str(last_error),
        duration_ms=duration_ms,
    )
    return {"ok": False, "error": str(last_error), "fallback_used": True, "duration_ms": duration_ms}


def _build_attachment_context(attachments: list[ChatAttachment], logger: BroadcastLogger) -> str:
    """РЎС‚СЂРѕРёС‚ С‚РµРєСЃС‚РѕРІС‹Р№ Р±Р»РѕРє СЃ СЃРѕРґРµСЂР¶РёРјС‹Рј text/code/markdown/json/log
    attachments РґР»СЏ РјРѕРґРµР»Рё. РЇРІРЅРѕ РѕС‚РґРµР»РµРЅРѕ РѕС‚ С‚РµРєСЃС‚Р° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ вЂ” СЌС‚Рѕ РЅРµ
    С‡Р°СЃС‚СЊ message, Р° РѕС‚РґРµР»СЊРЅС‹Р№ Р±Р»РѕРє, РґРѕР±Р°РІР»СЏРµРјС‹Р№ С‚РѕР»СЊРєРѕ РІ С‚Рѕ, С‡С‚Рѕ РІРёРґРёС‚
    РјРѕРґРµР»СЊ (session.add_user), РЅРµ РІ persisted conversation_store СЃРѕРѕР±С‰РµРЅРёРµ
    (С‚Р°Рј РѕСЃС‚Р°С‘С‚СЃСЏ СЂРѕРІРЅРѕ С‚Рѕ, С‡С‚Рѕ РЅР°РїРµС‡Р°С‚Р°Р» РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ).

    РџРµСЂРµРІРѕРґ вЂ” С‚РѕР»СЊРєРѕ РµСЃР»Рё СЏРІРЅРѕ РїРѕРїСЂРѕСЃРёР»Рё (att.translate=True, Phase 4C).
    РЎР±РѕР№ РїРµСЂРµРІРѕРґР° РЅРµ С‚РµСЂСЏРµС‚ attachment вЂ” РїСЂРѕСЃС‚Рѕ РїРѕРєР°Р·С‹РІР°РµС‚ РѕСЂРёРіРёРЅР°Р»."""
    blocks = []
    for att in attachments:
        if att.type not in _TEXT_ATTACHMENT_TYPES or not att.content:
            continue
        content = att.content

        label_suffix = ""
        if att.translate:
            target = att.target_lang or config.TRANSLATOR_DEFAULT_TARGET
            translation = _translate_text(content, config.TRANSLATOR_DEFAULT_SOURCE, target, True, logger)
            if translation["ok"]:
                content = translation["translated_text"]
                label_suffix = f" | translated -> {target}"
                logger.event(
                    "translator_context_injected",
                    name=att.name,
                    target_lang=target,
                    chars=len(content),
                    console_message=f"[TRANSLATOR] РїРµСЂРµРІРѕРґ {att.name} РґРѕР±Р°РІР»РµРЅ РІ РєРѕРЅС‚РµРєСЃС‚",
                )
            else:
                label_suffix = " | translation failed, showing original"
        blocks.append(f"[{att.name} | {att.type} | {att.size or '?'}{label_suffix}]\n```{_attachment_fence_lang(att)}\n{content}\n```")
    if not blocks:
        return ""
    return "Attached files:\n" + "\n\n".join(blocks)


@app.get("/api/attachments/{attachment_id}")
async def get_attachment_metadata(attachment_id: str) -> dict[str, Any]:
    if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
        raise HTTPException(status_code=404, detail="attachment not found")
    attachment = conversation_store.get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    return _public_attachment(attachment)


@app.get("/api/attachments/{attachment_id}/content")
async def get_attachment_content(attachment_id: str) -> FileResponse:
    if not _ATTACHMENT_ID_RE.fullmatch(attachment_id):
        raise HTTPException(status_code=404, detail="attachment not found")
    attachment = conversation_store.get_attachment(attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    path = _resolve_attachment_path(attachment["stored_relative_path"])
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="attachment content not found")
    return FileResponse(
        path,
        media_type=attachment.get("mime_type") or "application/octet-stream",
        filename=attachment.get("original_name") or attachment["stored_filename"],
    )


def _image_base64_payload(data_url: str) -> str:
    """Strips an optional data:...;base64, prefix, returning the pure base64
    payload that both base64.b64decode() and the ollama client's `images`
    field expect."""
    return data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url


def _decode_image_bytes(data_url: str) -> bytes:
    return base64.b64decode(_image_base64_payload(data_url), validate=True)


_IMAGE_OCR_ONLY_DISCLAIMER = (
    "Note: the block above is OCR text extraction only (glm-ocr) — it is NOT a full "
    "visual analysis of the image. If a separate 'Attached image vision' block also "
    "appears below, that one is the actual visual description; otherwise, do not "
    "describe objects, people, colors, or scenes you did not read as text above."
)

_VISION_GROUNDING_NOTE = (
    "The block above is a real visual description from an image-understanding model "
    "(qwen2.5vl) — it describes what is actually shown in the image (scene, objects, "
    "colors, style). Base your answer about the image's visual content on this "
    "description. Do not contradict it or invent additional visual details it does not "
    "mention."
)

_VISION_UNAVAILABLE_NOTE = (
    "The user's message looks like a request to describe what is shown in the image "
    "(scene/object description), but no image-understanding/vision result is available "
    "for this turn (see the vision status above, if any). Do not guess or invent a "
    "visual description from the OCR text alone. Tell the user honestly that image "
    "understanding is unavailable or failed right now. You may still show them the OCR "
    "text extracted above, if any was found."
)


async def _run_image_ocr(
    attachments: list[ChatAttachment], user_text: str, logger: BroadcastLogger
) -> tuple[str, list[dict[str, Any]]]:
    """Runs glm-ocr on every image attachment that carries data_url.
    Sequential вЂ” images are rare (typically 0-1 per message) and each OCR
    call already runs off the event loop via asyncio.to_thread, so there's no
    need for concurrency here. Never raises: an unavailable/failed OCR call
    becomes a warning line in the returned context instead of breaking chat
    (per the Phase 4B spec вЂ” 'РЅРµ Р»РѕРјР°С‚СЊ chat').

    Returns (context_block, results) вЂ” results is per-image status
    ("extracted" | "failed" | "unavailable") so the frontend can reflect it
    on the attachment chip (OCR extracted / OCR failed / glm-ocr not installed).
    """
    if not config.ENABLE_OCR:
        return "", []

    blocks: list[str] = []
    results: list[dict[str, Any]] = []
    for att in attachments:
        if att.type != "image" or not att.data_url:
            continue

        logger.event(
            "ocr_started",
            name=att.name,
            mime=att.mime,
            console_message=f"[OCR] Р·Р°РїСѓСЃРє glm-ocr РґР»СЏ {att.name}",
        )
        try:
            payload = _image_base64_payload(att.data_url)
            result = await asyncio.to_thread(ocr_service.extract_text, payload)
        except OcrModelNotInstalledError as exc:
            logger.error(
                "ocr_failed",
                console_message=f"[OCR] РјРѕРґРµР»СЊ РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅР° РґР»СЏ {att.name}: {exc}",
                name=att.name,
                error=str(exc),
                reason="not_installed",
            )
            blocks.append(f"OCR failed for image: {att.name}")
            results.append({"name": att.name, "status": "unavailable", "error": str(exc)})
            continue
        except OcrUnavailableError as exc:
            logger.error(
                "ocr_failed",
                console_message=f"[OCR] СЃР±РѕР№ РґР»СЏ {att.name}: {exc}",
                name=att.name,
                error=str(exc),
                reason="call_failed",
            )
            blocks.append(f"OCR failed for image: {att.name}")
            results.append({"name": att.name, "status": "failed", "error": str(exc)})
            continue

        raw_text = result["text"]
        cleaned_text = clean_ocr_text(raw_text)
        quality = ocr_quality(raw_text, cleaned_text, config.OCR_MIN_USEFUL_CHARS)
        preview = cleaned_text[: config.OCR_PREVIEW_CHARS]
        extracted_text = cleaned_text
        status = "extracted"
        injected_chars = 0
        if quality["quality"] == "low_quality":
            status = "low_quality"
            warning = f"OCR returned no useful readable text for image: {att.name}"
            blocks.append(warning)
            injected_chars = len(warning)
            extracted_text = ""
        elif len(extracted_text) > config.OCR_MAX_EXTRACTED_CHARS:
            extracted_text = extracted_text[: config.OCR_MAX_EXTRACTED_CHARS] + "\n...(truncated)"
            injected_chars = len(extracted_text)
        else:
            injected_chars = len(extracted_text)

        logger.event(
            "ocr_completed",
            name=att.name,
            raw_chars=len(raw_text),
            cleaned_chars=len(cleaned_text),
            injected_chars=injected_chars,
            chars=len(cleaned_text),
            elapsed_sec=result["elapsed_sec"],
            quality=quality["quality"],
            preview=preview,
            useful_chars=quality["useful_chars"],
            raw_blank_ratio=quality["raw_blank_ratio"],
            repeat_ratio=quality["repeat_ratio"],
            console_message=f"[OCR] {att.name} РіРѕС‚РѕРІРѕ Р·Р° {result['elapsed_sec']}СЃ ({len(result['text'])} СЃРёРјРІРѕР»РѕРІ)",
        )
        if quality["quality"] == "low_quality":
            logger.event(
                "ocr_low_quality",
                name=att.name,
                raw_chars=len(raw_text),
                cleaned_chars=len(cleaned_text),
                preview=preview,
                useful_chars=quality["useful_chars"],
                raw_blank_ratio=quality["raw_blank_ratio"],
                repeat_ratio=quality["repeat_ratio"],
                console_message=f"[OCR] low quality result for {att.name}",
            )
        results.append({
            "name": att.name,
            "status": status,
            "chars": len(cleaned_text),
            "preview": preview,
            "quality": quality["quality"],
        })
        if quality["quality"] == "low_quality":
            continue

        label_suffix = ""
        if att.translate and extracted_text:
            # Explicit opt-in only (Phase 4C) вЂ” translate the OCR result
            # itself before it reaches the model's context. Failure keeps the
            # original (untranslated) OCR text rather than dropping it.
            target = att.target_lang or config.TRANSLATOR_DEFAULT_TARGET
            translation = await asyncio.to_thread(
                _translate_text, extracted_text, config.TRANSLATOR_DEFAULT_SOURCE, target, True, logger,
            )
            if translation["ok"]:
                extracted_text = translation["translated_text"]
                label_suffix = f" | translated -> {target}"
                logger.event(
                    "translator_context_injected",
                    name=att.name,
                    target_lang=target,
                    chars=len(extracted_text),
                    console_message=f"[TRANSLATOR] РїРµСЂРµРІРѕРґ OCR {att.name} РґРѕР±Р°РІР»РµРЅ РІ РєРѕРЅС‚РµРєСЃС‚",
                )
            else:
                label_suffix = " | translation failed, showing original OCR text"

        if extracted_text:
            blocks.append(f"[{att.name} | {att.mime or 'image'} | {att.size or '?'}{label_suffix}]\n```text\n{extracted_text}\n```")

    if not blocks:
        return "", results

    context = "Attached image OCR:\n" + "\n\n".join(blocks) + f"\n\n{_IMAGE_OCR_ONLY_DISCLAIMER}"
    return context, results


async def _run_image_vision(
    attachments: list[ChatAttachment], user_text: str, logger: BroadcastLogger
) -> tuple[str, list[dict[str, Any]]]:
    """Runs qwen2.5vl on every image attachment, but ONLY when
    `core.image_intent.decide_vision()` says this turn asks for a
    scene/object description — see that module for the full precedence
    rules (OCR-only never triggers vision; both-explicit and the
    ambiguous-short-question-with-an-image fallback both do). Vision is
    deliberately never invoked just because an image is attached: it must be
    intent-gated so Siena doesn't pay the extra inference cost/latency, and
    never silently substitutes a guess, for plain OCR-only or captionless
    attachments.

    Never raises: an unavailable/failed vision call becomes a warning line in
    the returned context instead of breaking chat, same discipline as
    _run_image_ocr.

    Returns (context_block, results) — results is per-image status
    ("described" | "failed" | "unavailable") so the frontend can reflect it
    on the attachment chip.
    """
    if not config.ENABLE_IMAGE_UNDERSTANDING:
        return "", []

    image_attachments = [att for att in attachments if att.type == "image" and att.data_url]
    if not image_attachments:
        return "", []

    decision = decide_vision(user_text, True)
    if not decision.run_vision:
        return "", []

    logger.event(
        "vision_intent_detected",
        text=user_text[:200],
        reason=decision.reason,
        console_message=f"[VISION] intent detected ({decision.reason}) — calling qwen2.5vl",
    )

    blocks: list[str] = []
    results: list[dict[str, Any]] = []
    for att in image_attachments:
        logger.event(
            "vision_started",
            name=att.name,
            mime=att.mime,
            console_message=f"[VISION] starting qwen2.5vl for {att.name}",
        )
        try:
            payload = _image_base64_payload(att.data_url)
            result = await asyncio.to_thread(vision_service.describe_image, payload, user_text)
        except VisionModelNotInstalledError as exc:
            logger.error(
                "vision_failed",
                console_message=f"[VISION] model not installed for {att.name}: {exc}",
                name=att.name,
                error=str(exc),
                reason="not_installed",
            )
            blocks.append(f"Vision failed for image: {att.name} (model not installed)")
            results.append({"name": att.name, "status": "unavailable", "error": str(exc)})
            continue
        except VisionUnavailableError as exc:
            logger.error(
                "vision_failed",
                console_message=f"[VISION] call failed for {att.name}: {exc}",
                name=att.name,
                error=str(exc),
                reason="call_failed",
            )
            blocks.append(f"Vision failed for image: {att.name}")
            results.append({"name": att.name, "status": "failed", "error": str(exc)})
            continue

        description = result["text"]
        if len(description) > config.IMAGE_UNDERSTANDING_MAX_OUTPUT_CHARS:
            description = description[: config.IMAGE_UNDERSTANDING_MAX_OUTPUT_CHARS] + "\n...(truncated)"

        logger.event(
            "vision_completed",
            name=att.name,
            chars=len(description),
            elapsed_sec=result["elapsed_sec"],
            preview=description[:300],
            console_message=f"[VISION] {att.name} done in {result['elapsed_sec']}s ({len(description)} chars)",
        )
        results.append({"name": att.name, "status": "described", "chars": len(description), "preview": description[:300]})

        if description:
            blocks.append(f"[{att.name} | {att.mime or 'image'} | {att.size or '?'}]\n{description}")

    if not blocks:
        return "", results

    context = "Attached image vision (qwen2.5vl):\n" + "\n\n".join(blocks) + f"\n\n{_VISION_GROUNDING_NOTE}"
    return context, results


@app.post("/api/chat")
async def chat(request: ChatRequest) -> dict[str, Any]:
    text = request.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="message is required")
    if len(text) > config.CHAT_INPUT_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"message exceeds {config.CHAT_INPUT_MAX_CHARS} characters",
        )

    attachments = request.attachments
    if len(attachments) > config.MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(
            status_code=400,
            detail=f"too many attachments (max {config.MAX_ATTACHMENTS_PER_MESSAGE})",
        )
    total_attachment_chars = 0
    for att in attachments:
        if att.type in _TEXT_ATTACHMENT_TYPES:
            content_len = len(att.content or "")
            if content_len > config.MAX_ATTACHMENT_TEXT_CHARS:
                raise HTTPException(
                    status_code=400,
                    detail=f"attachment {att.name!r} exceeds {config.MAX_ATTACHMENT_TEXT_CHARS} characters",
                )
            total_attachment_chars += content_len
        elif att.type == "image" and att.data_url:
            # Р Р°Р·СЂРµС€РµРЅС‹ С‚РѕР»СЊРєРѕ image mime types, Рё С‚РѕР»СЊРєРѕ РѕРіСЂР°РЅРёС‡РµРЅРЅС‹Р№ decoded-СЂР°Р·РјРµСЂ
            # вЂ” base64 РІ РёСЃС‚РѕСЂРёРё/Р»РѕРіР°С… РЅРµ СЃРѕС…СЂР°РЅСЏРµС‚СЃСЏ, С‚РѕР»СЊРєРѕ РІСЂРµРјРµРЅРЅРѕ РґРµРєРѕРґРёСЂСѓРµС‚СЃСЏ
            # Р·РґРµСЃСЊ РґР»СЏ РїСЂРѕРІРµСЂРєРё СЂР°Р·РјРµСЂР° Рё РїРµСЂРµРґР°С‡Рё РІ glm-ocr (СЃРј. _run_image_ocr).
            if not (att.mime or "").startswith("image/"):
                raise HTTPException(
                    status_code=400,
                    detail=f"attachment {att.name!r} has non-image mime type {att.mime!r}",
                )
            try:
                image_bytes = _decode_image_bytes(att.data_url)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"attachment {att.name!r} has invalid image data: {exc}",
                ) from exc
            if len(image_bytes) > config.MAX_IMAGE_ATTACHMENT_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail=f"image {att.name!r} exceeds {config.MAX_IMAGE_ATTACHMENT_BYTES} bytes",
                )
    if total_attachment_chars > config.MAX_TOTAL_ATTACHMENT_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"total attachment text exceeds {config.MAX_TOTAL_ATTACHMENT_TEXT_CHARS} characters",
        )

    if chat_lock.locked():
        raise HTTPException(status_code=409, detail="chat generation already in progress")

    async with chat_lock:
        loop = asyncio.get_running_loop()
        conversation_id = request.conversation_id or session_store.current_id
        if conversation_id is None:
            raise HTTPException(status_code=409, detail="no active conversation; create or select a conversation first")
        if conversation_store.get_conversation(conversation_id, events_limit=1) is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        logger = BroadcastLogger(base_logger, trace_hub, loop, conversation_store, conversation_id)
        session = session_store.current() if conversation_id == session_store.current_id else session_store.build_session(conversation_id)

        # Conversation History вЂ” С‚РµС…РЅРёС‡РµСЃРєРёР№ Р¶СѓСЂРЅР°Р», РїРёС€РµС‚СЃСЏ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё,
        # СЌС‚Рѕ РЅРµ long_memory_save Рё РЅРµ СЂРµС€РµРЅРёРµ РјРѕРґРµР»Рё (СЃРј. DONEARCHITECTURE.md).
        # РҐСЂР°РЅРёС‚СЃСЏ СЂРѕРІРЅРѕ С‚Рѕ, С‡С‚Рѕ РЅР°РїРµС‡Р°С‚Р°Р» РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ вЂ” Р±РµР· СЃРѕРґРµСЂР¶РёРјРѕРіРѕ
        # attachments (СЃРј. _build_attachment_context) вЂ” С‡С‚РѕР±С‹ UI-РёСЃС‚РѕСЂРёСЏ РЅРµ
        # СЂР°Р·РґСѓРІР°Р»Р°СЃСЊ РІСЃС‚Р°РІР»РµРЅРЅС‹РјРё С„Р°Р№Р»Р°РјРё.
        user_message = conversation_store.append_message(
            conversation_id,
            "user",
            text,
            metadata={"status": "processing", "updated_at": _now_iso()},
        )
        persisted_attachments = _persist_uploaded_attachments(conversation_id, user_message["id"], attachments)

        # Wrapped in to_thread: _build_attachment_context is normally cheap,
        # but with att.translate=True it makes a blocking Ollama call
        # (_translate_text) and must not block the event loop.
        attachment_context = await asyncio.to_thread(_build_attachment_context, attachments, logger)
        image_ocr_context, ocr_results = await _run_image_ocr(attachments, text, logger)
        image_vision_context, vision_results = await _run_image_vision(attachments, text, logger)
        persisted_attachments = _persist_attachment_processing_results(persisted_attachments, ocr_results, vision_results)
        conversation_store.merge_message_metadata(
            user_message["id"],
            {
                "status": "processing",
                "updated_at": _now_iso(),
                "ocr_results": ocr_results,
                "vision_results": vision_results,
            },
        )

        # Honest fallback: the user clearly asked what the image shows, but
        # _run_image_vision didn't produce a usable description this turn
        # (disabled, model not installed, or the call itself failed — see
        # vision_results for which). Without this note the model would only
        # see OCR text and could be tempted to guess a visual description
        # from it.
        vision_unavailable_note = ""
        has_image_attachment = any(a.type == "image" and a.data_url for a in attachments)
        vision_was_requested = decide_vision(text, has_image_attachment).run_vision
        if vision_was_requested and not image_vision_context:
            vision_unavailable_note = _VISION_UNAVAILABLE_NOTE

        memory_intent_note = ""
        if wants_long_memory_save(text):
            logger.event(
                "memory_save_intent_detected",
                text=text,
                console_message="[MEMORY] похоже на явную просьбу сохранить в долговременную память",
            )
            memory_intent_note = (
                "Похоже, пользователь явно просит сохранить что-то в долговременную память "
                "(слова вроде «запомни», «сохрани», «добавь в память», «добавь что...», «запиши»). "
                "Если это так — вызови long_memory_save с этим фактом, а не отвечай обычным текстом "
                "без сохранения."
            )

        research_intent_note = ""
        if wants_grounded_research(text):
            logger.event(
                "research_grounding_intent_detected",
                text=text,
                console_message="[RESEARCH] похоже на вопрос об идентичности/статусе, требующий проверки через web_search",
            )
            research_intent_note = (
                "Похоже, пользователь спрашивает, кто/что это такое, или что произошло/происходит "
                "с конкретным человеком, организацией или темой (возможно с указанием диапазона "
                "лет). Твои внутренние знания могут быть устаревшими или неточными для подобных "
                "вопросов, особенно если тема связана с политикой, конфликтами, военными "
                "организациями или публичными фигурами. Прежде чем отвечать — вызови web_search, "
                "даже если тебе кажется, что ты уже знаешь ответ. Не отвечай по памяти без проверки "
                "для такого рода вопросов, и не утверждай, что использовала данные поиска, если "
                "web_search не была вызвана."
            )

        nucleares_context = ""
        if wants_nucleares_context(text):
            logger.event(
                "nucleares_context_injection_requested",
                text=text[:200],
                console_message="[NUCLEARES] user asked for game telemetry context",
            )
            try:
                nucleares_status_result = await asyncio.to_thread(nucleares_client.status)
            except Exception as exc:
                nucleares_status_result = {"game": "nucleares", "connected": False, "error": str(exc), "attempted": []}
            nucleares_context = build_nucleares_context(nucleares_status_result)
            if nucleares_status_result.get("connected"):
                logger.event(
                    "nucleares_context_injected",
                    base_url=nucleares_status_result.get("base_url"),
                    parameter_count=nucleares_status_result.get("parameter_count"),
                    chars=len(nucleares_context),
                    normalized_keys=list(nucleares_status_result.get("normalized", {}).keys()),
                    console_message=f"[NUCLEARES] game telemetry context added ({len(nucleares_context)} chars)",
                )
            else:
                logger.event(
                    "nucleares_context_unavailable",
                    error=nucleares_status_result.get("error"),
                    chars=len(nucleares_context),
                    console_message=f"[NUCLEARES] unavailable: {nucleares_status_result.get('error')}",
                )
        else:
            nucleares_skip_reason = nucleares_context_skip_reason(text)
            if nucleares_skip_reason:
                logger.event(
                    "nucleares_context_skipped",
                    reason=nucleares_skip_reason,
                    text=text[:200],
                    console_message=f"[NUCLEARES] context skipped: {nucleares_skip_reason}",
                )

        language_preference_note = _LANGUAGE_PREFERENCE_NOTES.get(config.PREFERRED_RESPONSE_LANGUAGE)
        combined_context = "\n\n".join(
            b for b in (
                attachment_context, image_ocr_context, image_vision_context,
                vision_unavailable_note, memory_intent_note, research_intent_note,
                nucleares_context, language_preference_note,
            ) if b
        )
        model_input = f"{text}\n\n{combined_context}" if combined_context and text else (combined_context or text)
        session.add_user(model_input)
        logger.event("user_message", content=text, conversation_id=conversation_id, attachment_count=len(attachments))

        if attachments:
            logger.event(
                "attachment_send",
                count=len(attachments),
                types=[a.type for a in attachments],
                names=[a.name for a in attachments],
                console_message=f"[ATTACHMENT] РѕС‚РїСЂР°РІР»РµРЅРѕ {len(attachments)} РІР»РѕР¶РµРЅРёР№: {[a.type for a in attachments]}",
            )
        if attachment_context:
            logger.event(
                "attachment_context_injected",
                count=sum(1 for a in attachments if a.type in _TEXT_ATTACHMENT_TYPES and a.content),
                chars=len(attachment_context),
                console_message=f"[ATTACHMENT] РєРѕРЅС‚РµРєСЃС‚ С„Р°Р№Р»РѕРІ РґРѕР±Р°РІР»РµРЅ РІ prompt ({len(attachment_context)} СЃРёРјРІРѕР»РѕРІ)",
            )
        if image_ocr_context:
            logger.event(
                "ocr_context_injected",
                count=sum(1 for a in attachments if a.type == "image" and a.data_url),
                chars=len(image_ocr_context),
                console_message=f"[OCR] РєРѕРЅС‚РµРєСЃС‚ РёР·РѕР±СЂР°Р¶РµРЅРёР№ РґРѕР±Р°РІР»РµРЅ РІ prompt ({len(image_ocr_context)} СЃРёРјРІРѕР»РѕРІ)",
            )
        if image_vision_context:
            logger.event(
                "vision_context_injected",
                count=sum(1 for a in attachments if a.type == "image" and a.data_url),
                chars=len(image_vision_context),
                console_message=f"[VISION] context added to prompt ({len(image_vision_context)} chars)",
            )
        elif vision_unavailable_note:
            logger.event(
                "image_understanding_unavailable",
                requested_text=text[:200],
                console_message="[VISION] user asked to describe the image, but no vision result is available this turn",
            )

        request_registry, _, _, _ = build_registry(logger)

        # Specialist routing (Phase 4D) + manual active chat model (Phase 4E)
        # — decided once, up front, from the user's own text, the current
        # _active_chat_model, and (image/code routing pass, HANDOFF_v2.md)
        # whether this turn already has independent code context: an
        # attached code/text file, or OCR text from an attached screenshot
        # that itself looks code/error-shaped. That context only ever widens
        # matching to the narrow _AMBIGUOUS_CODE_PATTERNS set (e.g. "что за
        # ошибка" said about an attached error screenshot) — a plain code
        # request routes correctly with no attachment at all regardless.
        # config.MANUAL_HEAVY_MODEL can ONLY reach this call via
        # _active_chat_model, which is itself only ever set by the validated
        # POST /api/models/active handler below — never inferred automatically.
        has_code_context = any(att.type == "code" for att in attachments) or (
            bool(image_ocr_context) and model_router.looks_like_code_or_error(image_ocr_context)
        )
        decision = model_router.route(text, active_chat_model=_active_chat_model, has_code_context=has_code_context)
        logger.event(
            "model_route_decision",
            model=decision.model,
            role=decision.role,
            mode=decision.mode,
            reason=decision.reason,
            is_specialist=decision.is_specialist,
            console_message=f"[ROUTER] {decision.role} ({decision.model}) вЂ” {decision.reason}",
        )
        turn_client = ollama_client if decision.model == config.PRIMARY_MODEL else _build_routed_client(decision.model)
        if decision.is_specialist:
            logger.event(
                "model_specialist_started",
                model=decision.model,
                role=decision.role,
                console_message=f"[ROUTER] Р·Р°РїСѓСЃРє СЃРїРµС†РёР°Р»РёСЃС‚Р° {decision.role} ({decision.model})",
            )
        specialist_start = time.monotonic()

        try:
            answer = await asyncio.to_thread(
                run_agent_loop,
                session=session,
                ollama_client=turn_client,
                registry=request_registry,
                logger=logger,
                max_iterations=config.MAX_ITERATIONS,
                max_context_messages=config.MAX_CONTEXT_MESSAGES,
            )
        except SienaInfraError as exc:
            conversation_store.merge_message_metadata(
                user_message["id"],
                {"status": "failed", "error": str(exc), "updated_at": _now_iso()},
            )
            logger.error("infra_error", console_message=f"[infra_error] {exc}", error=str(exc))
            if decision.is_specialist:
                logger.error(
                    "model_specialist_failed",
                    console_message=f"[ROUTER] СЃРїРµС†РёР°Р»РёСЃС‚ {decision.role} СѓРїР°Р»: {exc}",
                    model=decision.model,
                    role=decision.role,
                    error=str(exc),
                )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except MaxIterationsReached as exc:
            conversation_store.merge_message_metadata(
                user_message["id"],
                {"status": "failed", "error": str(exc), "updated_at": _now_iso()},
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            conversation_store.merge_message_metadata(
                user_message["id"],
                {"status": "failed", "error": str(exc), "updated_at": _now_iso()},
            )
            raise

        if decision.is_specialist:
            logger.event(
                "model_specialist_completed",
                model=decision.model,
                role=decision.role,
                duration_ms=round((time.monotonic() - specialist_start) * 1000),
                console_message=f"[ROUTER] СЃРїРµС†РёР°Р»РёСЃС‚ {decision.role} Р·Р°РІРµСЂС€РёР» С…РѕРґ",
            )

        global _last_used_model, _last_used_role
        _last_used_model, _last_used_role = decision.model, decision.role

        logger.event("final_answer", content=answer, conversation_id=conversation_id)
        assistant_message = conversation_store.append_message(conversation_id, "assistant", answer, model=decision.model)
        conversation_store.merge_message_metadata(
            user_message["id"],
            {
                "status": "completed",
                "error": None,
                "assistant_message_id": assistant_message["id"],
                "updated_at": _now_iso(),
                "ocr_results": ocr_results,
                "vision_results": vision_results,
            },
        )
        return {
            "answer": answer,
            "conversation_id": conversation_id,
            "message_id": user_message["id"],
            "assistant_message_id": assistant_message["id"],
            "attachments": [_public_attachment(a) for a in persisted_attachments],
            "ocr_results": ocr_results,
            "vision_results": vision_results,
            "model_used": decision.model,
            "model_role": decision.role,
            "routing_reason": decision.reason,
            "routing_mode": decision.mode,
            "manual_only": decision.mode == "manual_active_chat_model",
        }


@app.post("/api/translate")
async def translate_text(request: TranslateRequest) -> dict[str, Any]:
    """Explicit, standalone translation вЂ” never called automatically by
    /api/chat (Phase 4C spec: 'РЅРµ Р»РѕРјР°С‚СЊ РѕР±С‹С‡РЅС‹Р№ РґРёР°Р»РѕРі'). Used by the Chat
    UI's per-message Translate button, and by any future explicit
    translate=true attachment flow (see _translate_text, shared with the
    attachment/OCR context-injection path above)."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > config.TRANSLATOR_MAX_INPUT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"text exceeds {config.TRANSLATOR_MAX_INPUT_CHARS} characters",
        )
    if not config.ENABLE_TRANSLATOR:
        raise HTTPException(status_code=503, detail="Translator is disabled (ENABLE_TRANSLATOR=false)")

    loop = asyncio.get_running_loop()
    # No conversation_id вЂ” this call isn't tied to a specific chat turn, but
    # still gets a BroadcastLogger so translator_* events reach /ws/trace live
    # (conversation_store=None means _persist() is a no-op, see BroadcastLogger).
    logger = BroadcastLogger(base_logger, trace_hub, loop)
    result = await asyncio.to_thread(
        _translate_text, text, request.source_lang, request.target_lang, request.preserve_formatting, logger,
    )
    if not result["ok"]:
        raise HTTPException(status_code=503, detail=result["error"])

    return {
        "ok": True,
        "provider": result["provider"],
        "source_lang": request.source_lang,
        "target_lang": request.target_lang,
        "translated_text": result["translated_text"],
        "duration_ms": result["duration_ms"],
        "fallback_used": result["fallback_used"],
    }


@app.get("/api/conversations")
async def list_conversations(
    limit: int = Query(default=config.CONVERSATION_LIST_DEFAULT_LIMIT, ge=1, le=200),
) -> dict[str, Any]:
    return {
        "conversations": conversation_store.list_conversations(limit),
        "active_conversation_id": session_store.current_id,
    }


@app.post("/api/conversations")
async def create_conversation(payload: ConversationCreateRequest | None = None) -> dict[str, Any]:
    """РЇРІРЅРѕРµ РґРµР№СЃС‚РІРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ (РєРЅРѕРїРєР° "New Chat") вЂ” СЃРѕР·РґР°С‘С‚ РЅРѕРІС‹Р№ СЂР°Р·РіРѕРІРѕСЂ
    РІ ConversationStore Рё РґРµР»Р°РµС‚ РµРіРѕ С‚РµРєСѓС‰РёРј. РЎС‚Р°СЂС‹Рµ СЂР°Р·РіРѕРІРѕСЂС‹ РЅРёРєСѓРґР° РЅРµ
    РґРµРІР°СЋС‚СЃСЏ вЂ” РѕРЅРё СѓР¶Рµ РІ SQLite, Р° РЅРµ С‚РѕР»СЊРєРѕ РІ РїР°РјСЏС‚Рё РїСЂРѕС†РµСЃСЃР°.
    """
    conversation_id = session_store.new_conversation(title=payload.title if payload else None)
    base_logger.event(
        "conversation_new",
        conversation_id=conversation_id,
        console_message=f"[CONVERSATION] РЅРѕРІС‹Р№ С‡Р°С‚ СЃРѕР·РґР°РЅ: {conversation_id}",
    )
    await trace_hub.broadcast({"event": "conversation_new", "conversation_id": conversation_id})
    return {"conversation_id": conversation_id}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    conversation = conversation_store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/activate")
async def activate_conversation(conversation_id: str) -> dict[str, Any]:
    """Р”РµР»Р°РµС‚ conversation_id С‚РµРєСѓС‰РёРј Рё РІРѕСЃСЃС‚Р°РЅР°РІР»РёРІР°РµС‚ Session РёР· СЃРѕС…СЂР°РЅС‘РЅРЅРѕР№
    РёСЃС‚РѕСЂРёРё (С‚РѕР»СЊРєРѕ user/assistant СЃРѕРѕР±С‰РµРЅРёСЏ вЂ” СЂР°Р·РґРµР» "Р’РѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёРµ Session").
    """
    try:
        session_store.activate(conversation_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="conversation not found")

    base_logger.event(
        "conversation_activated",
        conversation_id=conversation_id,
        console_message=f"[CONVERSATION] Р°РєС‚РёРІРёСЂРѕРІР°РЅ: {conversation_id}",
    )
    await trace_hub.broadcast({"event": "conversation_activated", "conversation_id": conversation_id})
    return {"conversation_id": conversation_id, "message_count": len(session_store.current().get_messages())}


@app.patch("/api/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, payload: ConversationRenameRequest) -> dict[str, Any]:
    if conversation_store.get_conversation(conversation_id, events_limit=1) is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    title = payload.title.strip() or "New Chat"
    conversation_store.update_title(conversation_id, title)
    return {"conversation_id": conversation_id, "title": title}


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, Any]:
    if conversation_store.get_conversation(conversation_id, events_limit=1) is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    was_active = session_store.current_id == conversation_id
    conversation_store.delete_conversation(conversation_id)
    if was_active:
        session_store.clear_active()

    return {"deleted": conversation_id, "active_conversation_id": session_store.current_id}


@app.post("/api/session/new")
async def new_session() -> dict[str, Any]:
    """РћСЃС‚Р°РІР»РµРЅРѕ РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё вЂ” С‚РµРїРµСЂСЊ РїСЂРѕСЃС‚Рѕ alias
    POST /api/conversations (СЃРј. SessionStore.new_session)."""
    session_id = session_store.new_session()
    base_logger.event(
        "session_new",
        session_id=session_id,
        console_message=f"[SESSION] РЅРѕРІР°СЏ СЃРµСЃСЃРёСЏ СЃРѕР·РґР°РЅР°: {session_id}",
    )
    await trace_hub.broadcast({"event": "session_new", "session_id": session_id})
    return {"session_id": session_id}


@app.get("/api/session/current")
async def current_session() -> dict[str, Any]:
    """РћСЃС‚Р°РІР»РµРЅРѕ РґР»СЏ РѕР±СЂР°С‚РЅРѕР№ СЃРѕРІРјРµСЃС‚РёРјРѕСЃС‚Рё вЂ” session_id С‚РµРїРµСЂСЊ СЂР°РІРµРЅ
    conversation_id С‚РµРєСѓС‰РµРіРѕ Р°РєС‚РёРІРЅРѕРіРѕ СЂР°Р·РіРѕРІРѕСЂР°."""
    if session_store.current_id is None:
        return {"session_id": None, "message_count": 0}
    session = session_store.current()
    return {
        "session_id": session_store.current_id,
        "message_count": len(session.get_messages()),
    }


_LOG_LEVELS = {"debug", "info", "warn", "error"}
# Mirrors the small set whisper.cpp/miniaudio actually understands here —
# "auto" lets whisper.cpp's own language-detection decide.
_STT_LANGUAGES = {"auto", "ru", "en"}

# Settings Pass 2 — pure UI/display preference enums (no backend behavior,
# validated the same honest way as everything else: unknown values are a 400,
# never silently coerced to a default).
_APPEARANCE_THEMES = {"dark", "light", "system"}
_ACCENT_COLORS = {"sienna", "slate", "forest", "amber", "violet"}
_UI_FONT_SIZES = {"small", "default", "large"}
_UI_DENSITIES = {"comfortable", "compact"}
_STARTUP_PAGES = {"chat", "runtime", "settings"}
_CODE_FONT_SIZES = {"small", "default", "large"}
# Settings Pass 3 — soft chat-prompt language preference. "auto" (default)
# injects nothing at all, preserving today's behavior exactly.
_PREFERRED_RESPONSE_LANGUAGES = {"auto", "ru", "en"}

# Injected verbatim into combined_context (see chat()) only when
# config.PREFERRED_RESPONSE_LANGUAGE != "auto". Deliberately phrased as a
# soft preference, not an instruction to translate or a hard override — it
# must never fight an explicit user request in another language, code, or
# Siena's own natural Russian conversation behavior.
_LANGUAGE_PREFERENCE_NOTES = {
    "ru": (
        "[Мягкое пожелание пользователя: по возможности отвечай на русском. "
        "Это предпочтение, а не жёсткое правило — не в ущерб ясности, коду "
        "или явным просьбам ответить на другом языке.]"
    ),
    "en": (
        "[Soft user preference: reply in English when reasonably possible. "
        "This is a preference, not a hard rule — don't let it override "
        "clarity, code, or an explicit request to use another language.]"
    ),
}

# Real UI localization pass — must match the locale files registered in
# "Siena v2 Control Panel UI/src/i18n/index.ts". This is the application UI
# language only — completely separate from _STT_LANGUAGES/
# _PREFERRED_RESPONSE_LANGUAGES above (voice input / soft model-reply
# preference).
_INTERFACE_LANGUAGES = {"en", "ru"}


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return _settings_payload()


@app.post("/api/settings")
async def update_settings(update: SettingsUpdate) -> dict[str, Any]:
    """Р РµР°Р»СЊРЅРѕ РїСЂРёРјРµРЅСЏРµС‚ РЅР°СЃС‚СЂРѕР№РєРё Рє СЂР°Р±РѕС‚Р°СЋС‰РµРјСѓ РїСЂРѕС†РµСЃСЃСѓ вЂ” РЅРµ С‚РѕР»СЊРєРѕ СЃРѕС…СЂР°РЅСЏРµС‚.

    Runtime Р·РґРµСЃСЊ РЅРµ СЂРµС€Р°РµС‚, Р§РўРћ СЃС‚РѕРёС‚ РјРµРЅСЏС‚СЊ вЂ” РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃР°Рј РїСЂРёСЃР»Р°Р»
    РєРѕРЅРєСЂРµС‚РЅС‹Рµ Р·РЅР°С‡РµРЅРёСЏ С‡РµСЂРµР· Settings UI. Р•РґРёРЅСЃС‚РІРµРЅРЅРѕРµ, С‡С‚Рѕ РґРµР»Р°РµС‚ СЌС‚РѕС‚
    СЌРЅРґРїРѕРёРЅС‚ СЃР°Рј вЂ” С‚РµС…РЅРёС‡РµСЃРєР°СЏ РІР°Р»РёРґР°С†РёСЏ (С„РѕСЂРјР°С‚/РґРёР°РїР°Р·РѕРЅ/СЃСѓС‰РµСЃС‚РІРѕРІР°РЅРёРµ
    РјРѕРґРµР»Рё РІ Ollama), РѕРЅР° С‚РѕРіРѕ Р¶Рµ СЂРѕРґР°, С‡С‚Рѕ Рё presence/type-РїСЂРѕРІРµСЂРєРё РІ
    tools/registry.py (ARCHITECTURE.md, СЂР°Р·РґРµР» 4.4/7.2), РЅРµ СЃРјС‹СЃР»РѕРІР°СЏ РѕС†РµРЅРєР°.
    """
    changes = update.model_dump(exclude_none=True)
    if not changes:
        return _settings_payload()

    errors: list[str] = []
    if "max_iterations" in changes and changes["max_iterations"] < 1:
        errors.append("max_iterations РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ >= 1")
    if "request_timeout_seconds" in changes and changes["request_timeout_seconds"] < 1:
        errors.append("request_timeout_seconds РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ >= 1")
    if "delegate_timeout_seconds" in changes and changes["delegate_timeout_seconds"] < 1:
        errors.append("delegate_timeout_seconds РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ >= 1")
    if "num_ctx" in changes and changes["num_ctx"] < 512:
        errors.append("num_ctx РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ >= 512")
    if "num_predict" in changes and changes["num_predict"] == 0:
        errors.append("num_predict РЅРµ РјРѕР¶РµС‚ Р±С‹С‚СЊ 0 (РёСЃРїРѕР»СЊР·СѓР№С‚Рµ -1 РґР»СЏ В«Р±РµР· СЏРІРЅРѕРіРѕ РїСЂРµРґРµР»Р°В»)")
    if "max_context_messages" in changes and changes["max_context_messages"] < 1:
        errors.append("max_context_messages РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ >= 1")
    if "log_level" in changes and changes["log_level"] not in _LOG_LEVELS:
        errors.append(f"log_level РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РѕРґРЅРёРј РёР·: {', '.join(sorted(_LOG_LEVELS))}")
    if "stt_language" in changes and changes["stt_language"] not in _STT_LANGUAGES:
        errors.append(f"stt_language РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ РѕРґРЅРёРј РёР·: {', '.join(sorted(_STT_LANGUAGES))}")
    if "appearance_theme" in changes and changes["appearance_theme"] not in _APPEARANCE_THEMES:
        errors.append(f"appearance_theme must be one of: {', '.join(sorted(_APPEARANCE_THEMES))}")
    if "accent_color" in changes and changes["accent_color"] not in _ACCENT_COLORS:
        errors.append(f"accent_color must be one of: {', '.join(sorted(_ACCENT_COLORS))}")
    if "ui_font_size" in changes and changes["ui_font_size"] not in _UI_FONT_SIZES:
        errors.append(f"ui_font_size must be one of: {', '.join(sorted(_UI_FONT_SIZES))}")
    if "ui_density" in changes and changes["ui_density"] not in _UI_DENSITIES:
        errors.append(f"ui_density must be one of: {', '.join(sorted(_UI_DENSITIES))}")
    if "startup_page" in changes and changes["startup_page"] not in _STARTUP_PAGES:
        errors.append(f"startup_page must be one of: {', '.join(sorted(_STARTUP_PAGES))}")
    if "code_font_size" in changes and changes["code_font_size"] not in _CODE_FONT_SIZES:
        errors.append(f"code_font_size must be one of: {', '.join(sorted(_CODE_FONT_SIZES))}")
    if "preferred_response_language" in changes and changes["preferred_response_language"] not in _PREFERRED_RESPONSE_LANGUAGES:
        errors.append(f"preferred_response_language must be one of: {', '.join(sorted(_PREFERRED_RESPONSE_LANGUAGES))}")
    if "interface_language" in changes and changes["interface_language"] not in _INTERFACE_LANGUAGES:
        errors.append(f"interface_language must be one of: {', '.join(sorted(_INTERFACE_LANGUAGES))}")
    if "ollama_host" in changes and not changes["ollama_host"].startswith(("http://", "https://")):
        errors.append("ollama_host РґРѕР»Р¶РµРЅ РЅР°С‡РёРЅР°С‚СЊСЃСЏ СЃ http:// РёР»Рё https://")

    target_host = changes.get("ollama_host", config.OLLAMA_HOST)
    ollama = _ollama_status(target_host)
    if ollama.get("connected"):
        available_models = set(ollama.get("models", []))
        for field in ("primary_model", "code_model"):
            if field in changes and changes[field] not in available_models:
                errors.append(
                    f"{field}={changes[field]!r} РЅРµ РЅР°Р№РґРµРЅР° РІ Ollama ({target_host}). "
                    f"Р”РѕСЃС‚СѓРїРЅС‹: {', '.join(sorted(available_models)) or '(РїСѓСЃС‚Рѕ)'}"
                )
    # Р•СЃР»Рё Ollama РїСЂСЏРјРѕ СЃРµР№С‡Р°СЃ РЅРµРґРѕСЃС‚СѓРїРЅР° вЂ” РЅРµ Р±Р»РѕРєРёСЂСѓРµРј СЃРјРµРЅСѓ РјРѕРґРµР»Рё РїРѕ СЌС‚РѕР№ РїСЂРёС‡РёРЅРµ,
    # СЌС‚Рѕ Р±С‹Р»Р° Р±С‹ РЅРµРІРµСЂРЅРѕ РїСЂРёРїРёСЃР°РЅРЅР°СЏ РїСЂРёС‡РёРЅР° РѕС‚РєР°Р·Р° (РёРЅС„СЂР°СЃС‚СЂСѓРєС‚СѓСЂР°, Р° РЅРµ Р·РЅР°С‡РµРЅРёРµ).

    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    before = _settings_payload()

    if "primary_model" in changes:
        config.PRIMARY_MODEL = changes["primary_model"]
    if "code_model" in changes:
        old_code_model = config.CODE_MODEL
        config.CODE_MODEL = changes["code_model"]
        description = config.DELEGATE_MODELS.pop(
            old_code_model, "РЎРїРµС†РёР°Р»РёР·РёСЂРѕРІР°РЅРЅР°СЏ РјРѕРґРµР»СЊ РїСЂРѕРіСЂР°РјРјРёСЂРѕРІР°РЅРёСЏ (РєРѕРґ, СЂРµС„Р°РєС‚РѕСЂРёРЅРі, Р°РЅР°Р»РёР·, РѕР±СЉСЏСЃРЅРµРЅРёРµ РєРѕРґР°)."
        )
        config.DELEGATE_MODELS[config.CODE_MODEL] = description
    if "ollama_host" in changes:
        config.OLLAMA_HOST = changes["ollama_host"]
    if "max_iterations" in changes:
        config.MAX_ITERATIONS = changes["max_iterations"]
    if "request_timeout_seconds" in changes:
        config.REQUEST_TIMEOUT_SECONDS = changes["request_timeout_seconds"]
    if "delegate_timeout_seconds" in changes:
        config.DELEGATE_TIMEOUT_SECONDS = changes["delegate_timeout_seconds"]
    if "num_ctx" in changes:
        config.OLLAMA_NUM_CTX = changes["num_ctx"]
    if "num_predict" in changes:
        config.OLLAMA_NUM_PREDICT = changes["num_predict"]
    if "max_context_messages" in changes:
        config.MAX_CONTEXT_MESSAGES = changes["max_context_messages"]
    if "log_level" in changes:
        config.LOG_LEVEL = changes["log_level"]
        base_logger.set_level(config.LOG_LEVEL)
    if "enable_ocr" in changes:
        config.ENABLE_OCR = changes["enable_ocr"]
    if "enable_image_understanding" in changes:
        config.ENABLE_IMAGE_UNDERSTANDING = changes["enable_image_understanding"]
    if "enable_translator" in changes:
        config.ENABLE_TRANSLATOR = changes["enable_translator"]
    if "enable_code_specialist_auto" in changes:
        config.ENABLE_CODE_SPECIALIST_AUTO = changes["enable_code_specialist_auto"]
    if "enable_reviewer_explicit" in changes:
        config.ENABLE_REVIEWER_EXPLICIT = changes["enable_reviewer_explicit"]
    if "stt_language" in changes:
        config.WHISPER_CPP_LANGUAGE = changes["stt_language"]
    if "appearance_theme" in changes:
        config.APPEARANCE_THEME = changes["appearance_theme"]
    if "accent_color" in changes:
        config.ACCENT_COLOR = changes["accent_color"]
    if "ui_font_size" in changes:
        config.UI_FONT_SIZE = changes["ui_font_size"]
    if "ui_density" in changes:
        config.UI_DENSITY = changes["ui_density"]
    if "show_message_timestamps" in changes:
        config.SHOW_MESSAGE_TIMESTAMPS = changes["show_message_timestamps"]
    if "show_typing_animation" in changes:
        config.SHOW_TYPING_ANIMATION = changes["show_typing_animation"]
    if "copy_before_clear_chat" in changes:
        config.COPY_BEFORE_CLEAR_CHAT = changes["copy_before_clear_chat"]
    if "startup_page" in changes:
        config.STARTUP_PAGE = changes["startup_page"]
    if "code_font_size" in changes:
        config.CODE_FONT_SIZE = changes["code_font_size"]
    if "code_line_wrap" in changes:
        config.CODE_LINE_WRAP = changes["code_line_wrap"]
    if "code_syntax_highlighting" in changes:
        config.CODE_SYNTAX_HIGHLIGHTING = changes["code_syntax_highlighting"]
    if "code_show_line_numbers" in changes:
        config.CODE_SHOW_LINE_NUMBERS = changes["code_show_line_numbers"]
    if "code_show_language_badge" in changes:
        config.CODE_SHOW_LANGUAGE_BADGE = changes["code_show_language_badge"]
    if "code_show_copy_button" in changes:
        config.CODE_SHOW_COPY_BUTTON = changes["code_show_copy_button"]
    if "code_show_collapse_button" in changes:
        config.CODE_SHOW_COLLAPSE_BUTTON = changes["code_show_collapse_button"]
    if "code_show_save_button" in changes:
        config.CODE_SHOW_SAVE_BUTTON = changes["code_show_save_button"]
    if "show_experimental_stream_button" in changes:
        config.SHOW_EXPERIMENTAL_STREAM_BUTTON = changes["show_experimental_stream_button"]
    if "preferred_response_language" in changes:
        config.PREFERRED_RESPONSE_LANGUAGE = changes["preferred_response_language"]
    if "interface_language" in changes:
        config.INTERFACE_LANGUAGE = changes["interface_language"]

    client_affecting = {"primary_model", "ollama_host", "request_timeout_seconds", "num_ctx", "num_predict"}
    if client_affecting & changes.keys():
        _rebuild_ollama_client()

    after = _settings_payload()
    base_logger.event(
        "settings_updated",
        before=before,
        after=after,
        changed_fields=list(changes.keys()),
        console_message=f"[SETTINGS] РѕР±РЅРѕРІР»РµРЅРѕ: {', '.join(changes.keys())}",
    )
    await trace_hub.broadcast({"event": "settings_updated", "changed_fields": list(changes.keys())})

    # Persist only the subset of changed fields that actually survives a
    # restart (storage/settings_store.py::PERSISTABLE_FIELDS) — e.g.
    # ollama_host/max_iterations/delegate_timeout_seconds are applied above
    # but intentionally not written to disk (HANDOFF_v2.md §6). A disk-write
    # failure never undoes the runtime change already applied above; it's
    # logged and surfaced in trace, not raised back to the caller.
    persistable_changes = {k: v for k, v in changes.items() if k in PERSISTABLE_FIELDS}
    if persistable_changes:
        try:
            settings_store.save(persistable_changes)
        except OSError as exc:
            base_logger.error(
                "settings_save_failed",
                console_message=f"[SETTINGS] не удалось сохранить storage/settings.json: {exc}",
                error=str(exc),
                attempted_fields=list(persistable_changes.keys()),
            )
            await trace_hub.broadcast({"event": "settings_save_failed", "error": str(exc)})
        else:
            base_logger.event(
                "settings_saved",
                saved_fields=list(persistable_changes.keys()),
                console_message=f"[SETTINGS] сохранено в storage/settings.json: {', '.join(persistable_changes.keys())}",
            )
            await trace_hub.broadcast({"event": "settings_saved", "saved_fields": list(persistable_changes.keys())})

    return after


@app.get("/api/runtime/status")
async def runtime_status() -> dict[str, Any]:
    return _runtime_payload()


@app.get("/api/tools")
async def tools() -> dict[str, Any]:
    return {"tools": _runtime_payload()["registered_tools"]}


def _model_install_status(name: str, available: set[str], ollama_connected: bool) -> str:
    """installed/missing/unknown вЂ” matches by exact name or name: tag
    prefix (registry entries like 'glm-ocr' vs Ollama's 'glm-ocr:latest'),
    same fuzzy match used by GlmOcrService/TranslatorService.is_available()."""
    if not ollama_connected:
        return "unknown"
    is_installed = any(n == name or (n or "").startswith(f"{name}:") for n in available)
    return "installed" if is_installed else "missing"


@app.get("/api/models")
async def models() -> dict[str, Any]:
    """Model registry (Phase 4D) вЂ” static role/routing_mode metadata from
    config.MODEL_REGISTRY, combined with live Ollama install status. This
    replaces the old ad-hoc primary/delegate-only shape (nothing in the
    frontend depended on it yet вЂ” ModelsView was still on mock data)."""
    ollama = _ollama_status()
    available = set(ollama.get("models", []))
    return {
        "models": [
            {
                **entry,
                "status": _model_install_status(entry["name"], available, ollama["connected"]),
                "is_last_used": entry["name"] == _last_used_model,
                "is_active_chat_model": entry["name"] == _active_chat_model,
            }
            for entry in config.MODEL_REGISTRY
        ],
        "ollama_connected": ollama["connected"],
        "last_used_model": _last_used_model,
        "last_used_role": _last_used_role,
        "active_chat_model": _active_chat_model,
    }


@app.get("/api/models/active")
async def get_active_chat_model() -> dict[str, Any]:
    return {
        "active_chat_model": _active_chat_model,
        "allowed_manual_models": config.ALLOWED_MANUAL_CHAT_MODELS,
    }


@app.post("/api/models/active")
async def set_active_chat_model(request: SetActiveChatModelRequest) -> dict[str, Any]:
    """Manual active chat model switch (Phase 4E) вЂ” explicit human action
    only. Never used by the router as a fallback/heavy-reasoning trigger;
    it's the ONLY way config.MANUAL_HEAVY_MODEL can ever reach a normal chat
    turn (see core/model_router.py). Rejects anything not in
    config.ALLOWED_MANUAL_CHAT_MODELS (ornith/coder/glm-ocr/translategemma
    are valid models elsewhere but never allowed here) and anything not
    actually installed in Ollama вЂ” _active_chat_model is left unchanged on
    either failure."""
    global _active_chat_model
    model = request.model.strip()

    if model not in config.ALLOWED_MANUAL_CHAT_MODELS:
        base_logger.error(
            "active_model_change_failed",
            console_message=f"[ROUTER] РїРѕРїС‹С‚РєР° СѓСЃС‚Р°РЅРѕРІРёС‚СЊ РЅРµРґРѕРїСѓСЃС‚РёРјСѓСЋ active_chat_model: {model!r}",
            attempted_model=model,
            reason="not_allowed",
        )
        await trace_hub.broadcast({"event": "active_model_change_failed", "attempted_model": model, "reason": "not_allowed"})
        raise HTTPException(
            status_code=400,
            detail=f"{model!r} is not an allowed manual chat model (allowed: {config.ALLOWED_MANUAL_CHAT_MODELS})",
        )

    ollama = _ollama_status()
    if _model_install_status(model, set(ollama.get("models", [])), ollama["connected"]) != "installed":
        base_logger.error(
            "active_model_change_failed",
            console_message=f"[ROUTER] РјРѕРґРµР»СЊ {model!r} РЅРµ СѓСЃС‚Р°РЅРѕРІР»РµРЅР° РІ Ollama",
            attempted_model=model,
            reason="not_installed",
        )
        await trace_hub.broadcast({"event": "active_model_change_failed", "attempted_model": model, "reason": "not_installed"})
        raise HTTPException(status_code=400, detail=f"model {model!r} is not installed in Ollama")

    previous_model = _active_chat_model
    _active_chat_model = model
    base_logger.event(
        "active_model_changed",
        previous_model=previous_model,
        new_model=model,
        console_message=f"[ROUTER] active chat model РёР·РјРµРЅРµРЅР° РІСЂСѓС‡РЅСѓСЋ: {previous_model} -> {model}",
    )
    await trace_hub.broadcast({"event": "active_model_changed", "previous_model": previous_model, "new_model": model})
    return {"ok": True, "active_chat_model": _active_chat_model}


@app.get("/api/resources/status")
async def resources_status() -> dict[str, Any]:
    """Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — honest visibility
    only, no automatic policy. Three genuinely separate things (see
    core/resource_manager.py's module docstring): Ollama's own loaded
    models, the external qwen3_tts_ggml_vulkan tts-server.exe subprocess
    (which can outlive our own handle to it — the bug this Phase fixes),
    and whisper.cpp's normally-ephemeral whisper-cli.exe. Never raises —
    each section degrades to an honest unavailable/empty result on its own,
    same discipline as GET /api/runtime/status."""
    ollama = await asyncio.to_thread(ollama_process_status, config.OLLAMA_HOST)

    tts_provider = voice_service.tts if isinstance(voice_service.tts, QwenTTSGgmlVulkanProvider) else None
    if tts_provider is not None:
        parsed_port = 8080
        try:
            parsed_port = int(config.QWEN_TTS_SERVER_URL.rsplit(":", 1)[-1])
        except ValueError:
            pass
        tts_server = await asyncio.to_thread(
            tts_server_status,
            managed_by_backend=tts_provider.is_server_managed_by_us(),
            expected_exe_path=config.QWEN_TTS_EXE,
            host="127.0.0.1",
            port=parsed_port,
        )
    else:
        tts_server = {
            "running": None,
            "managed_by_backend": False,
            "pid": None,
            "path": None,
            "port_reachable": None,
            "expected_path_match": None,
            "note": "qwen3_tts_ggml_vulkan is not the active TTS provider (config.VOICE_TTS_PROVIDER)",
        }

    whisper_cli = await asyncio.to_thread(whisper_cli_status, config.WHISPER_CPP_EXE_PATH)

    return {
        "ollama_available": ollama["available"],
        "ollama_loaded_models": ollama["models"],
        "ollama_error": ollama["error"],
        "external_processes": {
            "tts_server": tts_server,
            "whisper_cli": whisper_cli,
        },
        "policy": {
            "phase": "manual_control_only",
            "auto_unload_enabled": False,
        },
    }


@app.get("/api/game/nucleares/status")
async def nucleares_status() -> dict[str, Any]:
    base_logger.event("nucleares_status_requested")
    await trace_hub.broadcast({"event": "nucleares_status_requested"})
    try:
        result = await asyncio.to_thread(nucleares_client.status)
    except Exception as exc:
        error = str(exc)
        base_logger.error(
            "nucleares_status_failed",
            console_message=f"[NUCLEARES] status failed: {error}",
            error=error,
        )
        await trace_hub.broadcast({"event": "nucleares_status_failed", "error": error})
        return {"game": "nucleares", "connected": False, "error": error, "attempted": []}

    if result.get("connected"):
        base_logger.event(
            "nucleares_status_connected",
            base_url=result.get("base_url"),
            parameter_count=result.get("parameter_count"),
        )
        await trace_hub.broadcast(
            {
                "event": "nucleares_status_connected",
                "base_url": result.get("base_url"),
                "parameter_count": result.get("parameter_count"),
            }
        )
        normalized_keys = list(result.get("normalized", {}).keys())
        base_logger.event(
            "nucleares_status_completed",
            base_url=result.get("base_url"),
            parameter_count=result.get("parameter_count"),
            normalized_keys=normalized_keys,
        )
        await trace_hub.broadcast(
            {
                "event": "nucleares_status_completed",
                "base_url": result.get("base_url"),
                "parameter_count": result.get("parameter_count"),
                "normalized_keys": normalized_keys,
            }
        )
    else:
        base_logger.error(
            "nucleares_status_failed",
            console_message=f"[NUCLEARES] status unavailable: {result.get('error')}",
            error=result.get("error"),
        )
        await trace_hub.broadcast({"event": "nucleares_status_failed", "error": result.get("error")})

    return result


# Tool models eligible for manual unload — mirrors config.MODEL_REGISTRY's
# "tool"/"code_specialist"/"reviewer_critic" roles (main_chat/manual_heavy_model
# are handled separately below, never included here by default).
_TOOL_MODEL_NAMES = [
    config.OCR_MODEL,
    config.IMAGE_UNDERSTANDING_MODEL,
    config.TRANSLATOR_MODEL,
    config.CODE_MODEL,
    config.REVIEWER_MODEL,
]


def _ollama_unload_model(model_name: str, host: str) -> dict[str, Any]:
    """Unloads one model from Ollama's memory via the documented
    keep_alive=0 trick (POST /api/generate, no prompt) — empirically
    confirmed (during development of this endpoint) to complete in ~0.1s
    and NOT trigger a real load when the model is already unloaded, so this
    is safe to call unconditionally rather than needing a pre-check. Never
    raises — returns {ok, error} instead."""
    try:
        response = requests.post(f"{host}/api/generate", json={"model": model_name, "keep_alive": 0}, timeout=15)
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    if response.status_code == 404:
        return {"ok": False, "error": f"model {model_name!r} not found in Ollama"}
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": None}


@app.post("/api/models/lifecycle/unload")
async def unload_models(request: ModelLifecycleUnloadRequest) -> dict[str, Any]:
    """Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — manual, explicit
    unload only. No automatic TTL/keep_alive policy is implemented anywhere
    in this pass; this endpoint only ever runs when a human clicks a button
    for it. Never unloads `_active_chat_model` unless the human explicitly
    named it via target="specific" (an intentional exception mirroring
    POST /api/models/active's own "explicit human action" discipline) —
    the tool_models/all_non_chat targets always exclude it."""
    target = request.target
    try:
        models_to_unload = resolve_unload_targets(
            target,
            request.model,
            known_models={entry["name"] for entry in config.MODEL_REGISTRY},
            tool_model_names=_TOOL_MODEL_NAMES,
            manual_heavy_model=config.MANUAL_HEAVY_MODEL,
            active_chat_model=_active_chat_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if chat_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="A chat request is currently in progress — avoid unloading models mid-generation. Try again in a moment.",
        )

    base_logger.event(
        "model_lifecycle_unload_requested",
        target=target,
        models=models_to_unload,
        console_message=f"[RESOURCE][lifecycle] unload requested: target={target}, models={models_to_unload}",
    )

    currently_loaded = {m.get("name") for m in (await asyncio.to_thread(ollama_process_status, config.OLLAMA_HOST))["models"]}

    results: list[dict[str, Any]] = []
    for model in models_to_unload:
        was_loaded = model in currently_loaded or f"{model}:latest" in currently_loaded
        base_logger.event(
            "model_lifecycle_unload_model_started",
            model=model,
            console_message=f"[RESOURCE][lifecycle] unloading {model}",
        )
        if not was_loaded:
            results.append({"model": model, "attempted": True, "ok": True, "note": "not loaded", "error": None})
            base_logger.event(
                "model_lifecycle_unload_model_completed",
                model=model,
                note="not loaded",
                console_message=f"[RESOURCE][lifecycle] {model} was not loaded — nothing to do",
            )
            continue

        outcome = await asyncio.to_thread(_ollama_unload_model, model, config.OLLAMA_HOST)
        results.append({"model": model, "attempted": True, "ok": outcome["ok"], "note": None, "error": outcome["error"]})
        if outcome["ok"]:
            base_logger.event(
                "model_lifecycle_unload_model_completed",
                model=model,
                console_message=f"[RESOURCE][lifecycle] {model} unloaded",
            )
        else:
            base_logger.error(
                "model_lifecycle_unload_model_failed",
                model=model,
                error=outcome["error"],
                console_message=f"[RESOURCE][lifecycle] {model} unload failed: {outcome['error']}",
            )

    return {"ok": all(r["ok"] for r in results), "target": target, "results": results}


@app.get("/api/memory/short")
async def short_memory() -> dict[str, Any]:
    return {"entries": short_store.search("")}


@app.delete("/api/memory/short")
async def clear_short_memory() -> dict[str, Any]:
    cleared = short_store.clear()
    base_logger.event("memory_tool_result", tool="short_memory_clear", cleared=cleared)
    await trace_hub.broadcast({"event": "memory_tool_result", "tool": "short_memory_clear", "cleared": cleared})
    return {"cleared": cleared}


@app.get("/api/memory/long")
async def long_memory(
    limit: int = Query(default=config.LONG_MEMORY_LIST_DEFAULT_LIMIT, ge=1, le=config.LONG_MEMORY_SEARCH_HARD_LIMIT),
    search: str = "",
) -> dict[str, Any]:
    entries = long_store.search(search, limit=limit) if search.strip() else long_store.list_recent(limit)
    return {"entries": entries}


@app.post("/api/memory/long")
async def save_long_memory(request: MemoryLongSaveRequest) -> dict[str, Any]:
    """Feedback row 'Save-to-memory' (Chat tab) — an explicit, human-confirmed
    action (the user reviewed/edited the text and clicked Save), never
    triggered automatically. Writes through the exact same
    `LongMemoryStore.save()` call as the `long_memory_save` tool
    (tools/memory_tools.py::LongMemorySaveTool) — same store, same method —
    just a different `source` tag ("feedback_row" vs "siena_v2") so entries
    saved by a human from the UI are distinguishable from ones the model
    chose to save itself."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > config.FEEDBACK_MEMORY_SAVE_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"text exceeds {config.FEEDBACK_MEMORY_SAVE_MAX_CHARS} characters",
        )

    base_logger.event(
        "memory_save_from_feedback_started",
        text=text,
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        console_message=f"[MEMORY][FEEDBACK] started save: {text[:80]}",
    )
    try:
        entry = long_store.save(text, source=request.source or "feedback_row")
    except Exception as exc:
        base_logger.error(
            "memory_save_from_feedback_failed",
            console_message=f"[MEMORY][FEEDBACK] save failed: {exc}",
            error=str(exc),
            conversation_id=request.conversation_id,
            message_id=request.message_id,
        )
        await trace_hub.broadcast({"event": "memory_save_from_feedback_failed", "error": str(exc)})
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    base_logger.event(
        "memory_save_from_feedback_saved",
        id=entry["id"],
        text=entry["text"],
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        console_message=f"[MEMORY][FEEDBACK] saved id={entry['id']}",
    )
    await trace_hub.broadcast({"event": "memory_save_from_feedback_saved", "id": entry["id"]})
    return {"saved": True, "entry": entry}


# --- Insights (candidate memory) вЂ” REST-only, human-in-the-loop actions.
# Runtime РЅРµ РїСЂРёРЅРёРјР°РµС‚ Р·РґРµСЃСЊ СЂРµС€РµРЅРёР№ Рѕ С‚РѕРј, СЃС‚РѕРёС‚ Р»Рё РєР°РЅРґРёРґР°С‚Сѓ СЃС‚Р°С‚СЊ
# long-term memory вЂ” РѕРЅРѕ СѓР¶Рµ РїСЂРёРЅСЏС‚Рѕ С‡РµР»РѕРІРµРєРѕРј РЅР°Р¶Р°С‚РёРµРј РєРЅРѕРїРєРё РІ РёРЅС‚РµСЂС„РµР№СЃРµ;
# СЌС‚Рё СЌРЅРґРїРѕРёРЅС‚С‹ С‚РѕР»СЊРєРѕ РёСЃРїРѕР»РЅСЏСЋС‚ СЌС‚Рѕ СЂРµС€РµРЅРёРµ Рё Р»РѕРіРёСЂСѓСЋС‚ СЂРµР·СѓР»СЊС‚Р°С‚. РњРѕРґРµР»СЊ РќР•
# РјРѕР¶РµС‚ РІС‹Р·РІР°С‚СЊ promote/reject/later/delete вЂ” СЌС‚Рѕ РЅРµ tools (СЃРј.
# tools/candidate_memory_tools.py).
@app.get("/api/insights")
async def list_insights(
    status: str = "pending",
    limit: int = Query(default=config.CANDIDATE_MEMORY_LIST_DEFAULT_LIMIT, ge=1, le=200),
) -> dict[str, Any]:
    return {"entries": candidate_store.list(status=status or None, limit=limit)}


@app.post("/api/insights/{candidate_id}/promote")
async def promote_insight(candidate_id: int) -> dict[str, Any]:
    try:
        result = promote_candidate(candidate_store, long_store, candidate_id)
    except SienaToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    base_logger.event(
        "candidate_memory_promoted",
        candidate_id=candidate_id,
        long_memory_id=result["long_memory_entry"]["id"],
        console_message=f"[MEMORY][CANDIDATE][PROMOTE] #{candidate_id} -> long_memory #{result['long_memory_entry']['id']}",
    )
    base_logger.event(
        "long_memory_saved",
        id=result["long_memory_entry"]["id"],
        text=result["long_memory_entry"]["text"],
        category=result["long_memory_entry"]["category"],
        importance=result["long_memory_entry"]["importance"],
        source=f"candidate_memory:{candidate_id}",
    )
    await trace_hub.broadcast({"event": "candidate_memory_promoted", "candidate_id": candidate_id})
    return {"promoted": candidate_id, "long_memory_entry": result["long_memory_entry"]}


@app.post("/api/insights/{candidate_id}/reject")
async def reject_insight(candidate_id: int) -> dict[str, Any]:
    updated = candidate_store.set_status(candidate_id, "rejected")
    if updated is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    base_logger.event("candidate_memory_rejected", candidate_id=candidate_id)
    await trace_hub.broadcast({"event": "candidate_memory_rejected", "candidate_id": candidate_id})
    return {"rejected": candidate_id}


@app.post("/api/insights/{candidate_id}/later")
async def defer_insight(candidate_id: int) -> dict[str, Any]:
    updated = candidate_store.set_status(candidate_id, "later")
    if updated is None:
        raise HTTPException(status_code=404, detail="candidate not found")

    base_logger.event("candidate_memory_deferred", candidate_id=candidate_id)
    await trace_hub.broadcast({"event": "candidate_memory_deferred", "candidate_id": candidate_id})
    return {"deferred": candidate_id}


@app.delete("/api/insights/{candidate_id}")
async def delete_insight(candidate_id: int) -> dict[str, Any]:
    deleted = candidate_store.delete(candidate_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="candidate not found")

    base_logger.event("candidate_memory_deleted", candidate_id=candidate_id)
    await trace_hub.broadcast({"event": "candidate_memory_deleted", "candidate_id": candidate_id})
    return {"deleted": candidate_id}


def _voice_audio_filename(path: str) -> str:
    return Path(path).name


@app.post("/api/voice/transcribe")
async def voice_transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
) -> dict[str, Any]:
    """STT вЂ” С‚РѕР»СЊРєРѕ С‚РµС…РЅРёС‡РµСЃРєРѕРµ РїСЂРµРІСЂР°С‰РµРЅРёРµ РіРѕР»РѕСЃР° РІ С‚РµРєСЃС‚ (push-to-talk).
    Runtime РЅРµ СЂРµС€Р°РµС‚, С‡С‚Рѕ РґРµР»Р°С‚СЊ СЃ СЂР°СЃРїРѕР·РЅР°РЅРЅС‹Рј С‚РµРєСЃС‚РѕРј вЂ” UI РїСЂРѕСЃС‚Рѕ РєР»Р°РґС‘С‚
    РµРіРѕ РІ РїРѕР»Рµ РІРІРѕРґР°, РїРѕР»СЊР·РѕРІР°С‚РµР»СЊ СЃР°Рј РЅР°Р¶РёРјР°РµС‚ Send (ARCHITECTURE.md,
    С„РёР»РѕСЃРѕС„РёСЏ РЅРµ РјРµРЅСЏРµС‚СЃСЏ).
    """
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(await file.read())
        tmp.close()

        effective_language = language or config.STT_LANGUAGE_DEFAULT
        base_logger.event(
            "voice_transcribe_start",
            model=config.STT_MODEL,
            device=voice_service.stt.device,
            language=effective_language,
            console_message=f"[VOICE][STT] СЂР°СЃРїРѕР·РЅР°РІР°РЅРёРµ РЅР°С‡Р°С‚Рѕ (model={config.STT_MODEL})",
        )
        start = time.monotonic()
        try:
            result = await asyncio.to_thread(voice_service.transcribe, tmp.name, effective_language)
        except STTUnavailableError as exc:
            base_logger.error(
                "stt_unavailable",
                console_message=f"[VOICE][STT] РЅРµРґРѕСЃС‚СѓРїРµРЅ: {exc}",
                error=str(exc),
            )
            base_logger.error("voice_error", console_message=f"[VOICE] РѕС€РёР±РєР° STT: {exc}", error=str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        duration_ms = round((time.monotonic() - start) * 1000)
        base_logger.event(
            "voice_transcribe_result",
            model=config.STT_MODEL,
            device=voice_service.stt.device,
            duration_ms=duration_ms,
            text_length=len(result["text"]),
            audio_duration_sec=result["duration_sec"],
            language=result["language"],
            console_message=f"[VOICE][STT] РіРѕС‚РѕРІРѕ Р·Р° {duration_ms}РјСЃ: {result['text'][:60]!r}",
        )
        return {"ok": True, "text": result["text"], "language": result["language"], "duration_sec": result["duration_sec"]}
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@app.post("/api/voice/stt/transcribe")
async def voice_stt_transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(None),
) -> dict[str, Any]:
    """STT via whisper.cpp (Phase 1, HANDOFF_v2.md) — a separate, standalone
    path from /api/voice/transcribe above (faster-whisper, still dormant —
    package not installed). Not called by any UI yet: mic recording stays
    frozen until a dedicated Phase 2. Same discipline as every other voice/
    endpoint — this only turns audio into text, never decides what happens
    with it.

    Only accepts .wav right now (no ffmpeg/webm/opus conversion — that
    would be a silent new dependency, out of scope here). Real audio
    duration is checked via wave.open() (not just upload byte size), which
    is reliable specifically because non-WAV uploads are already rejected.
    """
    effective_language = (language or config.WHISPER_CPP_LANGUAGE or "auto").strip() or "auto"

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="file is required and must not be empty")
    if len(raw_bytes) > config.WHISPER_CPP_MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"file exceeds {config.WHISPER_CPP_MAX_UPLOAD_BYTES} bytes",
        )

    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix != ".wav":
        raise HTTPException(
            status_code=400,
            detail=(
                f"only .wav is accepted right now (got {suffix or 'no extension'!r}) — "
                "no ffmpeg/webm/opus conversion is implemented"
            ),
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    try:
        tmp.write(raw_bytes)
        tmp.close()

        try:
            with wave.open(tmp.name, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                duration_sec = frames / rate if rate else 0.0
        except (wave.Error, EOFError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid WAV file: {exc}") from exc

        if duration_sec > config.WHISPER_CPP_MAX_AUDIO_SECONDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"audio duration {duration_sec:.1f}s exceeds "
                    f"{config.WHISPER_CPP_MAX_AUDIO_SECONDS}s limit"
                ),
            )

        base_logger.event(
            "stt_transcribe_requested",
            language=effective_language,
            duration_sec=round(duration_sec, 3),
            bytes=len(raw_bytes),
            console_message=(
                f"[VOICE][STT][whisper.cpp] requested ({duration_sec:.1f}s audio, lang={effective_language})"
            ),
        )

        if not whisper_cpp_stt_service.is_available():
            reason = whisper_cpp_stt_service.unavailable_reason() or "whisper.cpp STT unavailable"
            base_logger.error(
                "stt_transcribe_failed",
                stage="unavailable",
                error=reason,
                console_message=f"[VOICE][STT][whisper.cpp] unavailable: {reason}",
            )
            raise HTTPException(status_code=503, detail=reason)

        base_logger.event(
            "stt_transcribe_started",
            language=effective_language,
            console_message="[VOICE][STT][whisper.cpp] transcription started",
        )

        try:
            result = await asyncio.to_thread(whisper_cpp_stt_service.transcribe_wav, tmp.name, effective_language)
        except WhisperCppTimeoutError as exc:
            base_logger.error(
                "stt_transcribe_failed",
                stage="timeout",
                error=str(exc),
                console_message=f"[VOICE][STT][whisper.cpp] timed out: {exc}",
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except WhisperCppEmptyResultError as exc:
            base_logger.error(
                "stt_transcribe_failed",
                stage="empty_result",
                error=str(exc),
                console_message=f"[VOICE][STT][whisper.cpp] empty result: {exc}",
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (WhisperCppTranscriptionError, WhisperCppUnavailableError) as exc:
            base_logger.error(
                "stt_transcribe_failed",
                stage="transcription_error",
                error=str(exc),
                console_message=f"[VOICE][STT][whisper.cpp] failed: {exc}",
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        base_logger.event(
            "stt_transcribe_completed",
            language=result["language"],
            backend=result["backend"],
            elapsed_ms=result["elapsed_ms"],
            text_length=len(result["text"]),
            console_message=(
                f"[VOICE][STT][whisper.cpp] done in {result['elapsed_ms']}ms "
                f"(backend={result['backend']}): {result['text'][:60]!r}"
            ),
        )
        return {
            "text": result["text"],
            "language": result["language"],
            "provider": result["provider"],
            "backend": result["backend"],
            "elapsed_ms": result["elapsed_ms"],
            "confidence": None,
        }
    finally:
        Path(tmp.name).unlink(missing_ok=True)


@app.post("/api/voice/synthesize")
async def voice_synthesize(payload: VoiceSynthesizeRequest) -> dict[str, Any]:
    """TTS вЂ” С‚РѕР»СЊРєРѕ С‚РµС…РЅРёС‡РµСЃРєРѕРµ РїСЂРµРІСЂР°С‰РµРЅРёРµ С‚РµРєСЃС‚Р° РІ Р·РІСѓРє. РќРµ СЂРµС€Р°РµС‚, С‡С‚Рѕ
    СЃРєР°Р·Р°С‚СЊ вЂ” РѕР·РІСѓС‡РёРІР°РµС‚ СЂРѕРІРЅРѕ С‚РѕС‚ С‚РµРєСЃС‚, РєРѕС‚РѕСЂС‹Р№ РїСЂРёСЃР»Р°Р»Рё (РѕС‚РІРµС‚ РјРѕРґРµР»Рё РёР»Рё
    С‡С‚Рѕ СѓРіРѕРґРЅРѕ РґСЂСѓРіРѕРµ)."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    base_logger.event(
        "voice_synthesize_start",
        provider=config.TTS_PROVIDER,
        voice=payload.voice or config.TTS_SPEAKER,
        text_length=len(text),
        console_message=f"[VOICE][TTS] СЃРёРЅС‚РµР· РЅР°С‡Р°С‚ ({len(text)} СЃРёРјРІРѕР»РѕРІ)",
    )
    start = time.monotonic()
    try:
        result = await asyncio.to_thread(voice_service.synthesize, text, payload.voice)
    except TTSUnavailableError as exc:
        base_logger.error(
            "tts_unavailable",
            console_message=f"[VOICE][TTS] РЅРµРґРѕСЃС‚СѓРїРµРЅ: {exc}",
            error=str(exc),
        )
        base_logger.error("voice_error", console_message=f"[VOICE] РѕС€РёР±РєР° TTS: {exc}", error=str(exc))
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    duration_ms = round((time.monotonic() - start) * 1000)
    base_logger.event(
        "voice_synthesize_result",
        provider=config.TTS_PROVIDER,
        voice=result["voice"],
        duration_ms=duration_ms,
        audio_duration_sec=result["duration_sec"],
        console_message=f"[VOICE][TTS] РіРѕС‚РѕРІРѕ Р·Р° {duration_ms}РјСЃ, Р°СѓРґРёРѕ {result['duration_sec']}СЃ",
    )
    return {
        "ok": True,
        "audio_url": f"/api/voice/audio/{result['audio_filename']}",
        "audio_path": result["audio_path"],
        "duration_sec": result["duration_sec"],
        # Honest fallback visibility (HANDOFF_v2.md) — the frontend must
        # never assume the primary provider spoke just because the call
        # succeeded; VoiceService.synthesize() sets this to whichever
        # provider (primary or fallback) actually produced the audio.
        "provider": result.get("provider", config.TTS_PROVIDER),
    }


@app.get("/api/voice/audio/{filename}")
async def voice_audio(filename: str) -> FileResponse:
    safe_name = _voice_audio_filename(filename)
    if safe_name != filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = config.TTS_OUTPUT_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/voice/status")
async def voice_status() -> dict[str, Any]:
    status = voice_service.status()
    # Overrides stt_available/stt_model (previously always describing the
    # dormant faster-whisper provider, which isn't installed) with the
    # actual configured STT_PROVIDER (whisper.cpp, Phase 1) — the mic UI
    # still doesn't call any of this yet (frozen), but /api/voice/status
    # should honestly reflect what STT path is actually usable today.
    status.update({
        "stt_provider": config.STT_PROVIDER,
        "stt_available": whisper_cpp_stt_service.is_available(),
        "stt_reason": whisper_cpp_stt_service.unavailable_reason(),
        "stt_model": whisper_cpp_stt_service.model_path.name,
        "stt_backend_hint": "vulkan_greedy" if config.WHISPER_CPP_USE_VULKAN else "cpu",
    })
    return status


def _require_ggml_vulkan_provider() -> QwenTTSGgmlVulkanProvider:
    if not isinstance(voice_service.tts, QwenTTSGgmlVulkanProvider):
        raise HTTPException(
            status_code=400,
            detail="qwen3_tts_ggml_vulkan is not the active TTS provider (config.VOICE_TTS_PROVIDER)",
        )
    return voice_service.tts


@app.post("/api/voice/tts/test")
async def voice_tts_test(payload: VoiceTtsTestRequest) -> dict[str, Any]:
    """Явный smoke-test synthesis — всегда бьёт напрямую в
    qwen3_tts_ggml_vulkan provider (не через VoiceService.synthesize(), чтобы
    тихий fallback на Silero не спрятал реальную поломку сервера)."""
    provider = _require_ggml_vulkan_provider()
    try:
        result = await asyncio.to_thread(provider.synthesize_to_file, payload.text)
    except TTSUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/voice/tts/start")
async def voice_tts_start() -> dict[str, Any]:
    provider = _require_ggml_vulkan_provider()
    try:
        await asyncio.to_thread(provider.ensure_server_running)
    except TTSUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"ok": True, "running": True}


@app.post("/api/voice/tts/stop")
async def voice_tts_stop(force: bool = False) -> dict[str, Any]:
    """Resource Lifecycle Phase 1 (HANDOFF_v2.md) — honest stop, not just an
    optimistic one. The known bug this fixes: a backend restart/reload loses
    QwenTTSGgmlVulkanProvider's `_process` handle, so the OLD code here could
    return `ok=True, running=False` while the real external tts-server.exe
    was still alive on port 8080 the whole time (a human found this by hand
    — `ollama ps` was empty but tts-server.exe was still holding memory).

    Without `force`: if we don't hold the process handle, this now NEVER
    claims success just because our own handle is gone — it honestly reports
    whether an external tts-server.exe process still exists instead.
    With `force=true`: kills that external process too, but ONLY if its exe
    path matches `config.QWEN_TTS_EXE` exactly — never an arbitrary
    same-named process elsewhere on the machine. A future Speak/Stream click
    still auto-starts a fresh server normally either way (ensure_server_running()
    is untouched)."""
    provider = _require_ggml_vulkan_provider()
    base_logger.event(
        "tts_server_stop_requested",
        force=force,
        console_message=f"[RESOURCE][tts] stop requested (force={force})",
    )

    if provider.is_server_managed_by_us():
        await asyncio.to_thread(provider.stop_server)
        base_logger.event("tts_server_stop_completed", managed_by_backend=True, console_message="[RESOURCE][tts] managed server stopped normally")
        return {"ok": True, "running": False, "managed_by_backend": True, "external_process_found": False}

    external = await asyncio.to_thread(find_tts_server_processes, config.QWEN_TTS_EXE)
    if not external:
        base_logger.event("tts_server_stop_completed", managed_by_backend=False, console_message="[RESOURCE][tts] nothing to stop — no handle, no external process")
        return {"ok": True, "running": False, "managed_by_backend": False, "external_process_found": False}

    base_logger.event(
        "tts_server_external_found",
        pids=[p["pid"] for p in external],
        console_message=f"[RESOURCE][tts] external tts-server.exe found (pids={[p['pid'] for p in external]}), not ours",
    )

    if not force:
        return {
            "ok": False,
            "running": True,
            "managed_by_backend": False,
            "external_process_found": True,
            "message": "External tts-server.exe found but not killed without force=true",
        }

    expected = config.QWEN_TTS_EXE.resolve()
    killed: list[int] = []
    skipped: list[dict[str, Any]] = []
    for p in external:
        exe = p.get("exe")
        if not exe or Path(exe).resolve() != expected:
            skipped.append(p)
            continue
        try:
            proc = psutil.Process(p["pid"])
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            killed.append(p["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            base_logger.error(
                "tts_server_stop_failed",
                pid=p["pid"],
                error=str(exc),
                console_message=f"[RESOURCE][tts] force-kill failed for pid {p['pid']}: {exc}",
            )

    if skipped:
        base_logger.event(
            "tts_server_stop_completed",
            managed_by_backend=False,
            killed_pids=killed,
            skipped_path_mismatch=[s["pid"] for s in skipped],
            console_message=(
                f"[RESOURCE][tts] force-killed {killed}, skipped {[s['pid'] for s in skipped]} "
                "(exe path did not match expected qwentts.cpp build)"
            ),
        )
    else:
        base_logger.event(
            "tts_server_force_killed",
            killed_pids=killed,
            console_message=f"[RESOURCE][tts] force-killed external tts-server.exe (pids={killed})",
        )
        base_logger.event("tts_server_stop_completed", managed_by_backend=False, killed_pids=killed, console_message="[RESOURCE][tts] force stop completed")

    return {
        "ok": bool(killed) or not external,
        "running": bool(skipped),
        "managed_by_backend": False,
        "external_process_found": True,
        "killed_pids": killed,
        "skipped_path_mismatch_pids": [s["pid"] for s in skipped],
    }


def _stream_pcm_body(first_chunk: bytes, rest: Any, meta: dict[str, Any], start: float) -> Any:
    """Plain sync generator handed to StreamingResponse — Starlette runs it
    via iterate_in_threadpool, so the blocking `for chunk in rest` iteration
    (raw requests.iter_content under the hood, see
    QwenTTSGgmlVulkanProvider.stream_pcm) never blocks the event loop.
    `first_chunk` was already pulled off `rest` before this generator was
    created (see voice_tts_stream below) purely so the endpoint could return
    a proper 502/503 for an upstream failure BEFORE committing to a 200
    streaming response — this yields it first so the client still gets it.

    Known limitation (Phase 3, HANDOFF_v2.md, confirmed by live testing —
    not just theorized): an ASGI-level disconnect watcher
    (`await request.is_disconnected()`, polled from an asyncio task alongside
    a threading.Event checked between chunks) was tried here and DID NOT
    detect a client abort — neither a raw TCP socket close nor a real
    Electron/Chromium `fetch()` + `AbortController.abort()` ever flipped
    `is_disconnected()` to True while this generator was blocked waiting on
    tts-server's next chunk. Starlette's `iterate_in_threadpool` runs plain
    `next()` calls in a worker thread with no way to interrupt a blocking
    call already in progress, and nothing here proactively pushes an
    `http.disconnect` ASGI message while the app isn't reading/writing.
    Given that, the reliable signal for "the user clicked Stop" is the
    frontend's own client-reported `tts_stream_client_disconnected` event
    (via POST /api/trace/client-event, see useStreamingSpeech.ts) — the
    frontend always knows the instant it calls `AbortController.abort()`.
    The `except GeneratorExit` below is kept purely as defense in depth in
    case a future Starlette/anyio version does close() this generator
    directly; it is not the primary mechanism.

    Net effect: clicking Stop stops playback and resets the UI immediately
    (verified live) — but the abandoned upstream generation on this thread
    keeps running until tts-server finishes that utterance on its own, at
    which point this still logs tts_stream_completed even though nobody is
    listening anymore. Documented honestly rather than papered over.
    """
    total_bytes = len(first_chunk)
    try:
        yield first_chunk
        for chunk in rest:
            total_bytes += len(chunk)
            yield chunk
    except GeneratorExit:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        base_logger.event(
            "tts_stream_client_disconnected",
            **meta,
            total_bytes=total_bytes,
            elapsed_ms=elapsed_ms,
            console_message=f"[VOICE][TTS][stream] client disconnected after {total_bytes} bytes ({elapsed_ms}ms)",
        )
        raise
    except TTSUnavailableError as exc:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        base_logger.error(
            "tts_stream_failed",
            **meta,
            stage="mid_stream",
            total_bytes=total_bytes,
            elapsed_ms=elapsed_ms,
            error=str(exc),
            console_message=f"[VOICE][TTS][stream] failed mid-stream after {total_bytes} bytes: {exc}",
        )
        return
    else:
        elapsed_ms = round((time.monotonic() - start) * 1000)
        base_logger.event(
            "tts_stream_completed",
            **meta,
            total_bytes=total_bytes,
            elapsed_ms=elapsed_ms,
            console_message=f"[VOICE][TTS][stream] completed: {total_bytes} bytes in {elapsed_ms}ms",
        )


@app.post("/api/voice/tts/stream")
async def voice_tts_stream(payload: VoiceTtsStreamRequest) -> StreamingResponse:
    """Experimental (Phase 2/3, HANDOFF_v2.md) — backend-only raw PCM
    streaming, proxied straight from qwentts.cpp's tts-server.exe
    (response_format=pcm). NOT wired into any stable UI path: useSpeech.ts,
    the Speak button, and /api/voice/synthesize's stable WAV-per-request path
    (with its Silero fallback) are completely untouched by this endpoint —
    only the separate, clearly-marked-experimental Stream Speak button uses
    this.

    qwen3_tts_ggml_vulkan-only, by design, with no fallback — a fallback
    would silently swap to a non-streaming provider mid-request, which makes
    no sense for a streaming contract, so an inactive/wrong provider is an
    honest 501 here rather than a fake stream.
    """
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if not isinstance(voice_service.tts, QwenTTSGgmlVulkanProvider):
        raise HTTPException(
            status_code=501,
            detail=(
                "Streaming TTS requires qwen3_tts_ggml_vulkan as the active provider "
                "(config.VOICE_TTS_PROVIDER) — there is no streaming fallback."
            ),
        )
    provider = voice_service.tts

    speaker = payload.voice or config.QWEN_TTS_DEFAULT_SPEAKER
    language = payload.language or config.QWEN_TTS_DEFAULT_LANGUAGE
    # text itself is never logged, only its length — see task spec.
    meta = {"text_length": len(text), "speaker": speaker, "language": language}

    start = time.monotonic()
    base_logger.event(
        "tts_stream_requested",
        **meta,
        console_message=f"[VOICE][TTS][stream] requested ({len(text)} chars, voice={speaker})",
    )

    try:
        await asyncio.to_thread(provider.ensure_server_running)
    except TTSUnavailableError as exc:
        base_logger.error(
            "tts_stream_failed",
            **meta,
            stage="server_start",
            elapsed_ms=round((time.monotonic() - start) * 1000),
            error=str(exc),
            console_message=f"[VOICE][TTS][stream] server not available: {exc}",
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    base_logger.event(
        "tts_stream_server_ready",
        **meta,
        console_message="[VOICE][TTS][stream] tts-server reachable, opening stream",
    )

    # stream_pcm() is a plain generator — nothing inside it runs until the
    # first next() call below, which is also where ensure_server_running()
    # gets called a second (idempotent, near-instant) time as defense in
    # depth against the server dying in the gap since the check above.
    gen = provider.stream_pcm(text, voice=speaker, language=language)

    base_logger.event(
        "tts_stream_started",
        **meta,
        console_message="[VOICE][TTS][stream] stream request sent to tts-server",
    )

    def _prime() -> tuple[bytes, TTSUnavailableError | None]:
        try:
            return next(gen), None
        except StopIteration:
            return b"", None
        except TTSUnavailableError as exc:
            return b"", exc

    first_chunk, prime_error = await asyncio.to_thread(_prime)
    if prime_error is not None:
        base_logger.error(
            "tts_stream_failed",
            **meta,
            stage="open_stream",
            elapsed_ms=round((time.monotonic() - start) * 1000),
            error=str(prime_error),
            console_message=f"[VOICE][TTS][stream] upstream failed before first chunk: {prime_error}",
        )
        raise HTTPException(status_code=502, detail=str(prime_error)) from prime_error

    first_chunk_ms = round((time.monotonic() - start) * 1000)
    base_logger.event(
        "tts_stream_first_chunk",
        **meta,
        first_chunk_ms=first_chunk_ms,
        chunk_bytes=len(first_chunk),
        console_message=f"[VOICE][TTS][stream] first chunk after {first_chunk_ms}ms ({len(first_chunk)} bytes)",
    )

    body = _stream_pcm_body(first_chunk, gen, meta, start)
    return StreamingResponse(
        body,
        media_type="application/octet-stream",
        headers={
            "X-Siena-TTS-Provider": provider.PROVIDER_NAME,
            "X-Siena-TTS-Format": "pcm",
            "X-Siena-TTS-Sample-Rate": "24000",
            "X-Siena-TTS-Channels": "1",
        },
    )


# --- Voice profiles (Qwen3-TTS instruct/speaker/language/model_repo) вЂ”
# СЃРѕС…СЂР°РЅСЏРµРјС‹Рµ С‚РµС…РЅРёС‡РµСЃРєРёРµ РЅР°СЃС‚СЂРѕР№РєРё С‚РµРјР±СЂР° РіРѕР»РѕСЃР°, РќР• personality Siena.
# Runtime РЅРµ СЂРµС€Р°РµС‚, РєР°РєРѕР№ РїСЂРѕС„РёР»СЊ "Р»СѓС‡С€Рµ" вЂ” СЌС‚Рё СЌРЅРґРїРѕРёРЅС‚С‹ С‚РѕР»СЊРєРѕ РёСЃРїРѕР»РЅСЏСЋС‚
# СЏРІРЅРѕРµ РґРµР№СЃС‚РІРёРµ С‡РµР»РѕРІРµРєР° (UI/curl): СЃРѕР·РґР°С‚СЊ РїСЂРѕС„РёР»СЊ, РїРѕРјРµРЅСЏС‚СЊ РїРѕР»СЏ,
# РІС‹Р±СЂР°С‚СЊ Р°РєС‚РёРІРЅС‹Р№. РЎРј. voice/voice_profiles.py.
@app.get("/api/voice/profiles")
async def list_voice_profiles() -> dict[str, Any]:
    return {"profiles": [p.to_dict() for p in voice_profile_store.list_profiles()]}


@app.get("/api/voice/profiles/active")
async def get_active_voice_profile() -> dict[str, Any]:
    return voice_profile_store.get_active_profile().to_dict()


@app.post("/api/voice/profiles")
async def create_voice_profile(payload: VoiceProfileCreateRequest) -> dict[str, Any]:
    profile = VoiceProfile(
        id=payload.id,
        name=payload.name,
        provider=payload.provider,
        model_repo=payload.model_repo or config.QWEN_TTS_MODEL_REPO,
        language=payload.language,
        speaker=payload.speaker,
        instruct=payload.instruct,
        created_at="",
        updated_at="",
    )
    saved = voice_profile_store.save_profile(profile)
    base_logger.event(
        "voice_profile_saved",
        profile_id=saved.id,
        console_message=f"[VOICE][PROFILES] СЃРѕС…СЂР°РЅС‘РЅ РїСЂРѕС„РёР»СЊ {saved.id}",
    )
    await trace_hub.broadcast({"event": "voice_profile_saved", "profile_id": saved.id})
    return saved.to_dict()


@app.patch("/api/voice/profiles/{profile_id}")
async def update_voice_profile(profile_id: str, payload: VoiceProfileUpdateRequest) -> dict[str, Any]:
    try:
        updated = voice_profile_store.update_profile(
            profile_id,
            name=payload.name,
            speaker=payload.speaker,
            language=payload.language,
            model_repo=payload.model_repo,
            instruct=payload.instruct,
            provider=payload.provider,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    base_logger.event(
        "voice_profile_updated",
        profile_id=updated.id,
        console_message=f"[VOICE][PROFILES] РѕР±РЅРѕРІР»С‘РЅ РїСЂРѕС„РёР»СЊ {updated.id}",
    )
    await trace_hub.broadcast({"event": "voice_profile_updated", "profile_id": updated.id})
    return updated.to_dict()


@app.post("/api/voice/profiles/active")
async def set_active_voice_profile(payload: VoiceProfileActivateRequest) -> dict[str, Any]:
    try:
        activated = voice_profile_store.set_active_profile(payload.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    base_logger.event(
        "voice_profile_activated",
        profile_id=activated.id,
        console_message=f"[VOICE][PROFILES] Р°РєС‚РёРІРµРЅ РїСЂРѕС„РёР»СЊ {activated.id}",
    )
    await trace_hub.broadcast({"event": "voice_profile_activated", "profile_id": activated.id})
    return activated.to_dict()


@app.get("/api/logs/recent")
async def recent_logs(limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    return {"entries": _read_recent_jsonl(limit)}


@app.get("/api/trace/recent")
async def recent_trace(limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    logs = _read_recent_jsonl(limit)
    trace_events = [
        item for item in logs
        if item.get("event") in {
            "user_message",
            "ollama_raw_response",
            "model_response",
            "tool_dispatch",
            "tool_result",
            "short_memory_saved",
            "long_memory_saved",
            "model_delegate",
            "model_delegate_result",
            "empty_final_answer",
            "final_answer",
            "memory_tool_result",
            "attachment_add",
            "attachment_remove",
            "attachment_send",
            "attachment_context_injected",
            "attachment_unsupported",
            "attachment_too_large",
            "ocr_started",
            "ocr_completed",
            "ocr_low_quality",
            "ocr_failed",
            "ocr_context_injected",
            "vision_intent_detected",
            "vision_started",
            "vision_completed",
            "vision_failed",
            "vision_context_injected",
            "translator_started",
            "translator_completed",
            "translator_failed",
            "translator_fallback",
            "translator_context_injected",
            "model_route_decision",
            "model_specialist_started",
            "model_specialist_completed",
            "model_specialist_failed",
            "active_model_changed",
            "active_model_change_failed",
            "image_understanding_unavailable",
            "memory_save_intent_detected",
            "long_memory_save_started",
            "long_memory_save_failed",
            "settings_loaded",
            "settings_load_failed",
            "settings_saved",
            "settings_save_failed",
            "runtime_vram_probe",
            "research_grounding_intent_detected",
            "memory_save_from_feedback_started",
            "memory_save_from_feedback_saved",
            "memory_save_from_feedback_failed",
            "feedback_retry_requested",
            "feedback_retry_started",
            "feedback_retry_completed",
            "feedback_retry_failed",
            "tts_stream_requested",
            "tts_stream_server_ready",
            "tts_stream_started",
            "tts_stream_first_chunk",
            "tts_stream_completed",
            "tts_stream_failed",
            "tts_stream_client_disconnected",
            "stt_transcribe_requested",
            "stt_transcribe_started",
            "stt_transcribe_completed",
            "stt_transcribe_failed",
            "stt_cpu_fallback_started",
            "stt_cpu_fallback_completed",
            "stt_cpu_fallback_failed",
            # Client-reported (Phase 2 mic UI, HANDOFF_v2.md) — see
            # useVoiceRecorder.ts. Frontend-only lifecycle around the
            # getUserMedia recording flow; the backend stt_transcribe_*
            # events above cover the actual transcription call.
            "stt_ui_recording_requested",
            "stt_ui_permission_granted",
            "stt_ui_permission_denied",
            "stt_ui_recording_started",
            "stt_ui_recording_stopped",
            "stt_ui_transcribe_started",
            "stt_ui_transcribe_completed",
            "stt_ui_transcribe_failed",
            "stt_ui_cancelled",
            # Client-reported (Phase 3, experimental Voice Conversation Mode,
            # HANDOFF_v2.md) — see useVoiceConversation.ts. Hands-free
            # listen -> transcribe -> auto-send -> speak -> listen loop,
            # entirely separate from the stt_ui_* push-to-talk events above.
            "voice_conversation_started",
            "voice_conversation_listening",
            "voice_conversation_speech_detected",
            "voice_conversation_transcribe_started",
            "voice_conversation_transcribe_completed",
            "voice_conversation_chat_send_started",
            "voice_conversation_chat_send_completed",
            "voice_conversation_tts_started",
            "voice_conversation_tts_completed",
            "voice_conversation_stopped",
            "voice_conversation_failed",
            # Two-stage silence/finalize (Phase 3.1, HANDOFF_v2.md) — fixes
            # the original single-stage VAD cutting utterances off
            # mid-sentence. voice_conversation_silence_detected (singular,
            # first-pass event) is retired in favor of these five.
            "voice_conversation_soft_silence_detected",
            "voice_conversation_finalize_wait_started",
            "voice_conversation_resumed_before_finalize",
            "voice_conversation_utterance_finalized",
            "voice_conversation_utterance_ignored",
            # Resource/Model Lifecycle Phase 1 (HANDOFF_v2.md) — TTS server
            # lifecycle. tts_server_starting/_ready/_stopped are logged by
            # voice/qwen_tts_ggml_vulkan.py itself (pre-existing, just never
            # added to this allowlist before); the rest are new this pass.
            "tts_server_starting",
            "tts_server_ready",
            "tts_server_stopped",
            "tts_server_stop_requested",
            "tts_server_external_found",
            "tts_server_force_killed",
            "tts_server_stop_completed",
            "tts_server_stop_failed",
            # Resource/Model Lifecycle Phase 1 — manual Ollama tool-model unload.
            "model_lifecycle_unload_requested",
            "model_lifecycle_unload_model_started",
            "model_lifecycle_unload_model_completed",
            "model_lifecycle_unload_model_failed",
            # Nucleares Game Bridge Phase 1 — read-only local game telemetry.
            "nucleares_status_requested",
            "nucleares_status_connected",
            "nucleares_status_completed",
            "nucleares_status_failed",
            "nucleares_context_injection_requested",
            "nucleares_context_injected",
            "nucleares_context_unavailable",
            "nucleares_context_skipped",
            # Debug page (0.2.0 release readiness pass) — these already existed
            # and were already logged, just never added to this allowlist
            # before, so the Debug/Tool Trace UI couldn't show them.
            "voice_synthesize_start",
            "voice_synthesize_result",
            "candidate_memory_created",
            "candidate_memory_promoted",
            "candidate_memory_rejected",
            "candidate_memory_deferred",
            "candidate_memory_deleted",
        }
    ]
    return {"events": trace_events[-limit:]}


_ALLOWED_CLIENT_TRACE_EVENTS = {
    "attachment_add",
    "attachment_remove",
    "attachment_unsupported",
    "attachment_too_large",
    "feedback_retry_requested",
    "feedback_retry_started",
    "feedback_retry_completed",
    "feedback_retry_failed",
    # Client-reported (Phase 3, HANDOFF_v2.md) — see
    # useStreamingSpeech.ts / api/server.py::_stream_pcm_body's docstring for
    # why this is reported by the frontend instead of detected server-side.
    "tts_stream_client_disconnected",
    # Client-reported (Phase 2 mic UI, HANDOFF_v2.md) — see
    # useVoiceRecorder.ts. Covers the getUserMedia/recording lifecycle that
    # only the frontend can observe (permission prompts, user clicking
    # Stop/Cancel); the actual transcription call is logged server-side.
    "stt_ui_recording_requested",
    "stt_ui_permission_granted",
    "stt_ui_permission_denied",
    "stt_ui_recording_started",
    "stt_ui_recording_stopped",
    "stt_ui_transcribe_started",
    "stt_ui_transcribe_completed",
    "stt_ui_transcribe_failed",
    "stt_ui_cancelled",
    # Client-reported (Phase 3, experimental Voice Conversation Mode,
    # HANDOFF_v2.md) — see useVoiceConversation.ts.
    "voice_conversation_started",
    "voice_conversation_listening",
    "voice_conversation_speech_detected",
    "voice_conversation_transcribe_started",
    "voice_conversation_transcribe_completed",
    "voice_conversation_chat_send_started",
    "voice_conversation_chat_send_completed",
    "voice_conversation_tts_started",
    "voice_conversation_tts_completed",
    "voice_conversation_stopped",
    "voice_conversation_failed",
    # Two-stage silence/finalize (Phase 3.1, HANDOFF_v2.md).
    "voice_conversation_soft_silence_detected",
    "voice_conversation_finalize_wait_started",
    "voice_conversation_resumed_before_finalize",
    "voice_conversation_utterance_finalized",
    "voice_conversation_utterance_ignored",
}


@app.post("/api/trace/client-event")
async def client_trace_event(payload: ClientTraceEventRequest) -> dict[str, Any]:
    """РњРѕСЃС‚ РґР»СЏ С‡РёСЃС‚Рѕ РєР»РёРµРЅС‚СЃРєРёС… СЃРѕР±С‹С‚РёР№ (РґРѕР±Р°РІР»РµРЅРёРµ/СѓРґР°Р»РµРЅРёРµ attachment РґРѕ
    РѕС‚РїСЂР°РІРєРё, unsupported/too_large РµС‰С‘ РґРѕ РІС‹Р·РѕРІР° /api/chat) РІ С‚РѕС‚ Р¶Рµ
    JSONL+WS trace, С‡С‚Рѕ Рё СЃРµСЂРІРµСЂРЅС‹Рµ СЃРѕР±С‹С‚РёСЏ. Runtime РЅРµ СЂРµС€Р°РµС‚, С‡С‚Рѕ Р»РѕРіРёСЂРѕРІР°С‚СЊ
    вЂ” С‚РѕР»СЊРєРѕ Р·Р°РїРёСЃС‹РІР°РµС‚ РїСЂРёСЃР»Р°РЅРЅРѕРµ РєР»РёРµРЅС‚РѕРј, Рё С‚РѕР»СЊРєРѕ РёР· СЏРІРЅРѕ СЂР°Р·СЂРµС€С‘РЅРЅРѕРіРѕ
    СЃРїРёСЃРєР° (РЅРµ РѕС‚РєСЂС‹С‚С‹Р№ РєР°РЅР°Р» РґР»СЏ РїСЂРѕРёР·РІРѕР»СЊРЅС‹С… РґР°РЅРЅС‹С…)."""
    if payload.event not in _ALLOWED_CLIENT_TRACE_EVENTS:
        raise HTTPException(status_code=400, detail=f"unsupported client trace event: {payload.event}")
    base_logger.event(payload.event, **payload.fields)
    await trace_hub.broadcast({"event": payload.event, **payload.fields})
    return {"logged": payload.event}


@app.websocket("/ws/trace")
async def trace_socket(websocket: WebSocket) -> None:
    await trace_hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        trace_hub.disconnect(websocket)
