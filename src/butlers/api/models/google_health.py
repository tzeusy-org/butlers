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


class AccountStatus(BaseModel):
    """Per-account connector state for a single Google account.

    Populated by ``get_google_health_status`` from ``connector_registry``
    heartbeat rows keyed by ``endpoint_identity = google_health:user:<email>``.
    One entry per health-scoped Google account.
    """

    email: str
    """Authenticated Google email address — stable identifier for the account."""

    state: GoogleHealthConnectorState
    """Per-account operational state derived from connector_registry heartbeat."""

    error_message: str | None = None
    """Connector-reported failure reason from the heartbeat ``error_message``
    column, surfaced verbatim when the account's state is ``degraded`` or
    ``error``.  This is what lets the dashboard distinguish a *failing*
    connector (e.g. a Google Health API 403 → ``api_forbidden``) from a
    genuinely empty-but-healthy account.  ``None`` when the connector reports
    no error (healthy) or no heartbeat row exists yet."""

    scopes_granted: list[str]
    """Full Google Health scope URLs granted for this account.  Empty when the
    account row exists but has not yet completed the OAuth flow."""

    last_ingest_at: datetime | None = None
    """Most recent ingest timestamp for events originating from this account,
    or null when no events have been ingested yet."""

    last_token_refresh_at: datetime | None = None
    """Value of ``public.google_accounts.last_token_refresh_at`` for this account."""

    rate_limit_remaining: int | None = None
    """Most recently observed ``X-RateLimit-Remaining`` from the connector heartbeat,
    or null when the connector has not yet observed a rate-limit header."""

    sleep_sessions_7d: int = 0
    """Count of sleep-session ingestion events in the last 7 days for this account."""

    daily_summaries_7d: int = 0
    """Count of daily-summary ingestion events in the last 7 days for this account."""


class GoogleHealthStatusResponse(BaseModel):
    """Response for GET /api/connectors/google-health/status.

    Surfaces the data the Google Health connector status card renders:
    connection state, the full Google Health scope URLs that have been
    granted, the most recent ingest timestamp (derived from public.ingestion_events),
    the last token refresh timestamp (used by the UI's 7-day test-mode expiry
    heuristic), the most recently observed rate-limit headroom, and the
    ``google_health_test_mode`` metadata flag.

    Top-level summary fields are computed as worst-of across all per-account
    entries (error > degraded > healthy) so single-account installs render
    identically to the pre-multi-account shape (ADR-1).

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
    """Machine-readable state flag — healthy / degraded / error / not_configured.
    Computed as worst-of across all per-account entries."""

    error_message: str | None = None
    """Connector-reported failure reason for the worst-of account, surfaced
    verbatim when ``state`` is ``degraded`` or ``error``.  Lets the dashboard
    render a 'connector unavailable' signal (e.g. a Google Health API 403)
    instead of a silent empty state.  ``None`` when no account reports an
    error or no heartbeat exists yet."""

    sleep_sessions_7d: int = 0
    """Count of sleep-session ingestion events in the last 7 days.

    Derived from ``public.ingestion_events`` rows whose
    ``external_event_id`` matches ``google_health:sleep_session:*``."""

    daily_summaries_7d: int = 0
    """Count of daily-summary ingestion events in the last 7 days.

    Derived from ``public.ingestion_events`` rows whose
    ``external_event_id`` matches ``google_health:*:*`` but NOT
    ``google_health:sleep_session:*``."""

    accounts: list[AccountStatus] = []
    """Per-account status entries — one per health-scoped Google account.

    Empty list when no primary account exists (``state = not_configured``).
    For single-account installs this will contain exactly one entry and the
    top-level summary fields will mirror that entry's values."""

    primary_account_email: str | None = None
    """Email of the ``is_primary=true`` Google account, or null when no primary
    account is configured.  Single-account consumers can use this as the
    canonical identity without inspecting the ``accounts`` list."""


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
