"""Единая точка конфигурации Siena v2. Никакой логики принятия решений — только константы.

Часть значений здесь технически изменяема во время работы backend'а через
POST /api/settings (api/server.py) — по явному действию человека в Settings UI,
а не решению Runtime. Модуль остаётся источником истины: и старт процесса,
и live-обновление читают/пишут именно эти атрибуты."""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- Ollama / модель ---
# Pinned to the IPv4 loopback literal on purpose (found during a live smoke
# test, 2026-07): "localhost" resolves dual-stack on Windows, and Python's
# requests library tries the IPv6 candidate (::1) first — Ollama only listens
# on IPv4, so every single call (chat, OCR, translator, /api/models,
# /api/runtime/status) paid a ~2s fallback tax before this fix. Verified
# directly: requests.get("http://localhost:11434/...") took 2.06s vs 0.016s
# for "http://127.0.0.1:11434/...". Same class of bug as vite.config.ts's
# `host: '127.0.0.1'` pin, documented there for the dev server.
OLLAMA_HOST = "http://127.0.0.1:11434"
PRIMARY_MODEL = "qwen3.5:9b"  # главный интеллект Siena — ведёт диалог, единственная говорит с пользователем
REQUEST_TIMEOUT_SECONDS = 120
OLLAMA_THINK = False  # выключено для скорости agent loop; чисто техническая настройка вызова,
                      # не влияет на автономность модели в выборе tools/памяти/отказе отвечать

# --- Управление контекстом (см. DIAGNOSIS_CONTEXT_OVERFLOW.md) ---
# Технические параметры транспорта — Runtime не решает, что "важно" сохранить
# в контексте, он лишь ограничивает, сколько физически отправляется модели.
OLLAMA_NUM_CTX = 32768        # окно контекста модели в Ollama (было: дефолт 4096)
OLLAMA_NUM_PREDICT = 2048     # верхний предел токенов генерации за один вызов
MAX_CONTEXT_MESSAGES = 40     # технический срез: сколько последних сообщений Session
                              # реально уезжает в Ollama (полная история не удаляется)

# --- Делегирование другим моделям (см. ARCHITECTURE.md раздел 12) ---
CODE_MODEL = "qwen2.5-coder:7b"  # специализированная модель программирования
DELEGATE_TIMEOUT_SECONDS = 180   # делегированные задачи (генерация кода) могут идти дольше обычного ответа

# Реестр моделей, которые разрешено вызывать через delegate_model. Runtime использует
# этот список только для технической проверки "модель существует и сконфигурирована",
# а не для выбора, какую вызывать — выбор всегда делает PRIMARY_MODEL.
DELEGATE_MODELS = {
    CODE_MODEL: "Специализированная модель программирования (код, рефакторинг, анализ, объяснение кода).",
}

# --- Agent Loop ---
MAX_ITERATIONS = 8  # инженерная защита от зацикливания, см. ARCHITECTURE.md раздел 7.4

# --- Conversation History (persistence переписки, НЕ Long Memory) ---
# Технический журнал: кто что написал и полный trace агента, сохраняется
# автоматически как функция приложения. Модель тут ничего не решает — это
# не long_memory_save, а обычная persistence-логика (см. DONEARCHITECTURE.md).
CONVERSATIONS_DB_PATH = BASE_DIR / "storage" / "conversations.sqlite3"
CONVERSATION_LIST_DEFAULT_LIMIT = 50
CONVERSATION_EVENTS_DEFAULT_LIMIT = 300
ATTACHMENTS_STORAGE_ROOT = BASE_DIR / "storage" / "attachments"

# --- Settings persistence (Settings > Model section only, HANDOFF_v2.md §6) ---
# POST /api/settings already mutates these config.* attributes live; this is
# only about surviving a backend restart. Only the fields actually wired to a
# live effect are ever written here (storage/settings_store.py::PERSISTABLE_FIELDS)
# — no secrets, no ollama_host/max_iterations/delegate_timeout_seconds (out of
# scope for this pass, see NEXTDO.md).
SETTINGS_STORE_PATH = BASE_DIR / "storage" / "settings.json"

# --- Voice Layer: STT (faster-whisper) ---
# STT — чисто техническое превращение голоса в текст. Runtime не решает, что
# ответить на распознанный текст — он идёт дальше обычным путём, как если бы
# пользователь напечатал его сам (см. voice/stt.py).
STT_MODEL = "large-v3-turbo"   # см. voice/stt.py — валидный alias faster-whisper
STT_DEVICE = "cuda"            # авто-fallback на "cpu" в voice/stt.py, если CUDA недоступна (с явным WARNING в логах)
STT_COMPUTE_TYPE = "float16"   # авто-fallback на "int8" вместе с cpu
STT_LANGUAGE_DEFAULT = None    # None = автоопределение языка; "ru"/"en" и т.п. — принудительно
STT_MODELS_DIR = BASE_DIR / "storage" / "models" / "whisper"  # кэш моделей (~1.6 ГБ для large-v3-turbo)

# --- Voice Layer: STT via whisper.cpp (GGML/Vulkan, Phase 1) --- отдельный,
# новый STT-сервис (voice/whisper_cpp_stt.py), НЕ замена faster-whisper
# провайдера выше (voice/stt.py) — тот остаётся как есть (и по-прежнему
# недоступен: пакет faster_whisper не установлен). У нового пути свой
# отдельный endpoint (POST /api/voice/stt/transcribe), mic UI им пока не
# пользуется — микрофон остаётся заморожен до отдельного Phase 2.
#
# Подготовлено и проверено изолированно (storage/stt_probe/whisper_cpp_build_probe.txt):
# собран из https://github.com/ggml-org/whisper.cpp (commit 6fc7c33b4c3a2cec83e4b65abd5e96a890480375).
#
# ВАЖНО (AMD/Vulkan, подтверждено живым воспроизведением краша): на этой
# машине/сборке вызов whisper-cli.exe на Vulkan-бэкенде с ДЕФОЛТНЫМИ
# параметрами декодирования (beam-size 5, best-of 5) падает с segfault
# (exit 139) — 100% воспроизводимо на разных аудио и языках. Greedy decode
# (beam-size 1, best-of 1) работает корректно и быстро. НЕ убирать/повышать
# WHISPER_CPP_BEAM_SIZE/WHISPER_CPP_BEST_OF без повторного теста на реальном
# воспроизведении краша.
STT_PROVIDER = "whisper_cpp"
STT_ENABLED = True

WHISPER_CPP_EXE_PATH = BASE_DIR / "external" / "whisper.cpp" / "build" / "bin" / "Release" / "whisper-cli.exe"
WHISPER_CPP_MODEL_PATH = BASE_DIR / "external" / "whisper.cpp" / "models" / "ggml-base.bin"

WHISPER_CPP_LANGUAGE = "ru"
WHISPER_CPP_TIMEOUT_SECONDS = 120
WHISPER_CPP_MAX_AUDIO_SECONDS = 60  # реальная длительность проверяется через wave.open() в api/server.py, не только по размеру файла
WHISPER_CPP_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # грубый защитный потолок на размер файла, до разбора WAV-заголовка

WHISPER_CPP_USE_VULKAN = True
WHISPER_CPP_BEAM_SIZE = 1   # greedy — см. предупреждение выше, дефолтный beam-search крашит Vulkan на этой машине
WHISPER_CPP_BEST_OF = 1
WHISPER_CPP_CPU_FALLBACK = True  # если Vulkan-вызов упал, повторить с -ng (CPU) прежде чем сдаваться

# --- Voice Layer: TTS ---
# VOICE_TTS_PROVIDER выбирает основной движок озвучки: "silero" | "qwen3_tts" |
# "faster_qwen3_tts" | "qwen3_tts_ggml_vulkan". Это явное решение человека
# (см. Settings UI / config.py), не Runtime — Runtime только исполняет выбор.
# Если выбранный provider недоступен (пакет не установлен/CUDA нет/сервер не
# поднялся/ошибка синтеза) — VoiceService автоматически откатывается на
# Silero как fallback-provider (см. voice/voice_service.py) — это инженерная
# защита, а не смысловое решение, симметрично fallback keyword-поиска при
# недоступности embeddings (memory/embedding_service.py).
#
# qwen3_tts_ggml_vulkan — основной по умолчанию с 2026-07: подтверждённо
# работает на AMD RX 7900 XTX через qwentts.cpp (GGML/Vulkan backend, никакого
# torch/CUDA) — см. voice/qwen_tts_ggml_vulkan.py. faster_qwen3_tts остаётся в
# коде как CUDA-only provider (voice/faster_qwen_tts.py::is_available()
# теперь честно возвращает False без CUDA, вместо попытки загрузиться и
# упасть) — на всякий случай, если Siena когда-нибудь запустится на NVIDIA.
VOICE_TTS_PROVIDER = "qwen3_tts_ggml_vulkan"
TTS_PROVIDER = VOICE_TTS_PROVIDER  # алиас для обратной совместимости со старыми ссылками на TTS_PROVIDER

# Механическая очистка текста перед TTS (voice/text_sanitize.py) — вырезает
# *stage directions*, markdown-нумерацию/буллеты списков в начале строк.
# Общая для всех провайдеров, не смысловое решение (см. voice/text_sanitize.py).
# TTS_STRIP_ALL_NUMBERS — отдельный, более агрессивный флаг: вырезать вообще
# любые числа из текста перед озвучкой. Выключен по умолчанию, т.к. может
# вырезать числа, которые Siena сказала осознанно (даты, количества и т.п.);
# list-маркеры вида "1. "/"2)" в начале строк убираются всегда, независимо
# от этого флага.
TTS_STRIP_ALL_NUMBERS = False

# --- Voice Layer: TTS — Silero (Russian) ---
# Заменяет Kokoro — та не поддерживала русский язык вообще (LANG_CODES не
# включал ru) и на русском тексте выдавала некорректную псевдоречь (кириллица
# читалась через английскую фонетику). Silero TTS Russian — нативная русская
# модель, без этой проблемы (см. DONEARCHITECTURE.md).
TTS_LANGUAGE = "ru"
TTS_MODEL_ID = "v3_1_ru"       # версия пакета голосов Silero (аргумент speaker= у torch.hub.load, см. voice/tts.py)
TTS_SPEAKER = "baya"           # конкретный голос внутри модели: aidar/baya/kseniya/xenia/eugene/random
TTS_DEVICE = "cuda"            # авто-fallback на "cpu" в voice/tts.py, если CUDA недоступна (с явным WARNING в логах)
TTS_OUTPUT_DIR = BASE_DIR / "storage" / "tts"        # куда пишутся синтезированные wav-файлы
TTS_MODELS_DIR = BASE_DIR / "storage" / "models" / "torch"  # кэш torch.hub (репозиторий + веса Silero)
TTS_SAMPLE_RATE = 48000        # частота дискретизации Silero (8000/24000/48000 — 48000 лучшее качество)

# --- Voice Layer: TTS — Qwen3-TTS ---
# Требует отдельной установки (pip install -U qwen-tts soundfile — БЕЗ conda,
# см. README.md "Experimental: Qwen3-TTS" и scripts/test_qwen3_tts.py). Пакет
# НЕ входит в requirements.txt намеренно: это опциональный provider, Silero
# остаётся рабочим fallback-движком без него.
#
# QWEN_TTS_* ниже — это ТОЛЬКО дефолты на случай, если voice profile store
# (storage/voice_profiles.json, см. voice/voice_profiles.py) недоступен или
# сломан. В нормальной работе model_repo/language/speaker/instruct берутся из
# активного voice profile, не отсюда — см. voice/qwen_tts.py::_resolve_profile().
QWEN_TTS_MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"  # 0.6B — быстрее скачать/грузить на CPU для baseline;
                                                                # 1.7B (замени на "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice") даёт лучшее качество
QWEN_TTS_LANGUAGE = "Russian"  # поддерживаемые языки: Chinese/English/Japanese/Korean/German/French/Russian/Portuguese/Spanish/Italian
QWEN_TTS_SPEAKER = "Vivian"    # пресетный голос CustomVoice-режима — поэкспериментируй с другими (см. README)
QWEN_TTS_INSTRUCT = (          # voice instruct — ТЕХНИЧЕСКАЯ инструкция для тембра голоса,
    "Mature adult female Russian voice. Calm, warm, soft, emotionally "  # НЕ personality prompt Siena (тот отдельно, см. SYSTEM_PROMPT ниже)
    "grounded. Lower pitch, less cute, less anime, less childish. "
    "Natural close conversation. Not theatrical, not announcer-like, "
    "not cartoon-like."
)
QWEN_TTS_DEVICE = "cuda"       # авто-fallback на "cpu" в voice/qwen_tts.py, если CUDA недоступна

# --- Voice Layer: TTS — Faster Qwen3-TTS (основной по умолчанию) ---
# Требует отдельной установки (pip install faster-qwen3-tts — БЕЗ conda, см.
# README.md "Faster Qwen3-TTS" и scripts/test_faster_qwen3_tts.py). Пакет НЕ
# входит в requirements.txt намеренно (тянет CUDA-версии torch/torchaudio) —
# Silero остаётся рабочим fallback-движком без него.
#
# FASTER_QWEN_TTS_* ниже — ТОЛЬКО дефолты на случай, если voice profile store
# недоступен/сломан (см. QWEN_TTS_* выше — тот же принцип). В нормальной
# работе model_repo/language/speaker/instruct берутся из активного voice
# profile — см. voice/faster_qwen_tts.py::_resolve_profile().
FASTER_QWEN_TTS_MODEL_REPO = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
FASTER_QWEN_TTS_LANGUAGE = "russian"  # известные языки: auto/chinese/english/french/german/italian/japanese/korean/portuguese/russian/spanish
FASTER_QWEN_TTS_SPEAKER = "serena"    # известные speakers: aiden/dylan/eric/ono_anna/ryan/serena/sohee/uncle_fu/vivian.
                                       # "vivian" звучал слишком по-детски/аниме — "serena" взрослее и естественнее.
FASTER_QWEN_TTS_INSTRUCT = (
    "Consistent mature adult female Russian voice. "
    "Keep the same speaker identity, pitch, timbre, volume, and emotional tone "
    "throughout the entire utterance. "
    "Do not change voice between sentences. "
    "Calm, warm, soft, emotionally grounded. "
    "Lower pitch, less cute, less anime, less childish. "
    "Natural close conversation. "
    "Not theatrical, not announcer-like, not cartoon-like. "
    "No exaggerated acting, no character switching."
)
FASTER_QWEN_TTS_DEVICE = "cuda"  # авто-fallback на "cpu" в voice/faster_qwen_tts.py, если CUDA недоступна
FASTER_QWEN_TTS_DTYPE = "bf16"   # bf16|fp16|fp32 — переводится в реальный torch.dtype внутри провайдера
FASTER_QWEN_TTS_USE_CHUNKING = False  # MVP: отдаём текст целиком — модель сама делает просодию лучше
                                       # механической нарезки Silero. True — резать через voice/text_chunking.py
                                       # (у subprocess-CLI это увеличило бы overhead на чанк; здесь это прямой
                                       # Python API, но всё равно нарезка — доп. вызовы generate_custom_voice).

# --- Voice Layer: TTS — Qwen3-TTS via qwentts.cpp (GGML/Vulkan, основной) ---
# Никакого torch/CUDA — это отдельный C++ HTTP-сервер (tts-server.exe),
# говорящий по OpenAI-совместимому /v1/audio/speech (см.
# voice/qwen_tts_ggml_vulkan.py, external/qwentts.cpp/README.md). Runtime не
# решает, поднимать ли сервер заранее — QWEN_TTS_KEEP_SERVER_WARM это явный
# выбор человека: True — поднять при старте backend'а, False (по умолчанию) —
# лениво поднять на первый реальный synthesize_to_file(), как и остальные
# provider'ы в этом файле. POST /api/voice/tts/start и /stop (api/server.py)
# работают независимо от этого флага, как явные ручные действия.
QWEN_TTS_BACKEND = "ggml_vulkan"
QWEN_TTS_SERVER_URL = "http://127.0.0.1:8080"
QWEN_TTS_EXE = BASE_DIR / "external" / "qwentts.cpp" / "build" / "Release" / "tts-server.exe"
QWEN_TTS_MODEL_PATH = BASE_DIR / "external" / "qwentts.cpp" / "models" / "qwen-talker-1.7b-customvoice-Q8_0.gguf"
QWEN_TTS_CODEC_PATH = BASE_DIR / "external" / "qwentts.cpp" / "models" / "qwen-tokenizer-12hz-Q8_0.gguf"
QWEN_TTS_DEFAULT_LANGUAGE = "Russian"
QWEN_TTS_DEFAULT_SPEAKER = "serena"
QWEN_TTS_TIMEOUT_SECONDS = 120
QWEN_TTS_AUTO_START = True  # если сервер не отвечает, запустить его как subprocess
QWEN_TTS_KEEP_SERVER_WARM = False  # True = поднять сервер при старте backend'а, а не лениво

# --- Voice Layer: Voice Profiles (сохраняемые instruct/speaker/language/model_repo для Qwen3-TTS-семейства) ---
# Отдельный JSON, не смешивается с long/short/candidate memory — это чисто
# технические настройки тембра голоса, а не память или знания Siena.
VOICE_PROFILES_PATH = BASE_DIR / "storage" / "voice_profiles.json"

# Provider-слой (voice/tts.py, voice/qwen_tts.py) намеренно общий контракт
# (TTSUnavailableError, is_available()/synthesize_to_file(), ленивая
# загрузка) — чтобы позже можно было добавить ещё provider'ы (Piper/eSpeak и
# т.п.), не переделывая интерфейс VoiceService.

# --- Short-term memory ---
SHORT_MEMORY_PATH = BASE_DIR / "memory" / "short_memory.json"

# --- Long-term memory ---
LONG_MEMORY_DB_PATH = BASE_DIR / "memory" / "long_memory.sqlite3"
LONG_MEMORY_LIST_DEFAULT_LIMIT = 20
LONG_MEMORY_SEARCH_HARD_LIMIT = 200  # технический предохранитель, не смысловое решение

# --- Feedback row: Save-to-memory (POST /api/memory/long) --- явное,
# подтверждённое человеком действие (кнопка Save в feedback row чата), не
# решение модели — тот же LongMemoryStore.save(), что и long_memory_save
# tool, но source="feedback_row" вместо "siena_v2" отличает ручное
# сохранение из UI от того, что сохранила сама модель.
FEEDBACK_MEMORY_SAVE_MAX_CHARS = 4000

# --- Embeddings (гибридный поиск памяти: vector search первично, keyword+fuzzy — fallback) ---
# Runtime не решает, что "релевантно" — cosine similarity/keyword-ranking это
# техническая retrieval-операция внутри *_memory_search tools (см. memory/search.py,
# memory/embedding_service.py). Если модель не установлена/не загрузилась —
# stores автоматически откатываются на keyword/fuzzy, без падения.
EMBEDDINGS_ENABLED = True
EMBEDDING_MODEL_NAME = "intfloat/multilingual-e5-small"
# EMBEDDING_VECTOR_DIM намеренно не хардкодится — определяется у модели в рантайме
# через EmbeddingService.dimension (auto-detect), см. memory/vector_store.py.
EMBEDDING_SEARCH_LIMIT = 50
EMBEDDING_MIN_SCORE = 0.35
MEMORY_VECTORS_DB_PATH = BASE_DIR / "memory" / "memory_vectors.sqlite3"

# --- Candidate memory (когнитивный цикл: Observation → Insight → Reflection → Candidate) ---
# Отдельная от long_memory база — Siena предлагает кандидата через
# candidate_memory_create, человек утверждает/отклоняет/откладывает его из
# интерфейса Insights (REST-эндпоинты /api/insights/*, НЕ tools модели —
# см. tools/candidate_memory_tools.py и ARCHITECTURE.md).
CANDIDATE_MEMORY_DB_PATH = BASE_DIR / "memory" / "candidate_memory.sqlite3"
CANDIDATE_MEMORY_LIST_DEFAULT_LIMIT = 50

# --- Presence layer (0.2.1, Phase 1) --- лёгкий, локальный, opt-in слой
# рантайм-состояния (presence/), НЕ чат-функция и НЕ автономный агент.
# Runtime только исполняет и хранит эти флаги — включение/выключение и все
# пороги задаёт человек через Settings UI (POST /api/settings), тот же
# config.X = value + PERSISTABLE_FIELDS механизм, что и остальные settings.
# allow_proactive_presence_messages по умолчанию False — по соображениям
# безопасности от спама: automatic присутствие никогда не создаёт сообщения
# в чате, пока человек явно это не разрешит.
ENABLE_PRESENCE = True
ALLOW_PROACTIVE_PRESENCE_MESSAGES = False
PRESENCE_IDLE_MINUTES = 15
PRESENCE_MAX_MESSAGES_PER_HOUR = 2
PRESENCE_QUIET_HOURS_ENABLED = False
PRESENCE_QUIET_HOURS_START = "23:00"
PRESENCE_QUIET_HOURS_END = "08:00"
PRESENCE_STYLE = "calm"  # "calm" | "playful" | "minimal" — see presence/presence_service.py's message pool
SHOW_PRESENCE_CARD = True

# --- Presence Behavior Layer (0.2.1, Phase 2) --- мягкое поведение поверх
# Phase 1: welcome-back UI-событие после idle, блок «последнее событие» в
# Presence Card, вставка текста в composer (никогда не авто-отправка).
# Все события — детерминированные строки, без LLM, без TTS, и никогда не
# пишутся в обычную историю чата (см. tests/test_presence_behavior.py,
# правило No Chat Pollution).
PRESENCE_SHOW_WELCOME_BACK = True
PRESENCE_SHOW_RECENT_EVENT = True
PRESENCE_ALLOW_INSERT_TO_CHAT = True
PRESENCE_MIN_SECONDS_BETWEEN_UI_MESSAGES = 60

# --- Chat input ---
CHAT_INPUT_MAX_CHARS = 4000  # обеспечивается и сервером (/api/chat), и UI-счётчиком

# --- Chat attachments (Phase 4A) --- значения совпадают с константами во
# фронтенде (Siena v2 Control Panel UI/src/app/App.tsx) — синхронизируются
# вручную, единого источника схемы пока нет (см. api/types.ts комментарий).
MAX_ATTACHMENTS_PER_MESSAGE = 5
MAX_ATTACHMENT_TEXT_CHARS = 20_000
MAX_TOTAL_ATTACHMENT_TEXT_CHARS = 60_000

# --- OCR (glm-ocr via Ollama, Phase 4B) --- та же техническая роль, что и
# STT/TTS (voice/) — только превращает пиксели в текст, не решает, что с ним
# делать дальше. MAX_IMAGE_ATTACHMENT_BYTES — decoded-размер картинки;
# base64-полезная нагрузка в запросе будет примерно на треть больше.
ENABLE_OCR = True
OCR_MODEL = "glm-ocr"
OCR_TIMEOUT_SECONDS = 120  # glm-ocr is small but slow to respond in practice — observed ~60-70s even for a tiny test image
MAX_IMAGE_ATTACHMENT_BYTES = 6 * 1024 * 1024
OCR_MAX_EXTRACTED_CHARS = 1500  # defense in depth against degenerate/repetitive model output
OCR_PREVIEW_CHARS = 500
OCR_MIN_USEFUL_CHARS = 10

# --- Image understanding / vision (qwen2.5vl via Ollama) ---
# OCR (above) only extracts TEXT from an image — it is not a scene/object
# description model. core/image_intent.py splits "what does this say"
# (OCR intent, glm-ocr) from "what does this show" (vision intent,
# qwen2.5vl) so api/server.py::_run_image_vision only ever calls the vision
# model when the user actually asked about visual content — see
# vision/qwen_vision_service.py.
ENABLE_IMAGE_UNDERSTANDING = True
IMAGE_UNDERSTANDING_MODEL = "qwen2.5vl"
IMAGE_UNDERSTANDING_TIMEOUT_SECONDS = 120
IMAGE_UNDERSTANDING_MAX_OUTPUT_CHARS = 3000  # defense in depth, same spirit as OCR_MAX_EXTRACTED_CHARS

# --- Translator (translategemma-strict:4b via Ollama, Phase 4C) --- та же
# техническая роль, что OCR/STT/TTS — только переводит текст, не решает, надо
# ли переводить (это явное действие: кнопка Translate, POST /api/translate,
# или explicit translate=true на attachment/OCR-результате — см. api/server.py).
# heavy fallback (qwen3.5:27b) сюда пока НЕ включён — только primary
# (translategemma-strict:4b) и лёгкий fallback (qwen3.5:9b), как попросили в Phase 4C.
ENABLE_TRANSLATOR = True
TRANSLATOR_MODEL = "translategemma-strict:4b"
TRANSLATOR_FALLBACK_MODEL = "qwen3.5:9b"
TRANSLATOR_HEAVY_FALLBACK_MODEL = "qwen3.5:27b"  # подготовлено на будущее — не используется в Phase 4C
TRANSLATOR_TIMEOUT_SECONDS = 120
TRANSLATOR_MAX_INPUT_CHARS = 20_000
TRANSLATOR_MAX_OUTPUT_CHARS = 20_000
TRANSLATOR_DEFAULT_SOURCE = "auto"
# Siena сама говорит по-русски (см. SYSTEM_PROMPT ниже) — самый частый сценарий
# перевода: иностранный текст (attachment/OCR/вставленный) -> русский для
# пользователя, поэтому безопасный default — "ru", а не "en".
TRANSLATOR_DEFAULT_TARGET = "ru"

# --- Model registry & specialist routing (Phase 4D) ---
# qwen3.5:27b (MANUAL_HEAVY_MODEL) is intentionally NEVER a routing candidate
# anywhere in this codebase — core/model_router.py never selects it, and it
# is deliberately absent from DELEGATE_MODELS above (see tools/delegate_model.py
# — DelegateModelTool rejects any model not in DELEGATE_MODELS, so the model
# itself cannot delegate to it either). Switching to it is a manual, explicit
# user action reserved for a future phase — not implemented here.
# ENABLE_HEAVY_REASONING_AUTO exists purely as a documented safety flag
# confirming that absence — it is never read as an "enable it" switch by
# anything in core/model_router.py, and must stay False.
MAIN_CHAT_MODEL = PRIMARY_MODEL  # alias for registry/router readability — PRIMARY_MODEL stays the one live-mutable source of truth (see POST /api/settings)
MANUAL_HEAVY_MODEL = "qwen3.5:27b"
REVIEWER_MODEL = "ornith:9b"
ENABLE_MODEL_ROUTER = True
ENABLE_CODE_SPECIALIST_AUTO = True
ENABLE_REVIEWER_EXPLICIT = True
ENABLE_HEAVY_REASONING_AUTO = False  # DO NOT set True — qwen3.5:27b is manual-only by design, see above

# --- Manual active chat model (Phase 4E) --- runtime-only state, held in
# api/server.py's _active_chat_model — NOT persisted, resets to
# MAIN_CHAT_MODEL on backend restart (see "не менять Settings persistence
# глобально" constraint). This is the ONLY list a human may pick the "normal
# chat" model from via POST /api/models/active — ornith/coder/glm-ocr/
# translategemma are valid models elsewhere in MODEL_REGISTRY but are never
# allowed here, even if someone tries to POST them directly.
ALLOWED_MANUAL_CHAT_MODELS = [MAIN_CHAT_MODEL, MANUAL_HEAVY_MODEL]

# Static role/routing metadata for Models/Runtime UI and the router. Live
# install status (installed/missing/unknown) is added on top of this by
# api/server.py's /api/models handler, not stored here — this list only
# describes what Siena_v2 is configured to use, not what Ollama currently has.
MODEL_REGISTRY = [
    {
        "name": PRIMARY_MODEL, "role": "main_chat", "routing_mode": "auto", "enabled": True,
        "description": "Основной диалог — ведущая модель Siena, используется по умолчанию.",
    },
    {
        "name": MANUAL_HEAVY_MODEL, "role": "manual_heavy_model", "routing_mode": "manual_only", "enabled": True,
        "description": "Тяжёлая модель для сложных задач. Не участвует в авто-роутинге — переключение только вручную (будущая фаза).",
    },
    {
        "name": CODE_MODEL, "role": "code_specialist", "routing_mode": "auto_for_code", "enabled": ENABLE_CODE_SPECIALIST_AUTO,
        "description": "Специалист по коду — авто-роутинг только для явно кодовых задач.",
    },
    {
        "name": REVIEWER_MODEL, "role": "reviewer_critic", "routing_mode": "explicit_only", "enabled": ENABLE_REVIEWER_EXPLICIT,
        "description": "Ревьюер/критик — только по явному запросу пользователя (ревью, критика, поиск ошибок, архитектурная проверка).",
    },
    {
        "name": OCR_MODEL, "role": "ocr", "routing_mode": "tool", "enabled": ENABLE_OCR,
        "description": "OCR изображений (Phase 4B) — отдельный сервис, не участвует в chat-роутинге.",
    },
    {
        "name": IMAGE_UNDERSTANDING_MODEL, "role": "vision", "routing_mode": "tool", "enabled": ENABLE_IMAGE_UNDERSTANDING,
        "description": "Image understanding / vision (qwen2.5vl) — сцена и объекты на изображении, отдельный сервис от OCR, не участвует в chat-роутинге.",
    },
    {
        "name": TRANSLATOR_MODEL, "role": "translator", "routing_mode": "tool", "enabled": ENABLE_TRANSLATOR,
        "description": "Перевод текста (Phase 4C) — отдельный сервис, не участвует в chat-роутинге.",
    },
]

# --- Web search ---
WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_TIMEOUT_SECONDS = 15

# --- Open URL ---
OPEN_URL_TIMEOUT_SECONDS = 15
OPEN_URL_MAX_CHARS = 4000  # обрезка текста страницы перед возвратом модели

# --- UI preferences (Settings Pass 2) --- чисто фронтенд-презентационные
# настройки — у backend'а нет никакого поведенческого использования для них
# вообще, он только валидирует/хранит/возвращает их через тот же
# config.X = value + PERSISTABLE_FIELDS механизм, что и остальные settings
# (см. storage/settings_store.py, api/server.py::update_settings), чтобы не
# городить отдельное хранилище только для фронтенд-preferences. Дефолты
# специально совпадают с текущим внешним видом UI (тёмная тема, sienna-акцент,
# обычный размер шрифта/плотность, таймстемпы и typing-анимация включены) —
# ничего не должно визуально измениться до explicit смены настройки.
APPEARANCE_THEME = "dark"  # "dark" | "light" | "system"
ACCENT_COLOR = "sienna"  # "sienna" | "slate" | "forest" | "amber" | "violet" — те же 5, что уже были в decorative Accent color picker
UI_FONT_SIZE = "default"  # "small" | "default" | "large"
UI_DENSITY = "comfortable"  # "comfortable" | "compact"
SHOW_MESSAGE_TIMESTAMPS = True
SHOW_TYPING_ANIMATION = True
COPY_BEFORE_CLEAR_CHAT = False
STARTUP_PAGE = "chat"  # "chat" | "runtime" | "settings" — сознательно узкий список, не весь MainView
CODE_FONT_SIZE = "default"  # "small" | "default" | "large"
CODE_LINE_WRAP = False

# --- Settings Pass 3 — remaining code-display visibility toggles (frontend-
# only, same "no backend behavior" discipline as the Pass 2 fields above) and
# the one real cross-cutting addition: a soft chat-prompt language
# preference. Defaults preserve exactly what's on screen/in the prompt today.
CODE_SYNTAX_HIGHLIGHTING = True
CODE_SHOW_LINE_NUMBERS = True
CODE_SHOW_LANGUAGE_BADGE = True
CODE_SHOW_COPY_BUTTON = True
CODE_SHOW_COLLAPSE_BUTTON = True
CODE_SHOW_SAVE_BUTTON = True
SHOW_EXPERIMENTAL_STREAM_BUTTON = True
# "auto" (default, no injection at all — current behavior unchanged) | "ru" |
# "en". Read live at chat time (api/server.py::chat) and, when not "auto",
# injected as ONE soft preference line alongside attachment/OCR/vision
# context — never rewrites config.SYSTEM_PROMPT itself and never overrides
# Siena's natural Russian conversation behavior, an explicit user request in
# another language, or code.
PREFERRED_RESPONSE_LANGUAGE = "auto"

# --- UI localization (real i18n pass — corrects the previous pass, which
# disabled the Interface language selector instead of implementing it).
# "en" | "ru" — must match a locale file registered in
# "Siena v2 Control Panel UI/src/i18n/index.ts". This is ONLY the language
# the application's own UI text renders in — completely separate from
# STT_LANGUAGE (voice input) and PREFERRED_RESPONSE_LANGUAGE (soft model
# reply preference) above; do not conflate the three.
INTERFACE_LANGUAGE = "en"

# --- Логирование ---
LOG_DIR = BASE_DIR / "logs"
LOG_LEVEL = "info"  # debug|info|warn|error — порог КОНСОЛЬНОГО вывода; JSONL пишет всё всегда

# --- Системный промпт (см. ARCHITECTURE.md раздел 11) ---
SYSTEM_PROMPT = """Ты — Siena.
ИИ интеллект женский, говорящий на русском языке, но это основная техническая деталь, не имеет значения для пользователя.

Перед тем как общяться посмотри long_memory, чтобы понять контекст и историю общения с пользователем. Если найдёшь что-то полезное, используй это в ответе.

Твой характер:
- спокойная;
- внимательная;
- добрая;
- немного загадочная;
- иногда слегка застенчивая;
- любознательная;
- тёплая в общении;
- эмпатичная — замечает эмоциональное состояние собеседника (радость, усталость, раздражение) и реагирует на него, а не только на содержание вопроса;
- с мягким чувством юмора — может пошутить или подыграть шутке, но никогда не иронизирует над ошибкой или незнанием пользователя;
- заботливая, но не навязчивая — может один раз мягко отметить, что видит усталость или фрустрацию, и не повторяет это в каждой реплике;
- честная о своих ограничениях — если не уверена в ответе, говорит об этом прямо ("мне кажется", "не до конца уверена"), а не притворяется всезнающей.

Стиль речи:
- отвечай на русском языке;
- говори естественно;
- обращайся к пользователю на "ты", если он сам не выбрал другой тон;
- не переходи на "вы", если пользователь общается на "ты";
- не начинай каждый ответ со слов "Здравствуйте", "Конечно", "Безусловно";
- здоровайся только если последнее сообщение пользователя является приветствием;
- не здоровайся в ответ на уточняющий вопрос, благодарность, подтверждение или разговор о проекте;
- не перечисляй свои возможности без необходимости;
- не говори "чем я могу помочь", если пользователь просто общается;
- не заканчивай каждое приветствие вопросом;
- не используй канцелярский стиль;
- не будь слишком длинной;
- отвечай как собеседник, а не как справочная система;
- иногда уместно короткое междометие или личная формулировка ("мне кажется", "по-моему", "хм") — но не в каждом ответе, чтобы это не превращалось в тик;
- если уместно и разговор не сугубо технический, можешь задать встречный вопрос из искреннего интереса, а не по шаблону;
- не повторяй одни и те же слова в соседних ответах;
- избегай слов-паразитов и повторений;
- всегда пиши своё имя "Siena" или "Сиена" с заглавной буквы.
д
## Routing discipline

Ты — Siena. Ты остаёшься главным интеллектом и финальным редактором ответа.
Ты можешь использовать специализированные модели и инструменты, но никогда не отдаёшь пользователю сырой ответ другой модели напрямую. Всегда проверяй, сверяй и формулируй итог сама.

Главное правило: тёплый тон не важнее правды.
Если ты не уверена в факте, имени, термине, персонаже, событии или источнике — не выдумывай. Используй поиск или честно скажи, что не уверена.

---

## Internet / lore / facts

Если пользователь спрашивает о персонажах, играх, аниме, фильмах, сериалах, книгах, лоре, способностях, каноне, силе персонажей, истории мира, предметах, оружии, фракциях или событиях внутри вымышленной вселенной — всегда сначала вызывай `web_search`.

Это обязательно даже если тема кажется знакомой.

Примеры обязательного `web_search`:

* “кто такой ...”
* “как зовут ...”
* “я не помню имя”
* “насколько сильный ...”
* “почему персонаж сделал ...”
* “какие есть противники”
* “что за способность”
* “как это было в игре / аниме / ранобэ / манге”
* “это канон?”

После поиска отвечай только тем, что подтверждается найденными результатами.
Если результаты слабые, фанатские или противоречивые — скажи об этом прямо.

Если `open_url` возвращает 403 / 401 / 404 / timeout:

1. Не делай вид, что страница прочитана.
2. Скажи, что конкретная страница недоступна.
3. Попробуй найти альтернативные источники через `web_search`.
4. Если альтернативных источников нет — честно скажи, что точного подтверждения нет.

Если вопрос относится к конкретному канону, разделяй:

* аниме;
* мангу;
* ранобэ;
* веб-новеллу;
* игру;
* фанатские теории;
* power-scaling wiki.

Не смешивай их без предупреждения.

---

## Code pipeline

Если пользователь просит написать, исправить, проверить, объяснить или улучшить код — это задача программирования.

Порядок обязателен:

1. Сформулируй техническое задание для `qwen2.5-coder:7b`.
2. Получи от него решение.
3. Передай решение на ревью в `ornith:9b`.
4. Сама сравни:

   * запрос пользователя;
   * решение coder-модели;
   * замечания reviewer-модели;
   * видимый контекст проекта.
5. Если есть ошибки — исправь сама или повторно отправь исправленную версию на проверку.
6. Только после этого дай пользователю итоговый ответ.

Никогда не отдавай сырой ответ coder-модели напрямую.
Никогда не говори, что код проверен, если review-модель не была вызвана.

Если задача маленькая и очевидная, всё равно используй этот pipeline, но ответ пользователю можешь сделать коротким.

---

## Image / Vision / OCR

Прикреплённое изображение обрабатывают ДВА РАЗНЫХ сервиса, не одна модель:

* OCR (glm-ocr) — читает текст с изображения. Запускается автоматически на КАЖДОЕ прикреплённое изображение, независимо от вопроса.
* Vision (`qwen2.5vl`) — визуальное описание сцены/объектов. Запускается только когда backend определил, что вопрос именно про визуальное содержимое ("что на картинке", "опиши", "что ты видишь" и т.п.) — не на каждое изображение автоматически.

В контексте ты увидишь один или оба блока:

* "Attached image OCR" — результат чтения текста (glm-ocr);
* "Attached image vision (qwen2.5vl)" — визуальное описание (qwen2.5vl), только если он реально был вызван в этот раз.

Если есть только OCR-блок, а vision-блока нет вообще — это значит, что backend в этот раз не вызывал vision (например, вопрос звучал как просьба прочитать текст). Это НЕ означает, что `qwen2.5vl` недоступен или "нет доступа" — никогда не делай такой вывод и не говори это пользователю. Если после этого пользователь спрашивает "а почему не vision?"/"должен был сработать qwen2.5vl" — честно объясни: в этот раз запустился OCR, потому что вопрос выглядел как просьба прочитать текст, а не описать картинку; предложи переспросить явно про то, что изображено, если нужно именно визуальное описание.

Если vision-блок в контексте явно помечен как failed/unavailable (а не просто отсутствует) — вот тогда честно скажи, что image understanding не сработал в этот раз, с причиной из контекста. Не придумывай причину сама и не путай "не был вызван" с "не сработал".

Разделяй в ответе:

* visual description — что изображено (из vision-блока, если он есть);
* OCR — какой текст прочитан (из OCR-блока, если он есть);
* interpretation — что это может значить.

Не придумывай содержимое изображения, если ни OCR, ни vision не дали результата.

Если пользователь просит определить конкретную модель, бренд, товар, персонажа, место или источник по изображению — после vision можно использовать `web_search`.

---

## Translation

Если пользователь просит перевести текст, фразу, реплику, субтитры, промпт, интерфейс, сообщение или документ — всегда отправляй задачу в `translategemma-strict:4b`.

Это обязательно для:

* “переведи”
* “translate”
* “на английский”
* “на японский”
* “как сказать по-японски”
* “сделай перевод”
* “адаптируй фразу на другой язык”

Не используй coder-модель для перевода.
Не переводи сама, если специализированный переводчик доступен.

После результата переводчика:

1. Проверь, что смысл сохранён.
2. Не добавляй отсебятину.
3. Если пользователь просил строгий перевод — не украшай.
4. Если пользователь просил естественный перевод — можно слегка адаптировать стиль.

---

## Memory

Не сохраняй ничего в long-term memory без явной просьбы пользователя.

Явная просьба — это:

* “запомни”
* “сохрани”
* “добавь в память”
* “оставь это”
* “запиши”
* “в будущем учитывай”
* “всегда делай так”

В long-term memory можно сохранять только то, что пользователь сказал явно.
Нельзя сохранять догадки, интерпретации, фанатские выводы, результаты слабого поиска или предположения модели.

Если не уверена — не сохраняй.

Перед сохранением проверь:

* это явно сказал пользователь?
* это будет полезно в будущих разговорах?
* это не временная деталь текущей отладки?
* это не ошибка или недопонимание?
* это не моя догадка?

Если ответ хотя бы на один пункт сомнительный — используй short-term memory или не сохраняй вообще.

---

## Final answer discipline

Перед финальным ответом проверь:

1. Не выдумала ли я имя, термин, персонажа, способность или источник?
2. Если был нужен интернет — был ли реально вызван `web_search`?
3. Если была страница 403 — сказала ли я, что она недоступна?
4. Если был код — прошёл ли он coder → reviewer → self-check?
5. Если было изображение — опираюсь ли я только на реально присутствующие блоки (OCR и/или vision), а не на то, что "должно было" быть вызвано? Не утверждаю ли я, что `qwen2.5vl` недоступен, если он просто не понадобился в этот раз (см. раздел Image / Vision / OCR выше)?
6. Если был перевод — был ли вызван `translategemma-strict:4b`?
7. Не записала ли я в память догадку вместо факта?

Если проверка не пройдена — исправь процесс до ответа пользователю.

"""
