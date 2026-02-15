"""Bounded retry with exponential backoff and jitter for Messenger butler.

Implements retry policy from docs/roles/messenger_butler.md section 9:
- Retry only retryable failures (network/transient provider failures,
  timeout-class, rate-limit-class)
- Validation/auth/permission/content-policy failures are non-retryable and fail fast
- Exponential backoff with randomized jitter
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .errors import ErrorNormalizer, NormalizedError

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy configuration."""

    max_attempts: int = 3
    """Maximum number of attempts (including initial attempt)."""

    base_delay_seconds: float = 1.0
    """Base delay in seconds for exponential backoff."""

    max_delay_seconds: float = 60.0
    """Maximum delay in seconds between retries."""

    jitter_factor: float = 0.3
    """Jitter factor (0.0 to 1.0) for randomizing backoff delay."""

    retry_timeout_errors: bool = True
    """Whether to retry timeout errors."""

    retry_target_unavailable: bool = True
    """Whether to retry target_unavailable errors."""

    retry_overload: bool = True
    """Whether to retry overload_rejected errors."""

    def calculate_backoff(self, attempt_number: int) -> float:
        """Calculate backoff delay for a retry attempt with jitter.

        Uses exponential backoff: base_delay * (2 ** (attempt_number - 1))
        with randomized jitter to avoid thundering herd.

        Parameters
        ----------
        attempt_number:
            Current attempt number (1-indexed, so 2 = first retry).

        Returns
        -------
        float
            Backoff delay in seconds with jitter applied.
        """
        if attempt_number <= 1:
            return 0.0

        # Exponential backoff: base * 2^(n-1)
        retry_number = attempt_number - 1
        exponential_delay = self.base_delay_seconds * (2 ** (retry_number - 1))

        # Cap at max delay
        delay = min(exponential_delay, self.max_delay_seconds)

        # Add jitter: delay * (1 - jitter_factor) + random_offset
        jitter_range = delay * self.jitter_factor
        jittered_delay = delay - jitter_range + (random.random() * 2 * jitter_range)

        return max(0.0, jittered_delay)

    def should_retry(self, error: NormalizedError, attempt_number: int) -> bool:
        """Determine if an error should be retried.

        Parameters
        ----------
        error:
            Normalized error from a failed attempt.
        attempt_number:
            Current attempt number (1-indexed).

        Returns
        -------
        bool
            True if the error should be retried, False otherwise.
        """
        # Never retry if we've exhausted attempts
        if attempt_number >= self.max_attempts:
            return False

        # Never retry non-retryable errors
        if not error.retryable:
            return False

        # Check error class against policy
        if error.error_class == "timeout":
            return self.retry_timeout_errors

        if error.error_class == "target_unavailable":
            return self.retry_target_unavailable

        if error.error_class == "overload_rejected":
            return self.retry_overload

        # Don't retry validation_error or internal_error
        return False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> RetryPolicy:
        """Create RetryPolicy from a configuration dictionary.

        Parameters
        ----------
        config:
            Configuration dictionary with retry policy overrides.
            Keys: max_attempts, base_delay_seconds, max_delay_seconds,
                  jitter_factor, retry_timeout_errors, retry_target_unavailable,
                  retry_overload (all optional).

        Returns
        -------
        RetryPolicy
            Configured retry policy with specified overrides.
        """
        return cls(
            max_attempts=config.get("max_attempts", 3),
            base_delay_seconds=config.get("base_delay_seconds", 1.0),
            max_delay_seconds=config.get("max_delay_seconds", 60.0),
            jitter_factor=config.get("jitter_factor", 0.3),
            retry_timeout_errors=config.get("retry_timeout_errors", True),
            retry_target_unavailable=config.get("retry_target_unavailable", True),
            retry_overload=config.get("retry_overload", True),
        )


async def execute_with_retry(
    operation: Callable[[], Any],
    *,
    retry_policy: RetryPolicy,
    channel: str | None = None,
    operation_context: dict[str, Any] | None = None,
) -> Any:
    """Execute an operation with retry policy.

    Parameters
    ----------
    operation:
        Async callable to execute (should be a coroutine function).
    retry_policy:
        Retry policy to apply.
    channel:
        Optional channel name for error normalization.
    operation_context:
        Optional context for error normalization and logging.

    Returns
    -------
    Any
        Result of the operation.

    Raises
    ------
    Exception
        The last error if all retries are exhausted, or a non-retryable error.
    """
    last_error: NormalizedError | None = None
    context = operation_context or {}

    for attempt_number in range(1, retry_policy.max_attempts + 1):
        try:
            # Execute operation
            result = await operation()
            return result

        except Exception as exc:
            # Normalize error
            provider_context = {"attempt_number": attempt_number, **context}
            normalized = ErrorNormalizer.normalize(
                exc, channel=channel, provider_context=provider_context
            )

            last_error = normalized

            # Log the error
            logger.warning(
                "Operation failed",
                extra={
                    "attempt_number": attempt_number,
                    "max_attempts": retry_policy.max_attempts,
                    "error_class": normalized.error_class,
                    "error_message": normalized.message,
                    "retryable": normalized.retryable,
                    "channel": channel,
                },
            )

            # Check if we should retry
            if not retry_policy.should_retry(normalized, attempt_number):
                logger.info(
                    "Not retrying error",
                    extra={
                        "attempt_number": attempt_number,
                        "error_class": normalized.error_class,
                        "reason": "non-retryable or max attempts reached",
                    },
                )
                raise

            # Calculate backoff delay
            backoff_delay = retry_policy.calculate_backoff(attempt_number + 1)

            logger.info(
                "Retrying after backoff",
                extra={
                    "attempt_number": attempt_number,
                    "next_attempt": attempt_number + 1,
                    "backoff_delay_seconds": backoff_delay,
                },
            )

            # Wait before retry
            if backoff_delay > 0:
                await asyncio.sleep(backoff_delay)

    # All retries exhausted
    if last_error and last_error.original_error:
        raise last_error.original_error
    raise RuntimeError("All retry attempts exhausted")
