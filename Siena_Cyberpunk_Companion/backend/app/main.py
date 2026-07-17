import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import bridge, commands, events, health, presence, reactions, scenes, telemetry, voice, websocket
from app.config import Settings, get_settings
from app.services.decision_scheduler import DecisionScheduler
from app.services.bridge_registry import BridgeRegistry
from app.services.event_bus import EventBus
from app.services.event_filter import EventFilter
from app.services.event_synthesizer import EventSynthesizer
from app.services.reaction_planner import ReactionPlanner, create_reaction_provider
from app.services.reaction_dispatch import ReactionDispatchService
from app.services.companion_behavior_policy import CompanionBehaviorPolicy
from app.services.scene_context import SceneContextBuilder
from app.services.siena_core_reaction_provider import SienaCoreReactionProvider
from app.services.siena_core_client import DisabledSienaCoreClient
from app.services.state_diff import diff_states
from app.services.state_store import StateStore
from app.services.siena_tts_client import SienaTtsClient
from app.services.voice_behavior_policy import VoiceBehaviorPolicy
from app.services.voice_dispatch import VoiceDispatchService
from app.services.in_game_presence import InGamePresencePolicy, InGamePresenceProjection

logger = logging.getLogger("siena_observer")


@dataclass(slots=True)
class Services:
    settings: Settings
    store: StateStore
    bus: EventBus
    synthesizer: EventSynthesizer
    filter: EventFilter
    scheduler: DecisionScheduler
    reaction_planner: ReactionPlanner
    reaction_dispatch: ReactionDispatchService
    scene_builder: SceneContextBuilder
    behavior_policy: CompanionBehaviorPolicy
    voice_policy: VoiceBehaviorPolicy
    voice_dispatch: VoiceDispatchService
    presence: InGamePresenceProjection
    core_client: DisabledSienaCoreClient
    bridge_registry: BridgeRegistry
    diff: Callable = diff_states

    async def publish_event(self, event, capabilities=None):
        await self.bus.publish("event", event)
        await self.bus.publish("game_event", event)
        if self.settings.scene_enabled:
            scene_update = self.scene_builder.apply(event, capabilities)
            opportunity = self.behavior_policy.evaluate(scene_update)
            reaction = (
                await self.reaction_dispatch.handle_opportunity(opportunity)
                if opportunity
                else None
            )
            if not opportunity:
                await self.reaction_dispatch.observe_event(event)
        else:
            reaction = await self.reaction_dispatch.handle_event(event)
        await self.voice_dispatch.observe_event(event)
        if reaction:
            await self.bus.publish("siena_reaction", reaction)
            await self.voice_dispatch.handle_reaction(reaction)
        if self.settings.scene_enabled and scene_update.significant and scene_update.scene:
            await self.bus.publish("scene_context_updated", scene_update.scene)
        command = await self.scheduler.consider(event)
        if command:
            await self.bus.publish("command", command)
        return reaction, command

    def state_payload(self, state):
        data = state.model_dump(mode="json")
        data["derived"] = {"companion_stuck": self.synthesizer.is_stuck(state.session_id)}
        return data

    async def status_payload(self) -> dict:
        bridge_status = await self.bridge_registry.status()
        await self.store.activate(bridge_status.active_source)
        latest = await self.store.latest()
        scene = self.scene_builder.current()
        return {
            "backend": "online",
            "active_session": latest.session_id if latest else None,
            "last_packet_at": latest.captured_at.isoformat() if latest else None,
            "scheduler": await self.scheduler.status(),
            "planner": (await self.reaction_planner.status()).model_dump(mode="json"),
            "reaction_provider": (await self.reaction_dispatch.status()).model_dump(mode="json"),
            "scene": scene.model_dump(mode="json") if scene else None,
            "voice": (await self.voice_dispatch.status()).model_dump(mode="json"),
            "presence": self.presence.status().model_dump(mode="json"),
            "active_source": bridge_status.active_source,
            "bridge": bridge_status.model_dump(mode="json"),
        }


def create_services(settings: Settings | None = None) -> Services:
    config = settings or get_settings()
    bus = EventBus(config.event_buffer_size, config.websocket_queue_size, config.reaction_buffer_size)
    planner = ReactionPlanner(
        create_reaction_provider(config.reaction_provider),
        config.reactions_enabled,
        config.general_reaction_cooldown_seconds,
        config.same_event_cooldown_seconds,
    )
    core_provider = SienaCoreReactionProvider(
        enabled=config.siena_core_enabled,
        base_url=config.siena_core_base_url,
        api_token=config.siena_core_api_token,
        connect_timeout_seconds=config.siena_core_connect_timeout_seconds,
        request_timeout_seconds=config.siena_core_request_timeout_seconds,
        max_retries=config.siena_core_max_retries,
        max_response_chars=config.siena_core_max_response_chars,
        failure_threshold=config.siena_core_circuit_failure_threshold,
        circuit_reset_seconds=config.siena_core_circuit_reset_seconds,
        player_name=config.siena_core_player_name,
    )
    scene_builder = SceneContextBuilder(
        enabled=config.scene_enabled,
        event_history_limit=config.scene_event_history_limit,
        scene_history_limit=config.scene_history_limit,
        idle_gap_seconds=config.scene_idle_gap_seconds,
        max_duration_seconds=config.scene_max_duration_seconds,
        recent_event_window_seconds=config.scene_recent_event_window_seconds,
    )
    behavior_policy = CompanionBehaviorPolicy(
        max_reactions=config.scene_max_reactions,
        min_reaction_interval_seconds=config.scene_min_reaction_interval_seconds,
        critical_bypass=config.scene_critical_bypass,
        resolution_enabled=config.scene_resolution_reaction_enabled,
        opportunity_ttl_seconds=config.scene_recent_event_window_seconds,
    )
    dispatch = ReactionDispatchService(
        configured_provider=config.reaction_provider,
        planner=planner,
        bus=bus,
        core_provider=core_provider,
        fallback_provider=config.siena_core_fallback_provider,
        queue_capacity=config.siena_core_queue_size,
        recent_events_limit=config.siena_core_recent_events_limit,
        recent_reactions_limit=config.scene_recent_reactions_limit,
        max_event_age_seconds=config.siena_core_max_event_age_seconds,
        language=config.siena_core_language,
        scene_builder=scene_builder,
        scene_stale_grace_seconds=config.scene_reaction_stale_grace_seconds,
    )
    voice_policy = VoiceBehaviorPolicy(
        enabled=config.voice_enabled,
        muted=config.voice_muted,
        min_interval_seconds=config.voice_min_interval_seconds,
        same_text_cooldown_seconds=config.voice_same_text_cooldown_seconds,
        scene_max_clips=config.voice_scene_max_clips,
        critical_bypass=config.voice_critical_bypass,
        max_text_chars=config.voice_max_text_chars,
        max_event_age_seconds=config.voice_max_event_age_seconds,
        audio_ttl_seconds=config.voice_audio_ttl_seconds,
        language=config.voice_language,
        speaker=config.voice_speaker,
        require_tts_ready=config.voice_require_tts_ready,
    )
    tts_client = SienaTtsClient(
        base_url=config.tts_base_url,
        api_token=config.tts_api_token,
        connect_timeout_seconds=config.tts_connect_timeout_seconds,
        request_timeout_seconds=config.tts_request_timeout_seconds,
        max_retries=config.tts_max_retries,
        max_audio_bytes=config.tts_max_audio_bytes,
        failure_threshold=config.tts_circuit_failure_threshold,
        circuit_reset_seconds=config.tts_circuit_reset_seconds,
    )
    voice_dispatch = VoiceDispatchService(
        enabled=config.voice_enabled,
        muted=config.voice_muted,
        policy=voice_policy,
        bus=bus,
        tts_client=tts_client,
        scene_builder=scene_builder,
        queue_capacity=config.voice_queue_size,
        clip_history_limit=config.voice_clip_history_limit,
        audio_ttl_seconds=config.voice_audio_ttl_seconds,
        max_event_age_seconds=config.voice_max_event_age_seconds,
        language=config.voice_language,
        speaker=config.voice_speaker,
        interrupt_mode=config.voice_interrupt_mode,
        post_play_gap_ms=config.voice_post_play_gap_ms,
    )
    presence_policy = InGamePresencePolicy(
        generating_delay_ms=config.presence_generating_delay_ms,
        normal_duration_ms=config.presence_normal_duration_ms,
        high_duration_ms=config.presence_high_duration_ms,
        critical_duration_ms=config.presence_critical_duration_ms,
        fallback_duration_ms=config.presence_fallback_duration_ms,
        max_text_chars=config.presence_max_text_chars,
    )
    presence_projection = InGamePresenceProjection(
        enabled=config.presence_enabled,
        policy=presence_policy,
        poll_interval_ms=config.presence_poll_interval_ms,
    )
    bus.subscribe(presence_projection.observe)
    dispatch.on_reaction_published = voice_dispatch.handle_reaction
    return Services(
        settings=config,
        store=StateStore(),
        bus=bus,
        synthesizer=EventSynthesizer(
            config.companion_too_far_meters,
            config.companion_stuck_seconds,
            config.companion_movement_epsilon,
            config.health_low_threshold_percent,
            config.health_low_recovery_percent,
            config.health_critical_threshold_percent,
            config.heal_threshold_percent,
            config.idle_timeout_seconds,
            config.player_position_epsilon,
            config.session_disconnect_timeout_seconds,
        ),
        filter=EventFilter(config.damage_window_seconds, config.enemy_debounce_seconds, config.same_event_cooldown_seconds),
        scheduler=DecisionScheduler(),
        reaction_planner=planner,
        reaction_dispatch=dispatch,
        scene_builder=scene_builder,
        behavior_policy=behavior_policy,
        voice_policy=voice_policy,
        voice_dispatch=voice_dispatch,
        presence=presence_projection,
        core_client=DisabledSienaCoreClient(),
        bridge_registry=BridgeRegistry(config.protocol_version, config.bridge_timeout_seconds, config.bridge_registry_size, config.telemetry_source),
    )


async def maintain_pipeline(services: Services) -> None:
    while True:
        await asyncio.sleep(min(services.settings.damage_window_seconds / 2, 0.25))
        for event in [*services.filter.flush_due(), *services.synthesizer.expire_sessions()]:
            await services.publish_event(event)
        if services.presence.tick():
            await services.bus.publish("in_game_presence_status", services.presence.status())


def create_app(settings: Settings | None = None) -> FastAPI:
    services = create_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await services.reaction_dispatch.start()
        await services.voice_dispatch.start()
        task = asyncio.create_task(maintain_pipeline(services), name="event-pipeline-maintenance")
        logger.info("observer backend started on %s:%s", services.settings.host, services.settings.port)
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await services.reaction_dispatch.stop()
            await services.voice_dispatch.stop()

    app = FastAPI(title="Siena Cyberpunk Companion", version="0.8.4", lifespan=lifespan)
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=services.settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(bridge.router)
    app.include_router(telemetry.router)
    app.include_router(events.router)
    app.include_router(reactions.router)
    app.include_router(scenes.router)
    app.include_router(voice.router)
    app.include_router(presence.router)
    app.include_router(commands.router)
    app.include_router(websocket.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
