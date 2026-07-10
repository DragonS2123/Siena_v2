# NEXTDO.md — Ревизия Siena v2 vs HANDOFF.md (2026-07-07)

Документ фиксирует **фактическое** состояние кода на текущий момент в сравнении с
`Siena v2 Control Panel UI/HANDOFF.md`, без изменения кода. Ниже — выводы и
предложения, что делать дальше, в порядке приоритета.

---

## 0. Главный вывод

**HANDOFF.md устарел как контракт, но не как история.** Он описывает Electron+IPC
макет с демо-данными (версия 0.9.4-beta, 2025-07-06). Реальная архитектура давно
ушла от IPC к REST+WebSocket поверх FastAPI (`api/server.py`, 34 REST-маршрута +
`/ws/trace`), и почти все демо-массивы (`INITIAL_MESSAGES`, `TOOL_EVENTS`,
`SHORT_MEMORY`, `LONG_MEMORY`, `LOG_ENTRIES`, `MODEL_DATA`, `TIMING_DATA`) удалены и
заменены реальными данными. Часть возможностей (OCR/vision-честность, memory-intent,
переводчик, роутинг моделей, Qwen TTS Vulkan, candidate-memory/Insights) вообще не
упомянута в HANDOFF — документ не поспевает за кодом.

Второй важный вывод: **бэкенд для "Insights" (candidate memory) уже полностью
реализован** (`memory/search.py`, `memory/candidate_memory_store.py`,
`tools/candidate_memory_tools.py`, 5 REST-эндпоинтов `/api/insights*`, тесты
`tests/test_candidate_memory_tools.py`, `tests/test_memory_search.py`) — но во
фронтенде нет ни вкладки, ни единого упоминания `insights`. Это самый дешёвый и
самый нужный кусок работы прямо сейчас: бэкенд ждёт, фронтенд не звонит.

---

## 1. Сводная таблица по разделам HANDOFF

| § | Раздел | Статус | Комментарий |
|---|---|---|---|
| 1 | Design tokens | ✅ полностью | без изменений |
| 2 | Nav/shell | ⚠️ иначе/не полностью | нав-структура совпадает 1:1, но **нет вкладки Insights**, хотя бэкенд для неё готов |
| 3 | Splash | ✅ полностью, иначе | не demo-таймер, а реальные последовательные вызовы (`getRuntimeStatus`, `listConversations`, `getModels`, `getSettings`) + Retry при ошибке |
| 4 | Chat view | ✅ полностью | реальный `/api/chat`, markdown/fence-рендеринг добавлен сверх спеки |
| 5 | Composer | ✅ кроме voice | mic жёстко `disabled` ("AMD/CUDA migration") — намеренно |
| 6 | Attachments | ✅ полностью, шире спеки | OCR text+image статусы (`ocrStatus`), честные дисклеймеры vision |
| 7 | Feedback row | ⚠️ частично | Copy — реален; **Like/Dislike/Retry/Save всё ещё локальный стаб**, Retry — буквально no-op; добавлен непредусмотренный Translate |
| 8 | Voice Orb | ⏸️ заморожено | ровно тот же демо-`setTimeout`, намеренно не трогается |
| 9 | Inspector panel | ⚠️ частично | Tool activity/Context реальны; Delegation — "Not connected yet" |
| 10 | Tool Trace | ✅ полностью | `[object Object]` пофикшен, реальные события |
| 11 | Short Memory | ✅ полностью | |
| 12 | Long Memory | ✅ полностью + fuzzy-search | search/filter кнопки из спеки просто убраны |
| 13 | Logs | ✅ полностью | |
| 14 | Models | ✅ полностью, шире спеки | реальный роутинг моделей, `Set as active model` работает |
| 15 | Runtime | ⚠️ частично | сервисы/env реальны; **RAM/VRAM/CPU так и остались "n/a"/0%** |
| 16 | Debug | ⚠️ частично | Overview/Tool Calls реальны; **Delegation/Timing/Payload — "Not connected yet"** |
| 17 | Settings | ⚠️ почти весь стаб | только Model settings реально дергает `/api/settings`, но **не персистится на диск** (явный комментарий в `config.py`: "runtime-only state... NOT persisted") |
| 18 | Stub inventory | ✅ актуализирован кодом | все демо-массивы реально удалены |
| 19/20 | IPC events | ❌ неактуально | заменено REST+WS, ничего из списка не существует |
| 21 | Electron integration | ⚠️ иначе | нет preload.js/`window.siena`/contextIsolation — Electron это чистая оболочка окна, всё общение идёт по HTTP/WS напрямую |
| 22 | Checklist | частично | см. построчный разбор в аудите (голос — сознательно не начат, персист настроек — не начат, RAM/VRAM/CPU — не начаты) |

---

## 2. Возможности, реализованные, но не описанные в HANDOFF

- OCR-vs-vision честность (`core/image_intent.py`, `ocr/glm_ocr_service.py`)
- Memory-save intent detection (`core/memory_intent.py`)
- Погода: обязательное уточнение города (правило в `SYSTEM_PROMPT`)
- Research discipline / contradiction guard для web_search/open_url
- **Candidate memory / Insights (наблюдение→инсайт→рефлексия→кандидат)** — бэкенд полный, фронтенда нет
- Keyword+fuzzy поиск по долговременной памяти (`memory/search.py`)
- Переводчик (`translator/translator_service.py`, `/api/translate`, кнопка Translate)
- Роутинг/делегирование моделей (`core/model_router.py`, `tools/delegate_model.py`)
- Qwen3-TTS Vulkan провайдер (`voice/qwen_tts_ggml_vulkan.py`) + voice profiles — код готов, но недостижим из замороженного mic-кнопки

---

## 3. Предложения (что делать дальше), по приоритету

### P0 — дешёвое и готовое к подключению
1. **Фронтенд-вкладка Insights.** Бэкенд полностью готов (`GET /api/insights`,
   `promote|reject|later`, `DELETE`). Нужно: добавить `"insights"` в `NAV_PRIMARY`,
   экран со списком карточек (observation/insight/reflection/proposed_memory +
   confidence/category/status) и три кнопки-действия. Это самый большой ROI —
   ничего не нужно менять в бэкенде.
2. **Обновить сам HANDOFF.md** (или создать `HANDOFF_v2.md`), чтобы §19/§20 описывали
   реальный REST+WS контракт вместо IPC-каналов, а §2/§18 отражали реальный список
   вкладок и убранные демо-стабы. Иначе документ продолжит вводить в заблуждение
   любого, кто откроет его следующим.

### P1 — заметные пробелы в честности/полноте UI
3. **Feedback row**: Retry (буквально `onClick={() => {}}`) и Save-to-memory —
   либо подключить к реальному действию (Retry → повторный `/api/chat` с тем же
   промптом; Save → `long_memory_save`/`candidate_memory_create`), либо явно
   пометить как "Not connected yet" вместо тихого локального стейта, который
   выглядит как рабочая кнопка.
4. **Runtime meters (RAM/VRAM/CPU)**: сейчас жёстко "n/a"/0% — либо подключить
   реальный сбор (psutil/nvidia-smi), либо явно показать "Not connected yet"
   вместо цифр, похожих на настоящие.
5. **Debug: Delegation/Timing/Payload вкладки** — сейчас "Not connected yet",
   что честно, но если делегирование моделей (`model_router`) уже логируется в
   trace, можно частично закрыть Delegation-вкладку дешевле, чем кажется.

### P2 — персистентность и настройки
6. **Settings persistence**: сейчас `/api/settings` меняет только runtime-состояние
   процесса без сохранения на диск — рестарт бэкенда всё сбрасывает. Нужен простой
   JSON-стор (`storage/settings.json`, по аналогии с `storage/voice_profiles.json`),
   чтобы модельные настройки переживали перезапуск. Остальные секции Settings
   (Appearance/Startup/Tools/Code/Voice/Developer) — либо реализовать персист,
   либо явно оставить как "UI only" пометки (сейчас частично уже так и есть).

### P3 — то, что сознательно не трогаем сейчас
7. **Voice/STT/TTS/Voice Orb** — остаётся замороженным до завершения AMD/CUDA
   миграции; Qwen TTS Vulkan backend уже готов и ждёт разморозки UI.
8. **Electron IPC bridge (`window.siena`, preload.js)** — текущая архитектура (прямой
   HTTP/WS из рендерера) работает и проще; заводить IPC-мост есть смысл только если
   понадобится что-то, недоступное из браузерного контекста (нативные диалоги файлов,
   системный трей и т.п.). Предлагаю не делать этого превентивно.

---

## 4. Что не предлагается трогать прямо сейчас

- ~~Wagner/open_url/research-grounding регрессия~~ — **перепроверено вживую
  2026-07-07** (см. `scripts/test_wagner_regression.py`). Корень проблемы был
  не в contradiction guard, а в том, что модель часто вообще не вызывала
  `web_search` для вопросов-идентификаций/статуса ("кто такие X", "что
  произошло с X"). Добавлен `core/research_intent.py` (диагностический
  regex-нюдж, тот же паттерн что и `memory_intent.py`) + усилен
  `SYSTEM_PROMPT` (обязательный web_search для таких вопросов, запрет
  называть погибшего/арестованного человека новым руководителем,
  обязательные 2-3 узких запроса вместо одного общего). Результат: `Кто
  такие Пригожин и Уткин?`-класс вопросов теперь стабильно вызывает
  `web_search`/`open_url`, никогда не выдаёт "арест"/Белгородскую область,
  корректно называет авиакатастрофу 23 августа 2023 в Тверской области —
  **но точный день месяца иногда путается** (23↔24↔8) даже при верной
  причине/месте/именах — известное остаточное ограничение 9B-модели, не
  устранённое полностью только промпт-инженерией.
- Всё, что помечено "Not connected yet" в Debug/Inspector/Settings — намеренно
  честные заглушки, а не баги; трогать только по явному запросу.

---

*Отчёт основан на построчном аудите `api/server.py`, `App.tsx`, хуков
`useRuntimeStatus.tsx`/`useTraceSocket.tsx`, `memory/`, `tools/`, `config.py` и
сверке с планом `adaptive-jumping-adleman.md` (подтверждено: план уже реализован
на бэкенде, файлы от 2026-07-05 21:29–21:52). Код не изменялся.*
