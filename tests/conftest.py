from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient


def _blocked(kind: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"real {kind} is blocked in unit tests")
    return fail


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import config

    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "CONVERSATIONS_DB_PATH", tmp_path / "storage" / "conversations.sqlite3")
    monkeypatch.setattr(config, "ATTACHMENTS_STORAGE_ROOT", tmp_path / "storage" / "attachments")
    monkeypatch.setattr(config, "SETTINGS_STORE_PATH", tmp_path / "storage" / "settings.json")
    monkeypatch.setattr(config, "SHORT_MEMORY_PATH", tmp_path / "memory" / "short_memory.json")
    monkeypatch.setattr(config, "LONG_MEMORY_DB_PATH", tmp_path / "memory" / "long_memory.sqlite3")
    monkeypatch.setattr(config, "CANDIDATE_MEMORY_DB_PATH", tmp_path / "memory" / "candidate_memory.sqlite3")
    monkeypatch.setattr(config, "MEMORY_VECTORS_DB_PATH", tmp_path / "memory" / "memory_vectors.sqlite3")
    monkeypatch.setattr(config, "VOICE_PROFILES_PATH", tmp_path / "storage" / "voice_profiles.json")
    monkeypatch.setattr(config, "TTS_OUTPUT_DIR", tmp_path / "storage" / "tts")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(requests.sessions.Session, "request", _blocked("network"))
    monkeypatch.setattr(requests, "post", _blocked("network"))
    from api.app import create_app
    monkeypatch.setattr(subprocess, "Popen", _blocked("subprocess"))
    monkeypatch.setattr(subprocess, "run", _blocked("subprocess"))
    with TestClient(create_app()) as test_client:
        yield test_client
