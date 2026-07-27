def test_status_endpoints_are_controlled_when_dependencies_are_unavailable(client):
    assert client.get("/api/models").json()["available"] is False
    assert client.get("/api/ocr/status").status_code == 200
    assert client.get("/api/vision/status").status_code == 200
    assert client.get("/api/voice/status").status_code == 200
    assert client.get("/api/diagnostics").status_code == 200


def test_settings_ignore_removed_fields_during_migration(tmp_path):
    from storage.settings_store import SettingsStore
    path = tmp_path / "settings.json"
    path.write_text('{"model_roles":{"chat":"x"},"obsolete_feature":true}', encoding="utf-8")
    store = SettingsStore(path)
    assert store.migrate() == {"model_roles": {"chat": "x"}}
    assert path.with_suffix(".pre-core-cleanup.bak").exists()
    assert "obsolete_feature" not in path.read_text(encoding="utf-8")


def test_settings_translate_legacy_model_choices(tmp_path):
    from storage.settings_store import SettingsStore
    path = tmp_path / "settings.json"
    path.write_text('{"primary_model":"chat-local","code_model":"code-local"}', encoding="utf-8")
    roles = SettingsStore(path).migrate()["model_roles"]
    assert roles["chat"] == roles["memory"] == "chat-local"
    assert roles["coder"] == "code-local"


def test_trace_and_logs_are_bounded(client):
    for index in range(3):
        client.post("/api/trace/client-event", json={"type": "click", "detail": str(index)})
    assert len(client.get("/api/trace/recent?limit=2").json()["events"]) == 2
    assert client.get("/api/logs/recent").status_code == 200
