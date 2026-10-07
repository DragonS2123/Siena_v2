# Managed llama-server lifecycle

Siena Runtime создаёт `LlamaCppProcessManager`, запускает свой child при
`provider=llama_cpp` / `llama_cpp_managed=true` и останавливает его в
`close_runtime` / ASGI lifespan shutdown. Дополнительный cleanup зарегистрирован
через `atexit`. Конструктор и импорт модулей сами процессы не запускают.
Legacy Ollama runtime остаётся совместимым и не запускает manager.

## Изменённые файлы этого этапа

- Добавлены `core/llama_cpp_process.py`, `tests/test_llama_cpp_process.py`,
  `docs/llama_cpp_lifecycle.md`.
- Обновлены `config.py`, `core/runtime_settings.py`, `storage/settings_store.py`,
  `core/runtime.py`, `core/provider_factory.py`,
  `core/providers/llama_cpp_provider.py`, `api/routers/diagnostics.py`,
  `tests/test_model_provider.py`, `docs/llama_cpp_provider.md`.
- Изменения минимального provider этапа уже присутствовали в рабочем дереве
  и сохранены. `legacy OllamaClient`, Whisper, TTS, OCR, Vision, embeddings,
  Electron и systemd не изменялись.

## Конфигурация

Default endpoint — `http://127.0.0.1:8088`; порт 8080 зарезервирован для TTS и
отклоняется validator. Пример настройки для будущего явного переключения:

```json
{
  "provider": "llama_cpp",
  "llama_cpp_managed": true,
  "llama_cpp_binary": "external/gemma4-validation/runtime/llama-b11429/llama-server",
  "llama_cpp_model_path": "external/gemma4-validation/models/google_gemma-4-26B-A4B-it-Q4_K_M.gguf",
  "llama_cpp_model": "gemma-4-26B-A4B-it",
  "llama_cpp_host": "127.0.0.1",
  "llama_cpp_port": 8088,
  "llama_cpp_device": "Vulkan0",
  "llama_cpp_profile": "normal",
  "llama_cpp_startup_timeout": 120,
  "llama_cpp_shutdown_timeout": 15
}
```

`provider` — input alias для `inference_provider`. `llama_cpp_model` сохраняет
семантику API model id/alias из предыдущего этапа; файл GGUF задаётся отдельным
`llama_cpp_model_path`. Пути приходят из settings, defaults находятся в config.py;
относительные пути разрешаются относительно проекта, а не cwd процесса.
`llama_cpp_url` остаётся совместимым input: host/port и URL синхронизируются,
противоречивые значения отклоняются. Managed endpoint требует loopback HTTP.

`normal` имеет context 16384, `long` — 32768. Прежние имена
`gemma4-16k`/`gemma4-32k` принимаются как input aliases. GGUF для обоих один.
При смене profile соответствующий context выбирается автоматически, если он
не указан в patch; несовпадение отклоняется. Settings update launch-параметров
перезапускает принадлежащий runtime child; изменение generation options не
перезапускает его. Переключение на Ollama/external mode останавливает свой child.
Смена профиля может прервать текущую генерацию. Failed start сохраняется в
diagnostics; API остаётся доступным, а managed catalog возвращает unavailable.

Текущий пользовательский `storage/settings.json` **не переключался и не
изменялся**. Для перехода существующих настроек нужно явно выбрать provider
и назначить текстовым ролям Gemma через прежние model-role endpoints.

## Владение и readiness

Manager хранит собственный `Popen` handle и PID. `start` идемпотентен,
операции сериализуются RLock. File lock на port в log directory защищает от
второго экземпляра Siena; информация PID в lock-файле не используется для
adoption или сигналов. Занятый port вызывает отказ, даже если там работает
подходящий llama-server. Внешние процессы manager не останавливает.

Перед стартом проверяются ELF executable, читаемый GGUF header, RADV ICD,
порт и bounded `--list-devices`. Readiness требует live child, успешного
`/health`, нужного model id/alias и подходящего context в `/v1/models`.
Слушающий port должен принадлежать child: Linux `/proc/<pid>/fd` socket inodes
сопоставляются с LISTEN entries `/proc/<pid>/net/tcp{,6}`. Это закрывает гонку
с процессом, занявшим port после preflight; отсутствие доступа к procfs приводит
к отказу readiness, а не к подключению к чужому server.

Timeout или startup exit останавливает и reap-ит child, закрывает log handles
и освобождает lock. `stop` отправляет SIGTERM только своему child, ждёт
shutdown timeout, затем применяет kill с ещё одним ограниченным ожиданием.
Повторный stop безопасен. Stop во время startup сигнализирует stop event;
readiness loop прекращается. Если даже kill не завершился за timeout,
manager сохраняет ownership и сообщает ошибку, не создавая второго child.

Stdout/stderr пишутся непосредственно в отдельные append logs, без PIPE
и риска блокировки из-за непрочитанных pipe buffers. State, exit code,
last startup error и пути logs доступны в diagnostics. Автоматического
бесконечного restart при crash нет: доступны явный `restart` и reconfigure.

## Проверенный запуск b11429

Перед реализацией проверены реальные `--help` и `--list-devices` данной сборки.
Без фильтра она перечисляет `Vulkan0` — RX 7900 XTX и `Vulkan1` — Ryzen iGPU.
С выбранным environment проверка перечисляет только ожидаемую RX 7900 XTX.
Manager проверяет точное имя GPU на каждом запуске и отказывает для missing,
Ryzen, llvmpipe/lavapipe и другого Radeon. Selector и ожидаемое имя сохранены
в config; selector также задаётся settings.

Environment:

```bash
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json
GGML_VK_VISIBLE_DEVICES=0
```

Inherited `LLAMA_ARG_*`, conflicting `VK_ICD_FILENAMES` и `LLAMA_API_KEY`
не передаются child. Итоговая команда, с путями из settings:

```bash
llama-server --model "$GGUF" --alias gemma-4-26B-A4B-it \
  --host 127.0.0.1 --port 8088 --device Vulkan0 \
  --gpu-layers all --split-mode none --fit off --ctx-size 16384 \
  --parallel 1 --ctx-checkpoints 0 --batch-size 2048 --ubatch-size 512 \
  --flash-attn on --cache-type-k f16 --cache-type-v f16 \
  --jinja --reasoning auto --perf --no-ui --offline
```

Для `long` изменяется context на 32768. `all` и `fit off` требуют полного
поддерживаемого offload, без автоматического уменьшения числа GPU layers или
контекста; нехватка памяти приводит к failed start. CPU metadata/mapped buffers
и работа CPU по подготовке запросов по-прежнему возможны.

## Tests и живой прогон

На исходном lifecycle этапе `python -m pytest -q tests`: **140 passed**, включая 106 тестов
предыдущего этапа и 34 lifecycle теста. Покрыты start/readiness, already running,
duplicate/concurrent start, чужой port/manager, неправильный binary/GGUF,
startup timeout/exit, graceful stop, kill fallback, restart/profile change,
неправильная GPU, process crash, startup interruption, socket ownership race,
runtime settings reconfigure, ASGI lifespan cleanup и запрет повторного запуска
после close при позднем settings update. Subprocess fake;
readiness HTTP mock. TestClient запускался вне sandbox по установленной ранее
причине; fixtures блокируют реальные network/subprocess операции. Единственное
предупреждение — установленная Starlette deprecates TestClient/httpx.

До изменения reasoning живой прогон использовал новую manager implementation, real b11429, тот же
Gemma GGUF, `normal`, 8088 и отдельный test settings-файл. Цепочка
start → readiness → health → chat → streaming → diagnostics → graceful stop
прошла. Первый прогон: readiness 7,89 s. Финальный прогон с дополнительной
проверкой socket ownership: readiness **4,17 s**, SIGTERM stop **0,21 s**,
exit code 0, forced kill false. После stop child PID отсутствует и port 8088
свободен; независимый `ps -C llama-server` не показывает процессов.

Live artifacts (ignored):
`external/gemma4-validation/lifecycle-integration/result.json`,
`llama-server-8088.stdout.log`, `llama-server-8088.stderr.log` в том же каталоге.
Live harness отдельно прочитал kernel sysfs `mem_info_vram_used` для AMD
device 1002:744c: before около **1,60 GiB**, loaded **18,18 GiB**, after stop
около **1,61 GiB**. Это общая GPU память, включая desktop/driver, не per-process
usage и не production metric manager. Небольшой остаток несколько MiB зависит
от desktop/driver. Human-readable VRAM из stdout не парсится.

Пример diagnostics финального живого процесса до остановки:

```json
{
  "provider": "llama_cpp",
  "model": "gemma-4-26B-A4B-it",
  "profile": "normal",
  "context_size": 16384,
  "llama_server_state": "ready",
  "pid": 31380,
  "port": 8088,
  "device": "Vulkan0",
  "selected_gpu": "AMD Radeon RX 7900 XTX",
  "backend": "Vulkan",
  "uptime_seconds": 4.4,
  "last_startup_error": null
}
```

Полные значения находятся в result.json. `/api/runtime/status` и
`/api/diagnostics` возвращают lifecycle данные в `llama_server`; managed catalog
также добавляет их в `inference`. После stop state=stopped, pid=null, owned=false.

## Ограничения

Текущий default изменён на `--reasoning auto`; другие launch/lifecycle параметры
сохранены. Provider также перестал отключать thinking в HTTP payload по умолчанию.
После изменения **141 tests passed**. Последующее живое A/B, включая
token-budget limitation и cleanup, описано в [отдельном отчёте](llama_reasoning_ab.md).

- Manager предназначен для Linux/POSIX с procfs/flock; Windows Ollama/external
  import/runtime сохранён. Отсутствие нужного GPU/ICD не включает fallback.
- Production VRAM metric пока `null` с явным future-metric status. Нужен
  отдельный надёжный интерфейс и определение total/per-process usage.
- `long`/restart проверены fake tests; live managed smoke выполнен для normal.
  Сам Gemma runtime на 32K уже валидирован на предыдущем этапе.
- SIGKILL родительского Siena, power loss и некоторые fatal crashes не исполняют
  lifespan/atexit cleanup. Kernel parent-death supervision на этом этапе не
  добавлен; последующий manager всё равно не присваивает оставшийся внешний PID.
- Dedicated server logs пока append без автоматической ротации. Startup/restart
  синхронны и ограничены timeout; во время lifespan readiness приложение ещё
  не принимает запросы. Crash не вызывает бесконечные retry/restart.
- SELinux остался Enforcing. Системные пакеты, systemd и другие службы Siena
  не менялись. Ветка port/bazzite-linux, HEAD/checkpoint 13b8519; коммит не создан.
