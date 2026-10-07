# b11429: per-request reasoning budget

Проверено 2026-10-07 в `port/bazzite-linux`. `--reasoning auto` и все прочие
launch/lifecycle параметры сохранены. Новый router/agent не добавлен.

## Поддержка текущей сборки

Native `--help` b11429 показывает reasoning budget, с -1 как unrestricted.
Per-request поле `reasoning_budget_tokens` подтверждено в исходниках именно
этой сборки: [server-common.cpp](https://github.com/ggml-org/llama.cpp/blob/b11429/tools/server/server-common.cpp)
передаёт его sampler при наличии thinking end tags; [server-schema.cpp](https://github.com/ggml-org/llama.cpp/blob/b11429/tools/server/server-schema.cpp)
определяет числовой параметр. Live результаты ниже подтверждают работу budget
с текущим Gemma chat template.

Siena отправляет `reasoning_budget_tokens` в каждом reasoning-enabled запросе
`LlamaCppProvider`, включая streaming. `LlamaCppConfig` и runtime settings
отклоняют unrestricted -1. Новый setting `llama_cpp_reasoning_budget_tokens`
допускает 0..65536, сохраняется прежним SettingsStore и не требует restart
server. Разные request configs могут задавать 512/1024/2048; автоматического
переключателя сложных запросов не добавлено.

512 сейчас — **временный ограничитель ресурсов**, не подтверждённый default
достаточного качества. Benchmark не позволяет принять его по критерию качества.
Остальные production sampling settings и output limits не изменены;
пользовательский `storage/settings.json` не переключался и не редактировался.

## Одинаковая эталонная задача

Использованы тот же SYSTEM/PROMPT и exact Fraction oracle из
`tests/live/llama_reasoning_ab.py`: Байес с двумя поставщиками, двумя результатами
тестов и выбором платного третьего теста либо немедленного действия.
Модель Gemma 4 26B-A4B-it Q4_K_M, b11429, Vulkan0 / AMD Radeon RX 7900 XTX,
normal context 16384, full GPU offload, port 8088.

Каждый вариант — отдельный собственный server, одинаковый warmup, одинаковые
temperature 0, seed 42, общий max_tokens 8192 и timeout 300 s только для теста.
Все три server commands совпали; различалось только per-request поле budget.
Latency — до конца SSE, без startup/warmup и последующей tokenization.

| Budget | Reasoning text tokens* | Final text tokens* | Completion usage | Latency | Завершённый final | Числа верны** | Стратегия / policy |
| ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 512 | 511 | 264 | 780 | 5,961 s | да | 0/8 | неверны обе |
| 1024 | 1023 | 237 | 1265 | 9,605 s | да | 0/8 | стратегия верна, policy неверна |
| 2048 | 2047 | 2013 | 4065 | 30,772 s | да | 1/8 | верны обе |

\* В b11429 usage предоставляет только общий completion count, без отдельного
reasoning count. Два вида текста измерены настоящим tokenizer этой же модели
через `/tokenize`, add_special=false, parse_special=false; это retokenized text
counts, без служебных channel/EOS tokens. Их сумма во всех трёх случаях на 5
меньше completion usage. Это не оценка chars/4 и не отдельная server usage metric.
Попытка получить generated IDs через verbose streaming этой сборки не дала
пригодной instrumentation; для таблицы использованы явно обозначенные tokenizer
counts. Reasoning текст существовал только в RAM и не сохранён в артефакт.

\** Использован прежний допуск 1e-5. В 2048 проходит только p_defect_t3_negative;
строгое совпадение всех полей с шестью знаками не достигнуто ни в одном режиме.

Все три ответа завершились `finish_reason=stop`, без исчерпания output limit.
«Завершённый final» означает наличие всех требуемых полей в завершённом ответе;
соблюдение чистого JSON проверено отдельно: **ни один** не соблюдает его.
512/1024 возвращают JSON в Markdown fence; 2048 — английское объяснение с JSON
в конце. JSON извлечён только в benchmark evaluator для оценки чисел/решения;
production provider не получает дополнительный parser/repair.

512 выбрал accept вместо retest. 1024 выбрал retest, но reject после обоих
результатов, тогда как эталон требует reject при positive и accept при negative.
2048 использовал верную формулу четырёх совместных весов и верную политику,
но округлил нормализатор до 0.04395 вместо точного 0.043944, после чего внес
ошибки в вероятности и expected costs. Его cost_retest=9.119676 вместо
9.121486; p_defect=0.124232 вместо 0.124249.

Ни один вариант не решил задачу полностью корректно. 2048 полезнее для этой
сложной задачи, но намного медленнее и пока не подтверждает нужную точность.
512 не принят как default достаточного качества; он оставлен только как
временный bounded cap, чтобы Siena не использовала unrestricted reasoning.

## История и проверки

`core/chat_service.py` больше не накапливает/сохраняет reasoning в metadata
assistant message, включая checkpoints, failure и cancellation. Live thinking
дельты остаются доступны интерфейсу. Non-streaming agent loop уже пропускал
только role/content/tool_calls; provider также исключает reasoning из следующего
wire history. Старые записи conversation DB этим этапом не очищались.

Обновлены config, LlamaCppConfig/provider, provider_factory, runtime_settings,
SettingsStore, chat_service и tests. Добавлен ручной harness
`tests/live/llama_reasoning_budget_abc.py`; используется явно:

```bash
python tests/live/llama_reasoning_budget_abc.py
```

Артефакт без reasoning trace:
`external/gemma4-validation/reasoning-budget-abc/result.json`.
Native stdout/stderr находятся в подкаталогах 512/1024/2048. Все три собственных
PID после stop отсутствуют; SIGTERM exit code 0, forced kill false.
Settings SHA256 сохранился:
`c89037179fb05e9ce47008ce464ad8152016b33dc98d421e2e05497b1543a40c`.
Независимая проверка не нашла llama-server, порт 8088 успешно bind-ится.
Общая память RX 7900 XTX после stop — около 1,72 GiB по kernel sysfs,
включая desktop/driver. VRAM parser в manager не добавлялся.
Полный Python regression suite: **152 passed**, одно существующее
предупреждение Starlette TestClient/httpx. Проверены budgets в chat/stream,
запрет -1/invalid values, сохранение настройки и неизменность sampling,
отсутствие reasoning trace при completion, failure и cancellation.
Checkpoint/история не изменены; коммит не создан.
