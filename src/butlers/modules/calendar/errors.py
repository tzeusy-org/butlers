"""Calendar errors implementation."""

from __future__ import annotations

import re
from typing import Any

import httpx

from .constants import (
    SYNC_ERROR_KIND_AUTH,
    SYNC_ERROR_KIND_NONE,
    SYNC_ERROR_KIND_NOT_FOUND,
    SYNC_ERROR_KIND_TOKEN_EXPIRED,
    SYNC_ERROR_KIND_TRANSIENT,
)


class CalendarAuthError(RuntimeError):
    """Base error raised by Google Calendar auth/request helpers."""


class CalendarCredentialError(CalendarAuthError):
    """Raised when Google credential JSON is missing or invalid."""


class CalendarTokenRefreshError(CalendarAuthError):
    """Raised when refresh-token exchange fails."""


class CalendarRequestError(CalendarAuthError):
    """Raised when Google Calendar API request fails."""

    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Google Calendar API request failed ({status_code}): {message}")


class CalendarSyncTokenExpiredError(CalendarAuthError):
    """Raised when a sync token is expired or invalid; caller should do a full sync."""


def classify_sync_error_kind(last_error: str | None) -> str:
    """Classify a source's raw ``last_error`` into a coarse ``error_kind``.

    Returns one of :data:`SYNC_ERROR_KIND_NONE`, ``token_expired``, ``auth``,
    ``not_found``, or ``transient`` so the dashboard can distinguish a stale
    source that needs **Recover** (full re-sync) from one that needs
    **Reconnect** (re-authorization). The raw ``last_error`` string remains the
    source of truth; this is a derived hint only.

    A missing/empty error maps to ``none`` — classification does not depend on
    ``full_sync_required`` (a source pending a full re-sync without a recorded
    error is *stale*, not *failed*).
    """
    if not last_error:
        return SYNC_ERROR_KIND_NONE
    text = last_error.lower()
    if "410" in text or "gone" in text or "expired" in text or "sync token" in text:
        return SYNC_ERROR_KIND_TOKEN_EXPIRED
    if any(
        token in text
        for token in (
            "auth",
            "401",
            "403",
            "unauthor",
            "forbidden",
            "credential",
            "permission",
            "invalid_grant",
            "refresh token",
        )
    ):
        return SYNC_ERROR_KIND_AUTH
    if "404" in text or "not found" in text or "notfound" in text:
        return SYNC_ERROR_KIND_NOT_FOUND
    return SYNC_ERROR_KIND_TRANSIENT


def _is_transient_connectivity_error(exc: BaseException) -> bool:
    """Return true for transport failures that should retry on the next sync tick."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.TransportError, TimeoutError, ConnectionError)):
            return True
        if current.__cause__ is not None:
            current = current.__cause__
        elif not getattr(current, "__suppress_context__", False):
            current = current.__context__
        else:
            current = None
    return False


def _safe_google_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return " ".join(message.split())[:200]
        if isinstance(error_payload, str) and error_payload.strip():
            return " ".join(error_payload.split())[:200]

    raw_text = response.text.strip()
    if raw_text:
        return " ".join(raw_text.split())[:200]
    return "Request failed without an error payload"


def _google_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    error_payload = payload.get("error")
    if isinstance(error_payload, str) and error_payload.strip():
        return error_payload.strip()
    if isinstance(error_payload, dict):
        for key in ("code", "status", "reason"):
            value = error_payload.get(key)
            if isinstance(value, str | int | float | bool):
                value_text = str(value).strip()
                if value_text:
                    return value_text
    return None


def _redact_credential_values(message: str) -> str:
    """Redact known credential values from an error message (spec section 15.3).

    Calendar credentials are DB-managed, so redaction is pattern-based rather
    than sourced from process env vars.
    """
    redacted = message
    # key=value style pairs
    redacted = re.sub(
        r"(?i)\b(client_secret|refresh_token|access_token|token)\s*=\s*([^\s,;]+)",
        r"\1=[REDACTED]",
        redacted,
    )
    # JSON/Python dict style quoted values
    redacted = re.sub(
        r"""(?i)(['"]?(?:client_secret|refresh_token|access_token|token)['"]?\s*:\s*)(['"]).*?\2""",
        r'\1"[REDACTED]"',
        redacted,
    )
    # key: value style pairs
    redacted = re.sub(
        r"(?i)\b(client_secret|refresh_token|access_token|token)\s*:\s*([^\s,;]+)",
        r"\1: [REDACTED]",
        redacted,
    )
    return redacted


def _build_structured_error(
    exc: Exception,
    *,
    provider: str,
    calendar_id: str,
) -> dict[str, Any]:
    """Build a structured error dict per spec section 15.2.

    Sanitizes error messages to prevent credential leakage.
    """
    error_type = type(exc).__name__
    raw_message = str(exc)
    # Redact credential values before truncation (spec section 15.3).
    redacted = _redact_credential_values(raw_message)
    # Normalize whitespace and truncate to 200 chars.
    sanitized = " ".join(redacted.split())[:200]
    return {
        "status": "error",
        "error": sanitized,
        "error_type": error_type,
        "provider": provider,
        "calendar_id": calendar_id,
    }
