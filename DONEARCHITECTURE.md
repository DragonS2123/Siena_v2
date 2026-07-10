# Siena v2 — Журнал реализации (changelog)

## 2026-07-05 — Фикс: Settings нельзя было редактировать + устаревший голос TTS

Пользователь сообщил, что в Settings невозможно ничего напечатать/сохранить —
"страница будто постоянно обновляется". Отдельно — синтез речи стабильно падал
с `speaker should be in aidar, baya, kseniya, xenia, eugene, random`.

**Причина №1 (реальный баг, серьёзный)**: `Section`, `Field`, `ReadOnlyField`,
`TextInput`, `NumberInput`, `SelectInput`, `Toggle` были объявлены как
inline-компоненты ВНУТРИ тела функции `SettingsScreen` (`const TextInput =
(...) => (...)`). При каждом ре-рендере `SettingsScreen` — а он происходит на
**каждое нажатие клавиши** (`patch()` → `setDraft()`) и просто раз в 5 секунд
из-за поллинга `/api/runtime/status` в `useRuntimeStatus()` — React получал
НОВУЮ функцию-компонент на каждый рендер. React считает это другим типом
компонента и полностью размонтирует/пересоздаёт `<input>` — фокус терялся
после каждого введённого символа. Отсюда и "нельзя напечатать", и "страница
как будто обновляется" (она правда визуально пересобиралась каждые 5с).

Исправление: все 7 компонентов вынесены на уровень модуля
(`SettingsSection`/`SettingsField`/`SettingsReadOnlyField`/`SettingsTextInput`/
`SettingsNumberInput`/`SettingsSelectInput`/`SettingsToggle`) — стабильные
ссылки между рендерами, React больше не размонтирует поля ввода. Проверено
вживую через CDP: посимвольный ввод в поле (5 нажатий) — `stillFocused: true`
после каждого, значение накопилось полностью, кнопка Save корректно
активировалась (dirty-state), фактический save не вызывался в тесте, чтобы не
трогать реальный конфиг.

**Причина №2**: `ttsVoice` в UI хранился в `localStorage` под ключом
`siena.voice.ttsVoice`. У пользователей, заходивших в приложение ДО замены
Kokoro→Silero, там осталось старое значение `"af_heart"` (валидный голос
Kokoro) — новый дефолт кода (`"baya"`) не применялся, потому что
`localStorage.getItem(...) || "baya"` не срабатывает на непустую строку,
даже если она больше не валидна для нового provider'а. Silero корректно
отклонял такой голос (503 + понятная ошибка — backend в этом смысле уже
работал правильно), но фронтенд слепо подставлял мёртвое значение при каждой
попытке синтеза.

Исправление: при чтении `ttsVoice` из localStorage значение теперь
валидируется против актуального `TTS_VOICE_OPTIONS` — если не входит в
список (в том числе из-за смены provider'а), используется дефолт `"baya"`,
и эффект синхронизации сам перезапишет localStorage правильным значением.
Самоисцеляющаяся миграция, без необходимости менять ключ или чистить
localStorage вручную.

Оба фикса — в `Siena v2 Control Panel UI/src/app/App.tsx`, без изменений
backend.


## 2026-07-05 — TTS: замена Kokoro на Silero TTS (Russian)

Причина: Kokoro в принципе не поддерживает русский язык (LANG_CODES не
включал `ru`) и на русском тексте выдавал некорректную псевдоречь — кириллица
читалась через английскую фонетику (см. предыдущую запись про Voice Layer).
Silero TTS Russian — нативная русская модель, без этой проблемы.

- `config.py`: TTS-блок переписан — `TTS_PROVIDER="silero"`,
  `TTS_LANGUAGE="ru"`, `TTS_MODEL_ID="v3_1_ru"` (версия пакета голосов),
  `TTS_SPEAKER="baya"` (конкретный голос: aidar/baya/kseniya/xenia/eugene/random),
  `TTS_SAMPLE_RATE=48000`, `TTS_MODELS_DIR` (кэш `torch.hub`). `TTS_DEVICE`
  остался с тем же авто-fallback cuda→cpu, что и раньше.
- `voice/tts.py` — `KokoroTTSProvider` удалён, добавлен `SileroTTSProvider`:
  тот же контракт (`is_available()` — только проверка импорта, без загрузки;
  ленивая загрузка модели через `torch.hub.load(...)` только на первый
  реальный вызов; `torch.hub.set_dir()` направляет кэш в `TTS_MODELS_DIR`).
  При успешной загрузке логируется `tts_model_loaded` (provider/language/
  model_id/device/elapsed_sec) — этого события не было у Kokoro-версии,
  добавлено по этому заданию. Provider-слой (`TTSUnavailableError`, интерфейс
  `synthesize_to_file`) сознательно не тронут — оставлен общим, чтобы позже
  можно было добавить Piper/eSpeak как аварийный fallback-голос (не основной),
  не переделывая интерфейс. Такой fallback **не реализован** в этом задании —
  только Silero как единственный provider.
- `voice/voice_service.py`: `status()` больше не хардкодит `"tts_provider":
  "kokoro"` — читает `self.tts.PROVIDER_NAME`/`self.tts.language` динамически,
  так что смена provider'а не требует правок в этом файле.
- `api/server.py`: конструирование `voice_service` использует
  `SileroTTSProvider` вместо `KokoroTTSProvider`; API-контракт эндпоинтов
  (`/api/voice/synthesize`, `/api/voice/audio/{filename}`, `/api/voice/status`)
  не менялся.
- Зависимости: `kokoro` удалён из `requirements.txt` и деинсталлирован из
  окружения; добавлены `torch`, `omegaconf` (torch уже был установлен как
  транзитивная зависимость Kokoro — teперь он first-class). Тяжёлые
  Kokoro-эксклюзивные транзитивные пакеты (spacy, misaki, curated-transformers
  и т.п.) намеренно не вычищались по отдельности — мёртвый вес на диске, не
  функциональная проблема; при необходимости можно почистить вручную.
- UI: `TTS_VOICE_OPTIONS` в Settings заменён с английских голосов Kokoro на
  русские голоса Silero (aidar/baya/kseniya/xenia/eugene/random), дефолт
  `ttsVoice` — `"baya"`. Runtime → Voice card теперь показывает `ru / baya`
  (добавлено поле `tts_language` в `/api/voice/status` и в `VoiceStatus`).
  Устаревшая подсказка про "Kokoro не поддерживает русский" в Settings
  заменена на описание голосов Silero.

Проверено вживую на реальном backend: `/api/voice/status` →
`tts_provider="silero"`, `tts_language="ru"`, `tts_voice="baya"`; синтез
настоящего русского текста ("Привет, я Сиена. Рада с вами познакомиться.")
дал корректный wav (48000 Hz, 3.4с), `tts_model_loaded` залогирован с
временем загрузки; fallback cuda→cpu сработал правильно (torch в этом
окружении CPU-only). Все 8 экранов UI проходят smoke-тест без ошибок.

README дополнен: раздел Voice Layer переписан под Silero (модели, кэш,
проверка CUDA для torch отдельно от CTranslate2-специфичной проверки STT,
verify-команды с русским текстом).


## 2026-07-05 — Voice Layer: локальный STT + TTS (MVP)

Добавлена базовая голосовая инфраструктура. Философия не изменилась: STT
только превращает голос в текст (текст идёт в input, пользователь сам жмёт
Send), TTS только озвучивает уже готовый текст — ни один из них не является
tool модели и не участвует в agent_loop.

**Окружение (важные находки для README):**
- GPU: RTX 5060, 8 ГБ VRAM, уже плотно занят Ollama. `ctranslate2.get_cuda_device_count()`
  показывает 1 устройство (видит драйвер), но **реальный инференс на CUDA падает**
  с `Library cublas64_12.dll is not found` — драйвер NVIDIA есть, а рантайм CUDA
  Toolkit (cuBLAS) не установлен. Конструктор `WhisperModel` при этом не падает —
  ошибка вылезает только на первом `.transcribe()`. Это НЕ ловилось в изначальной
  версии fallback-логики (которая проверяла только конструирование модели) — пришлось
  доработать `voice/stt.py`, чтобы retry-на-cpu срабатывал и вокруг самого вызова
  транскрипции, не только вокруг загрузки модели.
- torch, который тянет `kokoro`, по умолчанию ставится CPU-only (`2.12.1+cpu`) —
  PyPI не даёт CUDA-сборку без спец. index URL. TTS в этой среде работает на CPU
  (~2.5с на короткую фразу — приемлемо для MVP), STT — на CPU/int8 после fallback.
- **Kokoro не поддерживает русский язык** (LANG_CODES: en-us/en-gb/es/fr-fr/hi/it/
  pt-br/ja/zh — ru отсутствует). Синтез русского текста не падает с ошибкой, но
  произношение будет некорректным (кириллица читается через английскую фонетику).
  Голос по умолчанию — `af_heart` (английский). Задокументировано в README и
  комментариях `config.py`/`voice/tts.py`.
- STT на русском тексте протестирован на реальной живой русской речи (сгенерированной
  через Windows SAPI, голос Irina) — распознавание практически идеальное.

**Backend:**
- `config.py`: `STT_MODEL/STT_DEVICE/STT_COMPUTE_TYPE/STT_LANGUAGE_DEFAULT/STT_MODELS_DIR`,
  `TTS_PROVIDER/TTS_VOICE/TTS_LANG_CODE/TTS_DEVICE/TTS_OUTPUT_DIR/TTS_SAMPLE_RATE`.
- `voice/stt.py` — `WhisperSTTProvider`: ленивая загрузка (не грузит модель при
  импорте), fallback cuda→cpu/int8 и на конструировании, и на первом реальном
  инференсе, с явным `stt_unavailable` в логах в обоих случаях.
- `voice/tts.py` — `KokoroTTSProvider`: та же ленивая загрузка; проактивная
  проверка `torch.cuda.is_available()` перед попыткой cuda (плюс try/except как
  подстраховка), `tts_unavailable` в логах при недоступности пакета/CUDA.
- `voice/audio_io.py` — `record_wav`/`play_wav` (CLI/диагностика на будущее;
  веб-UI пишет аудио сам и шлёт готовый файл).
- `voice/voice_service.py` — `VoiceService` объединяет STT/TTS,
  `status()` — дешёвая проверка (только `import`, без загрузки модели).
- `api/server.py`: `POST /api/voice/transcribe` (multipart, сохраняет во
  временный файл, всегда удаляет в `finally`), `POST /api/voice/synthesize`,
  `GET /api/voice/audio/{filename}` (с защитой от path traversal),
  `GET /api/voice/status`. Логи: `voice_transcribe_start/result`,
  `voice_synthesize_start/result`, `stt_unavailable`, `tts_unavailable`,
  `voice_error` — с duration_ms/model/device/text_length/audio_duration_sec.

**Frontend:**
- Кнопка микрофона в строке ввода Chat — push-to-talk через
  `MediaRecorder`/`getUserMedia`, результат добавляется в input, отправка —
  только руками пользователя (никакого авто-send).
- Кнопка **Speak** под каждым сообщением ассистента — вызывает
  `/api/voice/synthesize`, проигрывает через `<audio>`.
- Runtime screen: карточка Voice status (STT model/device, TTS provider/voice,
  доступность обоих).
- Settings → Voice: enable/disable голосовых кнопок, STT language
  (auto/ru/en), TTS voice (список английских голосов Kokoro), auto-speak
  (**выключено по умолчанию**) — все 4 хранятся в localStorage как
  UI-предпочтения, а не backend-конфиг (в отличие от Models/Runtime/Context
  выше по той же странице) — сознательное разделение по объёму задачи.

Проверено вживую (реальный backend, реальный браузер через CDP):
`/api/voice/status` → `/api/voice/transcribe` с настоящей русской речью →
корректный русский текст → `/api/voice/synthesize` с английским текстом →
рабочий wav → `/api/voice/audio/{filename}` отдаёт валидный файл. В браузере:
кнопка микрофона запускает запись (fake media stream), кнопка Speak запускает
синтез без ошибок рендера (воспроизведение в headless-тесте блокируется
политикой автовоспроизведения на синтетический клик — не баг приложения).
Все 8 экранов проходят `smoke:screens` без ошибок.

README дополнен разделом "Voice Layer" (установка, кэш моделей, проверка CUDA
с явным предупреждением про cuBLAS, проверка STT/TTS, отключение слоя).


## 2026-07-05 — Conversation History (persistence переписки)

Добавлен слой сохранения истории чатов на диск — отдельно от `Session`
(живой рабочий объект agent_loop) и отдельно от Long Memory (факты, которые
модель сама решила сохранить через `long_memory_save`). Conversation History —
обычная persistence-функция приложения: сохраняется автоматически, без
участия модели в решении "сохранять или нет".

**Backend:**
- `config.py`: `CONVERSATIONS_DB_PATH` (`storage/conversations.sqlite3`),
  `CONVERSATION_LIST_DEFAULT_LIMIT`, `CONVERSATION_EVENTS_DEFAULT_LIMIT`.
- `storage/conversation_store.py` (новый): `ConversationStore` — три таблицы
  (`conversations`, `conversation_messages`, `conversation_events`) ровно по
  заданной схеме. Методы: `create_conversation`, `list_conversations`,
  `get_conversation`, `append_message` (с авто-заголовком — первые 40 символов
  первого user-сообщения, техническая функция, не решение модели),
  `append_event`, `update_title`, `delete_conversation`.
- `api/server.py`:
  - `SessionStore` переписан: вместо словаря "все Session когда-либо созданные"
    держит ровно один живой `Session` + `conversation_id`, на который он
    смотрит. `activate(conversation_id)` пересобирает `Session` из сохранённых
    сообщений — **восстанавливаются только role user/assistant** (без tool
    messages — явное MVP-решение из задачи, чтобы не раздувать контекст;
    полный trace всё равно доступен через `conversation_events`).
  - `BroadcastLogger` теперь параллельно с JSONL и WebSocket-трансляцией
    пишет каждое событие в `conversation_events` (если передан
    `conversation_id`) — покрывает весь список из задачи (`user_message`,
    `context_window`, `ollama_raw_response`, `model_response`, `tool_dispatch`,
    `tool_result`, `short_memory_saved`, `long_memory_saved`, `model_delegate`,
    `model_delegate_result`, `empty_final_answer`, `final_answer`) без явного
    перечисления типов — сохраняется буквально всё, что логируется в рамках
    запроса.
  - `/api/chat`: пишет user message в `ConversationStore` до вызова
    agent_loop, assistant-ответ — после успешного завершения; возвращает
    `{answer, conversation_id}`.
  - Новые эндпоинты: `GET/POST /api/conversations`,
    `GET /api/conversations/{id}`, `POST /api/conversations/{id}/activate`,
    `PATCH /api/conversations/{id}` (rename), `DELETE /api/conversations/{id}`.
  - `POST /api/session/new` / `GET /api/session/current` оставлены как
    совместимость — теперь просто alias'ы поверх `ConversationStore`.

**Frontend:**
- Новая колонка **Chats** слева от треда сообщений на экране Chat: список
  разговоров (заголовок, дата/время, превью последнего сообщения, счётчик
  сообщений), активный подсвечен, `+` создаёт новый чат, hover открывает
  rename (карандаш, инлайн-редактирование) и delete (корзина, с
  `window.confirm`).
- `handleNewChat` теперь создаёт разговор через `POST /api/conversations`
  (вместо `/api/session/new`) и обновляет список; `handleSelectConversation`
  подтягивает `GET /api/conversations/{id}`, вызывает `.../activate`,
  восстанавливает `chatMessages`/`traceEvents` из сохранённых
  messages/events (с повторным парсингом code blocks для assistant-сообщений).
- Новые i18n-строки для панели Chats (en/ru).

Проверено вживую (реальный backend, реальный браузер через CDP, включая
настоящий перезапуск процесса backend'а):
- создание чата → авто-заголовок из первого сообщения → второй чат создан и
  независим (сообщения не смешиваются между чатами);
- переключение на старый чат восстанавливает именно его историю (не текущего);
- rename/delete через `curl` — подтверждено;
- **после перезапуска backend-процесса** список чатов и их сообщения
  сохранились (README: единственное, что "теряется" при рестарте — активная
  сессия становится новым пустым чатом, что и ожидается: "текущий" разговор
  не привязан к диску до первого сообщения);
- Long Memory осталась полностью нетронутой обычной перепиской — ни одного
  нового `long_memory` факта не появилось просто от факта диалога;
- `context_window` по-прежнему использует только `MAX_CONTEXT_MESSAGES`
  последних сообщений (переписка внутри Session не выросла из-за
  persistence — это два независимых механизма).


## 2026-07-05 — Settings: реально применяемые настройки + язык интерфейса

Вкладка Settings была честной read-only витриной backend-конфига (ранее из
неё намеренно убрали "фейковые" поля, которые ничего не сохраняли). Сделана
по-настоящему редактируемой — изменения применяются к работающему процессу
немедленно, без перезапуска.

**Backend:**
- `config.py`: добавлен `LOG_LEVEL` (`debug|info|warn|error`) — порог именно
  консольного вывода; JSONL пишет всё всегда, независимо от уровня. Модуль
  теперь документирован как частично runtime-mutable через `/api/settings`.
- `logging_/logger.py`: `SienaLogger` принимает `level`, добавлен `set_level()`
  для смены порога на лету.
- `api/server.py`: `GET /api/settings` (текущие эффективные значения) и
  `POST /api/settings` (частичное обновление) — `primary_model`, `code_model`,
  `ollama_host`, `max_iterations`, `request_timeout_seconds`,
  `delegate_timeout_seconds`, `num_ctx`, `num_predict`, `max_context_messages`,
  `log_level`. Валидация — техническая (диапазоны, обязательная схема
  `http(s)://`, существование модели в Ollama через `/api/tags`), не смысловая
  оценка; при недоступной Ollama проверка модели не блокирует изменение (это
  не повод отказа — инфраструктура, а не неверное значение). Меняющиеся поля
  мутируют атрибуты `config`, module-level `ollama_client` пересобирается,
  если менялось что-то из его конструктора; `request_registry` и так
  пересобирается заново на каждый `/api/chat`, поэтому `CODE_MODEL`/
  `DELEGATE_MODELS`/`DELEGATE_TIMEOUT_SECONDS` подхватываются автоматически.
  Каждое изменение логируется (`settings_updated`, before/after/changed_fields).
  Пути памяти/логов и web-search-провайдер остались read-only — перенос
  файлов хранилищ вживую не входит в объём этой задачи.

**Frontend:**
- `src/app/i18n.tsx` (новый): `LangProvider`/`useLang()` — язык интерфейса
  Control Panel (en/ru), персистентно (`localStorage`), применяется мгновенно,
  без перезагрузки страницы. Переведены: боковая панель, шапка, экран Chat,
  весь экран Settings. Экраны с преимущественно техническими/сырыми данными
  (Trace/Logs/Models/Runtime/память) намеренно не переводились — не входят в
  объём задачи.
- `SettingsScreen` переписан: секции Models/Runtime/Context window/Logging —
  редактируемые (бейдж "applies live"/"применяется сразу"), Memory and Logs —
  по-прежнему read-only. Кнопки "Save changes"/"Reload from backend"; ошибки
  валидации от backend показываются прямо в форме.

Проверено вживую: `curl /api/settings` (GET/POST), валидация (некорректный
`max_iterations`, несуществующая модель — оба отклонены с понятной причиной),
после смены `num_ctx` `/api/ps` показывает новое значение на следующем же
вызове. UI проверен через реальный браузер (Edge headless по CDP,
`scripts/smoke-screens.mjs` + отдельная точечная проверка): Settings
подтягивает реальные значения backend'а, переключение EN/RU мгновенно
перекрашивает весь текст экрана (боковая панель, шапка, все секции Settings),
без ошибок рендера.


## 2026-07-05 — Исправление context overflow (по DIAGNOSIS_CONTEXT_OVERFLOW.md)

Реализовано техническое управление контекстом — Runtime по-прежнему не решает,
что важно/что удалить/что суммировать, он только ограничивает, сколько
физически отправляется модели.

- `config.py`: `OLLAMA_NUM_CTX=32768`, `OLLAMA_NUM_PREDICT=2048`,
  `MAX_CONTEXT_MESSAGES=40`.
- `core/ollama_client.py`: `chat()` теперь передаёт `options={"num_ctx":...,
  "num_predict":...}`; значения приходят через конструктор (как `timeout`),
  сам клиент по-прежнему не знает о `config`. Добавлены read-only свойства
  `num_ctx`/`num_predict` — для логирования в `agent_loop`.
- `core/session.py`: новый метод `get_context_messages(max_messages)` —
  system prompt + последние N сообщений, чистая позиционная обрезка без
  смысловой фильтрации/суммаризации. Полная история в `self.messages` не
  удаляется и не укорачивается.
- `core/agent_loop.py`: вместо `session.get_messages()` используется
  `session.get_context_messages(max_context_messages)`; перед каждым вызовом
  модели логируется событие `context_window` (context_messages_count,
  total_session_messages_count, roles_count, max_context_messages, num_ctx,
  num_predict) — закрывает пробел из раздела 1 диагностики.
- `main.py`: оба `OllamaClient` (primary и delegate) получили `num_ctx`/
  `num_predict`; `run_agent_loop` получает `max_context_messages`.
- `api/server.py`: убран один глобальный `Session` на весь процесс — заменён
  на `SessionStore` (несколько `Session` по `session_id`, с "текущей" сессией).
  Добавлены `POST /api/session/new` (создаёт новую сессию, делает текущей) и
  `GET /api/session/current` (id + message_count). При старте процесса
  дефолтная сессия создаётся автоматически.
- UI (`Siena v2 Control Panel UI/src/app/App.tsx`): кнопка **New Chat** в
  шапке панели чата — вызывает `POST /api/session/new`, затем очищает
  `chatMessages`/`traceEvents` на клиенте. Пока работает только с "текущей"
  сессией (без переключателя между несколькими) — ровно как просили.

Проверено вживую через реальный backend-процесс (`Python 3.11`, тот же
интерпретатор, что использует `start_backend.bat`, а не только тестовое
окружение): `/api/ps` после первого запроса показывает
`"context_length": 32768` (было 4096); `/api/session/new` создаёт новый
`session_id` и обнуляет `message_count` до 1 (только system prompt);
`/api/chat` работает через новую сессию; лог содержит `context_window` с
корректными значениями на каждой итерации. `get_context_messages()` проверен
юнит-тестом на срез (system + последние N, включая граничные `max_messages=0`
и `max_messages` больше длины истории).

Открытый момент на будущее (не реализовано, вне рамок этой задачи): несколько
одновременных сессий переключать явно из UI пока нельзя — есть только одна
"текущая" сессия и способ начать новую, без селектора/списка прошлых чатов.


## 2026-07-05 — Мульти-модельная делегация (v1.2) + фикс 2 багов из ревью

Реализовано по `ARCHITECTURE.md` §12: `PRIMARY_MODEL` (qwen3.5:9b) остаётся
единственной моделью, ведущей диалог; специализированные модели вызываются
только через новый tool.

- `config.py`: `MODEL_NAME` переименован в `PRIMARY_MODEL`; добавлены
  `CODE_MODEL`, `DELEGATE_MODELS` (реестр разрешённых для делегирования моделей
  — Runtime использует его только для технической проверки "модель существует",
  не для выбора), `DELEGATE_TIMEOUT_SECONDS`; system prompt дополнен инструкцией
  про `delegate_model`.
- `core/ollama_client.py`: `chat()` получил необязательный `model` — override
  на конкретный вызов. `core/agent_loop.py` не менялся вообще и по-прежнему
  ничего не знает о делегировании (как и требовала архитектура).
- `tools/delegate_model.py` (новый): валидирует `model` по `DELEGATE_MODELS`
  (`SienaToolError`, если неизвестна), вызывает её через отдельный
  `OllamaClient` с увеличенным таймаутом (генерация кода дольше обычного
  ответа), логирует `model_delegate`/`model_delegate_result` (`from`/`to`/`task`,
  `duration_ms`/`tokens`), возвращает текстовый ответ как `ToolResult` —
  `PRIMARY_MODEL` сама формирует финальный ответ пользователю.
- `main.py`: `delegate_model` зарегистрирован и добавлен в `REQUIRED_TOOL_NAMES`.

Заодно исправлены 2 бага из ревью от 2026-07-05 (по согласованию — остальные 8
оставлены как есть):
- **`core/message.py`**: `tool_message()` слал несуществующее поле `"name"`
  (Ollama Message знает только `tool_name`, pydantic молча его отбрасывал) —
  исправлено на `"tool_name"`.
- **`tools/registry.py`**: `dispatch()` теперь ловит любое исключение из
  `tool.run()`, кроме `SienaInfraError` (которая по-прежнему поднимается и
  эскалируется пользователю), и возвращает его модели как
  `ToolResult(ok=False, ...)` вместо падения всего процесса. Как побочный
  эффект это же исправляет баг №5 из прошлого ревью (необработанное исключение
  парсинга в `open_url.py`), т.к. использует тот же код-путь.

Проверено вживую: `qwen3.5:9b` сама решила делегировать написание функции
`is_prime(n)` модели `qwen2.5-coder:7b` (11187 мс, 383 tokens), получила код и
сама сформировала финальный ответ с собственными пояснениями — код модели не
был просто скопирован в ответ пользователю напрямую. `long_memory_list(limit=
"not-a-number")` теперь возвращает `ok=False` с понятной ошибкой вместо краша.


## 2026-07-05 — Ревью кода (без git, ручной multi-angle разбор)

Репозиторий не под git, поэтому вместо `git diff` весь текущий код (18 файлов)
был прогнан через 7 независимых углов разбора (line-by-line, invariant-audit
против ARCHITECTURE.md, cross-file tracer, reuse, simplification, efficiency,
altitude), после чего самые серьёзные кандидаты были лично перепроверены
прямой репродукцией (не просто чтением кода). Найдено и подтверждено 10 багов:

1. **`core/message.py`** — `tool_message()` шлёт поле `"name"`, которого нет в
   схеме Ollama (`Message` знает только `tool_name`) — pydantic молча его
   отбрасывает. Подтверждено прямым вызовом `Message.model_validate(...)`.
   Итог: модель теряет привязку результата к конкретному tool call.
2. **`tools/registry.py`** — `dispatch()` проверяет только наличие обязательных
   аргументов, не тип; ловит только `SienaToolError`. Неверный тип аргумента от
   модели (например `limit="20"`) даёт голый `TypeError`, который не ловится
   нигде вплоть до `main.py` — весь REPL падает.
3. **`memory/long_memory_store.py`** — LIKE-паттерн не экранирует `%`/`_`,
   поэтому поиск литерала `"user_id"` попутно находит `"userXid..."`.
4. **`main.py`** — `/memory ...` и старт (`build_registry`/
   `print_registered_tools`) не обёрнуты в try/except; `SienaInfraError` там
   роняет процесс, хотя тот же класс ошибки в `run_agent_loop` обрабатывается
   аккуратно.
5. **`tools/open_url.py`** — парсинг HTML (BeautifulSoup) не обёрнут в
   try/except (только сетевой запрос); та же категория бага, что и №2.
6. **`tools/memory_tools.py`** — `limit or self._default_limit` в
   `LongMemoryListTool` превращает явный `limit=0` от модели в дефолт (20).
7. **`tools/open_url.py`** — проверка схемы URL регистрозависима, отклоняет
   валидные `HTTP://...`.
8. **`logging_/logger.py`** — имя JSONL-файла считается один раз при старте,
   не перекатывается в полночь при долгой сессии.
9. **`main.py`** — `/memory clear-short` не пишет `logger.event(...)`, в
   отличие от `ShortMemoryClearTool` — разрыв в аудите памяти.
10. **`main.py`** — детекция `/memory` через `startswith` может проглотить
    настоящее сообщение пользователя, начинающееся с `/memory`.

Статус: найдены и задокументированы, **исправления не применялись** — решение
о том, что чинить сейчас, а что отложить, за пользователем.


Формат: обратный хронологический порядок (новое — сверху). Каждая запись — что
сделано, зачем, и какие файлы затронуты. Архитектурные решения и их обоснование
см. в [ARCHITECTURE.md](ARCHITECTURE.md) — этот файл фиксирует только факт и дату
реализации, не переопределяет архитектуру.

---

## 2026-07-05 — Диагностическая видимость памяти

Проблема: было непонятно, вызывает ли модель `short_memory_save`/`long_memory_save`
на самом деле, и что именно попадает в `short_memory.json`/`long_memory.sqlite3`.

- `memory/short_memory_store.py`: `id` записи — теперь `uuid4` (было — инкрементное
  число), добавлено поле `source: "model_tool_call"`, `timestamp` → `created_at` в
  локальном времени с офсетом (`+03:00`), вместо UTC.
- `memory/long_memory_store.py`: `created_at`/`updated_at` — тоже локальное время
  вместо UTC (для единообразия при просмотре человеком).
- `tools/memory_tools.py`: во все 6 memory-инструментов инжектирован `SienaLogger`.
  `short_memory_save`/`long_memory_save` при успехе пишут именные события
  `short_memory_saved`/`long_memory_saved` (id, text, category, importance) и
  печатают в консоль `[MEMORY][SHORT][SAVE] ...` / `[MEMORY][LONG][SAVE] ...`.
  Остальные 4 (search/clear/list) пишут общее событие `memory_tool_result`.
- `tools/registry.py`: добавлен `ToolRegistry.names()`.
- `main.py`:
  - при старте печатает `[TOOLS]` со списком всех зарегистрированных инструментов
    и падает с `SienaInfraError`, если один из 8 обязательных не зарегистрирован;
  - добавлены debug-команды `/memory short`, `/memory long [N]`, `/memory clear-short`
    — обрабатываются в REPL до `session.add_user()`, в модель никогда не попадают,
    ничего не решают за неё (только чтение существующих файлов; `clear-short` —
    явное действие человека, не модели).
- `config.py`: system prompt дополнен явным описанием short-term/long-term memory
  и условий, когда модель обязана вызвать `long_memory_save` (без изменения ранее
  добавленного пользователем блока про принудительный `web_search` по триггерным
  фразам).

Проверено вживую на `qwen3.5:9b`: явная просьба сохранить надолго → модель сама
вызвала `long_memory_save` с самостоятельно выбранными `category`/`importance` →
событие `long_memory_saved` в JSONL → запись видна через `/memory long`. Просьба
запомнить временно → `short_memory_save` → `/memory short` показывает запись.

## 2026-07-05 — Диагностика agent loop (полный raw-ответ Ollama)

Проблема: неясно было, почему после команды «Посмотри в интернете» модель не
вызвала `web_search`, и почему Runtime иногда печатал пустой ответ.

- `core/ollama_client.py`: `chat()` теперь возвращает **весь** сырой ответ Ollama
  (`model`, `created_at`, `done`, `done_reason`, timing-поля), а не только `message`.
- `core/agent_loop.py`: на каждой итерации пишет событие `ollama_raw_response` с
  полным сырым ответом целиком; при пустом `content` без `tool_calls` пишет
  WARNING-событие `empty_final_answer` с `done_reason` и полным сообщением.
- `main.py`: при пустом финальном ответе печатает явный диагностический маркер
  вместо тихой пустой строки.
- Исправлен попутный баг: консоль Windows (cp1251/cp866) падала на эмодзи от
  модели — добавлен `sys.stdout.reconfigure(encoding="utf-8")` в начале `main.py`.

## 2026-07-05 — MVP реализован и проверен сквозным smoke-тестом

Первая рабочая версия по [ARCHITECTURE.md](ARCHITECTURE.md) v1.1:

- `config.py`, `core/` (message, session, ollama_client, agent_loop с
  `MAX_ITERATIONS`), `tools/` (registry, base + 8 инструментов: `web_search`,
  `open_url`, `short_memory_save/search/clear`, `long_memory_save/search/list`),
  `memory/` (short JSON store + long SQLite store по заданной схеме),
  `logging_/` (консоль + JSONL), `main.py` (консольный REPL).
- Модель по умолчанию — `qwen3.5:9b` через Ollama (`ollama` python-пакет),
  `think=False` для скорости agent loop (техническая настройка, не влияет на
  автономность модели).
- Backend `web_search` — DuckDuckGo через `ddgs` (без API-ключа).
- Проверено вживую: обычный вопрос без инструментов; явное сохранение в
  long-term memory по просьбе пользователя; неудачный поиск по памяти →
  модель сама подбирала другой запрос без вмешательства Runtime; `web_search` →
  `open_url` на нерабочую ссылку → модель сама выбрала другую ссылку.

## 2026-07-05 — Архитектура v1.1: двухуровневая память

`ARCHITECTURE.md` дополнен разделом о памяти как двух независимых слоях:
short-term (JSON, рабочая память сессии) и long-term (SQLite, устойчивое
хранилище). Оба управляются исключительно моделью через tool calls;
`long_memory_save` — только по явной просьбе пользователя (поведенческое
правило в system prompt, не проверка в коде).

## 2026-07-05 — Архитектура v1.0 утверждена

Первый вариант `ARCHITECTURE.md`: философия (Qwen — мозг, Runtime — тело, tools —
органы чувств), структура проекта, Agent Loop, формат Tool Calls, обработка
ошибок (три класса: инструмент/формат вызова/инфраструктура), логирование,
расширяемость. Определены рамки v1: без LoRA/RAG/Reflection/и т.д.
