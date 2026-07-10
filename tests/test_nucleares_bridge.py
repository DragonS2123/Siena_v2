from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import api.server as server  # noqa: E402
from game import nucleares_bridge as nb  # noqa: E402


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200, content_type: str = "text/html"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise nb.requests.HTTPError(f"status {self.status_code}")


def test_unavailable_webserver_returns_connected_false(monkeypatch):
    def fake_get(*args, **kwargs):
        raise nb.requests.ConnectionError("connection refused")

    monkeypatch.setattr(nb.requests, "get", fake_get)
    result = nb.NuclearesBridgeClient(hosts=["localhost"], ports=[8785]).status()

    assert result["connected"] is False
    assert result["game"] == "nucleares"
    assert "connection refused" in result["attempted"][0]["error"]


def test_root_html_variable_parsing_filters_varname():
    html = """
    <a href="/?variable=VARNAME">placeholder</a>
    <a href="/?variable=AMBIENT_TEMPERATURE">ambient</a>
    <a href="/?variable=AMBIENT_TEMPERATURE">ambient duplicate</a>
    <a href="/?variable=CONDENSER_PRESSURE">pressure</a>
    """

    assert nb.discover_variables(html) == ["AMBIENT_TEMPERATURE", "CONDENSER_PRESSURE"]


def test_variable_read_returns_raw_text_values(monkeypatch):
    def fake_get(url, **kwargs):
        assert url == "http://localhost:8785/?variable=AMBIENT_TEMPERATURE"
        return _FakeResponse(" 20\n", content_type="text/plain")

    monkeypatch.setattr(nb.requests, "get", fake_get)
    client = nb.NuclearesBridgeClient()

    assert client.read_variable("http://localhost:8785", "AMBIENT_TEMPERATURE") == "20"


def test_normalized_subset_includes_present_keys_only(monkeypatch):
    html = """
    <a href="/?variable=VARNAME"></a>
    <a href="/?variable=AMBIENT_TEMPERATURE"></a>
    <a href="/?variable=CONDENSER_TEMPERATURE"></a>
    <a href="/?variable=STEAM_GENERATOR_2_PRESSURE"></a>
    <a href="/?variable=UNRELATED_VALUE"></a>
    """
    values = {
        "AMBIENT_TEMPERATURE": "21",
        "CONDENSER_TEMPERATURE": "20",
        "STEAM_GENERATOR_2_PRESSURE": "42",
        "UNRELATED_VALUE": "ignored sample",
    }

    def fake_get(url, **kwargs):
        if url == "http://localhost:8785/":
            return _FakeResponse(html)
        key = url.rsplit("variable=", 1)[1]
        return _FakeResponse(values[key], content_type="text/plain")

    monkeypatch.setattr(nb.requests, "get", fake_get)
    result = nb.NuclearesBridgeClient(hosts=["localhost"], ports=[8785]).status()

    assert result["connected"] is True
    assert result["parameter_count"] == 4
    assert set(result["normalized"]) == {
        "ambient_temperature",
        "condenser_temperature",
        "steam_generator_2_pressure",
    }
    assert result["normalized"]["ambient_temperature"] == {"value": "21", "raw_key": "AMBIENT_TEMPERATURE"}
    assert "unrelated_value" not in result["normalized"]


def test_endpoint_shape_is_stable(monkeypatch):
    class _Client:
        def status(self):
            return {
                "game": "nucleares",
                "connected": True,
                "base_url": "http://localhost:8785",
                "timestamp": "2026-07-09T00:00:00+00:00",
                "parameter_count": 3,
                "normalized": {
                    "ambient_temperature": {"value": "21", "raw_key": "AMBIENT_TEMPERATURE"},
                },
                "raw_sample": {"ALARMS_ACTIVE": ""},
                "warnings": [],
            }

    monkeypatch.setattr(server, "nucleares_client", _Client())
    response = TestClient(server.app).get("/api/game/nucleares/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["game"] == "nucleares"
    assert payload["connected"] is True
    assert payload["base_url"] == "http://localhost:8785"
    assert payload["parameter_count"] == 3
    assert payload["normalized"]["ambient_temperature"]["raw_key"] == "AMBIENT_TEMPERATURE"
    assert payload["warnings"] == []
