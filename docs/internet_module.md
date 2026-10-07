# Минимальный Internet-модуль Siena

Ветка `port/bazzite-linux`, 2026-10-07. Исходный HEAD этого этапа:
`bd6365f564e65d0b6178218ae686e48b93bb872a`.
Checkpoint `checkpoint/pre-bazzite-migration-20261007`:
`13b8519eec946bd41fde52292861e1a78e4ffb06`, не изменён. Коммитов нет.

## Архитектура

```text
Gemma → существующий ToolRegistry
      → web_search(query, max_results=5): DDGS backend=auto
      → web_read(url): HTTPX → очищение HTML через Trafilatura
      → tool-role messages текущего Session
      → Gemma: конечный ответ
      → форматирование недостающих URL/title из компактных metadata
```

Выбор инструментов остаётся за Gemma. Нет planner/reviewer, нового router,
автоматического поиска на каждый запрос, отдельного search server или daemon.
Два инструмента добавлены к прежнему реестру. Runtime, provider, process
manager, routing, context architecture, STT/TTS и frontend не переписаны.
В system prompt добавлены только правила Internet: свежесть, уточнение города,
недоверенное содержимое и источники. Gemma, reasoning auto/budget 512,
sampling, модель и профиль сохранены.

Поиск возвращает список объектов `title`, `url`, `snippet` — максимум 5,
title до 500 символов, snippet до 1200. Никакие страницы результатов поиска
автоматически не читаются. DDGS поддерживает несколько backend'ов;
используется `backend="auto"` как fallback. См.
[официальную документацию DDGS](https://github.com/deedy5/ddgs#1-text).

DDGS выполняется в короткоживущем Python child process: внутренний timeout
библиотеки 5 с, общий deadline 20 с. Это обеспечивает прекращение backend
threads при отмене/timeout; worker не является сервером, не слушает порт и
завершается после одного вызова. Его результаты не кешируются Siena на диске.

`web_read` использует async HTTPX. Настройки:

* Только HTTP/HTTPS, обычные web-порты 80/443, без URL credentials.
* Проверка схемы/hostname/IP до DNS. Localhost, private/link-local/reserved,
  metadata endpoints и программные/неweb-схемы запрещены.
* Все DNS-ответы должны быть публичными; смешанные public/private отклоняются.
* Подключение к проверенному числовому IP при сохранении исходных HTTP Host
  и TLS SNI; TLS verification включена. Hostname повторно не разрешается
  при соединении. Keepalive отключён, чтобы не переиспользовать TLS connection
  для разных исходных имён на одном IP.
* Каждый redirect проверяется заново, максимум 4 перехода.
* Proxy environment отключён. Private DNS answer не превращается в соединение.
* Connect timeout 5 с, HTTP timeout 10 с, общий deadline 20 с на источник.
* До 2 MiB ответа: проверяется Content-Length и фактический streamed размер.
* Только `text/html` / `application/xhtml+xml`. JSON, PDF, изображения,
  octet-stream, бинарные/неHTML ответы отвергаются. Запрашивается identity
  encoding; ответы с compression отвергаются, защищая от decompression bombs.
* JavaScript, загрузка ресурсов страницы и сохранение HTML не выполняются.
* До 12 000 символов полезного текста на источник, плюс final URL, title,
  `truncated`. Trafilatura используется только как локальный HTML extractor:
  [extract API](https://trafilatura.readthedocs.io/en/latest/corefunctions.html#extract).

Отмена streaming turn прерывает async web I/O. Reader закрывает response,
search прекращает и reaps свой child. Старые инструменты исполняются прежним
синхронным способом. Nonstream API сохраняет прежнее поведение worker-thread
agent loop; disconnect там не даёт такой же немедленной отмены всего turn,
но каждый Internet-вызов имеет ограниченный deadline.

## Доверие, история, ссылки

Web-результаты маркируются `trust="untrusted_external_content"` и передаются
исключительно в `role="tool"`, никогда в system prompt. Текст вроде
`ignore previous instructions` сохраняется как данные, а не исполняется.
JSON serialization дополнительно экранирует `<`/`>` как Unicode escapes:
буквальные Gemma/ChatML role delimiters не попадают в model wire. При JSON
decode исходное содержимое остаётся прежним — это не удаление фактов/слов.

Сырые HTML, очищенный page text и поисковые snippets живут в Session текущего
turn. В conversation history остаются конечный ответ и компактные
`metadata.sources`: `url`, `title`, `tool`. До 15 metadata entries; успешные
read sources сохраняются с приоритетом перед search-only entries. Эти поля
различают найденные и реально прочитанные источники; metadata — не отдельное
утверждение, что каждый найденный результат использован в каждом тезисе.

Если Gemma пропустила ссылки, добавляется Markdown footer по успешно
прочитанным источникам. Если доступны только snippets, сохраняются выбранные
моделью ссылки с явной пометкой, что страницы не прочитаны; при отсутствии
ссылок перечисляются до 5 поисковых источников. Заголовки экранируются от
Markdown/HTML/template injection. Это форматирование, без дополнительного
inference, fact checker, planner или автоматического чтения.

## Изменённые файлы

* `tools/web.py` — два инструмента, validation, DNS pinning, reader, budgets.
* `tools/web_search_worker.py` — один DDGS auto search с bounded lifetime.
* `tools/registry.py` — async dispatch для Internet; legacy dispatch сохранён.
* `core/tool_registry.py` — регистрация двух инструментов.
* `core/message.py` — untrusted JSON envelope и escaping template tokens.
* `core/session.py` — компактные источники и форматирование ссылок.
* `core/agent_loop.py` — источники в metadata и footer nonstream ответа.
* `core/chat_service.py` — await web calls в streaming, trace, sources/footer.
* `config.py` — добавлены только Internet-правила в существующий system prompt.
* `requirements.txt` — DDGS и Trafilatura.
* `tests/test_web_tools.py` — mocked tests модуля и интеграции.
* `tests/test_architecture.py` — ожидаемый реестр дополнен двумя tools.
* `tests/live/live_internet.py` — повторяемые ручные live checks.
* `docs/internet_module.md` — этот отчёт.

## Dependencies и проверки

В существующее native Linux Python environment установлены:
`ddgs==9.16.0`, `trafilatura==2.3.1`. Используется уже имеющийся
`httpx==0.28.1`, Python 3.14.8. Requirements старых подсистем не переписаны.
На хосте Bazzite Atomic пакеты не устанавливались, Docker/SearXNG/systemd
и Ollama не разворачивались.

Для подготовленного окружения установка только новых зависимостей:

```bash
/tmp/siena-provider-test-venv/bin/python -m pip install ddgs==9.16.0 trafilatura==2.3.1
```

Полный Python suite: **223 passed**, 1 прежнее предупреждение Starlette;
из них **60** новых Internet tests. Проверены:
normal/zero search, search timeout; reader success/redirect; запрещённые
схемы, localhost, IPv4/IPv6 private, loopback/link-local/metadata;
private redirect и mixed DNS answers; pinning без повторного hostname lookup;
Host/SNI; oversized/неHTML/compressed/binary responses; malformed HTML;
untrusted injection/template tokens; cancellation reader/search/streaming turn;
эфемерность результатов в обоих chat paths; источники и bounded metadata.

```bash
/tmp/siena-provider-test-venv/bin/python -m pytest tests -q
/tmp/siena-provider-test-venv/bin/python tests/live/live_internet.py
# Альтернативный nonstream API:
/tmp/siena-provider-test-venv/bin/python tests/live/live_internet.py --non-stream
```

Live script запускается при уже работающем backend и сохраняет только
конечные ответы, compact metadata и trace, без HTML/page text/reasoning.
Production streaming проверен на исходных четырёх запросах, плюс явно
заданная Казань как benchmark city. Местоположение пользователя не использовано.

| Запрос | Финальный production streaming прогон | Время turn |
|---|---|---|
| «Какая сегодня погода?» | Уточнение города, без Internet tools и выдуманного местоположения | 1,932 с |
| После явного указания Казани | web_search → web_read; чтение погодного сайта завершилось timeout, fallback по snippets явно обозначен | 19,980 с |
| Последняя стабильная версия Bazzite | web_search → web_read официального GitHub release list; ответ `44.20261006.1` с источником | 14,050 с |
| «Что такое chmod?» | Обычный ответ, Internet tools не вызываются | 10,604 с |
| «Проверь в интернете последнюю версию Mesa» | web_search → web_read официального Mesa; ответ `26.2.4` со ссылкой | 13,028 с |

Отдельный streaming follow-up с явной просьбой прочитать официальный релиз
Bazzite прошёл: прочитан GitHub release list, ответ с названием и URL источника.
В этом ответе указана версия `44.20261006.1`; некоторые search-only ответы
опирались на устаревшее `44.20261006`. Это наблюдаемый предел search snippets
и выбора read моделью, а не основание автоматически считать snippet проверкой.
Live Mesa ответ после чтения официального сайта: `26.2.4`.

Пример compact trace, Mesa:

```text
tool_dispatch: web_search, argument_names=[query]
tool_result: web_search, ok=true
tool_dispatch: web_read, argument_names=[url]
source: https://mesa3d.org/ — Home — The Mesa 3D Graphics Library
tool_result: web_read, ok=true
generation.completed: answer + source URL/title
```

Артефакты в `external/audio-validation/results/`:
`internet-all-tests.log`, `internet-live-rerun.json`,
`internet-production-stream.json`, `internet-stream-live.json`,
`internet-runtime-initial.json`, `internet-runtime-final.json`,
`internet-voice-regression-final.log`.
Голосовая регрессия отдельно прошла на прежнем WAV:
GigaAM → «Сиена, привет…» → Gemma «Привет, да, я тебя слышу» → CosyVoice →
успешный PipeWire playback. Код voice/runtime/providers/frontend не изменён.
Финальная повторная голосовая проверка также прошла после всех изменений.
Effective settings до/после совпадают; пользовательский `storage/settings.json`
сохранён (SHA256 `c89037179fb05e9ce47008ce464ad8152016b33dc98d421e2e05497b1543a40c`).

## Известные ограничения

DDGS зависит от доступности/индексации сторонних backend'ов; возможны stale
snippets, rate limiting и ошибки. Некоторые погодные сайты не доступны
reader в установленный timeout. JS-only/CAPTCHA/login pages не поддерживаются;
JSON/PDF/compressed responses и нестандартные web-порты отвергаются.

Выбор search/read остаётся за Gemma при текущем budget 512 и production
sampling. Модель иногда пропускает read даже при указании его в policy;
автоматического router/enforcement нет. Source footer обеспечивает видимые
references, но не проверяет каждый тезис и не исправляет устаревшие snippets.
Погода по сниппетам не является независимой верификацией измерений.

Budget 12k/source — индивидуальный cap; новый суммарный context manager не
добавлялся. Nonstream cancellation сохраняет прежний предел thread-based
agent loop. Для UI streaming отмена Internet I/O проверена.
