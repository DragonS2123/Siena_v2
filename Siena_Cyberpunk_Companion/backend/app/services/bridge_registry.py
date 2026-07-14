import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.models.bridge import BridgeHeartbeat, BridgeHello, BridgeStatus
from app.models.game_state import GameState


class ProtocolMismatch(ValueError):
    pass


class BridgeUnavailable(ValueError):
    pass


@dataclass(slots=True)
class BridgeRecord:
    hello: BridgeHello
    compatible: bool
    last_hello_at: datetime
    last_heartbeat_at: datetime | None = None
    last_telemetry_at: datetime | None = None
    last_sequence: int | None = None
    session_id: str | None = None
    last_error: str | None = None
    disconnected: bool = False
    dropped_stale_states: int = 0
    reported_rate: float | None = None
    latency_ms: float | None = None
    telemetry_samples: deque[datetime] = field(default_factory=lambda: deque(maxlen=32))


class BridgeRegistry:
    def __init__(self, protocol_version: str = "1.0", timeout_seconds: float = 5.0, max_bridges: int = 8, configured_source: str = "auto") -> None:
        self.protocol_version = protocol_version
        self.timeout_seconds = timeout_seconds
        self.max_bridges = max_bridges
        self.configured_source = configured_source
        self._records: OrderedDict[str, BridgeRecord] = OrderedDict()
        self._source_seen: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def hello(self, hello: BridgeHello, now: datetime | None = None) -> BridgeStatus:
        clock = now or datetime.now(timezone.utc)
        compatible = hello.protocol_version == self.protocol_version
        error = None if compatible else f"incompatible protocol_version {hello.protocol_version!r}; backend requires {self.protocol_version!r}"
        async with self._lock:
            self._records[hello.bridge_id] = BridgeRecord(hello=hello, compatible=compatible, last_hello_at=clock, last_error=error)
            self._records.move_to_end(hello.bridge_id)
            while len(self._records) > self.max_bridges:
                self._records.popitem(last=False)
        if not compatible:
            raise ProtocolMismatch(error)
        return await self.status(clock)

    async def heartbeat(self, heartbeat: BridgeHeartbeat, now: datetime | None = None) -> BridgeStatus:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            record = self._records.get(heartbeat.bridge_id)
            if not record or not record.compatible:
                raise BridgeUnavailable("bridge must complete a compatible hello before heartbeat")
            record.last_heartbeat_at = clock
            record.session_id = heartbeat.session_id or record.session_id
            record.last_sequence = heartbeat.sequence if heartbeat.sequence is not None else record.last_sequence
            record.dropped_stale_states = heartbeat.dropped_stale_states
            record.reported_rate = heartbeat.telemetry_rate
            record.disconnected = False
            record.last_error = None
            self._source_seen["cet"] = clock
        return await self.status(clock)

    async def authorize_cet(self, bridge_version: str | None, now: datetime | None = None) -> None:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            record = self._primary_locked()
            if not record or not record.compatible or record.disconnected:
                raise BridgeUnavailable("CET telemetry requires a compatible bridge hello")
            if bridge_version != record.hello.bridge_version:
                raise BridgeUnavailable("telemetry bridge_version does not match the registered bridge")
            if not self._connected_locked(record, clock):
                raise BridgeUnavailable("bridge registration timed out; repeat bridge hello")

    async def telemetry(self, state: GameState, now: datetime | None = None) -> None:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            self._source_seen[state.source] = clock
            if state.source != "cet":
                return
            record = self._primary_locked()
            if not record:
                raise BridgeUnavailable("CET telemetry received without bridge hello")
            record.last_telemetry_at = clock
            record.last_sequence = state.sequence
            record.session_id = state.session_id
            record.telemetry_samples.append(clock)
            record.disconnected = False
            record.last_error = None
            captured_at = state.captured_at
            if captured_at.tzinfo is None:
                captured_at = captured_at.replace(tzinfo=timezone.utc)
            latency = (clock - captured_at).total_seconds() * 1000
            record.latency_ms = max(0.0, latency)

    async def disconnect(self, bridge_id: str, reason: str, now: datetime | None = None) -> BridgeStatus:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            record = self._records.get(bridge_id)
            if not record:
                raise BridgeUnavailable("unknown bridge_id")
            record.disconnected = True
            record.last_error = reason
        return await self.status(clock)

    async def select_source(self, now: datetime | None = None) -> str:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            if self.configured_source != "auto":
                return self.configured_source
            record = self._primary_locked()
            return "cet" if record and self._connected_locked(record, clock) else "simulator"

    async def status(self, now: datetime | None = None) -> BridgeStatus:
        clock = now or datetime.now(timezone.utc)
        async with self._lock:
            record = self._primary_locked()
            active = self.configured_source if self.configured_source != "auto" else ("cet" if record and self._connected_locked(record, clock) else "simulator")
            recent_cet = self._recent_locked("cet", clock)
            recent_simulator = self._recent_locked("simulator", clock)
            common = dict(active_source=active, configured_source=self.configured_source, source_conflict=recent_cet and recent_simulator, registered_bridges=len(self._records))
            if not record:
                return BridgeStatus(**common)
            return BridgeStatus(
                connected=self._connected_locked(record, clock), compatible=record.compatible,
                bridge_id=record.hello.bridge_id, bridge_version=record.hello.bridge_version,
                protocol_version=record.hello.protocol_version, game_version=record.hello.game_version,
                cet_version=record.hello.cet_version, last_hello_at=record.last_hello_at,
                last_heartbeat_at=record.last_heartbeat_at, last_telemetry_at=record.last_telemetry_at,
                last_sequence=record.last_sequence, session_id=record.session_id,
                capabilities=record.hello.capabilities, last_error=record.last_error,
                latency_ms=record.latency_ms,
                telemetry_rate=record.reported_rate if record.reported_rate is not None else self._rate_locked(record),
                dropped_stale_states=record.dropped_stale_states, **common,
            )

    def _primary_locked(self) -> BridgeRecord | None:
        for record in reversed(self._records.values()):
            if record.compatible:
                return record
        return next(reversed(self._records.values()), None) if self._records else None

    def _connected_locked(self, record: BridgeRecord, now: datetime) -> bool:
        if record.disconnected or not record.compatible:
            return False
        activity = max(item for item in (record.last_hello_at, record.last_heartbeat_at, record.last_telemetry_at) if item is not None)
        return (now - activity).total_seconds() <= self.timeout_seconds

    def _recent_locked(self, source: str, now: datetime) -> bool:
        seen = self._source_seen.get(source)
        return bool(seen and (now - seen).total_seconds() <= self.timeout_seconds)

    @staticmethod
    def _rate_locked(record: BridgeRecord) -> float:
        if len(record.telemetry_samples) < 2:
            return 0.0
        elapsed = (record.telemetry_samples[-1] - record.telemetry_samples[0]).total_seconds()
        return (len(record.telemetry_samples) - 1) / elapsed if elapsed > 0 else 0.0
