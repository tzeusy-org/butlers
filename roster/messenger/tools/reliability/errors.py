"""Error normalization for Messenger butler.

Maps provider-specific errors to canonical error classes from
docs/roles/messenger_butler.md section 9.

Canonical error classes:
- validation_error: Invalid input/targeting/auth/permission/content-policy failures
- target_unavailable: Provider/channel unavailable or throttled (retryable when transient)
- timeout: Timeout budget exceeded (retryable by policy)
- overload_rejected: Local admission overflow/saturation (retryable by policy)
- internal_error: Unexpected internal failures
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizedError:
    """Normalized error representation."""

    error_class: str
    """Canonical error class: validation_error, target_unavailable, timeout,
    overload_rejected, internal_error."""

    message: str
    """Human-readable error message."""

    retryable: bool
    """Whether the error is retryable."""

    original_error: Exception | None = None
    """Original exception (for debugging)."""

    provider_context: dict[str, Any] | None = None
    """Provider-specific error context."""


class ErrorNormalizer:
    """Normalize provider errors to canonical error classes.

    Error normalization rules (from section 9):
    - Invalid input/targeting -> validation_error
    - Provider/channel unavailable or throttled -> target_unavailable (retryable when transient)
    - Timeout budget exceeded -> timeout (retryable by policy)
    - Local admission overflow/saturation -> overload_rejected (retryable by policy)
    - Unexpected internal failures -> internal_error
    """

    @staticmethod
    def normalize(
        error: Exception,
        *,
        channel: str | None = None,
        provider_context: dict[str, Any] | None = None,
    ) -> NormalizedError:
        """Normalize an error to canonical error class.

        Parameters
        ----------
        error:
            The exception to normalize.
        channel:
            Optional channel name for channel-specific error handling.
        provider_context:
            Optional provider-specific context (status code, headers, etc).

        Returns
        -------
        NormalizedError
            Normalized error with canonical class, message, and retryability.
        """
        error_type = type(error).__name__
        error_msg = str(error)
        provider_ctx = provider_context or {}

        # Check for HTTP status codes in provider context
        status_code = provider_ctx.get("status_code")

        # Validation errors (non-retryable)
        if _is_validation_error(error, error_type, error_msg, status_code):
            return NormalizedError(
                error_class="validation_error",
                message=error_msg,
                retryable=False,
                original_error=error,
                provider_context=provider_ctx,
            )

        # Timeout errors (retryable by policy)
        if _is_timeout_error(error, error_type, error_msg):
            return NormalizedError(
                error_class="timeout",
                message=error_msg,
                retryable=True,
                original_error=error,
                provider_context=provider_ctx,
            )

        # Target unavailable / throttled (retryable when transient)
        if _is_target_unavailable(error, error_type, error_msg, status_code):
            return NormalizedError(
                error_class="target_unavailable",
                message=error_msg,
                retryable=True,
                original_error=error,
                provider_context=provider_ctx,
            )

        # Overload rejected (retryable by policy)
        if _is_overload_error(error, error_type, error_msg):
            return NormalizedError(
                error_class="overload_rejected",
                message=error_msg,
                retryable=True,
                original_error=error,
                provider_context=provider_ctx,
            )

        # Default to internal_error
        logger.warning(
            "Unmapped error type, defaulting to internal_error",
            extra={
                "error_type": error_type,
                "error_msg": error_msg,
                "channel": channel,
                "provider_context": provider_ctx,
            },
        )

        return NormalizedError(
            error_class="internal_error",
            message=f"Internal error: {error_msg}",
            retryable=False,
            original_error=error,
            provider_context=provider_ctx,
        )


def _is_validation_error(
    error: Exception, error_type: str, error_msg: str, status_code: int | None
) -> bool:
    """Check if error is a validation error (non-retryable)."""
    # HTTP 400, 401, 403, 422
    if status_code in {400, 401, 403, 422}:
        return True

    # ValueError, TypeError, KeyError
    if error_type in {"ValueError", "TypeError", "KeyError"}:
        return True

    # Common validation keywords
    validation_keywords = [
        "invalid",
        "missing",
        "required",
        "malformed",
        "unauthorized",
        "forbidden",
        "permission denied",
        "not allowed",
    ]

    error_msg_lower = error_msg.lower()
    return any(keyword in error_msg_lower for keyword in validation_keywords)


def _is_timeout_error(error: Exception, error_type: str, error_msg: str) -> bool:
    """Check if error is a timeout error (retryable)."""
    # asyncio.TimeoutError, httpx.TimeoutException, etc
    if "timeout" in error_type.lower():
        return True

    timeout_keywords = ["timeout", "timed out", "deadline exceeded"]
    error_msg_lower = error_msg.lower()
    return any(keyword in error_msg_lower for keyword in timeout_keywords)


def _is_target_unavailable(
    error: Exception, error_type: str, error_msg: str, status_code: int | None
) -> bool:
    """Check if error is target unavailable/throttled (retryable)."""
    # HTTP 429 (rate limit), 502, 503, 504
    if status_code in {429, 502, 503, 504}:
        return True

    # Connection errors
    if "connection" in error_type.lower():
        return True

    unavailable_keywords = [
        "unavailable",
        "service unavailable",
        "too many requests",
        "rate limit",
        "throttled",
        "connection",
        "network",
        "dns",
    ]

    error_msg_lower = error_msg.lower()
    return any(keyword in error_msg_lower for keyword in unavailable_keywords)


def _is_overload_error(error: Exception, error_type: str, error_msg: str) -> bool:
    """Check if error is local overload/saturation (retryable)."""
    # HTTP 429 from local admission control, 503 from local queue saturation
    overload_keywords = [
        "queue full",
        "overload",
        "capacity exceeded",
        "too many pending",
        "admission rejected",
    ]

    error_msg_lower = error_msg.lower()
    return any(keyword in error_msg_lower for keyword in overload_keywords)
