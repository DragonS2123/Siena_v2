"""CosyVoice3 native server adapter for the existing Siena voice API."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx

from voice.errors import TTSUnavailableError
from voice.text_sanitize import sanitize_text_for_tts_detailed


def wav_info(raw):
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise TTSUnavailableError("CosyVoice returned invalid WAV")
    pos, rate, align, size = 12, 0, 0, None
    while pos + 8 <= len(raw):
        kind, n = raw[pos:pos + 4], struct.unpack_from('<I', raw, pos + 4)[0]
        start = pos + 8
        if start + n > len(raw):
            raise TTSUnavailableError("CosyVoice WAV is truncated")
        if kind == b'fmt ' and n >= 16:
            encoding, channels, rate, _, align, bits = struct.unpack_from('<HHIIHH', raw, start)
            if encoding not in (1, 3) or channels != 1 or not rate or not align or bits not in (16, 32):
                raise TTSUnavailableError("unsupported CosyVoice WAV encoding")
        if kind == b'data':
            size = n
        pos = start + n + n % 2
    if size is None or not size or not rate or not align:
        raise TTSUnavailableError("CosyVoice WAV has no audio")
    return size / align / rate, rate


class CosyVoiceCppProvider:
    PROVIDER_NAME = "cosyvoice3_cpp"

    def __init__(self, binary, model, prompt, output_dir, log_dir, server_url,
                 device, expected_gpu, icd, voice="siena_ru_female", timeout=120):
        self._binary, self._model, self._prompt = map(Path, (binary, model, prompt))
        self._output_dir, self._log_dir = map(Path, (output_dir, log_dir))
        self._url = server_url.rstrip('/')
        parsed = urlsplit(self._url)
        if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
            raise ValueError("CosyVoice endpoint must be explicit loopback HTTP")
        self._host, self._port = parsed.hostname, parsed.port
        self._device, self._expected_gpu, self._icd = device, expected_gpu, icd
        self._voice, self._timeout = voice, timeout
        self._lock = threading.RLock()
        self._process = None
        self._logs = []

    @property
    def voice(self): return self._voice

    @property
    def language(self): return "Russian"

    @property
    def device(self): return self._device

    def is_available(self):
        return all(p.is_file() for p in (self._binary, self._model, self._prompt, self._icd))

    def is_server_managed_by_us(self):
        return self._process is not None and self._process.poll() is None

    def ensure_server_running(self):
        with self._lock:
            if self.is_server_managed_by_us():
                return
            self.stop_server()
            if not self.is_available():
                raise TTSUnavailableError("CosyVoice binary/model/voice/RADV files are missing")
            with socket.socket() as sock:
                try:
                    sock.bind((self._host, self._port))
                except OSError as exc:
                    raise TTSUnavailableError(f"CosyVoice port {self._port} is occupied; refusing adoption") from exc
            env = dict(os.environ)
            env.pop('VK_ICD_FILENAMES', None)
            env.update(VK_DRIVER_FILES=str(self._icd), GGML_VK_VISIBLE_DEVICES='0',
                       LD_LIBRARY_PATH=str(self._binary.parent.resolve()))
            command = [str(self._binary), '--api', '--model', str(self._model),
                       '--voice-prompt', f'{self._voice}={self._prompt}', '--backend', self._device,
                       '--host', self._host, '--port', str(self._port)]
            # Query stable GGML device APIs in a separate short-lived process,
            # using exactly the libraries/environment of the TTS child.
            probe = '''import ctypes,sys
from pathlib import Path
c=ctypes.CDLL(str(Path(sys.argv[1])/'libggml.so.0'))
c.ggml_backend_load_all_from_path.argtypes=[ctypes.c_char_p]
c.ggml_backend_load_all_from_path(sys.argv[1].encode())
c.ggml_backend_dev_count.restype=ctypes.c_size_t
c.ggml_backend_dev_get.argtypes=[ctypes.c_size_t]; c.ggml_backend_dev_get.restype=ctypes.c_void_p
for name in ('ggml_backend_dev_name','ggml_backend_dev_description'):
 f=getattr(c,name); f.argtypes=[ctypes.c_void_p]; f.restype=ctypes.c_char_p
c.ggml_backend_dev_type.argtypes=[ctypes.c_void_p]; c.ggml_backend_dev_type.restype=ctypes.c_int
ok=False
for i in range(c.ggml_backend_dev_count()):
 d=c.ggml_backend_dev_get(i)
 if c.ggml_backend_dev_name(d).decode()==sys.argv[2]:
  desc=c.ggml_backend_dev_description(d).decode()
  ok=c.ggml_backend_dev_type(d)==1 and sys.argv[3] in desc and not any(s in desc.lower() for s in ('ryzen','llvmpipe','lavapipe'))
sys.exit(0 if ok else 2)
'''
            try:
                check = subprocess.run([sys.executable, '-c', probe, str(self._binary.parent.resolve()),
                                        self._device, self._expected_gpu], env=env, capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TTSUnavailableError("CosyVoice GPU probe failed") from exc
            if check.returncode:
                raise TTSUnavailableError("CosyVoice expected discrete GPU unavailable; refusing fallback")
            self._log_dir.mkdir(parents=True, exist_ok=True)
            try:
                self._logs = [(self._log_dir / f'cosyvoice.{s}.log').open('ab') for s in ('stdout', 'stderr')]
                self._process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                                 stdout=self._logs[0], stderr=self._logs[1], start_new_session=True)
                deadline = time.monotonic() + 60
                with httpx.Client(timeout=2, trust_env=False) as client:
                    while time.monotonic() < deadline:
                        if self._process.poll() is not None:
                            raise TTSUnavailableError(f"CosyVoice exited during startup ({self._process.returncode})")
                        try:
                            response = client.get(self._url + '/healthz')
                            response.raise_for_status()
                            models = client.get(self._url + '/v1/models')
                            models.raise_for_status()
                            if any(m.get('id') == 'cosyvoice3-2512' for m in models.json().get('data', [])):
                                return
                        except (httpx.HTTPError, ValueError):
                            pass
                        time.sleep(.2)
                raise TTSUnavailableError("CosyVoice startup timed out")
            except Exception:
                self.stop_server()
                raise

    def stop_server(self):
        with self._lock:
            if self._process is not None:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=5)
                else:
                    self._process.wait()
                self._process = None
            for log in self._logs:
                log.close()
            self._logs.clear()

    def synthesize_to_file(self, text, voice=None):
        if voice is not None and voice != self._voice:
            raise TTSUnavailableError(f"unknown CosyVoice voice: {voice}")
        text = sanitize_text_for_tts_detailed(text, strip_all_numbers=False).text
        if not text.strip():
            raise TTSUnavailableError("no speakable text")
        self.ensure_server_running()
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                response = client.post(self._url + '/v1/audio/speech', json={
                    'model': 'cosyvoice3-2512', 'input': text, 'voice': self._voice, 'response_format': 'wav'})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TTSUnavailableError("CosyVoice speech request failed") from exc
        duration, rate = wav_info(response.content)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f'{uuid.uuid4()}.wav'
        path.write_bytes(response.content)
        return {'audio_path': str(path), 'audio_filename': path.name, 'provider': self.PROVIDER_NAME,
                'voice': self._voice, 'sample_rate': rate, 'duration_sec': duration,
                'elapsed_sec': round(time.monotonic() - start, 3), 'backend': 'vulkan'}
