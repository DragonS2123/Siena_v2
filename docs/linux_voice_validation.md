# Siena: голосовая цепочка на Bazzite

Проверено 2026-10-07, ветка `port/bazzite-linux`, Bazzite deployment
`44.20261006`, GNOME/Wayland, RX 7900 XTX, SELinux Enforcing.

## Реализованная цепочка

```text
Electron / Web Audio, микрофон PipeWire
  → WAV 16 kHz mono PCM16 → существующий Siena voice API
  → GigaAM-v3 e2e-RNNT Q8_0 / transcribe.cpp / Vulkan RADV
  → существующий chat API / managed Gemma
  → CosyVoice3-2512 Q8_0 / cosyvoice.cpp / Vulkan RADV
  → WAV 24 kHz mono float32 → Electron Audio / PipeWire
```

LLM-настройки этого этапа не изменены: Gemma Q4_K_M, normal 16384,
8088, reasoning auto, бюджет 512, прежние sampling и routing.
CosyVoice занимает 8080; backend — 8000. systemd не используется.

`voice/gigaam_stt.py` сохраняет существующий интерфейс STT. Отдельный
ограниченный по времени worker изолирует native binding и освобождает GPU
после каждого запроса. Worker принимает только WAV 16 kHz/mono/PCM16 до 60
секунд. Записи длиннее 24 секунд делятся по тихим участкам; абсолютные
таймкоды и streaming STT не реализованы. Язык — русский.

`voice/cosyvoice_cpp.py` запускает собственный cosyvoice-server лениво при
первом синтезе. Чужой процесс на 8080 не присваивает и не останавливает.
Проверяет `/healthz` и модель `/v1/models`, отправляет
`/v1/audio/speech`, хранит WAV в текущем TTS output directory. При завершении
runtime останавливает свой процесс через TERM, затем KILL при необходимости.
GGML-инференс использует Vulkan; подготовка voice prompt через ONNX выполнялась
на CPU. Изменений Whisper/qwentts.cpp и других подсистем нет.

## Устройство и артефакты

Все загруженные файлы находятся в игнорируемом `external/audio-validation`.
Исходники upstream — `external/transcribe.cpp` и `external/cosyvoice.cpp`.

* STT: [transcribe.cpp v0.3.1](https://github.com/handy-computer/transcribe.cpp/releases/tag/v0.3.1),
  native Linux cpu-vulkan bundle и Python binding `transcribe-cpp==0.3.1`.
  Архив `transcribe-native-0.3.1-linux-x86_64-cpu-vulkan.tar.gz`:
  SHA256 `7656cd88b563d79af4e483400aebd149c50aad2c3b20ced1f5a06ce1e50f435a`.
* TTS: [cosyvoice.cpp v0.1.3](https://github.com/Lourdle/cosyvoice.cpp/releases/tag/v0.1.3),
  `cosyvoice-1616b12-linux-x86_64-miniaudio-no_icu.tgz`, SHA256
  `58eeb9e7e84cbc21164e948bc5b5ff59892ac54499ae16c7216d1e9b97579bbc`.
  ICU-сборка требовала отсутствующий ICU 78; выбран upstream вариант без ICU.
  GGML shared libraries скопированы в каталог TTS из существующего b11429;
  исходные библиотеки LLM не менялись. Используется локальный LD_LIBRARY_PATH
  дочернего TTS процесса. Это проверенный набор для этой машины, не универсальный
  установщик или гарантия ABI для следующих релизов.
* [GigaAM GGUF](https://huggingface.co/handy-computer/gigaam-v3-e2e-rnnt-gguf),
  revision `b9b68a835993df09018237a11d2a9b8c1925844d`,
  `models/gigaam-v3-e2e-rnnt-Q8_0.gguf`, SHA256
  `78d63b47723b7f8d78c6113a6ef983b5a86e2a86f6c273e1f5cb6967b1c4467a`.
* [CosyVoice3 GGUF](https://huggingface.co/Lourdle/Fun-CosyVoice3-0.5B-2512-GGUF),
  revision `ab01740bbbfcc059fd748163787554f7c8977e64`,
  `models/CosyVoice3-2512_Q8_0.gguf`, SHA256
  `be133cb6154ca73cd1d213b1c9496def99ba9c1f7c14cd99f6b350af2eb7963d`.
  Дополнительные файлы подготовки голоса:
  `campplus.onnx` SHA256
  `a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73`,
  `speech_tokenizer_v3.int8.onnx` SHA256
  `d7caa04fbc2f54af196906469471b607ce2594b7e92ac07dddfb315103a74c72`.

STT проверяет PCI ID `0000:03:00.0`, Vulkan GPU и описание
`AMD Radeon RX 7900 XTX (RADV NAVI31)`. TTS проверяет Vulkan0 через GGML C API
в изолированном probe и описание RX 7900 XTX. Оба процесса получают только
RADV ICD `/usr/share/vulkan/icd.d/radeon_icd.x86_64.json` и
`GGML_VK_VISIBLE_DEVICES=0`. Несовпадение устройства приводит к ошибке;
CPU/iGPU/llvmpipe fallback не используется.

## Русский женский голос

Временный validation voice `siena_ru_female` построен из женской записи
[FLEURS, Google, ru_ru/test](https://huggingface.co/datasets/google/fleurs),
лицензия CC-BY-4.0, файл `4573705590156444548.wav`, 7,32 с. Текст:
«Помимо соревнований в среду, Карпендо принимал участие в двух индивидуальных
соревнованиях на чемпионатах».

Attribution: FLEURS, Google and dataset contributors; запись преобразована
в mono PCM16 16 kHz и использована для извлечения speaker prompt. Исходный
source WAV SHA256
`ccac1adf2d94742aefb318adb8c59d21e4bb0a77b9f2b17e5334548077951ccd`;
преобразованный reference WAV SHA256
`365dc41352cbef719187312a559623eecdfc9e557783999407eb4a7032f1fb77`.
Исходник, JSON attribution и преобразованный WAV сохранены в `voices/`.
Готовый `voices/russian-female.gguf` SHA256
`2f9c896007cff686a95e5362e2726c2715ae64752b002cabd1e413b08760a31e`.
Подготовка prompt заняла 1,267 с. Старые Qwen voice profiles не перенесены
в CosyVoice; данный голос выбирается в `config.COSYVOICE_VOICE` и
`config.COSYVOICE_PROMPT`.

## Результаты живой проверки

| Проверка | Результат |
|---|---|
| STT, запись 4,5 с | 0,357 с, все слова распознаны |
| STT, запись 10,98 с | 0,455 с, все слова распознаны |
| STT, запись 33,84 с | 1,045 с native; worker с разбиением — около 1,293 с |
| Standalone русский TTS | 10,92 с аудио за 2,216 с с загрузкой модели |
| Контроль TTS через GigaAM | Все слова тестовой фразы распознаны |
| API-цепочка: STT | 0,542 с |
| API-цепочка: Gemma | 1,922 с |
| API-цепочка: TTS | 0,506 с, ответ длиной 3,56 с |
| API-цепочка: обработка до воспроизведения | 2,970 с |
| PipeWire playback | pw-play завершился успешно, default Razer USB Sound Card |
| Electron / Wayland | UI отрисован, Gemma Ready, voice API доступен |
| Electron microphone | getUserMedia успешно, 48 kHz default source |
| Electron Speak | Реальный Audio.play → ended, 2,88 с, без playback error |
| VRAM с Gemma + CosyVoice | 21 103 792 128 B ≈ 19,65 GiB из 24 GiB |

API-цепочка проверена на синтетическом входном WAV с вопросом
«Сиена, привет. Ответь одним коротким предложением: ты меня слышишь?».
Распознано: «Сиена, привет. Ответь одним коротким предложением. Ты меня
слышишь?». Финальный ответ Gemma: «Привет, да, я тебя слышу».
Это проверка реальных inference/API/playback, но не подтверждение качества
распознавания голоса пользователя из его микрофона. Для последнего требуется
произнесённая пользователем фраза. Subjective оценка тембра также остаётся за
пользователем; транскрипция проверяет разборчивость, не естественность голоса.
VRAM снята через AMD sysfs `mem_info_vram_used`; это общая занятость GPU,
включая desktop и другие приложения, а не отдельная метрика процесса.

Подробные результаты: `results/stt-standalone.json`, `tts-standalone.json`,
`voice-chain-api.json`, `electron-live.log` и `electron-screenshot.png`.
Сохранены только конечные ответы; reasoning trace в history не добавлялся.

Python suite: 163 passed, 1 существующее предупреждение Starlette.
Frontend: typecheck/build прошли; 39 tests passed (5 files).
Дополнительно проверен shell syntax обоих Linux launch scripts.
Полный Python suite запускается командой `python -m pytest tests -q`;
корневой discovery также подхватывает независимые тесты загруженных upstream
репозиториев в `external/`, которые требуют собственных build environments.

## Запуск подготовленного рабочего места

Из корня проекта в одном терминале:

```bash
SIENA_PYTHON=/tmp/siena-provider-test-venv/bin/python bash scripts/start_linux.sh
```

В другом терминале:

```bash
bash scripts/start_desktop_linux.sh
```

Полный smoke test с реальным микрофоном и воспроизведением, при работающем backend:

```bash
/tmp/siena-provider-test-venv/bin/python tests/live/live_linux_voice.py --record-seconds 10
```

После сообщения Recording произнести вопрос. Вместо микрофона можно передать
`--input external/audio-validation/results/voice-chain-question.wav`.

Используется уже существующее нативное Linux Python-окружение. Новый Ollama
не устанавливался и daemon не запускался. В этом окружении ранее установлен
legacy Python SDK, необходимый пока для импортов сохранённого legacy кода.
Окружение в `/tmp` временное; перенос в постоянную Linux .venv — отдельная
задача с явным решением старых зависимостей, без изменения зафиксированного LLM.
Windows .venv не используется. UI зависимости установлены чистым `npm ci`
из существующего неизменённого lockfile; сборка Linux Electron 33.4.11.

Новые пользовательские данные находятся в игнорируемом `storage/linux/`.
Старый `storage/settings.json` не переключён, его SHA256 остался
`c89037179fb05e9ce47008ce464ad8152016b33dc98d421e2e05497b1543a40c`.
Для другой директории задать одинаковый `SIENA_DATA_DIR` обоим launch scripts.
Checkpoint/HEAD остались `13b8519eec946bd41fde52292861e1a78e4ffb06`;
коммитов нет.

В интерфейсе: открыть/создать чат; микрофон — запись, повторное нажатие —
STT в поле ввода; отправить текст; Speak — озвучить. Существующий
Conversation Mode включает автоматическую цепочку с VAD и возвратом к
прослушиванию после ответа. Это half-duplex: во время ответа микрофонный
сигнал не отправляется обратно в STT. Barge-in и streaming TTS не добавлялись.

Ограничения: нет оценки WER на полном датасете и длительного stress test;
качество длинной речи и произношения чисел требует дополнительной проверки.
Сборка no_icu не даёт полной ICU-нормализации текста. В Chromium/Electron 33
на Wayland наблюдались ошибки EGLImage и перезапуски compositor GPU process;
после восстановления UI отрисовался, микрофон и playback работают. В списке
процессов подтверждён fallback desktop rendering на SwiftShader. Это
отдельная проблема desktop rendering; Vulkan inference не использует
программный GPU. Старый Qwen profile picker пока не управляет CosyVoice.
