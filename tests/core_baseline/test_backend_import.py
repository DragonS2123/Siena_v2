from __future__ import annotations

import socket
import subprocess
import urllib.request
from pathlib import Path

import httpx
import pytest
import requests
import websockets.asyncio.client


def test_backend_imports_with_all_runtime_paths_isolated(backend, isolated_runtime: Path):
    assert backend.app.title == "Siena v2 Control Panel API"
    protected_objects = (
        backend.config.SETTINGS_STORE_PATH,
        backend.config.CONVERSATIONS_DB_PATH,
        backend.config.ATTACHMENTS_STORAGE_ROOT,
        backend.config.VOICE_PROFILES_PATH,
        backend.config.SHORT_MEMORY_PATH,
        backend.config.LONG_MEMORY_DB_PATH,
        backend.config.CANDIDATE_MEMORY_DB_PATH,
        backend.config.MEMORY_VECTORS_DB_PATH,
        backend.config.LOG_DIR,
    )
    for path in protected_objects:
        assert Path(path).is_relative_to(isolated_runtime)


def test_network_guard_blocks_loopback_and_http_clients():
    with pytest.raises(AssertionError, match="blocked"):
        socket.create_connection(("127.0.0.1", 8765))
    raw_socket = socket.socket()
    try:
        with pytest.raises(AssertionError, match="blocked"):
            raw_socket.connect(("::1", 8785))
    finally:
        raw_socket.close()
    with pytest.raises(AssertionError, match="blocked"):
        requests.get("http://localhost:11434/api/tags")
    with pytest.raises(AssertionError, match="blocked"):
        httpx.get("http://127.0.0.1:8000/api/health")
    with pytest.raises(AssertionError, match="blocked"):
        urllib.request.urlopen("http://localhost:8765/api/v1/health")
    with pytest.raises(AssertionError, match="blocked"):
        websockets.asyncio.client.connect("ws://127.0.0.1:8000/ws/trace")
    with pytest.raises(AssertionError, match="blocked"):
        subprocess.run(["forbidden-model-process"], check=False)
