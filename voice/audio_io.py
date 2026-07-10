"""Простые функции записи/воспроизведения WAV на стороне backend/CLI.

MVP голосового слоя — push-to-talk через браузер: UI сам пишет аудио через
Web Audio API и присылает готовый файл в /api/voice/transcribe. Эти функции
не используются веб-эндпоинтами напрямую — они для CLI-диагностики и на
будущее (например, main.py когда-нибудь захочет голосовой ввод локально).
"""

from __future__ import annotations

import sounddevice as sd
import soundfile as sf


def record_wav(seconds: float, output_path: str, sample_rate: int = 16000) -> str:
    frames = int(seconds * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    sf.write(output_path, audio, sample_rate)
    return output_path


def play_wav(path: str) -> None:
    data, sample_rate = sf.read(path, dtype="float32")
    sd.play(data, sample_rate)
    sd.wait()
