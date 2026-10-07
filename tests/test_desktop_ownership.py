from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from core.owned_exec import owned_command
from core.runtime import close_runtime


def test_owned_exec_keeps_pid_and_command():
    command = [sys.executable, '-c', 'import os; print(os.getpid())']
    child = subprocess.Popen(owned_command(command), stdout=subprocess.PIPE, text=True)
    output, _ = child.communicate(timeout=5)
    assert child.returncode == 0
    assert int(output.strip()) == child.pid


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux parent ownership')
def test_child_refuses_wrong_owner():
    helper = Path(__file__).resolve().parents[1] / 'core/owned_exec.py'
    result = subprocess.run([sys.executable, str(helper), '0', sys.executable, '-c', 'print("unexpected")'],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert 'owner exited' in result.stderr
    assert not result.stdout


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux parent ownership')
def test_parent_death_stops_only_its_native_child():
    code = ('import subprocess, sys; from core.owned_exec import owned_command; '
            'child=subprocess.Popen(owned_command([sys.executable,"-c","import time; time.sleep(60)"])); '
            'print(child.pid,flush=True); sys.stdin.readline()')
    parent = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    child_pid = int(parent.stdout.readline())
    try:
        # Wait for the exec guard before killing the owner.
        time.sleep(.15)
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        status = Path('/proc') / str(child_pid) / 'status'
        while True:
            try:
                if 'State:\tZ' in status.read_text():
                    break
            except (FileNotFoundError, ProcessLookupError):
                break
            if time.monotonic() >= deadline:
                pytest.fail('owned child survived owner death')
            time.sleep(.01)
    finally:
        if parent.poll() is None:
            parent.stdin.close()
            parent.wait(timeout=5)
        parent.stdout.close()


def test_runtime_stops_all_children_even_after_one_cleanup_error():
    called = []
    def failed():
        called.append('stt')
        raise RuntimeError('worker cleanup error')
    runtime = SimpleNamespace(background_tasks=[], stt=SimpleNamespace(close=failed),
        llama_cpp=SimpleNamespace(close=lambda: called.append('llama')),
        tts=SimpleNamespace(stop_server=lambda: called.append('cosy')))
    with pytest.raises(RuntimeError, match='worker cleanup'):
        close_runtime(runtime)
    assert called == ['stt', 'llama', 'cosy']
