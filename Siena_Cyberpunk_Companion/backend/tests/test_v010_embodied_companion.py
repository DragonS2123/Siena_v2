from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models.embodiment import EmbodimentSettings, EmbodimentState
from app.models.npc import NpcCommandName, NpcCommandOrigin, NpcCommandResult, NpcStatus
from app.models.voice import VoiceState
from app.services.embodied_companion import EmbodiedCompanion
from app.services.npc_command_queue import NpcCommandQueue, NpcQueueFullError


def make_service(settings=None, capacity=16):
    queue=NpcCommandQueue(enabled=True,capacity=capacity,default_expiry_seconds=10)
    return EmbodiedCompanion(queue,settings),queue


@pytest.mark.asyncio
async def test_player_system_and_speech_origins_are_distinct_and_llm_is_not_an_api_origin():
    service,queue=make_service(EmbodimentSettings(npc_presence_enabled=True))
    await service.player_command(NpcCommandName.STAY)
    item=await queue.claim_next(); assert item.origin is NpcCommandOrigin.PLAYER
    with TestClient(create_app(Settings(npc_controller_enabled=True))) as client:
        rejected=client.post('/api/v1/npc/commands',json={'command':'speech_start','origin':'llm'})
    assert rejected.status_code==422


@pytest.mark.asyncio
async def test_auto_spawn_waits_for_stable_session_and_is_duplicate_safe(state_factory):
    service,queue=make_service(EmbodimentSettings(npc_presence_enabled=True,npc_auto_spawn=True,npc_spawn_delay_seconds=3))
    first=state_factory(); first.captured_at=datetime(2026,7,17,12,0,tzinfo=timezone.utc)
    await service.observe_state(first); assert await queue.queue_size()==0
    later=first.model_copy(update={'sequence':2,'captured_at':first.captured_at+timedelta(seconds=4)})
    await service.observe_state(later); await service.observe_state(later.model_copy(update={'sequence':3}))
    assert await queue.queue_size()==1 and (await queue.claim_next()).command is NpcCommandName.SPAWN


@pytest.mark.asyncio
async def test_vehicle_and_combat_suspension_restore_previous_mode(state_factory):
    service,queue=make_service(EmbodimentSettings(npc_presence_enabled=True))
    await service.observe_state(state_factory(player__in_vehicle=True)); assert (await queue.claim_next()).command is NpcCommandName.SUSPEND
    await service.observe_state(state_factory(sequence=2,player__in_vehicle=False)); resume=await queue.claim_next(); assert resume.command is NpcCommandName.RESUME and resume.payload['mode']=='follow'
    await service.observe_state(state_factory(sequence=3,player__in_combat=True)); assert (await queue.claim_next()).payload['reason']=='combat'


@pytest.mark.asyncio
async def test_speech_lifecycle_is_bounded_sanitized_and_restores():
    service,queue=make_service(EmbodimentSettings(npc_presence_enabled=True))
    clip=SimpleNamespace(voice_request_id='u1',metadata={'text_preview':'**Hello** <script>'},priority=SimpleNamespace(value='high'),duration_ms=99999)
    await service.speech_event(clip,VoiceState.PLAYING); start=await queue.claim_next()
    assert start.command is NpcCommandName.SPEECH_START and start.origin is NpcCommandOrigin.SPEECH
    assert '<' not in start.payload['subtitle'] and start.payload['duration_ms']==30000
    second=SimpleNamespace(voice_request_id='u2',metadata={'text_preview':'overlap'},priority=SimpleNamespace(value='low'),duration_ms=1000)
    await service.speech_event(second,VoiceState.PLAYING); assert await queue.claim_next() is None
    await service.speech_event(clip,VoiceState.FAILED); assert (await queue.claim_next()).command is NpcCommandName.SPEECH_END
    assert service.active_utterance_id is None


@pytest.mark.asyncio
async def test_suspension_suppresses_low_body_action_but_allows_critical_warning(state_factory):
    service,queue=make_service(EmbodimentSettings(npc_presence_enabled=True))
    await service.observe_state(state_factory(player__in_combat=True)); await queue.claim_next()
    low=SimpleNamespace(voice_request_id='low',metadata={'text_preview':'low'},priority=SimpleNamespace(value='low'),duration_ms=1000)
    await service.speech_event(low,VoiceState.PLAYING); assert await queue.claim_next() is None
    critical=SimpleNamespace(voice_request_id='critical',metadata={'text_preview':'critical'},priority=SimpleNamespace(value='critical'),duration_ms=1000)
    await service.speech_event(critical,VoiceState.PLAYING); assert (await queue.claim_next()).command is NpcCommandName.SPEECH_START


@pytest.mark.asyncio
async def test_last_result_and_completion_timestamp_propagate_from_game_ack():
    _,queue=make_service(); item,_=await queue.enqueue(NpcCommandName.STATUS); await queue.claim_next()
    await queue.acknowledge(item.command_id,NpcCommandResult(result='success',status=NpcStatus(enabled=True,lifecycle_state='idle')))
    status=await queue.status(); assert status.last_command is NpcCommandName.STATUS and status.last_command_result=='success' and status.last_command_completed_at is not None


def test_settings_api_is_validated_and_v091_command_shape_stays_compatible():
    with TestClient(create_app(Settings(npc_controller_enabled=True))) as client:
        old=client.post('/api/v1/npc/commands',json={'command':'follow'})
        settings=client.get('/api/v1/npc/settings').json(); settings['npc_presence_enabled']=True
        updated=client.put('/api/v1/npc/settings',json=settings)
        invalid=client.put('/api/v1/npc/settings',json={**settings,'npc_follow_distance':999})
    assert old.status_code==201 and old.json()['command']=='follow'
    assert updated.status_code==200 and updated.json()['npc_presence_enabled'] is True
    assert invalid.status_code==422
