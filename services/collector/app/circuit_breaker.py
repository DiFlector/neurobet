"""Circuit breaker pattern and exponential backoff for resilient data collection."""

import enum
import logging
import time
from typing import Optional

logger = logging.getLogger("collector.circuit_breaker")


class CircuitState(str, enum.Enum):
    CLOSED = "CLOSED"        # Normal operations
    OPEN = "OPEN"            # Tripped, blocking requests to allow remote to recover
    HALF_OPEN = "HALF_OPEN"  # Testing a single probe request


class CircuitBreaker:
    """Circuit breaker with exponential backoff for network/browser operations."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self.total_trips = 0

    def can_execute(self) -> bool:
        """Check if request is allowed to execute."""
        now = time.time()
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self.last_failure_time and (now - self.last_failure_time >= self.recovery_timeout_seconds):
                logger.info("Circuit breaker recovery timeout elapsed. Transitioning from OPEN to HALF_OPEN.")
                self.state = CircuitState.HALF_OPEN
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow one test probe
            return True

        return True

    def record_success(self) -> None:
        """Record successful execution."""
        if self.state != CircuitState.CLOSED:
            logger.info("Probe succeeded. Circuit breaker transitioning from %s to CLOSED.", self.state.value)
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = None

    def record_failure(self, error: Exception) -> None:
        """Record failure and trip circuit if threshold exceeded."""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        logger.warning(
            "Recorded failure (%d/%d): %s",
            self.consecutive_failures,
            self.failure_threshold,
            error,
        )

        if self.state == CircuitState.HALF_OPEN:
            logger.warning("Probe failed in HALF_OPEN state. Re-opening circuit.")
            self.state = CircuitState.OPEN
            self.total_trips += 1
        elif self.consecutive_failures >= self.failure_threshold and self.state == CircuitState.CLOSED:
            logger.error(
                "Failure threshold (%d) reached! Tripping circuit breaker to OPEN state.",
                self.failure_threshold,
            )
            self.state = CircuitState.OPEN
            self.total_trips += 1

    def get_backoff_delay(self) -> float:
        """Calculate exponential backoff delay based on consecutive failures."""
        if self.consecutive_failures <= 0:
            return 0.0
        backoff = self.base_backoff_seconds * (2 ** (self.consecutive_failures - 1))
        return min(self.max_backoff_seconds, backoff)
