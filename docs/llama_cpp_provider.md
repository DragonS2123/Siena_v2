# Inference provider для Linux

Siena использует `llama-server` через OpenAI-compatible HTTP API. Provider
остаётся транспортом и не управляет процессами. Управление теперь выполняет
runtime через [LlamaCppProcessManager](llama_cpp_lifecycle.md). Внешний режим
доступен с `llama_cpp_managed=false`.

## Граница слоя

`ModelProvider` (`core/model_provider.py`) описывает `health`, `catalog`,
`diagnostics`, `chat`, `generate` и `stream_chat`. Вызовы поддерживают JSON
`response_format` и выбор tools. Отмена streaming-запроса выполняется отменой
async task или `aclose()` итератора; цепочка chat → timeout wrapper → provider
закрывает HTTP-соединение. Синхронный `chat` имеет конечный transport timeout;
отмена ожидающей `asyncio.to_thread` задачи сама по себе не прерывает этот
синхронный запрос. Это прежнее ограничение non-streaming agent loop.

`create_provider` выбирает transport по неизменяемому snapshot настроек.
ChatService, reviewer, продолжения ответов и delegate_model используют эту
фабрику. Agent loop получает `ModelProvider`, сохраняя существующий формат
сообщений, session history, tool dispatch и диагностику генерации.

`LlamaCppProvider` переводит аргументы tools между JSON string OpenAI API и
dict Siena, переносит `tool_call_id` в tool results, собирает фрагменты SSE tool
calls до завершения ответа и возвращает нормализованные `done_reason`, token
counts и timing-поля. Content/thinking в streaming — дельты; terminal chunk
содержит complete tool calls и usage, без повторения текста ответа. Ошибки
транспорта и повреждённые ответы становятся `SienaInfraError` / `SienaTimeoutError`.

Обходы b11429 находятся только внутри `LlamaCppProvider`:

- `tool_choice="none"`: definitions tools и поле tool_choice не передаются;
- specific function object: остаётся единственный выбранный tool,
  wire tool_choice становится `"required"`;
- `"auto"`: передаётся обычный список tools.

`OllamaProvider` адаптирует неизменённый `OllamaClient`. Старые chat/streaming
вызовы и формат истории сохранены. Дополнительные JSON/explicit tool-choice
опции адаптер явно отклоняет: существующий legacy client их не реализует.
`ModelCatalog` остаётся legacy-каталогом; runtime использует `ProviderCatalog`.

## Конфигурация

Новые Linux settings используют `llama_cpp`, Gemma и профиль 16K. Windows
defaults и старые назначения моделей сохранены. Файл прежнего формата,
содержащий `ollama_host`, сохраняет legacy transport, роли и context: перенос
не переключает существующий runtime незаметно. Текущий локальный
`storage/settings.json` не изменён.

Настройки целевого runtime:

```json
{
  "inference_provider": "llama_cpp",
  "llama_cpp_host": "127.0.0.1",
  "llama_cpp_port": 8088,
  "llama_cpp_managed": true,
  "llama_cpp_model": "gemma-4-26B-A4B-it",
  "llama_cpp_profile": "normal",
  "context_size": 16384
}
```

URL синхронизируется с host/port; прежний llama_cpp_url также принимается.
Отдельного захардкоженного port в transport нет. Model
должен совпадать с id или alias `/v1/models`. Например, внешнему серверу можно
передать `--alias gemma-4-26B-A4B-it`; если alias не задан, использовать
фактический id из его каталога. GGUF задаётся llama_cpp_model_path, binary —
llama_cpp_binary; их defaults находятся в config.py. HTTP provider сам файл
не загружает и не определяет фактический путь внешнего сервера.

В существующем settings-файле нужно явно выбрать provider и назначить
text-роли chat/deep/coder/reviewer/memory этому model id. OCR, vision и
embedding назначения остаются прежними. Через Siena API provider выбирается
`POST /api/settings` с приведёнными полями, роли назначаются отдельно
`PUT /api/models/roles/{role}` с `{"model":"gemma-4-26B-A4B-it"}`.
Эти запросы здесь не выполнялись; actual backend не запускался.

Второй профиль — `long`, context 32768; `normal` — 16384. Старые имена
gemma4-16k/gemma4-32k остаются input aliases. Смена профиля через settings
автоматически выбирает соответствующий context, если он не указан в patch.
Несогласованный context отклоняется. Оба профиля ссылаются на один GGUF.
Provider не меняет context внешнего сервера: сервер с меньшим `meta.n_ctx`
отмечается unavailable с понятной ошибкой. При отсутствии этого поля фактический
context остаётся неизвестным в diagnostics. Manager перезапускает свой child
при смене профиля; во внешнем режиме сервер настраивается отдельно.

Default endpoint теперь 127.0.0.1:8088. Port 8080 зарезервирован для TTS и
отклоняется настройками llama.cpp. TTS не менялся.

Reasoning по умолчанию разрешён: provider не отправляет
`reasoning_effort="none"`, а managed server использует `--reasoning auto`.
Каждый запрос ограничен `llama_cpp_reasoning_budget_tokens` (временный cap 512);
unrestricted -1 отклоняется. Live thinking не сохраняется в conversation metadata.
Thinking выбирает штатный chat template Gemma. Явный `LlamaCppConfig(reasoning=False)`
сохраняет прежний request override. Результаты и ограничения живого сравнения
описаны в [A/B отчёте](llama_reasoning_ab.md) и последующем
[budget A/B/C](llama_reasoning_budget_abc.md).

`/api/runtime/status` и `/api/diagnostics` содержат provider diagnostics в
`inference`, включая ожидаемый и наблюдаемый context. Полная lifecycle
диагностика находится также в `llama_server`.
Исторические Ollama keys сохранены для совместимости, но имеют `available=null`
при другом активном provider. Некоторые trace/message metadata всё ещё
называются `ollama_model`: это совместимые имена полей, не вызовы Ollama.

## Проверка

Добавлены `core/model_provider.py`, `core/provider_factory.py`,
`core/providers/__init__.py`, `core/providers/llama_cpp_provider.py`,
`core/providers/ollama_provider.py`, `tests/test_model_provider.py` и этот документ.
Изменены `config.py`, `core/runtime_settings.py`, `storage/settings_store.py`,
`core/runtime.py`, `core/chat_service.py`, `core/streaming_chat.py`,
`core/agent_loop.py`, `core/tool_registry.py`, `tools/delegate_model.py`,
`core/model_roles.py`, `api/routers/diagnostics.py`, `tests/conftest.py`.
`core/ollama_client.py`, `core/model_catalog.py` и `core/model_router.py`
не изменены. Работа выполнена в `port/bazzite-linux`; HEAD и checkpoint
`checkpoint/pre-bazzite-migration-20261007` остались на
`13b8519eec946bd41fde52292861e1a78e4ffb06`. Коммит не создан.

Новые mocked HTTP/SSE tests находятся в `tests/test_model_provider.py`:
обычный chat/generate, JSON object/schema, auto/forced/disabled tools,
полные tool calls после фрагментов SSE, usage trailer, UTF-8 boundaries,
timeout/connect/HTTP failures, malformed completion/catalog/SSE,
task cancellation/iterator close, profile configuration и legacy settings.
Integration tests проходят обычный и streaming tool loop Siena, а также
сохранение partial answer и закрытие сокета при disconnect.

Тесты запускаются явно из `tests/`; внешние upstream примеры с Windows-путями
не входят в набор Siena. Использована новая временная Linux venv
`/tmp/siena-provider-test-venv`, без старых Windows окружений. TestClient
требует запуска вне текущего sandbox: пустой TestClient внутри него зависает;
реальная сеть и subprocess блокируются test fixtures.

На этапе минимального provider: **106 tests passed**, включая 51 прежний тест
и 55 новых. Результат lifecycle этапа приведён в отдельном документе. Единственное
предупреждение — deprecation TestClient/httpx в установленной Starlette;
зависимости проекта в рамках этого этапа не обновлялись.

На этапе минимального provider живая проверка Gemma Q4_K_M и внешнего b11429 server
прошла 9/9: health/context 16K, русский system prompt, JSON schema, streaming,
auto tool call, tool result follow-up, specific forced tool workaround,
tools disabled workaround, streaming tool call. Существующий server на 8088
не перенастраивался и не останавливался. Дополнительный собственный тестовый
server остановлен до запросов после обнаружения уже работающего server.
Артефакт: `external/gemma4-validation/provider-integration/result-existing-server.json`
(ignored, содержит test responses). 32K на этом этапе проверен mocked tests;
живая валидация 16K/32K самого runtime выполнена на предыдущем этапе.

## Остаточные зависимости

Прямые Ollama вызовы остаются в legacy client/catalog, OCR
(`ocr/glm_ocr_service.py`), vision (`vision/qwen_vision_service.py`) и embeddings
(`memory/embedding_service.py`, `/api/embed`). Attachment service и OCR/vision
routers продолжают передавать им ollama_host. Поэтому Python SDK пока остаётся
в requirements, хотя Ollama daemon не является зависимостью нового LLM runtime.
Ничего из OCR/Vision/Whisper/TTS/Electron/systemd здесь не переносилось.

Managed lifecycle реализован в core/llama_cpp_process.py; конфигурация,
ownership, GPU validation и проверки описаны в llama_cpp_lifecycle.md.
SELinux остаётся Enforcing; контекст проекта уже исправлен на user_home_t.
