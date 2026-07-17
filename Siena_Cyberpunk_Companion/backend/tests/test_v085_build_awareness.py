from copy import deepcopy
from datetime import datetime, timezone

from conftest import BASE
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.priorities import EventPriority
from app.models.bridge import BridgeCapabilities
from app.models.game_event import EventType, GameEvent
from app.models.game_state import GameState
from app.main import create_app
from app.services.build_awareness import BuildAwareness
from app.services.event_synthesizer import EventSynthesizer
from app.services.scene_context import SceneContextBuilder
from app.services.state_diff import diff_states


NOW = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
KNOWN = {
    "offensive": "Items.OverheatLvl4Program",
    "control": "Items.WeaponMalfunctionLvl4Program",
    "recon": "Items.PingLvl4Program",
}


def make_state(records: list[str] | None, *, deck: bool = True, capacity: int | None = 8, truncated: bool = False, ram: float | None = 33, available: bool = True, weapon: str = "Items.Preset_Sidewinder_Default") -> GameState:
    data = deepcopy(BASE)
    data.update({"source": "cet", "bridge_version": "0.8.4", "captured_at": NOW.isoformat()})
    programs = None if records is None else [
        {"slot_id": f"AttachmentSlots.CyberdeckProgram{index}", "record_id": record, "quality": None, "iconic": None}
        for index, record in enumerate(records, 1)
    ]
    used = len(records) if records is not None else None
    data["deep_game_state"] = {
        "capabilities": {"player": True, "stats": True, "stat_pools": True, "weapon": True, "status_effects": True, "cyberdeck_identity": True, "cyberdeck_metadata": True, "cyberdeck_programs": True, "cyberdeck_capacity": True},
        "player": {"entity_available": available, "session_available": available, "is_pre_game": False, "position": None, "mounted_vehicle": False},
        "stats": {"level": 60, "street_cred": 50, "armor": 610, "power_level": 60, "health": 551.26, "memory": ram},
        "stat_pools": {"current_health": 500, "maximum_health": 551.26, "current_memory": ram, "maximum_memory": ram},
        "weapon": {"drawn": True, "record_id": weapon, "source": "active"},
        "status_effects": {"observed_count": 0, "truncated": False, "limit": 32},
        "cyberdeck": ({"record_id": "Items.AdvancedNetwatchNetdriverMKLegendary", "quality": "Legendary", "iconic": True, "tags": ["Cyberdeck"], "program_capacity": {"used": used, "empty": capacity - used if capacity is not None and used is not None else None, "total": capacity}, "programs": programs, "truncated": truncated} if deck else None),
    }
    return GameState.model_validate(data)


def profile(state: GameState):
    return BuildAwareness().build(state)


def test_no_player_has_no_profile_and_no_cyberdeck_is_explicit():
    assert profile(make_state([], available=False)) is None
    result = profile(make_state([], deck=False))
    assert result.summary_key == "no_cyberdeck" and result.operating_system is None


def test_cyberdeck_without_programs_and_all_unknown_stay_unknown():
    missing = profile(make_state(None))
    unknown = profile(make_state(["Items.UncataloguedProgram"]))
    assert missing.summary_key == "cyberdeck_unknown" and missing.quickhack_loadout.known_programs is None
    assert unknown.summary_key == "cyberdeck_unknown"
    assert unknown.quickhack_loadout.unknown_programs == 1
    assert unknown.quickhack_loadout.categories.unknown == 1


def test_one_known_program_and_narrow_majority_rules():
    assert profile(make_state([KNOWN["offensive"]])).summary_key == "offensive_netrunner"
    assert profile(make_state([KNOWN["offensive"]] * 3 + [KNOWN["control"], KNOWN["recon"]])).summary_key == "offensive_netrunner"
    assert profile(make_state([KNOWN["control"]] * 3 + [KNOWN["offensive"]])).summary_key == "control_netrunner"
    assert profile(make_state([KNOWN["recon"]] * 3 + [KNOWN["control"]])).summary_key == "recon_netrunner"
    assert profile(make_state([KNOWN["offensive"], KNOWN["control"]])).summary_key == "mixed_netrunner"


def test_partial_and_live_confirmed_eight_program_loadout():
    partial = profile(make_state([KNOWN["offensive"], "Items.UnknownProgram"]))
    assert (partial.quickhack_loadout.known_programs, partial.quickhack_loadout.unknown_programs) == (1, 1)
    records = [
        "Items.GrenadeExplodeLvl4Program", "Items.CommsCallInLvl4Program", "Items.PingLvl4Program",
        "Items.ContagionLvl4Program", "Items.OverheatLvl4Program", "Items.BrainMeltLvl4Program",
        "Items.SuicideLvl4Program", "Items.WeaponMalfunctionLvl4Program",
    ]
    full = profile(make_state(records))
    assert full.summary_key == "offensive_netrunner"
    assert (full.quickhack_loadout.known_programs, full.quickhack_loadout.unknown_programs) == (7, 1)
    assert full.quickhack_loadout.categories.offensive == 5


def test_missing_inconsistent_and_truncated_capacity_reduce_confidence():
    complete = profile(make_state([KNOWN["offensive"]]))
    missing = profile(make_state([KNOWN["offensive"]], capacity=None))
    truncated = profile(make_state([KNOWN["offensive"]], truncated=True))
    inconsistent_state = make_state([KNOWN["offensive"]])
    inconsistent_state.deep_game_state.cyberdeck.program_capacity.used = 2
    inconsistent = profile(inconsistent_state)
    assert missing.confidence < complete.confidence
    assert truncated.confidence < complete.confidence
    assert inconsistent.confidence < complete.confidence
    assert "cyberdeck_capacity_inconsistent" in inconsistent.limitations


def test_missing_ram_deterministic_confidence_and_clamp():
    state = make_state([KNOWN["offensive"]], ram=None)
    first = profile(state)
    second = profile(state)
    assert first.confidence == second.confidence
    assert 0 <= first.confidence <= 1
    assert "maximum_ram_unavailable" in first.limitations
    assert BuildAwareness._clamp(-9) == 0 and BuildAwareness._clamp(9) == 1


def test_active_weapon_is_context_only_and_session_unload_clears_profile():
    first = make_state([KNOWN["offensive"]], weapon="Items.Preset_Katana_Default")
    awareness = BuildAwareness()
    awareness.enrich(first)
    assert first.deep_game_state.build_profile.summary_key == "offensive_netrunner"
    unloaded = make_state([], available=False)
    awareness.enrich(unloaded)
    assert unloaded.deep_game_state.build_profile is None


def test_old_payloads_validate_without_profile():
    assert GameState.model_validate(deepcopy(BASE)).deep_game_state is None
    old = make_state([]).model_dump(mode="json")
    old["deep_game_state"].pop("build_profile")
    assert GameState.model_validate(old).deep_game_state.build_profile is None


def test_existing_telemetry_endpoint_adds_optional_profile_to_same_state_transport():
    state = make_state([KNOWN["offensive"]]).model_copy(update={"source": "simulator", "bridge_version": None})
    with TestClient(create_app(Settings())) as client:
        assert client.post("/api/v1/telemetry/state", json=state.model_dump(mode="json")).status_code == 202
        latest = client.get("/api/v1/telemetry/latest").json()
    assert latest["deep_game_state"]["build_profile"]["summary_key"] == "offensive_netrunner"


def test_scene_projection_is_bounded_and_profile_change_emits_no_game_event():
    caps = BridgeCapabilities(cyberdeck_identity=True, cyberdeck_programs=True, cyberdeck_capacity=True)
    builder = SceneContextBuilder()
    builder.apply(GameEvent(session_id="test-session", event_type=EventType.SESSION_STARTED, priority=EventPriority.P2_MEDIUM, created_at=NOW, source="cet", summary="start", deduplication_key="start"), caps)
    state = BuildAwareness().enrich(make_state([KNOWN["offensive"]]))
    builder.observe_state(state, caps)
    scene = builder.current()
    assert scene.build_style == "offensive_netrunner"
    assert len(scene.build_evidence) <= 12 and len(scene.build_limitations) <= 12
    changed = BuildAwareness().enrich(make_state([KNOWN["control"]]))
    events = EventSynthesizer().synthesize(state, changed, diff_states(state, changed), NOW, caps)
    assert events == []
