import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.models.reaction import CircuitBreakerStatus

logger = logging.getLogger("siena_observer.siena_core")


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
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
                logger.info("siena_circuit_half_open")
            else:
                raise CircuitOpenError("Siena Core circuit is open")
        if self.state == "half_open":
            if self._probe_in_flight:
                raise CircuitOpenError("Siena Core half-open probe is already running")
            self._probe_in_flight = True

    def record_success(self) -> None:
        recovered = self.state != "closed"
        self.state = "closed"
        self.consecutive_failures = 0
        self.opened_at = None
        self._probe_in_flight = False
        if recovered:
            logger.info("siena_circuit_closed")

    def record_failure(self) -> None:
        self._probe_in_flight = False
        self.consecutive_failures += 1
        if self.state == "half_open" or self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = self.clock()
            logger.warning("siena_circuit_opened consecutive_failures=%s", self.consecutive_failures)

    def status(self) -> CircuitBreakerStatus:
        return CircuitBreakerStatus(state=self.state, consecutive_failures=self.consecutive_failures, opened_at=self.opened_at)
