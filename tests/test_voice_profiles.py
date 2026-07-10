"""Тесты VoiceProfileStore: сидинг дефолтов, CRUD, устойчивость к
повреждённому файлу, и что instruct/model_repo/speaker реально сохраняются
между "перезапусками" (новый экземпляр store на том же файле)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice.voice_profiles import (  # noqa: E402
    DEFAULT_ACTIVE_PROFILE_ID,
    VoiceProfile,
    VoiceProfileStore,
)


def test_lazily_creates_file_with_three_default_profiles(tmp_path):
    path = tmp_path / "voice_profiles.json"
    assert not path.exists()

    store = VoiceProfileStore(path)

    assert path.exists()
    profiles = store.list_profiles()
    assert {p.id for p in profiles} == {"siena_default_adult", "siena_soft_companion", "siena_clear_technical"}
    assert store.get_active_profile().id == DEFAULT_ACTIVE_PROFILE_ID


def test_active_profile_defaults_to_mature_adult_instruct(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    active = store.get_active_profile()
    assert "anime" in active.instruct.lower()
    assert "mature" in active.instruct.lower()


def test_set_active_profile_persists_across_new_store_instance(tmp_path):
    path = tmp_path / "voice_profiles.json"
    store = VoiceProfileStore(path)
    store.set_active_profile("siena_soft_companion")

    reloaded = VoiceProfileStore(path)
    assert reloaded.get_active_profile().id == "siena_soft_companion"


def test_set_active_profile_unknown_id_raises():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        store = VoiceProfileStore(Path(d) / "voice_profiles.json")
        try:
            store.set_active_profile("does_not_exist")
            assert False, "should have raised"
        except ValueError:
            pass


def test_update_profile_changes_instruct_and_persists(tmp_path):
    path = tmp_path / "voice_profiles.json"
    store = VoiceProfileStore(path)

    updated = store.update_profile("siena_default_adult", instruct="A totally different instruct.")
    assert updated.instruct == "A totally different instruct."

    reloaded = VoiceProfileStore(path)
    assert reloaded.get_profile("siena_default_adult").instruct == "A totally different instruct."


def test_update_profile_unknown_id_raises(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    try:
        store.update_profile("nope", instruct="x")
        assert False, "should have raised"
    except ValueError:
        pass


def test_update_profile_ignores_none_fields(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    before = store.get_profile("siena_default_adult")
    updated = store.update_profile("siena_default_adult", speaker=None, instruct="only this changes")
    assert updated.speaker == before.speaker
    assert updated.instruct == "only this changes"


def test_save_profile_creates_new_profile(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    profile = VoiceProfile(
        id="custom_test",
        name="Custom",
        provider="qwen3_tts",
        model_repo="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        language="Russian",
        speaker="Serena",
        instruct="Test instruct",
        created_at="",
        updated_at="",
    )
    saved = store.save_profile(profile)
    assert saved.created_at  # timestamp filled in
    assert store.get_profile("custom_test") is not None
    assert len(store.list_profiles()) == 4


def test_save_profile_updates_existing_and_keeps_created_at(tmp_path):
    store = VoiceProfileStore(tmp_path / "voice_profiles.json")
    original = store.get_profile("siena_default_adult")

    replacement = VoiceProfile(
        id="siena_default_adult",
        name="Renamed",
        provider="qwen3_tts",
        model_repo=original.model_repo,
        language=original.language,
        speaker=original.speaker,
        instruct="New instruct text",
        created_at="",
        updated_at="",
    )
    saved = store.save_profile(replacement)
    assert saved.created_at == original.created_at  # preserved, not overwritten
    assert saved.instruct == "New instruct text"
    assert len(store.list_profiles()) == 3  # upsert, not append


def test_corrupted_file_self_heals_to_defaults(tmp_path):
    path = tmp_path / "voice_profiles.json"
    path.write_text("{ not valid json", encoding="utf-8")

    store = VoiceProfileStore.__new__(VoiceProfileStore)
    store._path = path
    store._logger = None
    # __init__ would try path.exists() (True here) so skip lazy-create and
    # go straight through _read()'s corruption-recovery path:
    active_id, profiles = store._read()

    assert active_id == DEFAULT_ACTIVE_PROFILE_ID
    assert len(profiles) == 3
    # and the file on disk was actually repaired:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["active_profile_id"] == DEFAULT_ACTIVE_PROFILE_ID
