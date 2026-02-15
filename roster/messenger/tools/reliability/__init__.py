"""Reliability infrastructure for Messenger butler.

Implements retry, timeout, and circuit-breaking contracts from
docs/roles/messenger_butler.md section 9.
"""

from __future__ import annotations

from .circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    CircuitOpenError,
    CircuitState,
)
from .errors import ErrorNormalizer, NormalizedError
from .retry import RetryPolicy, execute_with_retry
from .timeout import TimeoutConfig

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerState",
    "CircuitOpenError",
    "CircuitState",
    "ErrorNormalizer",
    "NormalizedError",
    "RetryPolicy",
    "TimeoutConfig",
    "execute_with_retry",
]
