"""Linux exec-only parent-death guard for runtime-owned native children.

No daemon or PID-file adoption. The child keeps its Popen PID after exec.
Normal shutdown still uses the manager's SIGTERM/wait/kill sequence.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import signal
import sys


def owned_command(command: list[str]) -> list[str]:
    if not sys.platform.startswith('linux'):
        return command
    return [sys.executable, str(Path(__file__).resolve()), str(os.getpid()), *map(str, command)]


def main():
    parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    # PR_SET_PDEATHSIG. Last-resort cleanup if the owner crashes or is killed
    # after its shutdown deadline. Avoid unsafe Python preexec_fn in threads.
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'unable to establish process ownership')
    if os.getppid() != parent:
        raise SystemExit('owner exited before child startup')
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == '__main__':
    main()
