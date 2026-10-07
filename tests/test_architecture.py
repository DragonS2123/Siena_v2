from __future__ import annotations

import importlib
from pathlib import Path


def test_import_has_no_runtime_or_file_side_effects(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    module = importlib.import_module("api.app")
    assert not hasattr(module.app.state, "runtime")
    assert list(tmp_path.rglob("*")) == []


def test_routes_and_registry_are_core_only(client):
    paths = set(client.app.openapi()["paths"])
    assert "/api/chat" in paths
    assert "/api/models" in paths
    assert "/api/voice/status" in paths
    forbidden_fragments = ("game", "remote", "presence", "computer")
    assert not any(fragment in path.lower() for path in paths for fragment in forbidden_fragments)
    assert client.get("/api/tools").json()["tools"]
    assert [item["name"] for item in client.get("/api/tools").json()["tools"]] == [
        "short_memory_save", "short_memory_search", "short_memory_clear",
        "long_memory_save", "long_memory_search", "long_memory_list", "long_memory_deactivate",
        "candidate_memory_create", "delegate_model", "web_search", "web_read",
    ]


def test_source_tree_has_no_forbidden_imports():
    root = Path(__file__).parents[1]
    sources = [*root.glob("api/**/*.py"), *root.glob("core/**/*.py"), *root.glob("tools/**/*.py")]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in sources)
    for forbidden in ("cyber" + "punk", "nucle" + "ares", "remote_" + "gateway", "computer " + "awareness"):
        assert forbidden not in text
