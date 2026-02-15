"""Circuit breaker for per-provider failure protection.

Implements circuit breaker pattern from docs/roles/messenger_butler.md section 9:
- Per-provider circuit breaker (closed, open, half-open states)
- Trip on consecutive failures above threshold
- Automatic recovery attempts after timeout
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from .errors import ErrorNormalizer, NormalizedError

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    """Normal operation, requests pass through."""

    OPEN = "open"
    """Circuit tripped, requests fail fast."""

    HALF_OPEN = "half_open"
    """Testing recovery, limited requests allowed."""


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""

    failure_threshold: int = 5
    """Number of consecutive failures before opening circuit."""

    recovery_timeout_seconds: float = 60.0
    """Time to wait before attempting recovery (OPEN -> HALF_OPEN)."""

    half_open_max_attempts: int = 3
    """Maximum attempts allowed in HALF_OPEN state before closing or re-opening."""

    half_open_success_threshold: int = 2
    """Successful attempts needed in HALF_OPEN to close circuit."""

    count_timeout_as_failure: bool = True
    """Whether to count timeout errors as failures for circuit trip."""

    count_target_unavailable_as_failure: bool = True
    """Whether to count target_unavailable errors as failures."""

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CircuitBreakerConfig:
        """Create CircuitBreakerConfig from a configuration dictionary."""
        return cls(
            failure_threshold=config.get("failure_threshold", 5),
            recovery_timeout_seconds=config.get("recovery_timeout_seconds", 60.0),
            half_open_max_attempts=config.get("half_open_max_attempts", 3),
            half_open_success_threshold=config.get("half_open_success_threshold", 2),
            count_timeout_as_failure=config.get("count_timeout_as_failure", True),
            count_target_unavailable_as_failure=config.get(
                "count_target_unavailable_as_failure", True
            ),
        )


@dataclass
class CircuitBreakerState:
    """Runtime state for a circuit breaker."""

    state: CircuitState = CircuitState.CLOSED
    """Current circuit state."""

    consecutive_failures: int = 0
    """Count of consecutive failures (reset on success)."""

    opened_at: datetime | None = None
    """Timestamp when circuit was opened (OPEN state)."""

    half_open_attempts: int = 0
    """Count of attempts in HALF_OPEN state."""

    half_open_successes: int = 0
    """Count of successful attempts in HALF_OPEN state."""

    last_error_class: str | None = None
    """Error class of the last failure."""

    last_error_message: str | None = None
    """Error message of the last failure."""


class CircuitBreaker:
    """Per-provider circuit breaker.

    Prevents cascading failures by failing fast when a provider is unhealthy.
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Circuit tripped due to failures, requests fail immediately
    - HALF_OPEN: Testing recovery, limited requests allowed

    State transitions:
    - CLOSED -> OPEN: After failure_threshold consecutive failures
    - OPEN -> HALF_OPEN: After recovery_timeout_seconds
    - HALF_OPEN -> CLOSED: After half_open_success_threshold successes
    - HALF_OPEN -> OPEN: After half_open_max_attempts without enough successes
    """

    def __init__(
        self,
        *,
        provider: str,
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        """Initialize circuit breaker for a provider.

        Parameters
        ----------
        provider:
            Provider/channel identifier (e.g., "telegram", "email").
        config:
            Optional circuit breaker configuration. Uses defaults if not provided.
        """
        self.provider = provider
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state.state

    @property
    def is_open(self) -> bool:
        """Whether circuit is currently open (failing fast)."""
        return self._state.state == CircuitState.OPEN

    async def execute(
        self,
        operation: Callable[[], Any],
        *,
        operation_context: dict[str, Any] | None = None,
    ) -> Any:
        """Execute operation through circuit breaker.

        Parameters
        ----------
        operation:
            Async callable to execute.
        operation_context:
            Optional context for error normalization.

        Returns
        -------
        Any
            Result of the operation.

        Raises
        ------
        CircuitOpenError
            If circuit is open and not ready for retry.
        Exception
            The underlying operation error.
        """
        async with self._lock:
            # Check circuit state before execution
            await self._check_state()

            if self._state.state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit breaker OPEN for provider {self.provider}",
                    provider=self.provider,
                    opened_at=self._state.opened_at,
                    last_error_class=self._state.last_error_class,
                )

        # Execute operation (outside lock to allow concurrent operations in CLOSED state)
        try:
            result = await operation()
            await self._record_success()
            return result

        except Exception as exc:
            # Normalize error
            normalized = ErrorNormalizer.normalize(
                exc, channel=self.provider, provider_context=operation_context
            )
            await self._record_failure(normalized)
            raise

    async def _check_state(self) -> None:
        """Check and update circuit state based on time and current state."""
        if self._state.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if self._state.opened_at is not None:
                elapsed = datetime.now(UTC) - self._state.opened_at
                recovery_timeout = timedelta(seconds=self.config.recovery_timeout_seconds)

                if elapsed >= recovery_timeout:
                    logger.info(
                        "Circuit breaker transitioning to HALF_OPEN",
                        extra={
                            "provider": self.provider,
                            "elapsed_seconds": elapsed.total_seconds(),
                        },
                    )
                    self._state.state = CircuitState.HALF_OPEN
                    self._state.half_open_attempts = 0
                    self._state.half_open_successes = 0

    async def _record_success(self) -> None:
        """Record successful operation."""
        async with self._lock:
            if self._state.state == CircuitState.CLOSED:
                # Reset failure counter
                self._state.consecutive_failures = 0

            elif self._state.state == CircuitState.HALF_OPEN:
                self._state.half_open_successes += 1

                logger.info(
                    "Circuit breaker HALF_OPEN success",
                    extra={
                        "provider": self.provider,
                        "successes": self._state.half_open_successes,
                        "threshold": self.config.half_open_success_threshold,
                    },
                )

                # Check if we can close the circuit
                if self._state.half_open_successes >= self.config.half_open_success_threshold:
                    logger.info(
                        "Circuit breaker transitioning to CLOSED",
                        extra={"provider": self.provider},
                    )
                    self._state.state = CircuitState.CLOSED
                    self._state.consecutive_failures = 0
                    self._state.opened_at = None
                    self._state.half_open_attempts = 0
                    self._state.half_open_successes = 0

    async def _record_failure(self, error: NormalizedError) -> None:
        """Record failed operation."""
        async with self._lock:
            # Determine if this error should count toward circuit trip
            should_count = self._should_count_failure(error)

            if not should_count:
                logger.debug(
                    "Error does not count toward circuit trip",
                    extra={
                        "provider": self.provider,
                        "error_class": error.error_class,
                    },
                )
                return

            self._state.last_error_class = error.error_class
            self._state.last_error_message = error.message

            if self._state.state == CircuitState.CLOSED:
                self._state.consecutive_failures += 1

                logger.warning(
                    "Circuit breaker failure recorded",
                    extra={
                        "provider": self.provider,
                        "consecutive_failures": self._state.consecutive_failures,
                        "threshold": self.config.failure_threshold,
                        "error_class": error.error_class,
                    },
                )

                # Check if we should open the circuit
                if self._state.consecutive_failures >= self.config.failure_threshold:
                    logger.error(
                        "Circuit breaker transitioning to OPEN",
                        extra={
                            "provider": self.provider,
                            "consecutive_failures": self._state.consecutive_failures,
                            "error_class": error.error_class,
                        },
                    )
                    self._state.state = CircuitState.OPEN
                    self._state.opened_at = datetime.now(UTC)

            elif self._state.state == CircuitState.HALF_OPEN:
                self._state.half_open_attempts += 1

                logger.warning(
                    "Circuit breaker HALF_OPEN failure",
                    extra={
                        "provider": self.provider,
                        "attempts": self._state.half_open_attempts,
                        "max_attempts": self.config.half_open_max_attempts,
                        "error_class": error.error_class,
                    },
                )

                # Check if we should re-open the circuit
                if self._state.half_open_attempts >= self.config.half_open_max_attempts:
                    logger.error(
                        "Circuit breaker re-opening from HALF_OPEN",
                        extra={"provider": self.provider},
                    )
                    self._state.state = CircuitState.OPEN
                    self._state.opened_at = datetime.now(UTC)
                    self._state.half_open_attempts = 0
                    self._state.half_open_successes = 0

    def _should_count_failure(self, error: NormalizedError) -> bool:
        """Determine if error should count toward circuit trip."""
        # Validation errors don't count (they're caller errors, not provider failures)
        if error.error_class == "validation_error":
            return False

        # Timeout errors count if configured
        if error.error_class == "timeout":
            return self.config.count_timeout_as_failure

        # Target unavailable counts if configured
        if error.error_class == "target_unavailable":
            return self.config.count_target_unavailable_as_failure

        # Overload and internal errors always count
        return True

    def get_status(self) -> dict[str, Any]:
        """Get current circuit breaker status.

        Returns
        -------
        dict
            Circuit breaker status with state, failure count, and timing info.
        """
        return {
            "provider": self.provider,
            "state": self._state.state.value,
            "consecutive_failures": self._state.consecutive_failures,
            "opened_at": self._state.opened_at.isoformat() if self._state.opened_at else None,
            "last_error_class": self._state.last_error_class,
            "last_error_message": self._state.last_error_message,
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout_seconds": self.config.recovery_timeout_seconds,
            },
        }


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and operation cannot proceed."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        opened_at: datetime | None = None,
        last_error_class: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.opened_at = opened_at
        self.last_error_class = last_error_class
