import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from app.models.npc import NpcCommand, NpcCommandName, NpcCommandOrigin, NpcCommandResult, NpcStatus

logger = logging.getLogger("siena_observer.npc")


class NpcQueueFullError(RuntimeError):
    pass


class NpcControllerDisabledError(RuntimeError):
    pass


class NpcUnknownClaimError(KeyError):
    pass


class NpcCommandQueue:
    def __init__(self, *, enabled: bool, capacity: int, default_expiry_seconds: float, clock: Callable[[], datetime] | None = None) -> None:
        self.enabled = enabled
        self.capacity = capacity
        self.default_expiry_seconds = default_expiry_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._queue: list[NpcCommand] = []
        self._claimed: dict[str, NpcCommand] = {}
        self._lock = asyncio.Lock()
        self._status = NpcStatus(enabled=enabled)
        self._last_game_contact: datetime | None = None

    def _trace(self, event: str, **values: object) -> None:
        safe = " ".join(f"{key}={value}" for key, value in values.items())
        logger.info("%s %s", event, safe)

    def _expiry(self, command: NpcCommandName) -> float:
        if command in {NpcCommandName.SPAWN, NpcCommandName.DESPAWN}: return 30.0
        if command is NpcCommandName.STATUS: return 5.0
        return self.default_expiry_seconds

    def _discard_expired(self, now: datetime) -> None:
        alive: list[NpcCommand] = []
        for item in self._queue:
            if item.expires_at <= now:
                self._trace("npc_command_expired", command_id=item.command_id, command=item.command.value)
            else:
                alive.append(item)
        self._queue = alive

    async def enqueue(self, command: NpcCommandName, *, origin: NpcCommandOrigin = NpcCommandOrigin.PLAYER, payload: dict | None = None) -> tuple[NpcCommand, bool]:
        if not self.enabled:
            self._trace("npc_command_rejected", command=command.value, reason="controller_disabled")
            raise NpcControllerDisabledError
        async with self._lock:
            now = self.clock()
            self._discard_expired(now)
            if command in {NpcCommandName.FOLLOW, NpcCommandName.CONFIGURE, NpcCommandName.SUSPEND, NpcCommandName.RESUME}:
                existing = next((item for item in self._queue if item.command is command and item.origin is origin), None)
                if existing: return existing, True
            if command is NpcCommandName.DESPAWN:
                self._queue = [item for item in self._queue if item.command is NpcCommandName.DESPAWN]
                if self._queue: return self._queue[0], True
            if len(self._queue) >= self.capacity:
                self._trace("npc_command_rejected", command=command.value, reason="queue_full")
                raise NpcQueueFullError
            item = NpcCommand(
                command_id=str(uuid4()), command=command, created_at=now,
                expires_at=now + timedelta(seconds=self._expiry(command)),
                expires_at_epoch_ms=int((now + timedelta(seconds=self._expiry(command))).timestamp() * 1000),
                origin=origin, payload=payload,
            )
            if command is NpcCommandName.DESPAWN: self._queue.insert(0, item)
            else: self._queue.append(item)
            self._trace("npc_command_enqueued", command_id=item.command_id, command=command.value)
            return item, False

    async def claim_next(self) -> NpcCommand | None:
        if not self.enabled: return None
        async with self._lock:
            now = self.clock()
            self._last_game_contact = now
            self._discard_expired(now)
            if not self._queue: return None
            item = self._queue.pop(0)
            self._claimed[item.command_id] = item
            while len(self._claimed) > self.capacity:
                self._claimed.pop(next(iter(self._claimed)))
            self._trace("npc_command_claimed", command_id=item.command_id, command=item.command.value)
            return item

    async def acknowledge(self, command_id: str, result: NpcCommandResult) -> None:
        async with self._lock:
            item = self._claimed.pop(command_id, None)
            if item is None: raise NpcUnknownClaimError(command_id)
            if result.status is not None:
                self._last_game_contact = self.clock()
                self._status = result.status.model_copy(update={"enabled": self.enabled, "backend_connected": True, "last_command": item.command, "last_command_result": result.result, "last_error": result.error, "last_command_completed_at": self.clock()})
            else:
                self._status = self._status.model_copy(update={
                    "backend_connected": True, "last_command": item.command,
                    "last_command_result": result.result, "last_error": result.error, "last_command_completed_at": self.clock(),
                })
            self._trace(f"npc_{item.command.value}_{'succeeded' if result.result == 'success' else 'failed'}", command_id=command_id, result=result.result)

    async def status(self) -> NpcStatus:
        async with self._lock:
            connected = None if not self.enabled or self._last_game_contact is None else (self.clock() - self._last_game_contact).total_seconds() <= 5.0
            return self._status.model_copy(update={"backend_connected": connected}, deep=True)

    async def queue_size(self) -> int:
        async with self._lock:
            self._discard_expired(self.clock())
            return len(self._queue)
