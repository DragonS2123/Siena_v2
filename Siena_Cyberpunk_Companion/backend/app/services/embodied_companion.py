import re
from datetime import datetime, timezone

from app.models.embodiment import EmbodimentSettings, EmbodimentState
from app.models.game_state import GameState
from app.models.npc import NpcCommandName, NpcCommandOrigin, NpcStatus
from app.models.voice import VoiceClip, VoiceState
from app.services.npc_command_queue import NpcCommandQueue


_PLAIN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f<>`*_#\[\]{}]")


class EmbodiedCompanion:
    """Deterministic arbiter; CET remains the sole owner of body API calls."""

    def __init__(self, queue: NpcCommandQueue, settings: EmbodimentSettings | None = None) -> None:
        self.queue = queue
        self.settings = settings or EmbodimentSettings()
        self.state = EmbodimentState.DISABLED if not self.settings.npc_presence_enabled else EmbodimentState.ABSENT
        self.requested_mode = "follow" if self.settings.npc_auto_follow else "stay"
        self.previous_mode = self.requested_mode
        self.session_id: str | None = None
        self.session_ready_at: datetime | None = None
        self.spawn_requested_session: str | None = None
        self.suspended_reason: str | None = None
        self.active_utterance_id: str | None = None

    def _transition(self, state: EmbodimentState) -> None:
        self.state = state

    async def player_command(self, command: NpcCommandName):
        if command in {NpcCommandName.SPAWN, NpcCommandName.FOLLOW, NpcCommandName.STAY, NpcCommandName.COME_HERE}:
            self.requested_mode = {NpcCommandName.SPAWN: "idle", NpcCommandName.FOLLOW: "follow", NpcCommandName.STAY: "stay", NpcCommandName.COME_HERE: "return"}[command]
        if command is NpcCommandName.DESPAWN:
            self._transition(EmbodimentState.CLEANING_UP)
        return await self.queue.enqueue(command, origin=NpcCommandOrigin.PLAYER)

    async def update_settings(self, value: EmbodimentSettings) -> EmbodimentSettings:
        previous = self.settings
        self.settings = value.model_copy(deep=True)
        if not value.npc_presence_enabled:
            self._transition(EmbodimentState.DISABLED)
            if previous.npc_presence_enabled:
                await self.queue.enqueue(NpcCommandName.DESPAWN, origin=NpcCommandOrigin.SYSTEM)
        elif self.state is EmbodimentState.DISABLED:
            self._transition(EmbodimentState.ABSENT)
        await self.queue.enqueue(NpcCommandName.CONFIGURE, origin=NpcCommandOrigin.SYSTEM, payload=value.model_dump(mode="json"))
        return self.settings.model_copy(deep=True)

    async def observe_state(self, state: GameState) -> None:
        if state.session_id != self.session_id:
            self.session_id = state.session_id
            self.session_ready_at = state.captured_at
            self.spawn_requested_session = None
            self.suspended_reason = None
            self.active_utterance_id = None
            self._transition(EmbodimentState.DISABLED if not self.settings.npc_presence_enabled else EmbodimentState.ABSENT)
        deep = state.deep_game_state.player if state.deep_game_state and state.deep_game_state.player else None
        eligible = state.game.running and state.game.loaded and not state.game.paused and (deep is None or deep.is_pre_game is not True) and (deep is None or deep.session_available is not False)
        if not eligible:
            return
        reason = "vehicle" if self.settings.npc_suspend_in_vehicle and state.player.in_vehicle else "combat" if self.settings.npc_suspend_during_combat and state.player.in_combat else None
        if reason and self.suspended_reason is None:
            self.previous_mode = self.requested_mode
            self.suspended_reason = reason
            self._transition(EmbodimentState.SUSPENDED)
            await self.queue.enqueue(NpcCommandName.SUSPEND, origin=NpcCommandOrigin.SYSTEM, payload={"reason": reason})
        elif reason is None and self.suspended_reason is not None:
            self.suspended_reason = None
            await self.queue.enqueue(NpcCommandName.RESUME, origin=NpcCommandOrigin.SYSTEM, payload={"mode": self.previous_mode})
        if reason is not None:
            return
        if not self.settings.npc_presence_enabled or not self.settings.npc_auto_spawn or self.spawn_requested_session == state.session_id:
            return
        ready_at = self.session_ready_at or state.captured_at
        if (state.captured_at - ready_at).total_seconds() >= self.settings.npc_spawn_delay_seconds:
            await self.queue.enqueue(NpcCommandName.SPAWN, origin=NpcCommandOrigin.SYSTEM)
            self.spawn_requested_session = state.session_id
            self._transition(EmbodimentState.SPAWNING)

    async def speech_event(self, clip: VoiceClip, state: VoiceState) -> None:
        if not self.settings.npc_presence_enabled or not self.settings.npc_voice_embodiment_enabled:
            return
        if state is VoiceState.PLAYING:
            if self.suspended_reason is not None and clip.priority.value != "critical":
                return
            if self.active_utterance_id and self.active_utterance_id != clip.voice_request_id:
                return
            text = _PLAIN.sub("", str(clip.metadata.get("text_preview", ""))).strip()[:180]
            self.active_utterance_id = clip.voice_request_id
            self.previous_mode = self.requested_mode
            await self.queue.enqueue(NpcCommandName.SPEECH_START, origin=NpcCommandOrigin.SPEECH, payload={
                "utterance_id": clip.voice_request_id[:64], "subtitle": text,
                "urgency": clip.priority.value, "duration_ms": min(max(clip.duration_ms, 250), 30_000),
                "gesture": "speaking_emphasis" if clip.priority.value in {"high", "critical"} else "speaking_neutral",
                "subtitles_enabled": self.settings.npc_in_game_subtitles_enabled,
            })
            self._transition(EmbodimentState.CONVERSING)
        elif state in {VoiceState.COMPLETED, VoiceState.FAILED, VoiceState.CANCELLED, VoiceState.SUPPRESSED} and self.active_utterance_id == clip.voice_request_id:
            await self.queue.enqueue(NpcCommandName.SPEECH_END, origin=NpcCommandOrigin.SPEECH, payload={"utterance_id": clip.voice_request_id[:64], "restore_mode": self.previous_mode})
            self.active_utterance_id = None

    async def status(self) -> NpcStatus:
        current = await self.queue.status()
        effective = current.effective_mode or current.mode
        lifecycle = current.lifecycle_state or current.mode or self.state.value
        return current.model_copy(update={
            "lifecycle_state": lifecycle, "requested_mode": self.requested_mode,
            "effective_mode": effective, "active_utterance_id": current.active_utterance_id or self.active_utterance_id,
            "suspended_reason": current.suspended_reason or self.suspended_reason,
        })
