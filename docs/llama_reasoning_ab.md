# Gemma 4: reasoning off / auto

Проверено 2026-10-07 в `port/bazzite-linux`, без изменения checkpoint/истории,
коммитов и пользовательского `storage/settings.json`.

Это исторический off/auto прогон до введения per-request budget. Текущий
provider всегда ограничивает reasoning; последующие результаты и defaults
приведены в [budget A/B/C](llama_reasoning_budget_abc.md). Старый harness теперь
также получает ограниченный default и не воспроизводит unrestricted прогон.

## Изменение default

В `core/llama_cpp_process.py` единственное изменение launch arguments:
`--reasoning off` → `--reasoning auto`. Все остальные параметры lifecycle,
GPU selection, timeouts, profiles, ownership и cleanup сохранены.
В `config.py` и `LlamaCppConfig` default reasoning теперь `True`:
provider больше не отправляет `reasoning_effort="none"` автоматически.
Gemma использует thinking через свой штатный chat template. Явное отключение
на уровне request по-прежнему доступно. Новые runtime слои не добавлены.

Unit tests проверяют `auto` в команде, отсутствие отключающего HTTP поля
по умолчанию и сохранение явного request override. Полный набор:
`python -m pytest -q tests` — **141 passed**, одно существующее предупреждение
Starlette TestClient/httpx.

## Методика live A/B

Использованы b11429, Gemma 4 26B-A4B-it Q4_K_M, `normal` / context 16384,
Vulkan0 / AMD Radeon RX 7900 XTX, полный GPU offload, port 8088.
Для каждого режима manager запускал собственный свежий server, проверял
readiness и выполнял одинаковый короткий warmup. Затем один и тот же сложный
русский запрос измерялся через streaming provider. Между двумя server commands
отличается только значение `--reasoning`; это проверяется harness.
HTTP requests используют одинаковые system/user prompts и параметры:
temperature 0, seed 42, max_tokens 8192, request timeout 300 s.
Изменённые для теста budget/timeout хранятся в отдельном test settings-файле;
production defaults и пользовательские настройки не менялись.

Latency ниже — время запроса до завершения stream, без startup/warmup.
Completion tokens берутся из server usage и включают thinking. Отдельное число
thinking tokens API здесь не предоставил; вместо него фиксируется наличие
thinking deltas и число их символов. Содержимое thinking не сохраняется.
Один прогон на режим; результаты не являются общим benchmark модели.

Повторяемый ручной тест, не собираемый pytest:

```bash
python tests/live/llama_reasoning_ab.py \
  --output external/gemma4-validation/reasoning-ab/bayes
```

## Основная задача: Байес и выбор действия

Запрос задаёт смесь двух поставщиков, условно независимые тесты только при
известных поставщике и состоянии детали, два наблюдаемых результата и выбор
между принятием, отклонением и третьим платным тестом. Ответ требуется как JSON
с восемью числовыми полями, стратегией и политикой после третьего теста.
Точный oracle в harness вычисляет четыре совместных веса и ожидаемые потери
через `fractions.Fraction`, без промежуточного округления.

| Показатель | off | auto |
| --- | ---: | ---: |
| Latency | 2,178 s | 62,755 s |
| Первый meaningful delta | 0,232 s | 0,239 s |
| Первый текст итогового ответа | 0,232 s | отсутствует |
| Prompt tokens | 530 | 528 |
| Completion tokens | 269 | 8192 |
| Generation speed по server timings | 137,74 tok/s | 130,98 tok/s |
| Thinking symbols | 0 | 12264 |
| Finish reason | stop | length |
| Качество конечного ответа | JSON валиден; 0/8 чисел верны; стратегия неверна | итоговый ответ отсутствует |

Различие prompt tokens возникает при применении server chat template в двух
reasoning modes; исходные system/user prompts идентичны.
Рубрика — десять проверок по 10 баллов: JSON object, восемь чисел с допуском
1e-5, правильные стратегия и политика. `off`: 10/100 только за валидный JSON.
В артефакте `auto`: 0/100 из-за отсутствия ответа; этот score не является
оценкой математической корректности незавершённого thinking.

Точные эталонные значения, округлённые для показа:

```json
{
  "p_defect": 0.124249,
  "p_supplier_b": 0.580666,
  "p_t3_positive": 0.156239,
  "p_defect_t3_positive": 0.666089,
  "p_defect_t3_negative": 0.023917,
  "cost_accept": 24.849809,
  "cost_reject": 26.893774,
  "cost_retest": 9.121486,
  "optimal_strategy": "retest",
  "policy": {"positive": "reject", "negative": "accept"}
}
```

`off` вернул `p_defect=0.045455` и `optimal_strategy="reject"`, то есть
быстро завершил запрос с неправильным решением. `auto` реально включил thinking,
но весь лимит 8192 completion tokens был израсходован до конечного JSON.
Улучшение качества конечного ответа этим прогоном не подтверждено.

Основной артефакт:
`external/gemma4-validation/reasoning-ab/bayes/result.json`.
Stdout/stderr: подкаталоги `bayes/off` и `bayes/auto`.

## Дополнительная стресс-проверка

Первый A/B использовал задачу выбора 5–7 из 12 проектов с тремя ресурсными
ограничениями, зависимостями, исключениями и лексикографическим optimum.
Oracle полного перебора нашёл 61 допустимый набор; optimum B,C,G,H,J,K,
value 71, cost 30, days 26, equipment 13.

Оба режима достигли 8192 completion tokens / finish reason `length`:
`off` — 62,603 s, невалидный JSON и повторяющийся текст;
`auto` — 62,574 s, 18727 thinking symbols, итоговый текст отсутствует.
Этот неуспешный результат сохранён отдельно:
`external/gemma4-validation/reasoning-ab/result-knapsack-stress.json`.

## Cleanup и вывод

Все четыре собственных server завершены SIGTERM, exit code 0, forced kill false.
PID отсутствуют после stop; независимая проверка не нашла `llama-server`,
порт 8088 свободен и успешно bind-ится. Пользовательские настройки сохранили
SHA256 `c89037179fb05e9ce47008ce464ad8152016b33dc98d421e2e05497b1543a40c`.
После A/B RX 7900 XTX использовала около 1,60–1,67 GiB по kernel sysfs — общая память
GPU с desktop/driver, не per-process metric. VRAM parser в manager не добавлялся.

Default оставлен **auto**, как запрошено. Live подтверждает активный thinking
и корректный lifecycle, но показывает риск исчерпания общего output budget
без конечного ответа на сложных запросах. Production budget не увеличивался;
его нельзя считать достаточным для таких задач на основании этих результатов.
