from datetime import datetime, timedelta, timezone
from typing import Callable


class TtsCircuitOpenError(RuntimeError):
    pass


class TtsCircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_seconds: float = 30, clock: Callable[[], datetime] | None = None) -> None:
        self.failure_threshold = failure_threshold
        self.reset_after = timedelta(seconds=reset_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.state = "closed"
        self.consecutive_failures = 0
        self.opened_at: datetime | None = None
        self._probe_in_flight = False

    def allow_request(self) -> None:
        now = self.clock()
        if self.state == "open":
            if self.opened_at and now - self.opened_at >= self.reset_after:
                self.state = "half_open"
                self._probe_in_flight = False
            else:
                raise TtsCircuitOpenError("TTS circuit is open")
        if self.state == "half_open":
            if self._probe_in_flight:
                raise TtsCircuitOpenError("TTS half-open probe is already running")
            self._probe_in_flight = True

    def ready_for_attempt(self) -> bool:
        if self.state != "open":
            return True
        return bool(self.opened_at and self.clock() - self.opened_at >= self.reset_after)

    def record_success(self) -> None:
        self.state = "closed"
        self.consecutive_failures = 0
        self.opened_at = None
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self.consecutive_failures += 1
        if self.state == "half_open" or self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = self.clock()

    def record_configuration_error(self) -> None:
        self._probe_in_flight = False
