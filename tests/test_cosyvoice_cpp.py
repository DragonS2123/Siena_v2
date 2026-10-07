from __future__ import annotations

import struct
import subprocess
from types import SimpleNamespace

import httpx
import pytest

from voice.cosyvoice_cpp import CosyVoiceCppProvider, wav_info
from voice.errors import TTSUnavailableError


def float_wav():
    fmt = struct.pack('<HHIIHH', 3, 1, 24000, 96000, 4, 32)
    audio = b'\0' * 9600
    payload = b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt + b'data' + struct.pack('<I', len(audio)) + audio
    return b'RIFF' + struct.pack('<I', len(payload)) + payload


@pytest.fixture
def tts(tmp_path):
    assets = [tmp_path / n for n in ('cosyvoice-server', 'model.gguf', 'voice.gguf', 'radv.json')]
    for p in assets: p.write_bytes(b'fixture')
    return CosyVoiceCppProvider(*assets[:3], tmp_path / 'out', tmp_path / 'logs', 'http://127.0.0.1:8080',
                                'Vulkan0', 'AMD Radeon RX 7900 XTX', assets[3])


def fake_runtime(monkeypatch, *, wrong_gpu=False, occupied=False, force=False):
    processes = []
    class Process:
        def __init__(self, command, **kwargs):
            self.command, self.returncode, self.terminated, self.killed = command, None, False, False
            assert command[command.index('--backend') + 1] == 'Vulkan0'
            assert kwargs['env']['GGML_VK_VISIBLE_DEVICES'] == '0'
            processes.append(self)
        def poll(self): return self.returncode
        def terminate(self): self.terminated = True
        def kill(self): self.killed = True
        def wait(self, timeout=None):
            if force and not self.killed: raise subprocess.TimeoutExpired(self.command, timeout)
            self.returncode = 0
            return 0
    class Socket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address):
            if occupied: raise OSError('occupied')
    monkeypatch.setattr('voice.cosyvoice_cpp.socket.socket', Socket)
    monkeypatch.setattr(subprocess, 'Popen', Process)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=2 if wrong_gpu else 0))
    real_client = httpx.Client
    def handle(request):
        if request.url.path == '/healthz': return httpx.Response(200, json={'status': 'ok'})
        if request.url.path == '/v1/models': return httpx.Response(200, json={'data': [{'id': 'cosyvoice3-2512'}]})
        if request.url.path == '/v1/audio/speech':
            import json
            body = json.loads(request.content)
            assert body['model'] == 'cosyvoice3-2512' and body['voice'] == 'siena_ru_female'
            return httpx.Response(200, content=float_wav())
        raise AssertionError(request.url)
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)))
    return processes


def test_native_float_wav_and_truncation():
    assert wav_info(float_wav()) == (.1, 24000)
    with pytest.raises(TTSUnavailableError): wav_info(float_wav()[:-1])
    with pytest.raises(TTSUnavailableError): wav_info(b'not audio')


def test_start_idempotent_synthesize_and_owned_stop(tts, monkeypatch):
    processes = fake_runtime(monkeypatch)
    try:
        tts.ensure_server_running()
        tts.ensure_server_running()
        assert len(processes) == 1
        result = tts.synthesize_to_file('Привет.')
        assert result['duration_sec'] == .1 and result['provider'] == 'cosyvoice3_cpp'
        assert tts.is_server_managed_by_us()
    finally: tts.stop_server()
    assert processes[0].terminated and not processes[0].killed
    assert not tts.is_server_managed_by_us()
    tts.stop_server()


@pytest.mark.parametrize('failure', ['gpu', 'port'])
def test_wrong_gpu_and_foreign_port_refused(tts, monkeypatch, failure):
    processes = fake_runtime(monkeypatch, wrong_gpu=failure == 'gpu', occupied=failure == 'port')
    with pytest.raises(TTSUnavailableError): tts.ensure_server_running()
    assert not processes


def test_forced_kill_fallback(tts, monkeypatch):
    processes = fake_runtime(monkeypatch, force=True)
    tts.ensure_server_running()
    tts.stop_server()
    assert processes[0].terminated and processes[0].killed
