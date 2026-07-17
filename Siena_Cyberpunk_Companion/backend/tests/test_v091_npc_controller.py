from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.npc import NpcCommandName, NpcCommandResult, NpcStatus
from app.services.npc_command_queue import NpcCommandQueue, NpcQueueFullError, NpcUnknownClaimError


class Clock:
    def __init__(self): self.now = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
    def __call__(self): return self.now
    def advance(self, seconds: float): self.now += timedelta(seconds=seconds)


def queue(*, capacity=16, clock=None):
    return NpcCommandQueue(enabled=True, capacity=capacity, default_expiry_seconds=10, clock=clock)


@pytest.mark.asyncio
async def test_allowed_command_accepted_and_unknown_enum_rejected_by_api():
    with TestClient(create_app(Settings(npc_controller_enabled=True))) as client:
        accepted = client.post("/api/v1/npc/commands", json={"command": "spawn"})
        rejected = client.post("/api/v1/npc/commands", json={"command": "arbitrary_method", "record_id": "Player"})
    assert accepted.status_code == 201
    assert accepted.json()["command"] == "spawn"
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_queue_is_bounded():
    item = queue(capacity=1)
    await item.enqueue(NpcCommandName.SPAWN)
    with pytest.raises(NpcQueueFullError): await item.enqueue(NpcCommandName.STATUS)


@pytest.mark.asyncio
async def test_expired_command_is_discarded():
    clock = Clock(); item = queue(clock=clock)
    await item.enqueue(NpcCommandName.STATUS)
    clock.advance(6)
    assert await item.claim_next() is None


@pytest.mark.asyncio
async def test_command_is_claimed_once_and_result_acknowledged_once():
    item = queue(); created, _ = await item.enqueue(NpcCommandName.FOLLOW)
    assert (await item.claim_next()).command_id == created.command_id
    assert await item.claim_next() is None
    await item.acknowledge(created.command_id, NpcCommandResult(result="success"))
    with pytest.raises(NpcUnknownClaimError): await item.acknowledge(created.command_id, NpcCommandResult(result="success"))


@pytest.mark.asyncio
async def test_duplicate_follow_is_coalesced():
    item = queue(); first, first_coalesced = await item.enqueue(NpcCommandName.FOLLOW)
    second, second_coalesced = await item.enqueue(NpcCommandName.FOLLOW)
    assert first.command_id == second.command_id
    assert first_coalesced is False and second_coalesced is True
    assert await item.queue_size() == 1


@pytest.mark.asyncio
async def test_despawn_replaces_queued_lower_priority_commands():
    item = queue()
    await item.enqueue(NpcCommandName.FOLLOW); await item.enqueue(NpcCommandName.LOOK_AT_PLAYER)
    despawn, _ = await item.enqueue(NpcCommandName.DESPAWN)
    assert await item.queue_size() == 1
    assert (await item.claim_next()).command_id == despawn.command_id


@pytest.mark.asyncio
async def test_status_fields_are_nullable_and_runtime_userdata_is_not_modeled():
    value = NpcStatus(enabled=True)
    payload = value.model_dump(mode="json")
    assert payload["resolved"] is None and payload["runtime_class"] is None
    assert payload["current_appearance"] is None and payload["temporary_appearance"] is False
    assert "entity_id" not in payload and "pointer" not in payload


def test_old_api_compatibility_is_additive():
    with TestClient(create_app(Settings())) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/commands/current").status_code == 200
        status = client.get("/api/v1/status")
    assert status.status_code == 200 and "npc" in status.json()


def test_controller_disabled_rejects_manual_enqueue_but_status_remains_available():
    with TestClient(create_app(Settings(npc_controller_enabled=False))) as client:
        assert client.post("/api/v1/npc/commands", json={"command": "spawn"}).status_code == 503
        status = client.get("/api/v1/npc/status")
    assert status.status_code == 200 and status.json()["enabled"] is False
