from __future__ import annotations

import hashlib
import importlib
import platform
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.dont_write_bytecode = True

_REAL_DATA_PATHS = (
    REPO_ROOT / "storage" / "settings.json",
    REPO_ROOT / "storage" / "conversations.sqlite3",
    REPO_ROOT / "storage" / "attachments",
    REPO_ROOT / "storage" / "voice_profiles.json",
    REPO_ROOT / "memory" / "short_memory.json",
    REPO_ROOT / "memory" / "long_memory.sqlite3",
    REPO_ROOT / "memory" / "candidate_memory.sqlite3",
    REPO_ROOT / "memory" / "memory_vectors.sqlite3",
    REPO_ROOT / "logs",
)
_HASH_LIMIT = 8 * 1024 * 1024


def _fingerprint_file(path: Path) -> tuple[Any, ...]:
    stat = path.stat()
    digest = None
    if stat.st_size <= _HASH_LIMIT:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ("file", stat.st_size, stat.st_mtime_ns, digest)


def _fingerprint(path: Path) -> tuple[Any, ...]:
    if not path.exists():
        return ("missing",)
    if path.is_file():
        return _fingerprint_file(path)
    entries: list[tuple[Any, ...]] = []
    for child in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        relative = child.relative_to(path).as_posix()
        if child.is_file():
            entries.append((relative, *_fingerprint_file(child)))
        elif child.is_dir():
            stat = child.stat()
            entries.append((relative, "dir", stat.st_mtime_ns))
    root_stat = path.stat()
    return ("dir", root_stat.st_mtime_ns, tuple(entries))


def _blocked(kind: str):
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(f"Core baseline blocked a real {kind} call")

    return fail


@pytest.fixture(scope="session", autouse=True)
def protect_real_user_data() -> Any:
    before = {path: _fingerprint(path) for path in _REAL_DATA_PATHS}
    yield
    after = {path: _fingerprint(path) for path in _REAL_DATA_PATHS}
    changed = [str(path.relative_to(REPO_ROOT)) for path in _REAL_DATA_PATHS if before[path] != after[path]]
    assert not changed, f"Core baseline changed protected user paths: {changed}"


@pytest.fixture(scope="session", autouse=True)
def baseline_environment(
    tmp_path_factory: pytest.TempPathFactory,
    protect_real_user_data: Any,
) -> Any:
    patch = pytest.MonkeyPatch()
    runtime_root = tmp_path_factory.mktemp("siena-core-baseline")

    # ollama's module-level client only uses these values to form its
    # User-Agent.  On Windows platform.machine() may shell out to `ver`;
    # provide deterministic values so backend import remains subprocess-free.
    patch.setattr(platform, "machine", lambda: "baseline-machine")
    patch.setattr(platform, "system", lambda: "Windows")
    patch.setattr(platform, "python_version", lambda: "3.12")

    patch.setattr(requests.sessions.Session, "request", _blocked("requests HTTP"))
    patch.setattr(urllib.request, "urlopen", _blocked("urllib HTTP"))
    patch.setattr(socket.socket, "connect", _blocked("raw socket"))
    patch.setattr(socket, "create_connection", _blocked("socket connection"))
    patch.setattr(subprocess, "Popen", _blocked("subprocess"))
    patch.setattr(subprocess, "run", _blocked("subprocess"))
    patch.setattr(subprocess, "call", _blocked("subprocess"))
    patch.setattr(subprocess, "check_call", _blocked("subprocess"))
    patch.setattr(subprocess, "check_output", _blocked("subprocess"))

    try:
        import httpx

        patch.setattr(httpx.Client, "request", _blocked("httpx HTTP"))
        patch.setattr(httpx.AsyncClient, "request", _blocked("httpx HTTP"))
    except ImportError:
        pass

    try:
        import websockets.asyncio.client

        patch.setattr(websockets.asyncio.client, "connect", _blocked("WebSocket"))
    except ImportError:
        pass

    import config

    original_base = config.BASE_DIR
    for name, value in vars(config).copy().items():
        if name.isupper() and isinstance(value, Path):
            try:
                relative = value.relative_to(original_base)
            except ValueError:
                relative = Path("isolated_paths") / name.lower()
            patch.setattr(config, name, runtime_root / relative)
    patch.setattr(config, "BASE_DIR", runtime_root)
    patch.setattr(config, "QWEN_TTS_AUTO_START", False)
    patch.setattr(config, "QWEN_TTS_KEEP_SERVER_WARM", False)
    patch.setattr(config, "REMOTE_GATEWAY_ENABLED", False)

    import core.system_metrics

    patch.setattr(
        core.system_metrics,
        "vram_metrics",
        lambda: {
            "vram_supported": False,
            "vram_used_gb": None,
            "vram_total_gb": None,
            "vram_percent": None,
            "vram_reason": "disabled_by_core_baseline",
        },
    )

    import remote_gateway.credentials

    patch.setattr(
        remote_gateway.credentials,
        "default_credentials_path",
        lambda: runtime_root / "remote_gateway" / "credentials.json",
    )

    sys.modules.pop("api.server", None)
    server = importlib.import_module("api.server")
    yield {"root": runtime_root, "server": server}

    sys.modules.pop("api.server", None)
    patch.undo()


@pytest.fixture(scope="session")
def isolated_runtime(baseline_environment: dict[str, Any]) -> Path:
    return baseline_environment["root"]


@pytest.fixture(scope="session")
def backend(baseline_environment: dict[str, Any]) -> ModuleType:
    return baseline_environment["server"]
