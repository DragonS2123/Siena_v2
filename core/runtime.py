"""Composition root for all stateful Siena services."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import config
from core.chat_service import ChatService
from core.attachment_service import AttachmentService
from core.data_migration import migrate_conversations
from core.model_catalog import ModelCatalog
from core.model_roles import ModelRoles
from core.runtime_settings import RuntimeSettingsService
from core.tool_registry import build_tool_registry
from core.trace_service import TraceService
from logging_.logger import SienaLogger
from storage.conversation_store import ConversationStore
from storage.settings_store import SettingsStore
from voice.qwen_tts_ggml_vulkan import QwenTTSGgmlVulkanProvider
from voice.voice_profiles import VoiceProfileStore
from voice.whisper_cpp_stt import WhisperCppSTTProvider


@dataclass
class Runtime:
    settings: RuntimeSettingsService
    roles: ModelRoles
    catalog: ModelCatalog
    conversations: ConversationStore
    registry: Any
    short_memory: Any
    long_memory: Any
    candidates: Any
    chat: ChatService
    stt: WhisperCppSTTProvider
    tts: QwenTTSGgmlVulkanProvider
    voice_profiles: VoiceProfileStore
    logger: SienaLogger
    trace: TraceService
    background_tasks: list[Any]


def _rotate_logs(log_dir: Path) -> None:
    if not log_dir.exists():
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.LOG_RETENTION_DAYS)
    files = sorted(log_dir.glob("siena_*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    for index, path in enumerate(files):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if index >= config.LOG_MAX_FILES or modified < cutoff:
            path.unlink(missing_ok=True)


def create_runtime() -> Runtime:
    _rotate_logs(config.LOG_DIR)
    trace = TraceService()
    logger = SienaLogger(config.LOG_DIR, config.LOG_LEVEL, trace.publish)
    settings_store = SettingsStore(config.SETTINGS_STORE_PATH)
    settings_store.migrate()
    settings = RuntimeSettingsService(settings_store, logger)
    logger.set_level(str(settings.current().get("log_level")))
    settings.subscribe(
        lambda snapshot, changed: logger.set_level(str(snapshot.get("log_level")))
        if "log_level" in changed else None
    )
    migrate_conversations(config.CONVERSATIONS_DB_PATH)
    roles = ModelRoles(settings)
    catalog = ModelCatalog(lambda: str(settings.current().get("ollama_host")))
    conversations = ConversationStore(config.CONVERSATIONS_DB_PATH, config.CONVERSATION_EVENTS_DEFAULT_LIMIT)
    interrupted_streams = conversations.list_active_stream_messages()
    recovered_streams = conversations.recover_interrupted_messages()
    if recovered_streams:
        logger.event("stream_messages_recovered", count=recovered_streams)

    def installed() -> set[str]:
        return {item["name"] for item in catalog.refresh().get("models", [])}

    registry, short, long, candidates = build_tool_registry(logger, settings, roles.assignments, installed)
    attachments = AttachmentService(config.ATTACHMENTS_STORAGE_ROOT, conversations, roles, settings, logger)
    chat = ChatService(conversations, roles, catalog, registry, long, attachments, logger, settings)
    background_tasks: list[Any] = []
    if interrupted_streams and bool(settings.current().get("auto_resume_interrupted")):
        try:
            loop = asyncio.get_running_loop()
            background_tasks = [
                loop.create_task(chat.resume_interrupted_message(record), name=f"resume-{record['id']}")
                for record in interrupted_streams
            ]
        except RuntimeError:
            logger.event("stream_resume_deferred", count=len(interrupted_streams), reason="no_running_event_loop")
    stt = WhisperCppSTTProvider(
        config.WHISPER_CPP_EXE_PATH,
        config.WHISPER_CPP_MODEL_PATH,
        config.WHISPER_CPP_TIMEOUT_SECONDS,
        config.WHISPER_CPP_BEAM_SIZE,
        config.WHISPER_CPP_BEST_OF,
        config.WHISPER_CPP_USE_VULKAN,
        config.WHISPER_CPP_CPU_FALLBACK,
        logger,
    )
    tts = QwenTTSGgmlVulkanProvider(
        config.QWEN_TTS_SERVER_URL,
        config.QWEN_TTS_EXE,
        config.QWEN_TTS_MODEL_PATH,
        config.QWEN_TTS_CODEC_PATH,
        config.QWEN_TTS_DEFAULT_LANGUAGE,
        config.QWEN_TTS_DEFAULT_SPEAKER,
        config.QWEN_TTS_TIMEOUT_SECONDS,
        config.TTS_OUTPUT_DIR,
        config.QWEN_TTS_AUTO_START,
        logger=logger,
    )
    profiles = VoiceProfileStore(config.VOICE_PROFILES_PATH, logger)
    return Runtime(
        settings, roles, catalog, conversations, registry, short, long,
        candidates, chat, stt, tts, profiles, logger, trace, background_tasks
    )


def close_runtime(runtime: Runtime) -> None:
    for task in runtime.background_tasks:
        if not task.done():
            task.cancel()
    runtime.tts.stop_server()

