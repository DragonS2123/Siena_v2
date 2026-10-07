# Memory v1 — Siena на Bazzite

Ветка `port/bazzite-linux`; исходный HEAD этапа:
`6c1db6fd1b28d8369b6e95bdf6ede095cfdacd2f`.
Checkpoint `checkpoint/pre-bazzite-migration-20261007` сохранён:
`13b8519eec946bd41fde52292861e1a78e4ffb06`. Автоматических коммитов нет.

## Устройство

```text
current conversation → recent final user/assistant messages (12 / 8000 chars)
current user query → SQLite lexical ranking → relevant active facts (5 / 2400 chars)
                  → existing Gemma turn → final answer
explicit current user request → existing memory tool → SQLite upsert/deactivate
```

Используются существующие `ConversationStore`, `LongMemoryStore`, SQLite и
tool registry. Нет vector DB, embeddings inference, planner/reviewer/agent,
нового router или отдельного сервера. Legacy embedding-конструктор оставлен
для совместимости диагностики; Memory v1 его не вызывает. VectorStore при
построении Memory v1 не создаётся. Старые vector-файлы не удаляются.

LLM provider/process manager/reasoning/sampling/model routing, voice,
Internet reader/search stack, Electron и launcher не изменены. В существующий
system prompt добавлен только небольшой блок правил Memory v1.

## Кратковременная память

Последние конечные сообщения **текущего** `conversation_id`, максимум 12
сообщений и 8000 символов. Полные старые сообщения, не помещающиеся в budget,
отбрасываются вместе с более старой частью, без усечения отдельных утверждений.
Tool/reasoning поля и незавершённые/отменённые assistant ответы в этот срез
не входят. Текущий запрос добавляется отдельно; generation/context limits
прежние. При восстановлении interrupted turn тот же срез применяется к
прошлым сообщениям; существующий continuation mechanism сохранён.

Legacy short-memory notes не подмешиваются автоматически. Их tools оставлены
для совместимости, scoped по `conversation_id`; запись только по явной
просьбе текущего пользователя, с теми же ограничениями секретов/происхождения.

## Долговременная память

Существующая таблица `long_memory` дополнена `active` и `fact_key`; id,
created_at, updated_at, text, category, importance, source и metadata сохранены.
Миграция идемпотентная, без удаления прежних фактов. До первого запуска
сохранены SQLite backups текущих Linux long/candidate stores.

Provenance:

* `source=user`: факт явно сообщён пользователем или внесён через memory API;
* `source=explicit preference`: явное предпочтение пользователя;
* `source=conversation`: запись из разговора, подтверждённая человеком через
  прежний candidate promotion flow; это не прямое свидетельство «ты мне сказал».

Сохраняются только компактные `conversation_id` / `message_id` или данные
подтверждения кандидата. Произвольные raw metadata не принимаются.
Из legacy source автоматически нормализуются только известные aliases:
`explicit_user_action` → `user`, `candidate_memory:<id>` → `conversation`.
Неизвестные старые источники остаются в management API, но не используются
для prompt retrieval до явного уточнения/обновления provenance.

Retrieval — до 2000 последних активных SQLite candidates с известным source,
нормализация слов, небольшое правило русских окончаний и GPU/CPU synonyms,
scoring по пересечению значимых query terms. Importance сама по себе не
включает запись. Пустой/нерелевантный запрос не получает произвольные факты.
Для основного GPU не подбирается запись о резервном GPU.

В prompt передаётся JSON `memory_v1` с id/text/source, максимум 5 фактов и
2400 символов **включая envelope**. Факты не режутся посередине: не поместившаяся
запись пропускается. Budget каждого факта — 1000 символов. Диагностика пишет
количество/объём, а не текст фактов.

## Запись, обновление и забывание

Gemma выбирает tool. Python проверяет разрешение текущего **исходного** user
message — без добавленного runtime/attachment/web context. ContextVar задаётся
только на время dispatch и изолирует одновременные диалоги. Нет автоматического
сохранения каждой реплики. Цитаты/код с командами запоминания не дают разрешения;
отрицание вроде «не сохраняй» запрещает запись.

Сохраняемый факт должен лексически опираться на текущие слова пользователя;
данные, придуманные моделью или полученные из web/tool/runtime, не могут
незаметно стать пользовательским фактом. Source выставляется Python, модель
не может присвоить его сама. Это консервативный guard, не semantic classifier.

`long_memory_save` поддерживает `fact_key` и `replaces_id`. Для основного/резервного
GPU/CPU стабильный ключ выводится локально, чтобы разные формулировки и новый
GPU обновляли ту же запись. Другие темы используют повторяемый ключ или id.
Exact duplicates также обновляются в той же строке. Upsert и деактивация
конкурирующих записей выполняются одной SQLite transaction.

`long_memory_deactivate(id)` работает только при явной просьбе забыть/удалить
соответствующий факт. Активный retrieval больше его не видит. Позднее явное
сохранение того же ключа восстанавливает запись. Permanent delete доступен
через API. UI и новые экраны не добавлены.

API:

```text
POST   /api/memory/long
PUT    /api/memory/long/{id}
POST   /api/memory/long/{id}/deactivate
DELETE /api/memory/long/{id}
GET    /api/memory/long?include_inactive=true
```

## Секреты и внешнее содержимое

Memory stores отказываются сохранять пароли/токены/API keys, private keys,
JWT, распространённые credential prefixes, seed phrases и похожие длинные
ключи; проверки действуют также для category/importance/fact_key/provenance
metadata. Старые подозрительные записи не попадают в retrieval.

Raw HTML/URLs, runtime delimiters, явные injection команды и размеченный
reasoning не принимаются как факт. Tool/web results живут только в текущем
turn; автоматического переноса в память нет. Candidate tool также требует
явную просьбу, а observation/insight/reflection заменяются техническими
пометками, чтобы не сохранять модельные рассуждения.

На границе persistence существующего ConversationStore также редактируются
явно помеченные credential values и типовые encoded keys; reasoning/thinking
metadata исключаются. Узнаваемый служебный план в final channel заменяется
технической пометкой при записи истории. Это ограниченная проверка хранения,
а не изменение provider-а, thinking режима или sampling. Для старых сообщений
такой же guard применяется перед передачей короткой истории модели; сами
старые SQLite rows автоматически не переписываются. Новый vault/DLP не добавлен.

## Проверки

**Unit suite: 284 passed**, 1 прежнее предупреждение Starlette. **61** новых
Memory v1 tests: релевантность и бюджеты; stable upsert/противоречие;
деактивация/удаление/восстановление; legacy migration; persistence/reopen;
provenance; секреты во всех сохраняемых полях; credential/reasoning guards
истории; quoted/negated writes; изоляция конкурентных turn; scoped short notes;
API management; оба chat paths с реальным memory dispatch; no vector access;
web/candidate content не сохраняется автоматически.
Артефакты: `external/audio-validation/results/memory-all-tests.log`,
`memory-live.json`, `memory-live-after-restart.json`, `memory-runtime-initial.json`,
`memory-runtime-final.json`, `memory-pre-v1-long_memory.sqlite3`,
`memory-pre-v1-candidate_memory.sqlite3`.

В одном первом live turn модель вернула текст служебного планирования в final
channel. Этот ответ не принят как корректный конечный ответ: только созданные
benchmark conversations удалены, результат оставлен как статус без текста,
live cycle повторён. Provider/reasoning architecture не менялись.

## Ограничения

Лексический подбор не обеспечивает семантическое понимание всех перефразировок;
GPU/CPU synonyms поддержаны явно. Общие противоречия между разными ключами
не распознаются автоматически — нужен прежний fact_key/replaces_id. За пределами
2000 candidates старые факты могут не найтись. Неизвестный legacy provenance
нуждается в ручном подтверждении. Secret filter консервативен: возможны ложные
отказы и нераспознаваемые нестандартные credentials.

Забывание означает исключение факта из активной долговременной памяти, а не
удаление всех ранее сказанных реплик из текущего разговора. Для проверки
забывания следует использовать новый диалог или явный поиск активной памяти.
Embeddings для v1 не требуются и не запускаются.


## Изменённые файлы

* `memory/policy.py`: budgets, lexical normalization, authorization/provenance
  scope, secret guards и история без reasoning fields.
* `memory/long_memory_store.py`: SQLite migration, lexical search, upsert,
  active/inactive entries, delete.
* `memory/user_memory_context.py`: query-dependent bounded JSON context.
* `memory/short_memory_store.py`: legacy notes scoped по conversation_id,
  fact validation.
* `tools/memory_tools.py`: guard старых tools, bounded reads, save update,
  новый `long_memory_deactivate`.
* `tools/candidate_memory_tools.py`: только явно запрошенный grounded candidate,
  без model observations/reflection; approved source=conversation.
* `core/tool_registry.py`: регистрация deactivate, отсутствие vector runtime.
* `core/session.py`, `core/agent_loop.py`: original user provenance для dispatch.
* `core/chat_service.py`: bounded recent history + query retrieval,
  provenance scope обоих chat paths, количественная диагностика.
* `storage/conversation_store.py`: guards только на границе persistence.
* `api/routers/memory.py`: edit/deactivate/delete, provenance и secret errors.
* `config.py`: небольшой Memory-блок, прочие настройки сохранены.
* `tests/test_memory_v1.py`, `tests/test_architecture.py`: новые случаи/реестр.
* `tests/live/live_memory.py`: повторяемый тест двух чатов + restart retrieval.
* `docs/memory_v1.md`: этот отчёт.

Новых зависимостей нет.


## Live результат

Два новых conversation_id: `34ad63e0-9391-4feb-834a-087d8e319c5d` (A),
`550bb47e-56fe-4591-b7c8-b6d4b024da3e` (B). Production sampling и budget 512
сохранены. В окончательном повторном цикле:

| Проверка | Результат | Время |
|---|---|---|
| A: «Запомни, мой основной GPU RX 7900 XTX» | long_memory_save; одна запись, source=user | 2,193 с |
| B: «Какая у меня основная видеокарта?» | ровно один факт в prompt; RX 7900 XTX | 1,093 с |
| B: «Что такое chmod?» | 0 memory facts; 0 tools | 7,276 с |
| A: исправление GPU на RX 9070 XT | тот же id=1, старый текст заменён | 3,085 с |
| B: текущий GPU | RX 9070 XT, несмотря на старые реплики | 4,157 с |
| A: удалить/забыть GPU | long_memory_deactivate; active retrieval пуст | 3,334 с |
| A: восстановить RX 7900 XTX | тот же id=1 снова active | 3,325 с |
| B: актуальная память | RX 7900 XTX, ровно 1 факт | 5,163 с |

После реального shutdown/start backend память получена в прежнем B (3,654 с)
и ещё одном полностью новом чате (1,676 с), без истории о GPU: RX 7900 XTX,
ровно один факт / 86 символов memory envelope. SQLite persist подтверждён.
В памяти оставлен реальный RX 7900 XTX, экспериментальный RX 9070 XT не активен.

Пример trace:

```text
A: tool_dispatch long_memory_save
   long_memory_saved id=1 source=user
B: memory_context count=1 characters=95 character_budget=2400
   final answer: «Твоя основная видеокарта — RX 7900 XTX»
B: chmod → memory_context count=0 characters=0 → final answer без tools
```

Голосовая регрессия на прежнем WAV прошла: GigaAM распознал «Сиена, привет…»;
Gemma ответила «Привет, я тебя слышу»; CosyVoice синтезировал, PipeWire воспроизвёл.
Лог: `external/audio-validation/results/memory-voice-regression.log`.
Effective settings до/после совпадают; исходный пользовательский settings-файл
не изменён. Gemma 4, Vulkan0 / RX 7900 XTX, context=16384, reasoning budget=512.
