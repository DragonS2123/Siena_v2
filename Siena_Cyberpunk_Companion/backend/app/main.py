import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import bridge, commands, events, health, reactions, telemetry, websocket
from app.config import Settings, get_settings
from app.services.decision_scheduler import DecisionScheduler
from app.services.bridge_registry import BridgeRegistry
from app.services.event_bus import EventBus
from app.services.event_filter import EventFilter
from app.services.event_synthesizer import EventSynthesizer
from app.services.reaction_planner import ReactionPlanner, create_reaction_provider
from app.services.siena_core_client import DisabledSienaCoreClient
from app.services.state_diff import diff_states
from app.services.state_store import StateStore

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
    core_client: DisabledSienaCoreClient
    bridge_registry: BridgeRegistry
    diff: Callable = diff_states

    async def publish_event(self, event):
        await self.bus.publish("event", event)
        await self.bus.publish("game_event", event)
        reaction = await self.reaction_planner.consider(event)
        if reaction:
            await self.bus.publish("siena_reaction", reaction)
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
        return {
            "backend": "online",
            "active_session": latest.session_id if latest else None,
            "last_packet_at": latest.captured_at.isoformat() if latest else None,
            "scheduler": await self.scheduler.status(),
            "planner": (await self.reaction_planner.status()).model_dump(mode="json"),
            "active_source": bridge_status.active_source,
            "bridge": bridge_status.model_dump(mode="json"),
        }


def create_services(settings: Settings | None = None) -> Services:
    config = settings or get_settings()
    return Services(
        settings=config,
        store=StateStore(),
        bus=EventBus(config.event_buffer_size, config.websocket_queue_size, config.reaction_buffer_size),
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
        reaction_planner=ReactionPlanner(
            create_reaction_provider(config.reaction_provider),
            config.reactions_enabled,
            config.general_reaction_cooldown_seconds,
            config.same_event_cooldown_seconds,
        ),
        core_client=DisabledSienaCoreClient(),
        bridge_registry=BridgeRegistry(config.protocol_version, config.bridge_timeout_seconds, config.bridge_registry_size, config.telemetry_source),
    )


async def maintain_pipeline(services: Services) -> None:
    while True:
        await asyncio.sleep(min(services.settings.damage_window_seconds / 2, 0.25))
        for event in [*services.filter.flush_due(), *services.synthesizer.expire_sessions()]:
            await services.publish_event(event)


def create_app(settings: Settings | None = None) -> FastAPI:
    services = create_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(maintain_pipeline(services), name="event-pipeline-maintenance")
        logger.info("observer backend started on %s:%s", services.settings.host, services.settings.port)
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Siena Cyberpunk Companion", version="0.3.0", lifespan=lifespan)
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
    app.include_router(commands.router)
    app.include_router(websocket.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
