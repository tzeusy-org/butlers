"""Pydantic models for Google Health dashboard API endpoints.

Covers:
- GET /api/connectors/google-health/status response
- DELETE /api/connectors/google-health/disconnect response

Spec: openspec/changes/google-health-connector/specs/dashboard-google-accounts/spec.md
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class GoogleHealthConnectorState(StrEnum):
    """Operational state of the Google Health connector.

    Mirrors the heartbeat states documented by ``connector-base-spec`` with an
    additional ``not_configured`` value that covers the dashboard-only case of
    "no primary Google account yet" — the connector never reports this itself
    but the UI needs a distinct non-error way to render the missing account.
    """

    healthy = "healthy"
    """All three Google Health scopes granted and the connector is polling."""

    degraded = "degraded"
    """Scopes missing, no primary account, or the connector is in a re-check loop."""

    error = "error"
    """Refresh token invalidated or repeated API failures — requires owner intervention."""

    not_configured = "not_configured"
    """No primary Google account exists in public.google_accounts."""


class GoogleHealthStatusResponse(BaseModel):
    """Response for GET /api/connectors/google-health/status.

    Surfaces the data the Google Health connector status card renders:
    connection state, the full Google Health scope URLs that have been
    granted, the most recent ingest timestamp (derived from public.ingestion_events),
    the last token refresh timestamp (used by the UI's 7-day test-mode expiry
    heuristic), the most recently observed rate-limit headroom, and the
    ``google_health_test_mode`` metadata flag.

    No credential material is returned — only state flags, timestamps, and
    scope URLs.
    """

    connected: bool
    """True when all three Google Health scope URLs are in granted_scopes
    AND the connector heartbeat is healthy. A convenience boolean derived
    from ``state`` for call sites that only need connected / not-connected."""

    scopes_granted: list[str]
    """Full Google Health scope URLs present on the primary account's
    ``granted_scopes``. Empty list when no Google Health scopes are granted
    or no primary account exists."""

    last_ingest_at: datetime | None = None
    """Timestamp of the most recent ``public.ingestion_events`` row with
    ``source_provider = 'google_health'``, or null when no events have been
    ingested yet."""

    last_token_refresh_at: datetime | None = None
    """Value of ``public.google_accounts.last_token_refresh_at`` for the primary
    account. Null when the account has never refreshed (typical immediately
    after pairing) or when no primary account exists."""

    rate_limit_remaining: int | None = None
    """Most recently observed ``X-RateLimit-Remaining`` value across all
    resource polls, or null when no rate-limit header has ever been observed.
    Null is explicit — distinct from 0 (rate-limited) or absent."""

    test_mode: bool = False
    """True when ``metadata.google_health_test_mode = true`` on the primary
    Google account. Drives the orange/red warning banner on the status card."""

    state: GoogleHealthConnectorState
    """Machine-readable state flag — healthy / degraded / error / not_configured."""

    sleep_sessions_7d: int = 0
    """Count of sleep-session ingestion events in the last 7 days.

    Derived from ``public.ingestion_events`` rows whose
    ``external_event_id`` matches ``google_health:sleep_session:*``."""

    daily_summaries_7d: int = 0
    """Count of daily-summary ingestion events in the last 7 days.

    Derived from ``public.ingestion_events`` rows whose
    ``external_event_id`` matches ``google_health:*:*`` but NOT
    ``google_health:sleep_session:*``."""


class GoogleHealthDisconnectResponse(BaseModel):
    """Response for DELETE /api/connectors/google-health/disconnect.

    Reports whether the scope-selective revocation succeeded and how many
    scope URLs were removed from ``public.google_accounts.granted_scopes``.

    The Google account row and its companion entity are preserved — only
    the three Google Health scope URLs are stripped, and the connector is
    expected to transition to ``degraded`` on its next granted_scopes check.
    """

    success: bool = True

    message: str
    """Human-readable summary, e.g. "Google Health disconnected (3 scope(s) removed)."""

    scopes_removed: list[str] = []
    """The specific full scope URLs that were removed from ``granted_scopes``.
    Empty list when no Google Health scopes were present (idempotent no-op)."""
