from __future__ import annotations

import json
import subprocess
import pytest

from core.errors import SienaInfraError, SienaTimeoutError
from voice.gigaam_stt import GigaAMSTTProvider


class FakeWorker:
    def __init__(self, command, **kwargs):
        self.command, self.kwargs = command, kwargs
        self.returncode = None
        self.failure = None
        self.terminated = self.killed = False
        self.ignore_term = False
        self.communications = 0

    def communicate(self, timeout):
        self.communications += 1
        assert timeout in (120, 5)
        if self.failure == 'timeout' and not self.killed:
            raise subprocess.TimeoutExpired(self.command, timeout)
        self.returncode = 2 if self.failure == 'exit' else (-9 if self.killed else 0)
        if self.killed: return '', ''
        return ('not json' if self.failure == 'malformed' else json.dumps({'text': '' if self.failure == 'empty' else 'Привет.', 'segments': 1}),
                'expected discrete GPU unavailable')
    def poll(self): return self.returncode
    def terminate(self):
        self.terminated = True
        if not self.ignore_term: self.returncode = -15
    def kill(self): self.killed = True; self.returncode = -9
    def wait(self, timeout):
        if self.returncode is None: raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode


@pytest.fixture
def stt(tmp_path, monkeypatch):
    library, model, icd = (tmp_path / n for n in ('libtranscribe.so', 'gigaam.gguf', 'radv.json'))
    for p in (library, model, icd): p.write_bytes(b'fixture')
    monkeypatch.setattr('voice.gigaam_stt.importlib.util.find_spec', lambda _: object())
    return GigaAMSTTProvider(library, model, '0000:03:00.0', 'AMD Radeon RX 7900 XTX', icd)


def test_transcribe_exact_gpu_worker_and_result(stt, monkeypatch):
    workers = []
    def spawn(command, **kwargs):
        assert command[command.index('--device-id')+1] == '0000:03:00.0'
        assert command[command.index('--expected-gpu')+1] == 'AMD Radeon RX 7900 XTX'
        assert kwargs['env']['GGML_VK_VISIBLE_DEVICES'] == '0'
        assert 'VK_ICD_FILENAMES' not in kwargs['env']
        assert kwargs['stdout'] == subprocess.PIPE and kwargs['stderr'] == subprocess.PIPE
        worker = FakeWorker(command, **kwargs); workers.append(worker); return worker
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    result = stt.transcribe_wav('input.wav')
    assert result['text'] == 'Привет.' and result['backend'] == 'vulkan'
    assert not stt._workers and workers[0].communications == 1


@pytest.mark.parametrize('kind',['timeout','exit','malformed','empty'])
def test_transcription_failures_are_controlled(stt, monkeypatch, kind):
    workers = []
    def spawn(command, **kwargs):
        worker = FakeWorker(command, **kwargs); worker.failure=kind; workers.append(worker); return worker
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    with pytest.raises(SienaTimeoutError if kind=='timeout' else SienaInfraError): stt.transcribe_wav('input.wav')
    assert len(workers)==1 and not stt._workers
    if kind=='timeout': assert workers[0].killed and workers[0].communications==2


def test_missing_assets_and_wrong_language(stt):
    assert stt.is_available()
    stt.model_path.unlink()
    assert not stt.is_available() and 'GGUF' in stt.unavailable_reason()
    with pytest.raises(SienaInfraError, match='Russian'): stt.transcribe_wav('input.wav','en')
    with pytest.raises(SienaInfraError, match='GGUF'): stt.transcribe_wav('input.wav')


@pytest.mark.parametrize('force',[False,True])
def test_close_only_owned_worker_handles_and_duplicate_shutdown(stt, force):
    worker=FakeWorker(['owned']); worker.ignore_term=force
    stt._workers.add(worker)
    stt.close(); stt.close()
    assert worker.terminated and worker.killed==force
    assert stt._closing
    with pytest.raises(SienaInfraError, match='shutting down'): stt.transcribe_wav('input.wav')
