"""Google Calendar connector runtime for live ingestion via syncToken poll flow.

This connector implements incremental sync of Google Calendar events to the
Switchboard using the events.list API with syncToken checkpointing. It follows
the same multi-account architecture as the Gmail connector.

Key behaviors:
- Multi-account support via public.google_accounts (calendar scope discovery)
- Per-account asyncio poll loops with error isolation
- Incremental sync via events.list(syncToken=...) with 410-Gone fallback to full sync
- Cursor persistence via cursor_store (switchboard.connector_registry)
- "Event starting soon" synthetic notifications at configurable lead time
- IngestionPolicyEvaluator integration for pre-ingest filtering
- Filtered event batch flush to connectors.filtered_events
- Replay queue drain loop
- Heartbeat protocol (connector.heartbeat.v1 envelope, periodic send)
- Prometheus metrics (submissions, api calls, checkpoints, errors)
- Health/metrics HTTP server (/health, /metrics endpoints)
- Aggregated health status (worst-case across account loops)

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=google_calendar (required)
- CONNECTOR_CHANNEL=google_calendar (required)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_HEALTH_PORT (optional, default 40085)
- CONNECTOR_BUTLER_DB_NAME (optional; local butler DB for per-butler overrides)
- BUTLER_SHARED_DB_NAME (optional; shared credential DB, defaults to 'butlers')
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120)
- GCAL_POLL_INTERVAL_S (optional, default 60): seconds between incremental syncs
- GCAL_ACCOUNT_RESCAN_INTERVAL_S (optional, default 300): account re-scan cadence
- GCAL_STARTING_SOON_LEAD_MINUTES (optional, default 15): notification lead time
- GCAL_STARTING_SOON_EXTRA_LEAD_MINUTES (optional, comma-separated): extra lead times
- GCAL_STARTING_SOON_WINDOW_HOURS (optional, default 2): look-ahead window in hours

Security requirements:
- Never commit credentials or session artifacts to version control
- OAuth credentials resolved exclusively from DB (butler_secrets + entity_info)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics, get_error_type
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import db_params_from_env, should_retry_with_ssl_disable
from butlers.google_credentials import InvalidGoogleCredentialsError, load_google_credentials
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "google_calendar"
_CONNECTOR_CHANNEL = "google_calendar"
_CONNECTOR_PROVIDER = "google_calendar"

# Calendar scope required for event access
_GCAL_SCOPE = "https://www.googleapis.com/auth/calendar"
_GCAL_SCOPE_READONLY = "https://www.googleapis.com/auth/calendar.readonly"

# Google Calendar API base URL
_GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Default config values
_DEFAULT_POLL_INTERVAL_S = 60
_DEFAULT_ACCOUNT_RESCAN_INTERVAL_S = 300
_DEFAULT_STARTING_SOON_LEAD_MINUTES = 15
_DEFAULT_STARTING_SOON_WINDOW_HOURS = 2
_DEFAULT_HEALTH_PORT = 40085
_DEFAULT_MAX_INFLIGHT = 8
# Seen-set pruning: remove entries for events that started this many minutes ago
_SEEN_SET_PRUNE_PAST_MINUTES = 60

# Rate-limit retry config
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY_S = 2.0
_RATE_LIMIT_MAX_DELAY_S = 30.0


# ---------------------------------------------------------------------------
# Health status models
# ---------------------------------------------------------------------------


class AccountHealthStatus(BaseModel):
    """Per-account health status for the multi-account calendar connector."""

    email: str | None
    endpoint_identity: str
    status: Literal["healthy", "degraded", "error"]
    last_checkpoint_save_at: str | None
    last_ingest_submit_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    error: str | None = None


class MultiAccountHealthStatus(BaseModel):
    """Aggregated health status across all calendar account loops."""

    status: Literal["healthy", "degraded", "error"]
    uptime_seconds: float
    active_accounts: int
    account_health: list[AccountHealthStatus]
    timestamp: str


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarAccountConfig:
    """Configuration for a single Google Calendar account loop.

    Fields:
        email: Google account email address.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        refresh_token: OAuth refresh token.
        switchboard_mcp_url: Switchboard MCP server URL.
        poll_interval_s: Seconds between incremental sync cycles.
        max_inflight: Max concurrent inflight requests.
        health_port: TCP port for health/metrics server.
        heartbeat_interval_s: Seconds between heartbeat sends.
        starting_soon_lead_minutes: Lead time for "starting soon" notifications (minutes).
        starting_soon_extra_lead_minutes: Additional lead times for "starting soon" notifications.
        starting_soon_window_hours: Look-ahead window for upcoming events (hours).
    """

    email: str
    client_id: str
    client_secret: str
    refresh_token: str
    switchboard_mcp_url: str
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    max_inflight: int = _DEFAULT_MAX_INFLIGHT
    health_port: int = _DEFAULT_HEALTH_PORT
    heartbeat_interval_s: int = 120
    starting_soon_lead_minutes: int = _DEFAULT_STARTING_SOON_LEAD_MINUTES
    starting_soon_extra_lead_minutes: tuple[int, ...] = ()
    starting_soon_window_hours: int = _DEFAULT_STARTING_SOON_WINDOW_HOURS

    @property
    def endpoint_identity(self) -> str:
        """Canonical endpoint identity for this account."""
        return f"google_calendar:user:{self.email}"

    @property
    def cursor_key(self) -> str:
        """Key for cursor store lookup."""
        return self.endpoint_identity

    @property
    def all_lead_minutes(self) -> list[int]:
        """Return all configured lead times (primary + extra)."""
        base = [self.starting_soon_lead_minutes]
        base.extend(self.starting_soon_extra_lead_minutes)
        # Deduplicate and sort ascending
        return sorted(set(base))


@dataclass
class CalendarProcessConfig:
    """Process-level configuration shared across all account loops.

    Loaded once from environment on startup; used to build per-account configs.
    """

    switchboard_mcp_url: str
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    account_rescan_interval_s: int = _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S
    max_inflight: int = _DEFAULT_MAX_INFLIGHT
    health_port: int = _DEFAULT_HEALTH_PORT
    heartbeat_interval_s: int = 120
    starting_soon_lead_minutes: int = _DEFAULT_STARTING_SOON_LEAD_MINUTES
    starting_soon_extra_lead_minutes: tuple[int, ...] = field(default_factory=tuple)
    starting_soon_window_hours: int = _DEFAULT_STARTING_SOON_WINDOW_HOURS

    @classmethod
    def from_env(cls) -> CalendarProcessConfig:
        """Load process config from environment variables."""

        def _int_env(key: str, default: int) -> int:
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Invalid value for %s=%r, using default %d", key, raw, default)
                return default

        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL", "").strip()
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL is required")

        extra_leads_raw = os.environ.get("GCAL_STARTING_SOON_EXTRA_LEAD_MINUTES", "").strip()
        extra_leads: tuple[int, ...] = ()
        if extra_leads_raw:
            parsed: list[int] = []
            for part in extra_leads_raw.split(","):
                part = part.strip()
                try:
                    parsed.append(int(part))
                except ValueError:
                    logger.warning(
                        "Invalid value in GCAL_STARTING_SOON_EXTRA_LEAD_MINUTES: %r (skipped)",
                        part,
                    )
            extra_leads = tuple(parsed)

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            poll_interval_s=_int_env("GCAL_POLL_INTERVAL_S", _DEFAULT_POLL_INTERVAL_S),
            account_rescan_interval_s=_int_env(
                "GCAL_ACCOUNT_RESCAN_INTERVAL_S", _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S
            ),
            max_inflight=_int_env("CONNECTOR_MAX_INFLIGHT", _DEFAULT_MAX_INFLIGHT),
            health_port=_int_env("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
            heartbeat_interval_s=_int_env("CONNECTOR_HEARTBEAT_INTERVAL_S", 120),
            starting_soon_lead_minutes=_int_env(
                "GCAL_STARTING_SOON_LEAD_MINUTES", _DEFAULT_STARTING_SOON_LEAD_MINUTES
            ),
            starting_soon_extra_lead_minutes=extra_leads,
            starting_soon_window_hours=_int_env(
                "GCAL_STARTING_SOON_WINDOW_HOURS", _DEFAULT_STARTING_SOON_WINDOW_HOURS
            ),
        )

    def make_account_config(
        self,
        *,
        email: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        metadata_calendar: dict[str, Any] | None = None,
    ) -> CalendarAccountConfig:
        """Build a per-account config, applying metadata overrides from google_accounts."""
        poll_interval = self.poll_interval_s
        lead_minutes = self.starting_soon_lead_minutes
        window_hours = self.starting_soon_window_hours

        if metadata_calendar:
            if "poll_interval_s" in metadata_calendar:
                try:
                    poll_interval = int(metadata_calendar["poll_interval_s"])
                except (ValueError, TypeError):
                    pass
            if "starting_soon_lead_minutes" in metadata_calendar:
                try:
                    lead_minutes = int(metadata_calendar["starting_soon_lead_minutes"])
                except (ValueError, TypeError):
                    pass
            if "starting_soon_window_hours" in metadata_calendar:
                try:
                    window_hours = int(metadata_calendar["starting_soon_window_hours"])
                except (ValueError, TypeError):
                    pass

        return CalendarAccountConfig(
            email=email,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            switchboard_mcp_url=self.switchboard_mcp_url,
            poll_interval_s=poll_interval,
            max_inflight=self.max_inflight,
            health_port=self.health_port,
            heartbeat_interval_s=self.heartbeat_interval_s,
            starting_soon_lead_minutes=lead_minutes,
            starting_soon_extra_lead_minutes=self.starting_soon_extra_lead_minutes,
            starting_soon_window_hours=window_hours,
        )


# ---------------------------------------------------------------------------
# Seen-set for "starting soon" dedup
# ---------------------------------------------------------------------------


@dataclass
class StartingSoonSeenSet:
    """In-memory seen-set for deduplicating "event starting soon" notifications.

    Key: (event_id, lead_minutes)
    Value: event_start datetime (used for pruning past events)
    """

    _entries: dict[tuple[str, int], datetime] = field(default_factory=dict)

    def has_seen(self, event_id: str, lead_minutes: int) -> bool:
        """Return True if this (event_id, lead_minutes) pair was already notified."""
        return (event_id, lead_minutes) in self._entries

    def mark_seen(self, event_id: str, lead_minutes: int, event_start: datetime) -> None:
        """Record that a notification was sent for this (event_id, lead_minutes)."""
        self._entries[(event_id, lead_minutes)] = event_start

    def prune(self, now: datetime) -> int:
        """Remove entries for events that have already started (event_start < now).

        Returns the number of entries pruned.
        """
        to_remove = [key for key, start in self._entries.items() if start < now]
        for key in to_remove:
            del self._entries[key]
        return len(to_remove)

    def prune_past_events(self, now: datetime | None = None) -> int:
        """Alias for prune() with optional now parameter.

        Task 4.4: Remove seen-set entries for events that started in the past.
        More than _SEEN_SET_PRUNE_PAST_MINUTES minutes ago.
        """
        if now is None:
            now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=_SEEN_SET_PRUNE_PAST_MINUTES)
        to_remove = [key for key, start in self._entries.items() if start < cutoff]
        for key in to_remove:
            del self._entries[key]
        return len(to_remove)

    def size(self) -> int:
        """Return the number of entries in the seen-set."""
        return len(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Compat config + standalone envelope/scan functions (task 4.1-4.5 public API)
# ---------------------------------------------------------------------------
# These standalone classes/functions provide a testable, side-effect-free API
# for the starting-soon notification logic. They are also used by PR #728 tests.


class GoogleCalendarConnectorConfig:
    """Compat per-account config for direct CalendarConnectorRuntime instantiation.

    This is the config shape used by tests and single-account deployments. The
    multi-account manager uses CalendarAccountConfig instead.

    Adapts to CalendarAccountConfig via to_account_config().
    """

    def __init__(
        self,
        *,
        switchboard_mcp_url: str,
        connector_endpoint_identity: str,
        gcal_client_id: str,
        gcal_client_secret: str,
        gcal_refresh_token: str,
        connector_provider: str = "google_calendar",
        connector_channel: str = "google_calendar",
        connector_max_inflight: int = _DEFAULT_MAX_INFLIGHT,
        gcal_starting_soon_lead_minutes: int = _DEFAULT_STARTING_SOON_LEAD_MINUTES,
        gcal_poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S,
        gcal_calendar_ids: list[str] | None = None,
    ) -> None:
        self.switchboard_mcp_url = switchboard_mcp_url
        self.connector_endpoint_identity = connector_endpoint_identity
        self.gcal_client_id = gcal_client_id
        self.gcal_client_secret = gcal_client_secret
        self.gcal_refresh_token = gcal_refresh_token
        self.connector_provider = connector_provider
        self.connector_channel = connector_channel
        self.connector_max_inflight = connector_max_inflight
        self.gcal_starting_soon_lead_minutes = gcal_starting_soon_lead_minutes
        self.gcal_poll_interval_s = gcal_poll_interval_s
        self.gcal_calendar_ids = gcal_calendar_ids or []

    def to_account_config(self) -> CalendarAccountConfig:
        """Convert to the manager-facing CalendarAccountConfig."""
        # Extract email from endpoint_identity pattern: "google_calendar:user:<email>"
        parts = self.connector_endpoint_identity.split(":", 2)
        email = parts[2] if len(parts) >= 3 else self.connector_endpoint_identity
        return CalendarAccountConfig(
            email=email,
            client_id=self.gcal_client_id,
            client_secret=self.gcal_client_secret,
            refresh_token=self.gcal_refresh_token,
            switchboard_mcp_url=self.switchboard_mcp_url,
            max_inflight=self.connector_max_inflight,
            starting_soon_lead_minutes=self.gcal_starting_soon_lead_minutes,
            poll_interval_s=self.gcal_poll_interval_s,
        )


# Compat alias: GoogleCalendarAccountRuntime for tests that import the old name
# The real implementation lives in CalendarConnectorRuntime.
_GoogleCalendarAccountRuntimeBase = None  # resolved below after class definition


def build_event_envelope(
    event: dict[str, Any],
    *,
    event_type: str,
    endpoint_identity: str,
    connector_channel: str = "google_calendar",
    connector_provider: str = "google_calendar",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a Google Calendar event change.

    Standalone function for testability. Used by scan_starting_soon family.
    Per spec: connector-google-calendar/spec.md §Requirement: ingest.v1 Field Mapping.
    """
    if observed_at is None:
        observed_at = datetime.now(UTC)

    event_id = event.get("id", "unknown")
    updated = event.get("updated", observed_at.isoformat())
    organizer_email = event.get("organizer", {}).get("email") or "unknown"
    idempotency_key = f"gcal:{endpoint_identity}:{event_id}:{updated}"

    # Build normalized text per spec (shared formatter).
    summary = event.get("summary", "(no title)")
    normalized_text = _build_normalized_text(
        event_type=event_type,
        summary=summary,
        start_dt=_parse_event_start(event),
        end_dt=_parse_event_end(event),
        location=event.get("location"),
        organizer_email=organizer_email,
        attendee_count=len(event.get("attendees", []) or []),
    )

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": connector_channel,
            "provider": connector_provider,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": event_id,
            "observed_at": observed_at.isoformat(),
        },
        "sender": {
            "identity": organizer_email,
        },
        "payload": {
            "raw": event,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "ingestion_tier": "full",
            "policy_tier": "default",
        },
    }


def build_starting_soon_envelope(
    event: dict[str, Any],
    *,
    lead_minutes: int,
    endpoint_identity: str,
    connector_channel: str = "google_calendar",
    connector_provider: str = "google_calendar",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for an 'event starting soon' notification.

    Per spec: connector-google-calendar/spec.md §Scenario: Starting soon event field mapping.
    - external_event_id: "starting_soon:<event_id>"
    - idempotency_key: "gcal:<endpoint_identity>:starting_soon:<event_id>:<lead_minutes>"
    - policy_tier: "interactive" (time-sensitive)

    Task 4.3: event_starting_soon envelope with interactive policy tier.
    """
    if observed_at is None:
        observed_at = datetime.now(UTC)

    event_id = event.get("id", "unknown")
    organizer_email = event.get("organizer", {}).get("email") or "unknown"
    idempotency_key = f"gcal:{endpoint_identity}:starting_soon:{event_id}:{lead_minutes}"

    # Build normalized text per spec (shared formatter).
    summary = event.get("summary", "(no title)")
    normalized_text = _build_normalized_text(
        event_type="starting_soon",
        summary=summary,
        start_dt=_parse_event_start(event),
        end_dt=_parse_event_end(event),
        location=event.get("location"),
        organizer_email=organizer_email,
        attendee_count=len(event.get("attendees", []) or []),
    )

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": connector_channel,
            "provider": connector_provider,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"starting_soon:{event_id}",
            "external_thread_id": event_id,
            "observed_at": observed_at.isoformat(),
        },
        "sender": {
            "identity": organizer_email,
        },
        "payload": {
            "raw": event,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "ingestion_tier": "full",
            "policy_tier": "interactive",
        },
    }


def _is_event_cancelled(event: dict[str, Any]) -> bool:
    """Return True if the event is cancelled."""
    return event.get("status") == "cancelled"


def scan_starting_soon(
    events: list[dict[str, Any]],
    seen_set: StartingSoonSeenSet,
    *,
    lead_minutes: int,
    now: datetime | None = None,
    endpoint_identity: str,
    connector_channel: str = "google_calendar",
    connector_provider: str = "google_calendar",
) -> list[dict[str, Any]]:
    """Scan upcoming events and return starting-soon ingest envelopes.

    Implements tasks 4.1-4.4 (not 4.5 — restart recovery is scan_starting_soon_on_restart):
    - Task 4.1: Scan upcoming events within the lead-time window after each sync cycle.
    - Task 4.2: Check in-memory seen-set keyed by (event_id, lead_minutes).
    - Task 4.3: Build event_starting_soon envelopes with 'interactive' policy tier.
    - Task 4.4: Prune seen-set of past events before scanning.

    Returns a list of ingest.v1 envelopes for events newly entering the lead-time window.
    """
    if lead_minutes <= 0:
        return []

    if now is None:
        now = datetime.now(UTC)

    # Task 4.4: Prune past events before scanning
    pruned = seen_set.prune_past_events(now=now)
    if pruned > 0:
        logger.debug(
            "Starting-soon seen-set: pruned %d past entries (endpoint=%s)",
            pruned,
            endpoint_identity,
        )

    envelopes: list[dict[str, Any]] = []
    window_end = now + timedelta(minutes=lead_minutes)

    for event in events:
        if _is_event_cancelled(event):
            continue

        # Skip all-day events — only timed events can have starting-soon notifications
        if not event.get("start", {}).get("dateTime"):
            continue

        event_start = _parse_event_start(event)
        if event_start is None:
            continue

        # Skip events that have already started
        if event_start <= now:
            continue

        # Check if event falls within the lead-time window
        if event_start > window_end:
            continue

        event_id = event.get("id", "")
        if not event_id:
            continue

        # Task 4.2: Dedup check against seen-set
        if seen_set.has_seen(event_id, lead_minutes):
            logger.debug(
                "Starting-soon: skipping already-seen event_id=%s lead_minutes=%d",
                event_id,
                lead_minutes,
            )
            continue

        # Task 4.3: Build starting-soon envelope with interactive policy tier
        envelope = build_starting_soon_envelope(
            event,
            lead_minutes=lead_minutes,
            endpoint_identity=endpoint_identity,
            connector_channel=connector_channel,
            connector_provider=connector_provider,
            observed_at=now,
        )
        envelopes.append(envelope)

        # Task 4.2: Mark as seen to prevent duplicate notifications
        seen_set.mark_seen(event_id, lead_minutes, event_start)
        logger.info(
            "Starting-soon notification queued: event_id=%s lead_minutes=%d start=%s",
            event_id,
            lead_minutes,
            event_start.isoformat(),
        )

    return envelopes


def scan_starting_soon_on_restart(
    events: list[dict[str, Any]],
    seen_set: StartingSoonSeenSet,
    *,
    lead_minutes: int,
    now: datetime | None = None,
    endpoint_identity: str,
    connector_channel: str = "google_calendar",
    connector_provider: str = "google_calendar",
) -> list[dict[str, Any]]:
    """Emit starting-soon notifications on connector restart for events not yet started.

    Task 4.5: Restart recovery — on restart, check upcoming events within the
    lead-time window and emit notifications for events that have not yet started.
    These may be overdue (the connector was down during the normal lead-time window)
    but the event hasn't started yet.

    Called once at startup; does NOT prune the seen-set (it's empty on restart anyway).
    """
    if lead_minutes <= 0:
        return []

    if now is None:
        now = datetime.now(UTC)

    envelopes: list[dict[str, Any]] = []
    # On restart, catch overdue notifications: no lower bound on window
    window_end = now + timedelta(minutes=lead_minutes)

    for event in events:
        if _is_event_cancelled(event):
            continue

        # Skip all-day events
        if not event.get("start", {}).get("dateTime"):
            continue

        event_start = _parse_event_start(event)
        if event_start is None:
            continue

        # Only emit for events that haven't started yet
        if event_start <= now:
            continue

        # On restart, emit for any upcoming event within the lead window
        if event_start > window_end:
            continue

        event_id = event.get("id", "")
        if not event_id:
            continue

        # Dedup check (seen-set is empty on fresh start, but guard anyway)
        if seen_set.has_seen(event_id, lead_minutes):
            continue

        envelope = build_starting_soon_envelope(
            event,
            lead_minutes=lead_minutes,
            endpoint_identity=endpoint_identity,
            connector_channel=connector_channel,
            connector_provider=connector_provider,
            observed_at=now,
        )
        envelopes.append(envelope)
        seen_set.mark_seen(event_id, lead_minutes, event_start)
        logger.info(
            "Restart recovery: starting-soon notification queued: "
            "event_id=%s lead_minutes=%d start=%s",
            event_id,
            lead_minutes,
            event_start.isoformat(),
        )

    return envelopes


class GoogleCalendarAccountRuntime:
    """Compat runtime class for tests and single-account deployments.

    Wraps the standalone scan_starting_soon / scan_starting_soon_on_restart functions
    and provides a testable _emit_starting_soon_notifications interface. Accepts a
    GoogleCalendarConnectorConfig (PR-style config with gcal_* field names) and a
    pre-wired mcp_client for unit tests.

    Task 4.1-4.5: Used by test_google_calendar_notifications.py to verify the
    starting-soon notification pipeline.
    """

    def __init__(
        self,
        config: GoogleCalendarConnectorConfig | CalendarAccountConfig,
        cursor_pool: Any = None,
        shared_pool: Any = None,
        *,
        mcp_client: CachedMCPClient | None = None,
    ) -> None:
        if isinstance(config, GoogleCalendarConnectorConfig):
            self._config = config.to_account_config()
        else:
            self._config = config

        self._mcp_client = mcp_client
        self._seen_set: StartingSoonSeenSet = StartingSoonSeenSet()
        # In-memory upcoming events cache (event_id -> full event dict)
        self._upcoming_events: dict[str, dict[str, Any]] = {}

    async def _emit_starting_soon_notifications(
        self,
        *,
        is_restart: bool = False,
    ) -> None:
        """Scan upcoming events and emit starting-soon notifications.

        Task 4.1: Called after each sync cycle.
        Task 4.5: Called on startup with is_restart=True for recovery.
        """
        lead_minutes = self._config.starting_soon_lead_minutes
        if lead_minutes <= 0:
            return

        upcoming = list(self._upcoming_events.values())
        now = datetime.now(UTC)
        endpoint_identity = self._config.endpoint_identity

        if is_restart:
            envelopes = scan_starting_soon_on_restart(
                upcoming,
                self._seen_set,
                lead_minutes=lead_minutes,
                now=now,
                endpoint_identity=endpoint_identity,
            )
        else:
            envelopes = scan_starting_soon(
                upcoming,
                self._seen_set,
                lead_minutes=lead_minutes,
                now=now,
                endpoint_identity=endpoint_identity,
            )

        if not envelopes:
            return

        if self._mcp_client is None:
            raise RuntimeError("MCP client not initialized")

        for envelope in envelopes:
            await self._mcp_client.call_tool("ingest", envelope)


# ---------------------------------------------------------------------------
# CalendarConnectorRuntime — per-account connector
# ---------------------------------------------------------------------------


class CalendarConnectorRuntime:
    """Google Calendar connector runtime for a single Google account.

    Implements:
    - Token refresh + rate-limit retry
    - Initial full sync (establishes syncToken baseline)
    - Incremental sync poll loop
    - 410 Gone → fallback to full sync
    - Event change classification (created/updated/deleted)
    - ingest.v1 envelope normalization
    - "Starting soon" synthetic notifications
    - IngestionPolicyEvaluator integration
    - Filtered event batch flush
    - Replay queue drain
    - Heartbeat protocol
    - Prometheus metrics
    - Checkpoint-after-acceptance cursor advancement
    """

    def __init__(
        self,
        config: CalendarAccountConfig,
        db_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool

        # HTTP client for Google Calendar API
        self._http_client: httpx.AsyncClient | None = None

        # MCP client for Switchboard submission
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url, client_name="google-calendar-connector"
        )

        # Token management
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

        # Runtime state
        self._running = False
        self._stopped = False  # guard against double-stop
        self._semaphore = asyncio.Semaphore(config.max_inflight)

        # Current sync token (checkpoint)
        self._sync_token: str | None = None

        # In-memory upcoming event cache for "starting soon" synthesis.
        # Maps event_id → (event, start_dt) for events in the look-ahead window.
        # The full event dict is retained so starting-soon envelopes can be built
        # through the canonical builder (location, attendees, idempotency_key).
        self._upcoming_events: dict[str, tuple[dict[str, Any], datetime]] = {}

        # Seen-set for "starting soon" dedup
        self._seen_set = StartingSoonSeenSet()

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=config.endpoint_identity,
        )

        # Health tracking
        self._start_time = time.time()
        self._last_checkpoint_save: float | None = None
        self._last_ingest_submit: float | None = None
        self._source_api_ok: bool | None = None

        # Heartbeat
        self._heartbeat: ConnectorHeartbeat | None = None

        # Ingestion policy evaluators
        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=f"connector:{_CONNECTOR_TYPE}:{config.endpoint_identity}",
            db_pool=db_pool,
        )
        self._global_ingestion_policy = IngestionPolicyEvaluator(
            scope="global",
            db_pool=db_pool,
        )

        # Filtered event buffer
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=config.endpoint_identity,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the connector runtime: initialize, sync, and run the poll loop."""
        logger.info(
            "CalendarConnectorRuntime starting: email=%s endpoint_identity=%s",
            self._config.email,
            self._config.endpoint_identity,
        )

        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30)

        try:
            # Load ingestion policy rules (lazy background refresh after this)
            await self._ingestion_policy.ensure_loaded()
            await self._global_ingestion_policy.ensure_loaded()

            # Start heartbeat
            self._heartbeat = ConnectorHeartbeat(
                config=HeartbeatConfig.from_env(
                    connector_type=_CONNECTOR_TYPE,
                    endpoint_identity=self._config.endpoint_identity,
                ),
                mcp_client=self._mcp_client,
                metrics=self._metrics,
                get_health_state=self._get_health_state,
                get_checkpoint=self._get_checkpoint,
            )
            self._heartbeat.start()

            # Drain any pending replay items before starting live sync
            await self._drain_replay_pending()

            # Run main poll loop
            await self._run_poll_loop()

        finally:
            await self.stop()

    async def stop(self) -> None:
        """Gracefully stop the connector runtime. Idempotent: safe to call multiple times."""
        if self._stopped:
            return
        self._stopped = True
        self._running = False

        if self._heartbeat is not None:
            await self._heartbeat.stop()
            self._heartbeat = None

        # Cancel any in-flight background policy refresh tasks
        for evaluator in (self._ingestion_policy, self._global_ingestion_policy):
            bg_task = evaluator._background_refresh_task
            if bg_task is not None and not bg_task.done():
                bg_task.cancel()

        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

        await self._mcp_client.aclose()

        logger.info(
            "CalendarConnectorRuntime stopped: email=%s",
            self._config.email,
        )

    # ------------------------------------------------------------------
    # Main poll loop
    # ------------------------------------------------------------------

    async def _run_poll_loop(self) -> None:
        """Main incremental sync poll loop."""
        while self._running:
            try:
                # Ensure we have a sync token (full sync on first run).
                # Inside the retry loop so transient failures (e.g. expired
                # OAuth token that gets refreshed externally) are retried
                # instead of killing the account loop permanently.
                if self._sync_token is None:
                    await self._ensure_sync_token()
                await self._run_one_poll_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._source_api_ok = False
                self._metrics.record_error(
                    error_type=get_error_type(exc),
                    operation="poll_cycle",
                )
                logger.error(
                    "Calendar poll cycle error: email=%s error=%s",
                    self._config.email,
                    exc,
                    exc_info=True,
                )
                # Back off before retrying
                await asyncio.sleep(min(self._config.poll_interval_s, 30))
                continue

            if self._running:
                await asyncio.sleep(self._config.poll_interval_s)

    async def _run_one_poll_cycle(self) -> None:
        """Execute a single incremental sync cycle."""
        if self._sync_token is None:
            await self._ensure_sync_token()
            return

        try:
            events, next_sync_token = await self._incremental_sync(self._sync_token)
        except _SyncTokenExpiredError:
            logger.warning(
                "Calendar: syncToken expired (410 Gone) for email=%s, performing full sync",
                self._config.email,
            )
            await self._perform_full_sync(ingest_events=True)
            return

        if not events and next_sync_token == self._sync_token:
            # No changes; just update any starting-soon notifications
            await self._check_starting_soon()
            return

        # Process changed events
        ingested_count = 0
        for event in events:
            if not self._running:
                break
            ingested = await self._process_event(event)
            if ingested:
                ingested_count += 1

        # Advance checkpoint only after processing
        if next_sync_token and next_sync_token != self._sync_token:
            self._sync_token = next_sync_token
            await self._save_sync_token(next_sync_token)

        # Flush filtered events buffer
        await self._flush_filtered_events()

        # Check for upcoming "starting soon" notifications
        await self._check_starting_soon()

        if ingested_count:
            logger.debug(
                "Calendar poll cycle complete: email=%s ingested=%d",
                self._config.email,
                ingested_count,
            )

    # ------------------------------------------------------------------
    # SyncToken management
    # ------------------------------------------------------------------

    async def _ensure_sync_token(self) -> None:
        """Load syncToken from cursor store; if missing, perform full sync."""
        if self._sync_token is not None:
            return

        # Try loading from cursor store
        if self._cursor_pool is not None:
            try:
                stored_token = await load_cursor(
                    self._cursor_pool, _CONNECTOR_TYPE, self._config.endpoint_identity
                )
                if stored_token:
                    self._sync_token = stored_token
                    logger.info(
                        "Calendar: loaded syncToken from cursor store: email=%s",
                        self._config.email,
                    )
                    return
            except Exception as exc:
                logger.warning(
                    "Calendar: failed to load cursor from store for email=%s: %s",
                    self._config.email,
                    exc,
                )

        # No stored token — perform initial full sync to establish baseline
        logger.info(
            "Calendar: no syncToken found, performing initial full sync: email=%s",
            self._config.email,
        )
        await self._perform_full_sync(ingest_events=False)

    async def _save_sync_token(self, token: str) -> None:
        """Persist syncToken to cursor store."""
        if self._cursor_pool is None:
            return
        try:
            await save_cursor(
                self._cursor_pool, _CONNECTOR_TYPE, self._config.endpoint_identity, token
            )
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save("success")
            logger.debug("Calendar: saved syncToken for email=%s", self._config.email)
        except Exception as exc:
            self._metrics.record_checkpoint_save("error")
            logger.warning(
                "Calendar: failed to save cursor for email=%s: %s", self._config.email, exc
            )

    # ------------------------------------------------------------------
    # Google Calendar API calls
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        now = datetime.now(UTC)
        if (
            self._access_token is not None
            and self._token_expires_at is not None
            and now < self._token_expires_at - timedelta(seconds=60)
        ):
            return self._access_token

        # Refresh the token
        assert self._http_client is not None
        resp = await self._http_client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._config.refresh_token,
                "client_id": self._config.client_id,
                "client_secret": self._config.client_secret,
            },
            timeout=15,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed: HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = now + timedelta(seconds=expires_in)
        logger.debug("Calendar: token refreshed for email=%s", self._config.email)
        return self._access_token

    async def _gcal_api_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_method: str = "events.list",
    ) -> dict[str, Any]:
        """Call a Google Calendar API GET endpoint with retry on 429/503.

        Returns the parsed JSON response body.
        Raises _SyncTokenExpiredError on 410 Gone.
        Raises RuntimeError on unrecoverable HTTP errors.
        """
        assert self._http_client is not None
        delay = _RATE_LIMIT_BASE_DELAY_S
        for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
            token = await self._get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            url = f"{_GCAL_API_BASE}{path}"

            try:
                resp = await self._http_client.get(
                    url, headers=headers, params=params or {}, timeout=30
                )
            except httpx.TransportError as exc:
                self._metrics.record_source_api_call(api_method, "error")
                raise RuntimeError(f"Calendar API transport error: {exc}") from exc

            self._metrics.record_source_api_call(api_method, str(resp.status_code))

            if resp.status_code == 200:
                self._source_api_ok = True
                return resp.json()

            if resp.status_code == 410:
                # syncToken expired
                self._source_api_ok = True
                raise _SyncTokenExpiredError("syncToken expired (410 Gone)")

            if resp.status_code in (401, 403):
                # Clear token to force refresh next call
                self._access_token = None
                self._token_expires_at = None
                self._source_api_ok = False
                raise RuntimeError(
                    f"Calendar API auth error: HTTP {resp.status_code}: {resp.text[:200]}"
                )

            if resp.status_code in (429, 503) and attempt < _RATE_LIMIT_MAX_RETRIES:
                self._source_api_ok = False
                logger.warning(
                    "Calendar API rate limited (HTTP %d) for email=%s, retry %d/%d in %.1fs",
                    resp.status_code,
                    self._config.email,
                    attempt,
                    _RATE_LIMIT_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RATE_LIMIT_MAX_DELAY_S)
                continue

            self._source_api_ok = False
            raise RuntimeError(f"Calendar API error: HTTP {resp.status_code}: {resp.text[:200]}")

        self._source_api_ok = False
        raise RuntimeError(f"Calendar API: exhausted retries for {path}")

    async def _perform_full_sync(self, *, ingest_events: bool) -> None:
        """Perform a full calendar sync to establish syncToken.

        On first run (ingest_events=False), events are not ingested — we just
        establish the baseline syncToken. On syncToken expiry (ingest_events=True),
        events are ingested during the full sync.
        """
        logger.info(
            "Calendar: starting full sync (ingest_events=%s) for email=%s",
            ingest_events,
            self._config.email,
        )

        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
        }

        page_token: str | None = None
        all_events: list[dict[str, Any]] = []
        next_sync_token: str | None = None

        while True:
            if page_token:
                params["pageToken"] = page_token
            elif "pageToken" in params:
                del params["pageToken"]

            data = await self._gcal_api_get("/calendars/primary/events", params)

            events_page = data.get("items", [])
            all_events.extend(events_page)

            next_sync_token = data.get("nextSyncToken")
            page_token = data.get("nextPageToken")

            if not page_token:
                break

        # Populate upcoming events cache for "starting soon" detection
        self._upcoming_events.clear()
        now = datetime.now(UTC)
        window_end = now + timedelta(hours=self._config.starting_soon_window_hours)
        for event in all_events:
            event_id = event.get("id", "")
            start_dt = _parse_event_start(event)
            if start_dt and now <= start_dt <= window_end:
                self._upcoming_events[event_id] = (event, start_dt)

        if ingest_events:
            for event in all_events:
                if not self._running:
                    break
                await self._process_event(event)

        if next_sync_token:
            self._sync_token = next_sync_token
            await self._save_sync_token(next_sync_token)

        logger.info(
            "Calendar: full sync complete for email=%s: %d events, syncToken saved",
            self._config.email,
            len(all_events),
        )

    async def _incremental_sync(self, sync_token: str) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch changed events since the given syncToken.

        Returns (events, next_sync_token).
        Raises _SyncTokenExpiredError on 410 Gone.
        """
        params: dict[str, Any] = {
            "syncToken": sync_token,
            "showDeleted": "true",
        }

        all_events: list[dict[str, Any]] = []
        next_sync_token: str | None = None
        page_token: str | None = None

        while True:
            if page_token:
                params["pageToken"] = page_token
            elif "pageToken" in params:
                del params["pageToken"]

            data = await self._gcal_api_get("/calendars/primary/events", params)

            events_page = data.get("items", [])
            all_events.extend(events_page)

            next_sync_token = data.get("nextSyncToken")
            page_token = data.get("nextPageToken")

            if not page_token:
                break

        return all_events, next_sync_token

    # ------------------------------------------------------------------
    # Event processing and ingestion
    # ------------------------------------------------------------------

    async def _process_event(self, event: dict[str, Any]) -> bool:
        """Process a single calendar event: classify, evaluate policy, ingest.

        Returns True if the event was ingested, False if filtered or errored.
        """
        event_id = event.get("id", "")
        status = event.get("status", "confirmed")
        summary = event.get("summary", "(no title)")

        # Determine change type
        if status == "cancelled":
            change_type = "deleted"
        elif event.get("created") == event.get("updated"):
            change_type = "created"
        else:
            change_type = "updated"

        # Build ingest envelope fields
        observed_at = datetime.now(UTC).isoformat()
        start_dt = _parse_event_start(event)
        end_dt = _parse_event_end(event)
        organizer = event.get("organizer", {})
        organizer_email = organizer.get("email", "unknown")
        attendees = event.get("attendees", [])
        attendee_emails = [a.get("email", "") for a in attendees if a.get("email")]

        # Build normalized_text
        normalized_text = _build_normalized_text(
            event_type=change_type,
            summary=summary,
            start_dt=start_dt,
            end_dt=end_dt,
            location=event.get("location"),
            organizer_email=organizer_email,
            attendee_count=len(attendee_emails),
        )

        # Build ingestion policy envelope for pre-ingest evaluation
        policy_envelope = IngestionEnvelope(
            sender_address=organizer_email,
            source_channel=_CONNECTOR_CHANNEL,
            raw_key=event_id,
        )

        # Evaluate connector-scoped policy (synchronous — TTL refresh is background)
        try:
            decision = self._ingestion_policy.evaluate(policy_envelope)
        except Exception as exc:
            logger.warning("Calendar: policy evaluation failed for event %s: %s", event_id, exc)
            decision = None

        if decision is not None and not decision.allowed:
            self._filtered_event_buffer.record(
                external_message_id=event_id,
                source_channel=_CONNECTOR_CHANNEL,
                sender_identity=organizer_email,
                subject_or_preview=summary,
                filter_reason=FilteredEventBuffer.reason_policy_rule(
                    decision.matched_rule_type or "connector_rule",
                    decision.action,
                    decision.matched_rule_type or "unknown",
                ),
                full_payload=FilteredEventBuffer.full_payload(
                    channel=_CONNECTOR_CHANNEL,
                    provider=_CONNECTOR_PROVIDER,
                    endpoint_identity=self._config.endpoint_identity,
                    external_event_id=event_id,
                    external_thread_id=None,
                    observed_at=observed_at,
                    sender_identity=organizer_email,
                    raw=event,
                    normalized_text=normalized_text,
                ),
            )
            return False

        # Evaluate global ingestion policy
        try:
            global_decision = self._global_ingestion_policy.evaluate(policy_envelope)
        except Exception as exc:
            logger.warning(
                "Calendar: global policy evaluation failed for event %s: %s", event_id, exc
            )
            global_decision = None

        if global_decision is not None and not global_decision.allowed:
            self._filtered_event_buffer.record(
                external_message_id=event_id,
                source_channel=_CONNECTOR_CHANNEL,
                sender_identity=organizer_email,
                subject_or_preview=summary,
                filter_reason=FilteredEventBuffer.reason_policy_rule(
                    global_decision.matched_rule_type or "global_rule",
                    global_decision.action,
                    global_decision.matched_rule_type or "unknown",
                ),
                full_payload=FilteredEventBuffer.full_payload(
                    channel=_CONNECTOR_CHANNEL,
                    provider=_CONNECTOR_PROVIDER,
                    endpoint_identity=self._config.endpoint_identity,
                    external_event_id=event_id,
                    external_thread_id=None,
                    observed_at=observed_at,
                    sender_identity=organizer_email,
                    raw=event,
                    normalized_text=normalized_text,
                ),
            )
            return False

        # Update upcoming events cache for "starting soon" detection
        if start_dt is not None:
            now = datetime.now(UTC)
            window_end = now + timedelta(hours=self._config.starting_soon_window_hours)
            if change_type == "deleted":
                self._upcoming_events.pop(event_id, None)
            elif now <= start_dt <= window_end:
                self._upcoming_events[event_id] = (event, start_dt)
            else:
                self._upcoming_events.pop(event_id, None)

        # Build ingest.v1 envelope
        envelope = _build_ingest_envelope(
            event_id=event_id,
            change_type=change_type,
            summary=summary,
            event=event,
            endpoint_identity=self._config.endpoint_identity,
            observed_at=observed_at,
            organizer_email=organizer_email,
            normalized_text=normalized_text,
        )

        return await self._submit_envelope(event_id, envelope, summary)

    async def _submit_envelope(self, event_id: str, envelope: dict[str, Any], preview: str) -> bool:
        """Submit an ingest.v1 envelope to the Switchboard."""
        try:
            async with self._semaphore:
                await self._submit_to_ingest_api(envelope)

            self._metrics.record_ingest_submission("success")
            self._last_ingest_submit = time.time()
            logger.debug(
                "Calendar: ingested event %s (%r) for email=%s",
                event_id,
                preview,
                self._config.email,
            )
            return True

        except Exception as exc:
            self._metrics.record_ingest_submission("error")
            self._metrics.record_error(
                error_type=get_error_type(exc),
                operation="ingest_submit",
            )
            # Record in filtered buffer for replay
            self._filtered_event_buffer.record(
                external_message_id=event_id,
                source_channel=_CONNECTOR_CHANNEL,
                sender_identity="",
                subject_or_preview=preview,
                filter_reason=FilteredEventBuffer.reason_submission_error(),
                status="error",
                error_detail=str(exc),
                full_payload=FilteredEventBuffer.full_payload(
                    channel=_CONNECTOR_CHANNEL,
                    provider=_CONNECTOR_PROVIDER,
                    endpoint_identity=self._config.endpoint_identity,
                    external_event_id=event_id,
                    external_thread_id=None,
                    observed_at=datetime.now(UTC).isoformat(),
                    sender_identity="",
                    raw=envelope.get("payload", {}).get("raw", {}),
                    normalized_text=envelope.get("payload", {}).get("normalized_text"),
                ),
            )
            logger.warning(
                "Calendar: failed to ingest event %s for email=%s: %s",
                event_id,
                self._config.email,
                exc,
            )
            return False

    async def _submit_to_ingest_api(self, envelope: dict[str, Any]) -> None:
        """Submit the ingest.v1 envelope to the Switchboard via MCP."""
        result = await self._mcp_client.call_tool("ingest", envelope)
        if not isinstance(result, dict):
            raise RuntimeError(
                f"Switchboard returned non-dict result for ingest envelope: result={result!r}"
            )
        status = result.get("status", "")
        if not status:
            raise RuntimeError(
                f"Switchboard result missing or empty status for ingest envelope: result={result!r}"
            )
        if status not in ("accepted", "duplicate", "queued"):
            raise RuntimeError(
                f"Switchboard rejected envelope: status={status!r}, result={result!r}"
            )

    # ------------------------------------------------------------------
    # "Starting soon" synthetic notifications
    # ------------------------------------------------------------------

    async def _check_starting_soon(self) -> None:
        """Check upcoming events and emit "starting soon" notifications."""
        now = datetime.now(UTC)

        # Prune past events from the seen-set
        pruned = self._seen_set.prune(now)
        if pruned:
            logger.debug(
                "Calendar: pruned %d past entries from starting-soon seen-set for email=%s",
                pruned,
                self._config.email,
            )

        # Prune stale upcoming events cache
        to_remove = [eid for eid, (_, start_dt) in self._upcoming_events.items() if start_dt < now]
        for eid in to_remove:
            del self._upcoming_events[eid]

        for event_id, (event, start_dt) in list(self._upcoming_events.items()):
            summary = event.get("summary", "(no title)")
            for lead_minutes in self._config.all_lead_minutes:
                notify_at = start_dt - timedelta(minutes=lead_minutes)

                # Fire if we're within one poll interval of notify_at
                # (to handle restarts and avoid double-firing)
                lookahead = timedelta(seconds=self._config.poll_interval_s)
                if notify_at <= now + lookahead and now <= start_dt:
                    if self._seen_set.has_seen(event_id, lead_minutes):
                        continue

                    # Emit "starting soon" envelope via the canonical builder so it
                    # carries the spec external_event_id ("starting_soon:<event_id>") and
                    # a stable control.idempotency_key alongside the full event payload.
                    envelope = build_starting_soon_envelope(
                        event,
                        lead_minutes=lead_minutes,
                        endpoint_identity=self._config.endpoint_identity,
                    )

                    try:
                        await self._submit_to_ingest_api(envelope)
                        self._seen_set.mark_seen(event_id, lead_minutes, start_dt)
                        self._last_ingest_submit = time.time()
                        self._metrics.record_ingest_submission("success")
                        logger.info(
                            "Calendar: 'starting soon' notification sent: "
                            "email=%s event_id=%s lead_minutes=%d summary=%r",
                            self._config.email,
                            event_id,
                            lead_minutes,
                            summary,
                        )
                    except Exception as exc:
                        self._metrics.record_ingest_submission("error")
                        logger.warning(
                            "Calendar: failed to send 'starting soon' for event %s: %s",
                            event_id,
                            exc,
                        )

    # ------------------------------------------------------------------
    # Filtered events + replay
    # ------------------------------------------------------------------

    async def _flush_filtered_events(self) -> None:
        """Flush accumulated filtered events to the DB."""
        if self._db_pool is None:
            return
        if len(self._filtered_event_buffer) == 0:
            return
        await self._filtered_event_buffer.flush(self._db_pool)

    async def _drain_replay_pending(self) -> None:
        """Drain replay_pending events from connectors.filtered_events."""
        if self._db_pool is None:
            return
        try:
            await drain_replay_pending(
                pool=self._db_pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=self._config.endpoint_identity,
                submit_fn=self._submit_to_ingest_api,
                drain_logger=logger,
            )
        except Exception as exc:
            logger.warning(
                "Calendar: replay drain failed for email=%s: %s",
                self._config.email,
                exc,
            )

    # ------------------------------------------------------------------
    # Health state callbacks
    # ------------------------------------------------------------------

    def _get_health_state(self) -> tuple[str, str | None]:
        """Return (state, error_message) tuple for heartbeat."""
        if self._source_api_ok is None:
            return "degraded", "Starting up, API not yet checked"
        if self._source_api_ok:
            return "healthy", None
        return "error", "Google Calendar API is not reachable"

    def _get_checkpoint(self) -> tuple[str | None, datetime | None]:
        """Return (cursor, updated_at) tuple for heartbeat."""
        checkpoint_ts: datetime | None = None
        if self._last_checkpoint_save is not None:
            checkpoint_ts = datetime.fromtimestamp(self._last_checkpoint_save, UTC)
        return self._sync_token, checkpoint_ts


# ---------------------------------------------------------------------------
# CalendarAccountLoop — wraps runtime in an isolated asyncio task
# ---------------------------------------------------------------------------


class CalendarAccountLoop:
    """Per-account Google Calendar ingestion loop.

    Wraps a CalendarConnectorRuntime for a single Google account.
    Runs as an independent asyncio task; errors are isolated from other accounts.
    """

    def __init__(
        self,
        email: str,
        config: CalendarAccountConfig,
        db_pool: asyncpg.Pool | None,
        cursor_pool: asyncpg.Pool | None,
    ) -> None:
        self.email = email
        self.endpoint_identity = config.endpoint_identity
        self._config = config
        self._runtime = CalendarConnectorRuntime(config, db_pool=db_pool, cursor_pool=cursor_pool)
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None

    def start(self) -> None:
        """Launch the per-account ingestion loop as an asyncio task."""
        self._task = asyncio.create_task(self._run(), name=f"gcal-account-{self.email}")
        self._task.add_done_callback(self._on_done)

    async def _run(self) -> None:
        try:
            logger.info(
                "Calendar account loop starting: email=%s endpoint_identity=%s",
                self.email,
                self.endpoint_identity,
            )
            await self._runtime.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            logger.error(
                "Calendar account loop failed: email=%s error=%s",
                self.email,
                exc,
                exc_info=True,
            )
            raise

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                self._error = str(exc)

    async def stop(self) -> None:
        """Gracefully stop the account loop: complete in-flight, checkpoint, stop."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        await self._runtime.stop()
        logger.info("Calendar account loop stopped: email=%s", self.email)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_health(self) -> AccountHealthStatus:
        """Return per-account health snapshot."""
        runtime = self._runtime

        last_checkpoint_save_at = None
        if runtime._last_checkpoint_save is not None:
            last_checkpoint_save_at = datetime.fromtimestamp(
                runtime._last_checkpoint_save, UTC
            ).isoformat()

        last_ingest_submit_at = None
        if runtime._last_ingest_submit is not None:
            last_ingest_submit_at = datetime.fromtimestamp(
                runtime._last_ingest_submit, UTC
            ).isoformat()

        if runtime._source_api_ok is None:
            connectivity: Literal["connected", "disconnected", "unknown"] = "unknown"
        elif runtime._source_api_ok:
            connectivity = "connected"
        else:
            connectivity = "disconnected"

        error_msg = self._error
        if not self.is_running and error_msg:
            account_status: Literal["healthy", "degraded", "error"] = "error"
        elif runtime._source_api_ok is False:
            account_status = "error"
        else:
            account_status = "healthy"

        return AccountHealthStatus(
            email=_redact_email(self.email),
            endpoint_identity=self.endpoint_identity,
            status=account_status,
            last_checkpoint_save_at=last_checkpoint_save_at,
            last_ingest_submit_at=last_ingest_submit_at,
            source_api_connectivity=connectivity,
            error=error_msg,
        )


# ---------------------------------------------------------------------------
# CalendarConnectorManager — top-level multi-account orchestrator
# ---------------------------------------------------------------------------


class CalendarConnectorManager:
    """Top-level orchestrator for the multi-account Google Calendar connector.

    Discovers all active Google accounts with calendar scopes from
    public.google_accounts, spawns independent CalendarAccountLoop instances
    per account, and manages their lifecycle.

    Supports:
    - Periodic account re-scan at GCAL_ACCOUNT_RESCAN_INTERVAL_S (default 300)
    - On-demand reload via SIGHUP
    - Aggregated health endpoint across all accounts (/health, /metrics)
    - Degraded startup when no qualifying accounts found
    - Per-account error isolation
    """

    def __init__(
        self,
        process_config: CalendarProcessConfig,
        db_pool: asyncpg.Pool,
        cursor_pool: asyncpg.Pool | None,
    ) -> None:
        self._process_config = process_config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool

        # Active account loops keyed by email
        self._loops: dict[str, CalendarAccountLoop] = {}

        # State
        self._start_time = time.time()
        self._running = False
        self._reload_event = asyncio.Event()
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Credential store (shared across accounts for app creds)
        self._credential_store: CredentialStore | None = None

    def _get_credential_store(self) -> CredentialStore:
        """Return or initialize the CredentialStore for app credentials."""
        if self._credential_store is None:
            self._credential_store = CredentialStore(self._db_pool)
        return self._credential_store

    async def _discover_qualifying_accounts(
        self,
    ) -> list[tuple[str | None, dict[str, Any] | None]]:
        """Query public.google_accounts for active accounts with calendar scope.

        Returns list of (email, metadata_calendar) tuples where metadata_calendar
        is the parsed ``calendar`` subsection of the account's metadata JSONB column.
        Only accounts with status='active' and calendar scope in granted_scopes are returned.
        """
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT email, granted_scopes, metadata
                    FROM public.google_accounts
                    WHERE status = 'active'
                    ORDER BY is_primary DESC, connected_at ASC
                    """
                )
        except Exception as exc:
            logger.warning("Calendar manager: failed to query google_accounts (non-fatal): %s", exc)
            return []

        qualifying = []
        for row in rows:
            email = row["email"]
            scopes = list(row["granted_scopes"] or [])
            metadata = row["metadata"] or {}

            has_calendar_scope = any(s in (_GCAL_SCOPE, _GCAL_SCOPE_READONLY) for s in scopes)
            if not has_calendar_scope:
                logger.debug(
                    "Calendar manager: skipping account %r — no calendar scope in "
                    "granted_scopes=%s",
                    email,
                    scopes,
                )
                continue

            metadata_calendar: dict[str, Any] | None = None
            if isinstance(metadata, dict):
                cal_section = metadata.get("calendar")
                if isinstance(cal_section, dict):
                    metadata_calendar = cal_section

            qualifying.append((email, metadata_calendar))

        return qualifying

    async def _resolve_credentials_for_account(
        self,
        email: str,
    ) -> dict[str, str] | None:
        """Resolve OAuth credentials for a single Google account.

        Returns dict with client_id, client_secret, refresh_token on success.
        Returns None if credentials cannot be resolved (account is skipped).
        """
        try:
            store = self._get_credential_store()
            creds = await load_google_credentials(store, pool=self._db_pool, account=email)
            if creds is None:
                logger.warning(
                    "Calendar manager: no credentials found for account %r — skipping", email
                )
                return None
            return {
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "refresh_token": creds.refresh_token,
            }
        except InvalidGoogleCredentialsError as exc:
            logger.warning(
                "Calendar manager: invalid credentials for account %r (skipping): %s",
                email,
                exc,
            )
            return None
        except Exception as exc:
            logger.warning(
                "Calendar manager: credential resolution failed for account %r (skipping): %s",
                email,
                exc,
            )
            return None

    async def _sync_accounts(
        self,
    ) -> tuple[list[str], list[str], list[str]]:
        """Discover qualifying accounts and reconcile running loops.

        Returns (added, removed, unchanged) email lists.
        """
        qualifying = await self._discover_qualifying_accounts()

        # Compute desired set
        desired_emails: set[str] = set()
        account_metadata: dict[str, dict[str, Any] | None] = {}
        for email, metadata_calendar in qualifying:
            if email is None:
                continue
            desired_emails.add(email)
            account_metadata[email] = metadata_calendar

        current_emails = set(self._loops.keys())

        # Detect dead loops: account still in self._loops but task has finished
        # (e.g. crashed due to token error).  Remove them so they get re-added.
        dead_emails: set[str] = set()
        for email in current_emails & desired_emails:
            loop = self._loops[email]
            if not loop.is_running:
                logger.warning(
                    "Calendar manager: account loop for %r is dead (error=%s) — will restart",
                    email,
                    loop._error,
                )
                await loop.stop()
                del self._loops[email]
                dead_emails.add(email)

        current_emails -= dead_emails
        to_add = (desired_emails - current_emails) | dead_emails
        to_remove = current_emails - desired_emails
        unchanged = current_emails & desired_emails

        # Stop removed loops (graceful: complete in-flight, checkpoint, stop)
        for email in to_remove:
            loop = self._loops.pop(email)
            logger.info("Calendar manager: stopping loop for removed account %r", email)
            await loop.stop()

        # Start new loops
        added: list[str] = []
        for email in to_add:
            creds = await self._resolve_credentials_for_account(email)
            if creds is None:
                continue

            metadata_calendar = account_metadata.get(email)
            try:
                account_config = self._process_config.make_account_config(
                    email=email,
                    client_id=creds["client_id"],
                    client_secret=creds["client_secret"],
                    refresh_token=creds["refresh_token"],
                    metadata_calendar=metadata_calendar,
                )
            except Exception as exc:
                logger.warning(
                    "Calendar manager: failed to build config for account %r (skipping): %s",
                    email,
                    exc,
                )
                continue

            loop = CalendarAccountLoop(
                email=email,
                config=account_config,
                db_pool=self._db_pool,
                cursor_pool=self._cursor_pool,
            )
            self._loops[email] = loop
            loop.start()
            added.append(email)
            logger.info("Calendar manager: started loop for new account %r", email)

        return added, list(to_remove), list(unchanged)

    def _start_health_server(self) -> None:
        """Start aggregated health/metrics endpoint in background thread."""
        app = FastAPI(title="Google Calendar Connector Health")

        @app.get("/health")
        async def health() -> MultiAccountHealthStatus:
            return self._get_multi_account_health()

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest(REGISTRY)

        @app.post("/reload")
        async def reload_accounts() -> dict[str, Any]:
            """Trigger immediate account re-scan."""
            if self._main_loop is not None and self._main_loop.is_running():
                self._main_loop.call_soon_threadsafe(self._reload_event.set)
            return {"status": "reload_triggered"}

        port = self._process_config.health_port
        sock = make_health_socket("127.0.0.1", port)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        self._health_server = uvicorn.Server(config)

        def run_server() -> None:
            asyncio.run(self._health_server.serve(sockets=[sock]))

        self._health_thread = Thread(target=run_server, daemon=True)
        self._health_thread.start()
        logger.info("Calendar manager: health server started on port %d", port)

    def _get_multi_account_health(self) -> MultiAccountHealthStatus:
        """Build aggregated health status across all account loops (worst-case)."""
        uptime = time.time() - self._start_time
        account_statuses = [loop.get_health() for loop in self._loops.values()]

        # Worst-case aggregation
        overall: Literal["healthy", "degraded", "error"] = "healthy"
        if not account_statuses:
            overall = "degraded"  # No accounts → degraded idle mode
        else:
            statuses = [a.status for a in account_statuses]
            if "error" in statuses:
                overall = "error"
            elif "degraded" in statuses:
                overall = "degraded"

        return MultiAccountHealthStatus(
            status=overall,
            uptime_seconds=uptime,
            active_accounts=len(self._loops),
            account_health=account_statuses,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _setup_sighup(self) -> None:
        """Register SIGHUP handler to trigger immediate account re-scan."""
        try:
            loop = asyncio.get_event_loop()

            def _on_sighup() -> None:
                logger.info("Calendar manager: SIGHUP received — triggering account reload")
                self._reload_event.set()

            loop.add_signal_handler(signal.SIGHUP, _on_sighup)
        except (OSError, NotImplementedError):
            logger.debug("Calendar manager: SIGHUP not available on this platform")

    async def start(self) -> None:
        """Start the connector manager: discover accounts, start loops, run rescan loop."""
        self._running = True
        self._main_loop = asyncio.get_running_loop()

        # Start health server
        self._start_health_server()

        # Register SIGHUP for reload
        self._setup_sighup()

        # Initial account discovery
        added, removed, unchanged = await self._sync_accounts()
        logger.info(
            "Calendar manager: initial account sync — added=%d removed=%d unchanged=%d",
            len(added),
            len(removed),
            len(unchanged),
        )

        if not self._loops:
            logger.warning(
                "Calendar manager: no qualifying accounts found at startup. "
                "Running in idle/degraded mode. Will retry at rescan interval=%ds.",
                self._process_config.account_rescan_interval_s,
            )

        # Wait for Switchboard readiness
        try:
            await wait_for_switchboard_ready(self._process_config.switchboard_mcp_url)
        except TimeoutError:
            logger.warning(
                "Calendar manager: Switchboard readiness probe timed out; proceeding anyway."
            )

        # Main rescan loop
        try:
            await self._run_rescan_loop()
        finally:
            await self.stop()

    async def _run_rescan_loop(self) -> None:
        """Periodically re-scan for account changes, also triggered by reload events."""
        rescan_interval = self._process_config.account_rescan_interval_s
        while self._running:
            try:
                await asyncio.wait_for(self._reload_event.wait(), timeout=rescan_interval)
                logger.info("Calendar manager: reload triggered — re-scanning accounts")
                self._reload_event.clear()
            except TimeoutError:
                logger.debug("Calendar manager: periodic re-scan triggered")

            if not self._running:
                break

            added, removed, unchanged = await self._sync_accounts()
            if added or removed:
                logger.info(
                    "Calendar manager: account sync — added=%s removed=%s unchanged=%d",
                    added,
                    removed,
                    len(unchanged),
                )

    async def stop(self) -> None:
        """Gracefully stop all account loops and the manager."""
        self._running = False

        for email, loop in list(self._loops.items()):
            logger.info("Calendar manager: stopping loop for %r", email)
            await loop.stop()
        self._loops.clear()

        logger.info("Calendar manager: all account loops stopped")


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------


class _SyncTokenExpiredError(Exception):
    """Raised when Google Calendar API returns 410 Gone (syncToken expired)."""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _parse_event_start(event: dict[str, Any]) -> datetime | None:
    """Parse event start time from a Google Calendar event dict."""
    start = event.get("start", {})
    dt_str = start.get("dateTime") or start.get("date")
    if not dt_str:
        return None
    return _parse_dt(dt_str)


def _parse_event_end(event: dict[str, Any]) -> datetime | None:
    """Parse event end time from a Google Calendar event dict."""
    end = event.get("end", {})
    dt_str = end.get("dateTime") or end.get("date")
    if not dt_str:
        return None
    return _parse_dt(dt_str)


def _parse_dt(dt_str: str) -> datetime | None:
    """Parse an ISO-8601 datetime string to an aware datetime.

    Handles:
    - All-day events (YYYY-MM-DD): returns midnight UTC on that date.
    - RFC3339 with Z suffix (2026-04-01T10:00:00Z): normalizes Z→+00:00.
    - Naive datetimes (no timezone info): assumed UTC.
    - Fractional seconds: handled by fromisoformat.
    """
    if not dt_str:
        return None
    try:
        # All-day events have date-only format: "YYYY-MM-DD"
        if len(dt_str) == 10:
            return datetime(
                int(dt_str[0:4]),
                int(dt_str[5:7]),
                int(dt_str[8:10]),
                tzinfo=UTC,
            )
        # Strip 'Z' and replace with +00:00 for fromisoformat compat (handles Z suffix)
        normalized = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        # Assume UTC for naive datetimes (no timezone info)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, IndexError):
        return None


def _format_dt_utc(dt: datetime | None) -> str:
    """Format a datetime as ``YYYY-MM-DD HH:MM UTC``, or ``?`` when absent."""
    if dt is None:
        return "?"
    dt_utc = dt.astimezone(UTC) if dt.tzinfo is not None else dt
    return dt_utc.strftime("%Y-%m-%d %H:%M UTC")


def _build_normalized_text(
    *,
    event_type: str,
    summary: str,
    start_dt: datetime | None,
    end_dt: datetime | None,
    location: str | None,
    organizer_email: str,
    attendee_count: int,
) -> str:
    """Build a structured normalized_text string per the Google Calendar spec.

    Per connector-google-calendar/spec.md §Scenario: Normalized text format, the
    canonical shape is::

        "[Calendar: <event_type>] <title> | <start> - <end> | <location>
         | <attendee_count> attendees | Organizer: <organizer>"

    where ``event_type`` is one of ``created``, ``updated``, ``deleted`` or
    ``starting_soon``. ``location`` falls back to ``(no location)`` when absent.
    """
    location_str = location.strip() if location and location.strip() else "(no location)"
    organizer_str = organizer_email or "unknown"
    return (
        f"[Calendar: {event_type}] {summary}"
        f" | {_format_dt_utc(start_dt)} - {_format_dt_utc(end_dt)}"
        f" | {location_str}"
        f" | {attendee_count} attendees"
        f" | Organizer: {organizer_str}"
    )


def _build_ingest_envelope(
    *,
    event_id: str,
    change_type: str,
    summary: str,
    event: dict[str, Any],
    endpoint_identity: str,
    observed_at: str,
    organizer_email: str,
    normalized_text: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a calendar event.

    Per spec connector-google-calendar §ingest.v1 Field Mapping:
    - ``control.idempotency_key`` = ``"gcal:<endpoint_identity>:<event_id>:<updated>"``
      (canonical, event-ID + Google ``updated`` timestamp derived) so re-ingesting
      the same event revision dedups deterministically rather than by payload hash.
    - ``control.ingestion_tier`` = ``"full"``.
    - ``event.external_thread_id`` = the event ID (events are their own thread).
    """
    updated = event.get("updated") or observed_at
    idempotency_key = f"gcal:{endpoint_identity}:{event_id}:{updated}"
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": event_id,
            "external_thread_id": event_id,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": organizer_email,
        },
        "payload": {
            "raw": event,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "ingestion_tier": "full",
            "policy_tier": "default",
        },
    }


def _redact_email(email: str | None) -> str | None:
    """Redact an email address for safe inclusion in health responses.

    Shows the first 2 characters of the local part, then ***, then @domain.
    """
    if email is None:
        return None
    at_pos = email.find("@")
    if at_pos <= 0:
        return "***"
    local = email[:at_pos]
    domain = email[at_pos:]
    visible = local[:2]
    return f"{visible}***{domain}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_google_calendar_connector() -> None:
    """Main async entry point for the Google Calendar connector."""
    configure_logging()
    logger.info("Google Calendar connector starting")

    process_config = CalendarProcessConfig.from_env()

    # Set up DB pools
    import asyncpg

    db_params = db_params_from_env()
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"
    shared_db_name = shared_db_name_from_env()

    # Create DB pool for credentials and policy rules
    db_pool: asyncpg.Pool | None = None
    try:
        pool_kwargs: dict[str, Any] = {
            "host": str(db_params.get("host") or "localhost"),
            "port": int(db_params.get("port") or 5432),
            "user": str(db_params.get("user") or "butlers"),
            "password": str(db_params.get("password") or "butlers"),
            "database": shared_db_name,
            "min_size": 1,
            "max_size": 5,
            "command_timeout": 10,
        }
        ssl = db_params.get("ssl")
        if ssl is not None:
            pool_kwargs["ssl"] = ssl
        pool_kwargs["setup"] = connector_setup_role

        try:
            db_pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if should_retry_with_ssl_disable(exc, pool_kwargs.get("ssl")):
                pool_kwargs["ssl"] = "disable"
                db_pool = await asyncpg.create_pool(**pool_kwargs)
            else:
                raise

        logger.info("Calendar connector: DB pool established (db=%s)", shared_db_name)
    except Exception as exc:
        logger.warning(
            "Calendar connector: DB pool failed (credentials and policy unavailable): %s",
            exc,
        )
        db_pool = None

    # Create cursor pool (may reuse same DB or a separate one)
    cursor_pool: asyncpg.Pool | None = None
    try:
        from butlers.connectors.cursor_store import create_cursor_pool

        cursor_params = db_params_from_env()
        cursor_pool = await create_cursor_pool(
            host=str(cursor_params.get("host") or "localhost"),
            port=int(cursor_params.get("port") or 5432),
            user=str(cursor_params.get("user") or "butlers"),
            password=str(cursor_params.get("password") or "butlers"),
            database=local_db_name,
            ssl=str(cursor_params["ssl"]) if cursor_params.get("ssl") is not None else None,
        )
        logger.info("Calendar connector: cursor pool established (db=%s)", local_db_name)
    except Exception as exc:
        logger.warning(
            "Calendar connector: cursor pool failed (checkpoint persistence unavailable): %s",
            exc,
        )
        cursor_pool = None

    try:
        manager = CalendarConnectorManager(
            process_config=process_config,
            db_pool=db_pool or _NullPool(),  # type: ignore[arg-type]
            cursor_pool=cursor_pool,
        )
        await manager.start()
    finally:
        if cursor_pool is not None:
            await cursor_pool.close()
        if db_pool is not None:
            await db_pool.close()


class _NullPool:
    """Minimal stub for asyncpg.Pool when DB is unavailable.

    Methods raise immediately so callers get explicit errors rather than
    AttributeError or hung futures. Only the minimal surface used by the
    connector manager and runtime is stubbed out.
    """

    def acquire(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "Calendar connector: DB pool is unavailable. "
            "Set DATABASE_URL or POSTGRES_* env vars to enable credential resolution."
        )

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Calendar connector: DB pool is unavailable.")

    async def fetch(self, *args: Any, **kwargs: Any) -> list:
        return []

    async def fetchrow(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def close(self) -> None:
        pass


def main() -> None:
    """Synchronous entry point for use as a console script."""
    asyncio.run(run_google_calendar_connector())


if __name__ == "__main__":
    main()
