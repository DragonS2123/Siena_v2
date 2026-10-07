"""Manual microphone/WAV → Siena STT → chat → TTS → PipeWire smoke test.

Run with the backend already started, for example:
  python tests/live/live_linux_voice.py --record-seconds 10
  python tests/live/live_linux_voice.py --input /path/to/16khz-mono-pcm16.wav
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path)
    source.add_argument('--record-seconds', type=float)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    if args.record_seconds is not None and not 1 <= args.record_seconds <= 60:
        parser.error('record-seconds must be between 1 and 60')
    results = Path(__file__).resolve().parents[2] / 'external/audio-validation/results'
    results.mkdir(parents=True, exist_ok=True)
    wav = args.input
    if args.record_seconds is not None:
        wav = results / f'microphone-{time.time_ns()}.wav'
        print(f'Recording {args.record_seconds:g} seconds. Speak into the microphone now.', flush=True)
        capture = subprocess.Popen(['pw-record', '--format=s16', '--rate=16000',
                                    '--channels=1', str(wav)])
        try:
            capture.wait(timeout=args.record_seconds)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if capture.poll() is None:
                capture.send_signal(signal.SIGINT)
                try:
                    capture.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    capture.kill()
                    capture.wait(timeout=5)
        if not wav.is_file() or wav.stat().st_size <= 44:
            raise RuntimeError('PipeWire did not produce microphone audio')
    if not wav.is_file():
        parser.error('input WAV does not exist')
    report = {'input': str(wav), 'live_microphone': args.record_seconds is not None}
    with httpx.Client(base_url=args.url.rstrip('/'), timeout=180, trust_env=False) as client:
        def post(endpoint, **kwargs):
            response = client.post(endpoint, **kwargs)
            response.raise_for_status()
            return response.json()

        start = time.monotonic()
        with wav.open('rb') as audio:
            transcript = post('/api/voice/stt/transcribe',
                              files={'file': (wav.name, audio, 'audio/wav')}, data={'language': 'ru'})
        report['stt_seconds'] = round(time.monotonic() - start, 3)
        report['transcript'] = transcript
        print('Recognized:', transcript['text'], flush=True)
        conversation = post('/api/conversations', json={'title': 'Linux voice smoke test'})
        start = time.monotonic()
        reply = post('/api/chat', json={'conversation_id': conversation['conversation_id'],
                                       'message': transcript['text'], 'mode': 'chat'})
        report['chat_seconds'] = round(time.monotonic() - start, 3)
        report['answer'] = reply['answer']
        report['conversation_id'] = conversation['conversation_id']
        print('Siena:', reply['answer'], flush=True)
        start = time.monotonic()
        speech = post('/api/voice/synthesize', json={'text': reply['answer']})
        report['tts_seconds'] = round(time.monotonic() - start, 3)
        report['speech'] = speech
        download = client.get(speech['audio_url'])
        download.raise_for_status()
        output = results / f'voice-answer-{time.time_ns()}.wav'
        output.write_bytes(download.content)
        subprocess.run(['pw-play', str(output)], check=True, timeout=180)
        report['playback'] = 'completed'
    path = results / f'voice-smoke-{time.time_ns()}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Results:', path)


if __name__ == '__main__':
    main()
