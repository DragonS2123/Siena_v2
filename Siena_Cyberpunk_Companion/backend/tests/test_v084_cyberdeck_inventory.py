from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.priorities import EventPriority
from app.models.bridge import BridgeCapabilities, BridgeHello
from app.models.game_event import EventType, GameEvent
from app.models.game_state import DeepCyberdeckState, GameState
from app.services.event_synthesizer import EventSynthesizer
from app.services.scene_context import SceneContextBuilder
from app.services.state_diff import diff_states
from conftest import BASE


NOW = datetime(2026, 7, 16, 20, tzinfo=timezone.utc)
CAPS = BridgeCapabilities(cyberdeck_identity=True, cyberdeck_metadata=True, cyberdeck_programs=True, cyberdeck_capacity=True)


def program(slot: int, record: str | None = None) -> dict:
    return {
        "slot_id": f"AttachmentSlots.CyberdeckProgram{slot}",
        "record_id": record or f"Items.Program{slot}",
        "quality": "Legendary",
        "iconic": False,
    }


def deck_payload(*, programs: list[dict] | None = None, used: int | None = 1, empty: int | None = 7, total: int | None = 8, truncated: bool = False) -> dict:
    return {
        "record_id": "Items.AdvancedNetwatchNetdriverMKLegendary",
        "quality": "Legendary",
        "iconic": True,
        "tags": ["Cyberware", "Cyberdeck", "Iconic_OS_CW"],
        "program_capacity": {"used": used, "empty": empty, "total": total},
        "programs": programs if programs is not None else [program(1)],
        "truncated": truncated,
    }


def state_with_deck(cyberdeck: dict | None, sequence: int = 1) -> GameState:
    payload = deepcopy(BASE)
    payload.update({"source": "cet", "bridge_version": "0.8.4", "sequence": sequence, "captured_at": NOW.isoformat()})
    payload["deep_game_state"] = {
        "capabilities": {
            "player": True, "stats": True, "stat_pools": True, "weapon": True, "status_effects": True,
            "cyberdeck_identity": True, "cyberdeck_metadata": cyberdeck is not None,
            "cyberdeck_programs": cyberdeck is not None, "cyberdeck_capacity": cyberdeck is not None,
        },
        "player": None, "stats": None, "stat_pools": None, "weapon": None, "status_effects": None,
        "cyberdeck": cyberdeck,
    }
    return GameState.model_validate(payload)


def test_no_player_and_no_system_replacement_item_are_nullable():
    state = state_with_deck(None)
    assert state.deep_game_state is not None and state.deep_game_state.cyberdeck is None


def test_sandevistan_or_berserk_without_cyberdeck_tag_stays_null():
    # The production reader rejects the OS before serialization; the wire contract represents both as null.
    assert state_with_deck(None).deep_game_state.cyberdeck is None


def test_cyberdeck_detected_with_allowlisted_metadata():
    deck = state_with_deck(deck_payload()).deep_game_state.cyberdeck
    assert deck is not None
    assert deck.record_id == "Items.AdvancedNetwatchNetdriverMKLegendary"
    assert deck.tags == ["Cyberware", "Cyberdeck", "Iconic_OS_CW"]


def test_duplicate_active_and_area_deck_is_one_wire_block():
    state = state_with_deck(deck_payload())
    assert isinstance(state.deep_game_state.cyberdeck, DeepCyberdeckState)


def test_generic_item_root_is_rejected_as_program_slot():
    invalid = program(1)
    invalid["slot_id"] = "AttachmentSlots.GenericItemRoot"
    with pytest.raises(ValidationError):
        DeepCyberdeckState.model_validate(deck_payload(programs=[invalid]))


def test_one_program_and_empty_program_metadata_are_supported():
    one = DeepCyberdeckState.model_validate(deck_payload(programs=[program(1)]))
    empty = DeepCyberdeckState.model_validate(deck_payload(programs=[], used=0, empty=8, total=8))
    assert len(one.programs) == 1
    assert empty.programs == [] and empty.program_capacity.used == 0


def test_eight_programs_have_stable_slot_order():
    deck = DeepCyberdeckState.model_validate(deck_payload(programs=[program(i) for i in range(8, 0, -1)], used=8, empty=0, total=8))
    assert [item.slot_id[-1] for item in deck.programs] == list("12345678")


def test_duplicate_program_part_is_removed_by_slot_and_record_id():
    duplicate = program(2, "Items.PingLvl4Program")
    deck = DeepCyberdeckState.model_validate(deck_payload(programs=[duplicate, duplicate], used=1, empty=7, total=8))
    assert len(deck.programs) == 1


def test_missing_program_item_id_is_rejected_safely():
    invalid = program(1)
    invalid["record_id"] = None
    with pytest.raises(ValidationError):
        DeepCyberdeckState.model_validate(deck_payload(programs=[invalid]))


def test_full_and_mixed_capacity_collections():
    full = DeepCyberdeckState.model_validate(deck_payload(programs=[program(i) for i in range(1, 9)], used=8, empty=0, total=8))
    mixed = DeepCyberdeckState.model_validate(deck_payload(programs=[program(1)], used=1, empty=3, total=4))
    assert full.program_capacity.total == 8
    assert (mixed.program_capacity.used, mixed.program_capacity.empty, mixed.program_capacity.total) == (1, 3, 4)


def test_inconsistent_capacity_is_rejected_and_partial_capacity_is_nullable():
    with pytest.raises(ValidationError):
        DeepCyberdeckState.model_validate(deck_payload(used=2, empty=7, total=8))
    partial = DeepCyberdeckState.model_validate(deck_payload(used=1, empty=None, total=None))
    assert partial.program_capacity.used == 1 and partial.program_capacity.total is None
    unavailable = DeepCyberdeckState.model_validate(deck_payload(used=None, empty=None, total=None))
    assert len(unavailable.programs) == 1 and unavailable.program_capacity.total is None


def test_program_list_is_strictly_bounded_and_truncation_flag_survives():
    with pytest.raises(ValidationError):
        DeepCyberdeckState.model_validate(deck_payload(programs=[program((i % 8) + 1, f"Items.Program{i}") for i in range(9)]))
    assert DeepCyberdeckState.model_validate(deck_payload(truncated=True)).truncated is True


def test_old_v07_v081_and_v082_payloads_remain_compatible():
    assert GameState.model_validate(deepcopy(BASE)).deep_game_state is None
    for version in ("0.8.1", "0.8.2"):
        payload = state_with_deck(None).model_dump(mode="json")
        payload["bridge_version"] = version
        payload["deep_game_state"].pop("cyberdeck")
        for name in ("cyberdeck_identity", "cyberdeck_metadata", "cyberdeck_programs", "cyberdeck_capacity"):
            payload["deep_game_state"]["capabilities"].pop(name)
        assert GameState.model_validate(payload).deep_game_state.cyberdeck is None


def test_old_hello_defaults_v084_capabilities_false():
    hello = BridgeHello.model_validate({
        "bridge_id": "old", "bridge_version": "0.8.2", "protocol_version": "1.0",
        "transport": "red_http_client", "capabilities": {},
    })
    assert not hello.capabilities.cyberdeck_identity
    assert not hello.capabilities.cyberdeck_programs


def test_scene_context_projects_bounded_quickhack_ids_without_events():
    builder = SceneContextBuilder()
    builder.apply(GameEvent(
        session_id="test-session", event_type=EventType.SESSION_STARTED, priority=EventPriority.P2_MEDIUM,
        created_at=NOW, source="cet", summary="start", deduplication_key="start",
    ), CAPS)
    state = state_with_deck(deck_payload(programs=[program(i) for i in range(1, 9)], used=8, empty=0, total=8))
    assert builder.observe_state(state, CAPS)
    scene = builder.current()
    assert scene.cyberdeck_record_id == "Items.AdvancedNetwatchNetdriverMKLegendary"
    assert scene.cyberdeck_program_count == 8 and scene.cyberdeck_program_capacity == 8
    assert len(scene.installed_quickhack_record_ids) == 8
    events = EventSynthesizer().synthesize(None, state, diff_states(None, state), NOW, CAPS)
    assert all("cyberdeck" not in event.event_type.value and "quickhack" not in event.event_type.value for event in events)
