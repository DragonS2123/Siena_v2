from __future__ import annotations

from core.model_catalog import ModelCatalog


class Response:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self._payload


class Session:
    def get(self, url, timeout):
        if url.endswith("/api/tags"):
            return Response({"models": [{"name": "qwen:7b", "size": 123, "details": {"family": "qwen", "parameter_size": "7B", "quantization_level": "Q4"}}]})
        return Response({"models": [{"name": "qwen:7b"}]})


def test_catalog_normalizes_installed_and_loaded_models():
    result = ModelCatalog("http://ollama", session=Session()).refresh()
    assert result["available"] is True
    assert result["models"][0] == {
        "name": "qwen:7b", "tag": "7b", "family": "qwen", "parameter_size": "7B",
        "quantization": "Q4", "size": 123, "modified_at": None, "digest": None,
        "capabilities": [], "loaded": True,
    }


def test_catalog_reports_unavailable():
    class Offline:
        def get(self, *_args, **_kwargs):
            raise OSError("offline")
    result = ModelCatalog("http://ollama", session=Offline()).refresh()
    assert result["available"] is False and result["models"] == []


def test_role_assignment_and_missing_state(client, monkeypatch):
    runtime = client.app.state.runtime
    catalog = {"available": True, "models": [{"name": "installed:1"}]}
    monkeypatch.setattr(runtime.catalog, "refresh", lambda: catalog)
    response = client.put("/api/models/roles/chat", json={"model": "installed:1"})
    assert response.status_code == 200
    assert runtime.roles.assignments()["chat"] == "installed:1"
    catalog["models"] = []
    assert next(item for item in runtime.roles.describe(catalog) if item["role"] == "chat")["missing"] is True
    assert client.put("/api/models/roles/chat", json={"model": "absent:1"}).status_code == 422
