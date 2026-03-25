"""Google Calendar connector runtime — incremental sync and ingestion loop.

Implements tasks 3.1–3.6 from openspec/changes/connector-google-calendar/tasks.md:
- Initial full sync (no syncToken) to establish baseline
- Incremental sync poll using events.list(syncToken=...) with pagination
- Expired syncToken handling (410 Gone → full resync fallback)
- Event change classification (created/updated/deleted)
- ingest.v1 envelope normalization with idempotency key
- Checkpoint-after-acceptance cursor advancement

Multi-account architecture mirrors the Gmail connector:
- Discover accounts from shared.google_accounts (status='active', 'calendar' in granted_scopes)
- Resolve OAuth credentials per-account from butler_secrets + entity_info
- Spawn independent asyncio poll loops per account
- Dynamic account discovery via periodic re-scan (GCAL_ACCOUNT_RESCAN_INTERVAL_S, default 300s)

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=google_calendar (required)
- CONNECTOR_CHANNEL=google_calendar (required)
- DATABASE_URL or POSTGRES_* (DB connectivity; defaults apply if unset)
- GCAL_POLL_INTERVAL_S (optional, default 60)
- GCAL_STARTING_SOON_LEAD_MINUTES (optional, default 15)
- GCAL_ACCOUNT_RESCAN_INTERVAL_S (optional, default 300)
- CONNECTOR_MAX_INFLIGHT (optional, default 8)
- CONNECTOR_HEALTH_PORT (optional, default 40084)
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from datetime import UTC, datetime, timedelta
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import asyncpg

import httpx
import uvicorn
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest
from pydantic import BaseModel, ConfigDict

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics, get_error_type
from butlers.core.logging import configure_logging
from butlers.credential_store import shared_db_name_from_env
from butlers.db import db_params_from_env, should_retry_with_ssl_disable
from butlers.google_credentials import (
    InvalidGoogleCredentialsError,
    load_google_credentials,
)
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "google_calendar"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"
_DEFAULT_POLL_INTERVAL_S = 60
_DEFAULT_STARTING_SOON_LEAD_MINUTES = 15
_DEFAULT_ACCOUNT_RESCAN_INTERVAL_S = 300
_DEFAULT_MAX_INFLIGHT = 8
_DEFAULT_HEALTH_PORT = 40084
_DEFAULT_HEARTBEAT_INTERVAL_S = 120

# HTTP status indicating syncToken has expired
_HTTP_GONE = 410

# Exponential backoff configuration
_BACKOFF_INITIAL_S = 5.0
_BACKOFF_MAX_S = 300.0
_BACKOFF_MULTIPLIER = 2.0

# Calendar scope name to check in granted_scopes
_CALENDAR_SCOPE = "calendar"

# ---------------------------------------------------------------------------
# Health status models
# ---------------------------------------------------------------------------


class AccountHealthStatus(BaseModel):
    """Per-account health status for multi-account connectors."""

    email: str | None
    endpoint_identity: str
    status: Literal["healthy", "degraded", "error"]
    last_checkpoint_save_at: str | None
    last_sync_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    error: str | None = None


class MultiAccountHealthStatus(BaseModel):
    """Aggregated health status for the multi-account Google Calendar connector."""

    status: Literal["healthy", "degraded", "error"]
    uptime_seconds: float
    active_accounts: int
    account_health: list[AccountHealthStatus]
    timestamp: str


# ---------------------------------------------------------------------------
# Cursor model
# ---------------------------------------------------------------------------


class GCalCursor(BaseModel):
    """Persistent cursor for Google Calendar sync."""

    model_config = ConfigDict(extra="forbid")

    sync_token: str
    last_updated_at: str  # ISO 8601 timestamp

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> GCalCursor:
        return cls.model_validate_json(raw)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------


async def _refresh_access_token(
    client: httpx.AsyncClient,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str:
    """Exchange a refresh token for a fresh access token.

    Returns the access token string.

    Raises
    ------
    RuntimeError
        If the token endpoint returns a non-2xx response.
    """
    resp = await client.post(
        _GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed: HTTP {resp.status_code} — {resp.text[:200]}")
    body = resp.json()
    access_token = body.get("access_token")
    if not access_token:
        raise RuntimeError(f"Token refresh returned no access_token: {body}")
    return access_token


# ---------------------------------------------------------------------------
# Event normalization
# ---------------------------------------------------------------------------


def _classify_event(event: dict[str, Any], *, is_initial_sync: bool) -> str:
    """Classify a Google Calendar event change.

    Returns one of: 'event_created', 'event_updated', 'event_deleted'.

    Per spec §Event Change Classification:
    - status='cancelled' → event_deleted
    - non-cancelled on initial sync → event_created (but these are not ingested for baseline)
    - non-cancelled on incremental sync → event_updated
      (we cannot determine new vs. known without a local cache; default to updated)
    """
    if event.get("status") == "cancelled":
        return "event_deleted"
    if is_initial_sync:
        return "event_created"
    return "event_updated"


def _extract_organizer_email(event: dict[str, Any], account_email: str) -> str:
    """Extract the organizer email, defaulting to the account email."""
    organizer = event.get("organizer", {})
    email = organizer.get("email", "").strip()
    return email.lower() if email else account_email.lower()


def _format_event_time(time_obj: dict[str, Any] | None) -> str:
    """Format a Google Calendar event time object to a human-readable string."""
    if not time_obj:
        return "unknown"
    # dateTime for timed events, date for all-day events
    dt = time_obj.get("dateTime") or time_obj.get("date") or ""
    return dt


def _build_normalized_text(event: dict[str, Any], event_type: str) -> str:
    """Construct the normalized_text payload field.

    Format::

        [Calendar: <type>] <title> | <start> - <end> | <location> | <n> attendees | Organizer: <org>
    """
    # Map internal event_type to display label
    display_type_map = {
        "event_created": "created",
        "event_updated": "updated",
        "event_deleted": "deleted",
        "event_starting_soon": "starting_soon",
    }
    display_type = display_type_map.get(event_type, event_type)

    title = event.get("summary", "(no title)")
    start = _format_event_time(event.get("start"))
    end = _format_event_time(event.get("end"))
    location = event.get("location", "")
    attendees = event.get("attendees", [])
    attendee_count = len(attendees) if attendees else 0
    organizer = event.get("organizer", {})
    organizer_email = organizer.get("email", "unknown")

    parts = [
        f"[Calendar: {display_type}] {title}",
        f"{start} - {end}",
    ]
    if location:
        parts.append(location)
    parts.append(f"{attendee_count} attendees")
    parts.append(f"Organizer: {organizer_email}")

    return " | ".join(parts)


def _build_ingest_envelope(
    event: dict[str, Any],
    event_type: str,
    endpoint_identity: str,
    account_email: str,
    observed_at: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope dict for a Google Calendar event change.

    Per spec §ingest.v1 Field Mapping.
    """
    event_id = event.get("id", "")
    updated_timestamp = event.get("updated", observed_at)
    organizer_email = _extract_organizer_email(event, account_email)
    normalized_text = _build_normalized_text(event, event_type)

    idempotency_key = f"gcal:{endpoint_identity}:{event_id}:{updated_timestamp}"

    return {
        "source": {
            "channel": "google_calendar",
            "provider": "google_calendar",
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "event_type": event_type,
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


def _build_starting_soon_envelope(
    event: dict[str, Any],
    endpoint_identity: str,
    account_email: str,
    observed_at: str,
    lead_minutes: int,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for an 'event starting soon' notification.

    Per spec §Starting soon event field mapping.
    """
    event_id = event.get("id", "")
    organizer_email = _extract_organizer_email(event, account_email)
    normalized_text = _build_normalized_text(event, "event_starting_soon")

    idempotency_key = f"gcal:{endpoint_identity}:starting_soon:{event_id}:{lead_minutes}"

    return {
        "source": {
            "channel": "google_calendar",
            "provider": "google_calendar",
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "event_type": "event_starting_soon",
            "external_event_id": f"starting_soon:{event_id}",
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
            "policy_tier": "interactive",
        },
    }


# ---------------------------------------------------------------------------
# Sync loop core
# ---------------------------------------------------------------------------


class GCalSyncLoop:
    """Per-account Google Calendar sync and ingestion loop.

    Handles:
    - Initial full sync (no syncToken) — persists nextSyncToken, skips ingestion
    - Incremental sync with pagination — ingests changed events
    - Expired syncToken fallback (HTTP 410) — full resync with ingestion
    - Checkpoint-after-acceptance cursor advancement
    - "Event starting soon" notification synthesis
    """

    def __init__(
        self,
        email: str,
        endpoint_identity: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        cursor_pool: asyncpg.Pool | None,
        mcp_client: CachedMCPClient,
        *,
        poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S,
        starting_soon_lead_minutes: int = _DEFAULT_STARTING_SOON_LEAD_MINUTES,
        calendar_ids: list[str] | None = None,
        policy_evaluator: IngestionPolicyEvaluator | None = None,
    ) -> None:
        self.email = email
        self.endpoint_identity = endpoint_identity
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._cursor_pool = cursor_pool
        self._mcp_client = mcp_client
        self._poll_interval_s = poll_interval_s
        self._starting_soon_lead_minutes = starting_soon_lead_minutes
        self._calendar_ids = calendar_ids or ["primary"]
        self._policy_evaluator = policy_evaluator

        # Cached access token and its expiry time
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

        # Runtime state
        self._running = False
        self._last_sync_at: float | None = None
        self._last_checkpoint_save: float | None = None
        self._source_api_ok: bool | None = None
        self._error: str | None = None

        # "Starting soon" seen-set: keyed by (event_id, lead_minutes)
        self._starting_soon_seen: set[tuple[str, int]] = set()
        # Upcoming event cache: event_id → event dict
        self._upcoming_events: dict[str, dict[str, Any]] = {}

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=endpoint_identity,
        )

    async def _ensure_access_token(self, client: httpx.AsyncClient) -> str:
        """Return a valid access token, refreshing if expired."""
        now = time.time()
        # Refresh 60 seconds before expiry
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        logger.debug("Refreshing access token for %s", self.email)
        token = await _refresh_access_token(
            client, self._client_id, self._client_secret, self._refresh_token
        )
        self._access_token = token
        # Tokens typically valid for 1 hour; use conservative 3590s
        self._token_expires_at = now + 3590
        return token

    async def _list_events(
        self,
        client: httpx.AsyncClient,
        calendar_id: str,
        *,
        sync_token: str | None = None,
        page_token: str | None = None,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Call Google Calendar events.list.

        Returns the raw JSON response dict.

        Raises
        ------
        httpx.HTTPStatusError
            For non-retryable HTTP errors (including 410 Gone for expired tokens).
        RuntimeError
            For unexpected response format.
        """
        access_token = await self._ensure_access_token(client)
        params: dict[str, Any] = {}
        if sync_token:
            params["syncToken"] = sync_token
        if page_token:
            params["pageToken"] = page_token

        url = f"{_GCAL_API_BASE}/calendars/{calendar_id}/events"
        headers = {"Authorization": f"Bearer {access_token}"}

        backoff = _BACKOFF_INITIAL_S
        last_exc: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                resp = await client.get(url, headers=headers, params=params, timeout=30.0)
                status_str = "success" if resp.status_code < 400 else "error"
                self._metrics.record_source_api_call("events.list", status_str)

                if resp.status_code == _HTTP_GONE:
                    # Let caller handle expired syncToken
                    resp.raise_for_status()

                if resp.status_code in (429, 503):
                    # Rate limit or service unavailable — retry with backoff
                    if attempt < max_retries:
                        logger.warning(
                            "Calendar API rate limited (HTTP %d) for %s, retry %d/%d in %.1fs",
                            resp.status_code,
                            self.email,
                            attempt + 1,
                            max_retries,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * _BACKOFF_MULTIPLIER, _BACKOFF_MAX_S)
                        continue

                resp.raise_for_status()
                return resp.json()

            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    logger.warning(
                        "Calendar API call failed for %s (attempt %d/%d): %s",
                        self.email,
                        attempt + 1,
                        max_retries,
                        exc,
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * _BACKOFF_MULTIPLIER, _BACKOFF_MAX_S)
                else:
                    raise RuntimeError(
                        f"Calendar API call failed after {max_retries} retries: {exc}"
                    ) from last_exc

        raise RuntimeError(f"Calendar API call exhausted retries for {self.email}")

    async def _load_cursor(self) -> GCalCursor | None:
        """Load the persisted sync cursor from DB."""
        if self._cursor_pool is None:
            return None
        raw = await load_cursor(self._cursor_pool, _CONNECTOR_TYPE, self.endpoint_identity)
        if raw is None:
            return None
        try:
            return GCalCursor.from_json(raw)
        except Exception:
            logger.warning(
                "Failed to parse cursor for %s; treating as missing cursor",
                self.endpoint_identity,
            )
            return None

    async def _save_cursor(self, cursor: GCalCursor) -> None:
        """Persist the sync cursor to DB."""
        if self._cursor_pool is None:
            return
        try:
            await save_cursor(
                self._cursor_pool,
                _CONNECTOR_TYPE,
                self.endpoint_identity,
                cursor.to_json(),
            )
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save("success")
            logger.debug("Saved cursor for %s", self.endpoint_identity)
        except Exception as exc:
            self._metrics.record_checkpoint_save("error")
            logger.error("Failed to save cursor for %s: %s", self.endpoint_identity, exc)

    async def _submit_envelope(self, envelope: dict[str, Any]) -> bool:
        """Submit an ingest.v1 envelope to the Switchboard via MCP.

        Returns True on acceptance, False otherwise.
        """
        idempotency_key = envelope.get("control", {}).get("idempotency_key", "")
        try:
            # Apply ingestion policy gate if configured
            if self._policy_evaluator is not None:
                organizer_email = envelope.get("sender", {}).get("identity", "")
                policy_envelope = IngestionEnvelope(
                    sender_address=organizer_email,
                    source_channel="google_calendar",
                    raw_key=organizer_email,
                )
                result = self._policy_evaluator.evaluate(policy_envelope)
                if result.action == "block":
                    logger.debug(
                        "Event blocked by ingestion policy: %s",
                        idempotency_key,
                    )
                    # Policy block is an intentional skip, not an ingestion failure.
                    # Return True so that _incremental_sync treats this as accepted
                    # for checkpointing purposes (cursor must still advance).
                    return True

            start = time.monotonic()
            await self._mcp_client.call_tool("ingest", {"envelope": envelope})
            latency = time.monotonic() - start
            self._metrics.record_ingest_submission("success", latency)
            self._last_sync_at = time.time()
            return True
        except Exception as exc:
            error_type = get_error_type(exc)
            self._metrics.record_ingest_submission("error")
            self._metrics.record_error(error_type, "ingest_submit")
            logger.error(
                "Failed to submit envelope %s for %s: %s",
                idempotency_key,
                self.endpoint_identity,
                exc,
            )
            return False

    async def _full_sync(
        self,
        client: httpx.AsyncClient,
        calendar_id: str,
        *,
        ingest_events: bool = False,
    ) -> str | None:
        """Perform a full events.list (no syncToken) to establish baseline.

        Args:
            ingest_events: If True, ingest events (used during 410 recovery).
                           If False, skip ingestion (baseline establishment only).

        Returns:
            The final nextSyncToken, or None on failure.
        """
        logger.info(
            "Starting full sync for %s (calendar_id=%s, ingest=%s)",
            self.email,
            calendar_id,
            ingest_events,
        )
        page_token: str | None = None
        next_sync_token: str | None = None
        event_count = 0
        all_accepted = True

        while True:
            try:
                response = await self._list_events(
                    client,
                    calendar_id,
                    page_token=page_token,
                )
            except Exception as exc:
                logger.error(
                    "Full sync failed for %s (calendar_id=%s): %s",
                    self.email,
                    calendar_id,
                    exc,
                )
                self._source_api_ok = False
                self._metrics.record_error("full_sync_failed", "full_sync")
                return None

            self._source_api_ok = True
            items = response.get("items", [])
            observed_at = datetime.now(UTC).isoformat()

            for event in items:
                # Update upcoming events cache
                event_id = event.get("id", "")
                if event_id:
                    if event.get("status") == "cancelled":
                        self._upcoming_events.pop(event_id, None)
                    else:
                        self._upcoming_events[event_id] = event

                if ingest_events:
                    event_type = _classify_event(event, is_initial_sync=False)
                    envelope = _build_ingest_envelope(
                        event,
                        event_type,
                        self.endpoint_identity,
                        self.email,
                        observed_at,
                    )
                    accepted = await self._submit_envelope(envelope)
                    if not accepted:
                        all_accepted = False
                    event_count += 1

            next_sync_token = response.get("nextSyncToken")
            page_token = response.get("nextPageToken")

            if page_token is None:
                # No more pages
                break

        if not ingest_events:
            logger.info(
                "Full sync baseline established for %s (calendar_id=%s)",
                self.email,
                calendar_id,
            )
        else:
            logger.info(
                "Full sync recovery complete for %s (calendar_id=%s, events=%d)",
                self.email,
                calendar_id,
                event_count,
            )
            if not all_accepted:
                # Some events failed to ingest — do not advance the cursor so
                # that the next poll cycle retries from a full resync again.
                logger.warning(
                    "Some events failed to ingest during full sync recovery for %s; "
                    "cursor will not advance",
                    self.email,
                )
                return None

        return next_sync_token

    async def _incremental_sync(
        self,
        client: httpx.AsyncClient,
        calendar_id: str,
        sync_token: str,
    ) -> tuple[bool, str | None]:
        """Perform an incremental events.list(syncToken=...) poll.

        Returns:
            (expired, next_sync_token):
            - expired=True if the syncToken was expired (HTTP 410)
            - next_sync_token is the new token to persist (None on error)
        """
        page_token: str | None = None
        next_sync_token: str | None = None
        events_batch: list[dict[str, Any]] = []

        # Collect all pages first before any ingestion
        while True:
            try:
                response = await self._list_events(
                    client,
                    calendar_id,
                    sync_token=sync_token if page_token is None else None,
                    page_token=page_token,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _HTTP_GONE:
                    logger.warning(
                        "syncToken expired (HTTP 410) for %s, falling back to full sync",
                        self.email,
                    )
                    return True, None
                logger.error(
                    "Calendar API error during incremental sync for %s: %s",
                    self.email,
                    exc,
                )
                self._source_api_ok = False
                self._metrics.record_error("http_error", "incremental_sync")
                return False, None
            except Exception as exc:
                logger.error(
                    "Incremental sync failed for %s: %s",
                    self.email,
                    exc,
                )
                self._source_api_ok = False
                self._metrics.record_error("incremental_sync_failed", "incremental_sync")
                return False, None

            self._source_api_ok = True
            events_batch.extend(response.get("items", []))
            next_sync_token = response.get("nextSyncToken")
            page_token = response.get("nextPageToken")

            if page_token is None:
                break

        if not events_batch:
            logger.debug("No changes for %s (calendar_id=%s)", self.email, calendar_id)
            return False, next_sync_token

        # Ingest all collected events
        observed_at = datetime.now(UTC).isoformat()
        all_accepted = True

        for event in events_batch:
            event_id = event.get("id", "")
            # Update upcoming events cache
            if event_id:
                if event.get("status") == "cancelled":
                    self._upcoming_events.pop(event_id, None)
                else:
                    self._upcoming_events[event_id] = event

            event_type = _classify_event(event, is_initial_sync=False)
            envelope = _build_ingest_envelope(
                event,
                event_type,
                self.endpoint_identity,
                self.email,
                observed_at,
            )
            accepted = await self._submit_envelope(envelope)
            if not accepted:
                all_accepted = False

        if all_accepted:
            return False, next_sync_token
        else:
            # On partial failure, do not advance cursor (safe replay on restart)
            logger.warning(
                "Some events failed to ingest for %s; cursor will not advance",
                self.email,
            )
            return False, None

    async def _check_starting_soon(self) -> None:
        """Scan upcoming events and emit 'event starting soon' notifications.

        Deduplication via seen-set keyed by (event_id, lead_minutes).
        """
        if self._starting_soon_lead_minutes <= 0:
            return

        now = datetime.now(UTC)
        lead_cutoff = now + timedelta(minutes=self._starting_soon_lead_minutes)
        observed_at = now.isoformat()
        events_to_prune: list[str] = []

        for event_id, event in list(self._upcoming_events.items()):
            start_obj = event.get("start", {})
            start_str = start_obj.get("dateTime") or start_obj.get("date")
            if not start_str:
                continue

            try:
                # Parse start time
                if "T" in start_str:
                    # dateTime with timezone
                    if start_str.endswith("Z"):
                        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    elif "+" in start_str[10:] or start_str.count("-") > 2:
                        start_dt = datetime.fromisoformat(start_str)
                    else:
                        # No timezone info — assume UTC
                        start_dt = datetime.fromisoformat(start_str).replace(tzinfo=UTC)
                else:
                    # All-day event (date only) — skip for starting soon
                    continue
            except ValueError:
                logger.debug("Cannot parse event start time: %s", start_str)
                continue

            # Prune past events
            if start_dt < now:
                events_to_prune.append(event_id)
                self._starting_soon_seen.discard((event_id, self._starting_soon_lead_minutes))
                continue

            # Check if event is within the lead-time window
            if now <= start_dt <= lead_cutoff:
                key = (event_id, self._starting_soon_lead_minutes)
                if key not in self._starting_soon_seen:
                    self._starting_soon_seen.add(key)
                    envelope = _build_starting_soon_envelope(
                        event,
                        self.endpoint_identity,
                        self.email,
                        observed_at,
                        self._starting_soon_lead_minutes,
                    )
                    await self._submit_envelope(envelope)
                    logger.info(
                        "Emitted starting_soon notification for event %s (lead=%dm)",
                        event_id,
                        self._starting_soon_lead_minutes,
                    )

        # Prune past events from cache
        for event_id in events_to_prune:
            self._upcoming_events.pop(event_id, None)

    async def run_once(
        self,
        client: httpx.AsyncClient,
        calendar_id: str,
    ) -> None:
        """Execute a single poll cycle for the given calendar.

        Loads cursor, performs full or incremental sync, saves cursor
        after acceptance, then checks for starting-soon events.
        """
        cursor = await self._load_cursor()

        if cursor is None:
            # Initial full sync — establish baseline, do NOT ingest
            next_sync_token = await self._full_sync(client, calendar_id, ingest_events=False)
            if next_sync_token:
                new_cursor = GCalCursor(
                    sync_token=next_sync_token,
                    last_updated_at=datetime.now(UTC).isoformat(),
                )
                await self._save_cursor(new_cursor)
        else:
            # Incremental sync
            expired, next_sync_token = await self._incremental_sync(
                client, calendar_id, cursor.sync_token
            )

            if expired:
                # 410 Gone — discard stale token, full resync with ingestion
                next_sync_token = await self._full_sync(client, calendar_id, ingest_events=True)

            if next_sync_token:
                new_cursor = GCalCursor(
                    sync_token=next_sync_token,
                    last_updated_at=datetime.now(UTC).isoformat(),
                )
                await self._save_cursor(new_cursor)

        # After each sync cycle, check for starting-soon notifications
        await self._check_starting_soon()

    async def run_loop(self) -> None:
        """Main poll loop. Runs until cancelled."""
        self._running = True
        logger.info(
            "GCalSyncLoop starting: email=%s endpoint_identity=%s",
            self.email,
            self.endpoint_identity,
        )

        async with httpx.AsyncClient() as client:
            while self._running:
                for calendar_id in self._calendar_ids:
                    try:
                        await self.run_once(client, calendar_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._error = str(exc)
                        self._source_api_ok = False
                        self._metrics.record_error(get_error_type(exc), "poll_cycle")
                        logger.error(
                            "Poll cycle error for %s (calendar_id=%s): %s",
                            self.email,
                            calendar_id,
                            exc,
                            exc_info=True,
                        )

                try:
                    await asyncio.sleep(self._poll_interval_s)
                except asyncio.CancelledError:
                    break

        self._running = False
        logger.info("GCalSyncLoop stopped: email=%s", self.email)

    def stop(self) -> None:
        """Signal the loop to stop."""
        self._running = False

    def get_health(self) -> AccountHealthStatus:
        """Return a per-account health snapshot."""
        last_checkpoint_save_at = None
        if self._last_checkpoint_save is not None:
            last_checkpoint_save_at = datetime.fromtimestamp(
                self._last_checkpoint_save, UTC
            ).isoformat()

        last_sync_at = None
        if self._last_sync_at is not None:
            last_sync_at = datetime.fromtimestamp(self._last_sync_at, UTC).isoformat()

        if self._source_api_ok is None:
            connectivity: Literal["connected", "disconnected", "unknown"] = "unknown"
        elif self._source_api_ok:
            connectivity = "connected"
        else:
            connectivity = "disconnected"

        if self._error and self._source_api_ok is False:
            status: Literal["healthy", "degraded", "error"] = "error"
        elif self._source_api_ok is False:
            status = "degraded"
        else:
            status = "healthy"

        return AccountHealthStatus(
            email=self.email,
            endpoint_identity=self.endpoint_identity,
            status=status,
            last_checkpoint_save_at=last_checkpoint_save_at,
            last_sync_at=last_sync_at,
            source_api_connectivity=connectivity,
            error=self._error,
        )


# ---------------------------------------------------------------------------
# Per-account loop wrapper
# ---------------------------------------------------------------------------


class GCalAccountLoop:
    """Per-account asyncio task wrapper around GCalSyncLoop."""

    def __init__(
        self,
        email: str,
        sync_loop: GCalSyncLoop,
    ) -> None:
        self.email = email
        self.endpoint_identity = sync_loop.endpoint_identity
        self._sync_loop = sync_loop
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None

    def start(self) -> None:
        """Launch the per-account sync loop as an asyncio task."""
        self._task = asyncio.create_task(self._run(), name=f"gcal-account-{self.email}")
        self._task.add_done_callback(self._on_done)

    async def _run(self) -> None:
        try:
            await self._sync_loop.run_loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            logger.error(
                "GCalAccountLoop failed: email=%s error=%s",
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
        """Gracefully stop the account loop."""
        self._sync_loop.stop()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("GCalAccountLoop stopped: email=%s", self.email)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def get_health(self) -> AccountHealthStatus:
        """Return per-account health snapshot."""
        health = self._sync_loop.get_health()
        # Override error from task if available
        if self._error and health.error is None:
            health = AccountHealthStatus(
                **{**health.model_dump(), "error": self._error, "status": "error"}
            )
        return health


# ---------------------------------------------------------------------------
# Process-level config
# ---------------------------------------------------------------------------


class GCalProcessConfig(BaseModel):
    """Process-level configuration for the multi-account Google Calendar connector manager."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Switchboard MCP
    switchboard_mcp_url: str

    # Connector identity
    connector_provider: str = "google_calendar"
    connector_channel: str = "google_calendar"
    connector_max_inflight: int = _DEFAULT_MAX_INFLIGHT

    # Health check
    connector_health_port: int = _DEFAULT_HEALTH_PORT

    # Heartbeat
    connector_heartbeat_interval_s: int = _DEFAULT_HEARTBEAT_INTERVAL_S

    # Google Calendar sync defaults (overridable per-account via metadata.calendar)
    gcal_poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    gcal_starting_soon_lead_minutes: int = _DEFAULT_STARTING_SOON_LEAD_MINUTES
    gcal_account_rescan_interval_s: int = _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S

    @classmethod
    def from_env(cls) -> GCalProcessConfig:
        """Load process-level config from environment variables."""

        def _int_env(key: str, default: int) -> int:
            raw = os.environ.get(key, str(default))
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} must be an integer, got: {raw}") from exc

        return cls(
            switchboard_mcp_url=os.environ["SWITCHBOARD_MCP_URL"],
            connector_provider=os.environ.get("CONNECTOR_PROVIDER", "google_calendar"),
            connector_channel=os.environ.get("CONNECTOR_CHANNEL", "google_calendar"),
            connector_max_inflight=_int_env("CONNECTOR_MAX_INFLIGHT", _DEFAULT_MAX_INFLIGHT),
            connector_health_port=_int_env("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
            connector_heartbeat_interval_s=_int_env(
                "CONNECTOR_HEARTBEAT_INTERVAL_S", _DEFAULT_HEARTBEAT_INTERVAL_S
            ),
            gcal_poll_interval_s=_int_env("GCAL_POLL_INTERVAL_S", _DEFAULT_POLL_INTERVAL_S),
            gcal_starting_soon_lead_minutes=_int_env(
                "GCAL_STARTING_SOON_LEAD_MINUTES", _DEFAULT_STARTING_SOON_LEAD_MINUTES
            ),
            gcal_account_rescan_interval_s=_int_env(
                "GCAL_ACCOUNT_RESCAN_INTERVAL_S", _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S
            ),
        )


# ---------------------------------------------------------------------------
# Multi-account connector manager
# ---------------------------------------------------------------------------


async def _create_shared_db_pool(params: dict[str, Any], db_name: str) -> asyncpg.Pool:
    """Create an asyncpg pool for the shared database."""
    import asyncpg as _asyncpg

    pool_kwargs: dict[str, Any] = {
        "host": params.get("host") or "localhost",
        "port": params.get("port") or 5432,
        "user": params.get("user") or "butlers",
        "password": params.get("password") or "butlers",
        "database": db_name,
        "min_size": 1,
        "max_size": 4,
        "command_timeout": 5,
    }
    ssl = params.get("ssl")
    if ssl is not None:
        pool_kwargs["ssl"] = ssl

    try:
        return await _asyncpg.create_pool(**pool_kwargs)
    except Exception as exc:
        if should_retry_with_ssl_disable(exc, ssl):
            pool_kwargs["ssl"] = "disable"
            return await _asyncpg.create_pool(**pool_kwargs)
        raise


async def _resolve_calendar_credentials_from_db(
    db_pool: asyncpg.Pool,
    shared_pool: asyncpg.Pool,
    email: str,
) -> tuple[str, str, str] | None:
    """Resolve (client_id, client_secret, refresh_token) for a Google account.

    Returns None if credentials cannot be resolved (logged, not raised).
    """
    from butlers.credential_store import CredentialStore

    store = CredentialStore(db_pool)
    try:
        creds = await load_google_credentials(store, pool=shared_pool, account=email)
        if creds is None:
            logger.warning("No credentials found for Google Calendar account: %s", email)
            return None
        return creds.client_id, creds.client_secret, creds.refresh_token
    except InvalidGoogleCredentialsError as exc:
        logger.warning("Invalid credentials for Google Calendar account %s: %s", email, exc)
        return None
    except Exception as exc:
        logger.error(
            "Error resolving credentials for Google Calendar account %s: %s",
            email,
            exc,
            exc_info=True,
        )
        return None


class GCalConnectorManager:
    """Top-level orchestrator for multi-account Google Calendar connector.

    Discovers all active Google accounts with calendar scope from
    shared.google_accounts, spawns independent GCalAccountLoop instances,
    and manages their lifecycle.

    Supports:
    - Periodic account re-scan at GCAL_ACCOUNT_RESCAN_INTERVAL_S (default 300)
    - Aggregated health endpoint across all accounts
    - Degraded startup when no qualifying accounts found
    """

    def __init__(
        self,
        process_config: GCalProcessConfig,
        db_pool: asyncpg.Pool,
        cursor_pool: asyncpg.Pool | None,
        shared_pool: asyncpg.Pool,
    ) -> None:
        self._process_config = process_config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool
        self._shared_pool = shared_pool

        # Active account loops keyed by email
        self._loops: dict[str, GCalAccountLoop] = {}

        # Shared MCP client (one connection, all accounts share)
        self._mcp_client = CachedMCPClient(
            process_config.switchboard_mcp_url,
            client_name="google_calendar_connector",
        )

        # Runtime state
        self._start_time = time.time()
        self._running = False
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Rescan task
        self._rescan_task: asyncio.Task[None] | None = None

    async def _discover_qualifying_accounts(
        self,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """Query shared.google_accounts for active accounts with calendar scope.

        Returns list of (email, metadata_calendar) tuples where metadata_calendar
        is the parsed ``calendar`` subsection of the account's metadata JSONB column.
        Only accounts with status='active' and 'calendar' in granted_scopes are returned.
        """
        try:
            async with self._shared_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT email, granted_scopes, metadata
                    FROM shared.google_accounts
                    WHERE status = 'active'
                    ORDER BY is_primary DESC, connected_at ASC
                    """
                )
        except Exception as exc:
            logger.error("Failed to query google_accounts: %s", exc)
            return []

        result: list[tuple[str, dict[str, Any] | None]] = []
        for row in rows:
            email: str | None = row["email"]
            if not email:
                continue
            granted_scopes = row["granted_scopes"] or []
            if _CALENDAR_SCOPE not in granted_scopes:
                logger.debug(
                    "Skipping account %s: 'calendar' not in granted_scopes=%s",
                    email,
                    granted_scopes,
                )
                continue

            metadata_raw = row["metadata"]
            metadata_calendar: dict[str, Any] | None = None
            if metadata_raw:
                try:
                    meta = (
                        metadata_raw if isinstance(metadata_raw, dict) else json.loads(metadata_raw)
                    )
                    metadata_calendar = meta.get("calendar")
                except Exception:
                    pass

            result.append((email, metadata_calendar))

        return result

    def _make_sync_loop(
        self,
        email: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        metadata_calendar: dict[str, Any] | None,
    ) -> GCalSyncLoop:
        """Create a GCalSyncLoop for the given account, applying metadata overrides."""
        cfg = self._process_config
        meta = metadata_calendar or {}

        poll_interval_s = int(meta.get("poll_interval_s", cfg.gcal_poll_interval_s))
        starting_soon_lead_minutes = int(
            meta.get("starting_soon_lead_minutes", cfg.gcal_starting_soon_lead_minutes)
        )
        calendar_ids_raw = meta.get("calendar_ids")
        calendar_ids = list(calendar_ids_raw) if calendar_ids_raw else ["primary"]

        endpoint_identity = f"google_calendar:user:{email}"

        return GCalSyncLoop(
            email=email,
            endpoint_identity=endpoint_identity,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            cursor_pool=self._cursor_pool,
            mcp_client=self._mcp_client,
            poll_interval_s=poll_interval_s,
            starting_soon_lead_minutes=starting_soon_lead_minutes,
            calendar_ids=calendar_ids,
        )

    async def _spawn_loop(
        self,
        email: str,
        metadata_calendar: dict[str, Any] | None,
    ) -> None:
        """Resolve credentials and spawn a GCalAccountLoop for the given email."""
        creds = await _resolve_calendar_credentials_from_db(self._db_pool, self._shared_pool, email)
        if creds is None:
            logger.warning("Skipping account %s: credentials unavailable", email)
            return

        client_id, client_secret, refresh_token = creds
        sync_loop = self._make_sync_loop(
            email, client_id, client_secret, refresh_token, metadata_calendar
        )
        account_loop = GCalAccountLoop(email=email, sync_loop=sync_loop)
        account_loop.start()
        self._loops[email] = account_loop
        logger.info("Spawned GCalAccountLoop for %s", email)

    async def _stop_loop(self, email: str) -> None:
        """Gracefully stop and remove the loop for the given email."""
        loop = self._loops.pop(email, None)
        if loop is not None:
            await loop.stop()
            logger.info("Stopped GCalAccountLoop for %s", email)

    async def _rescan_accounts(self) -> None:
        """Reconcile running loops with currently active accounts."""
        try:
            active_accounts = await self._discover_qualifying_accounts()
        except Exception as exc:
            logger.error("Account rescan failed: %s", exc, exc_info=True)
            return

        active_emails = {email for email, _ in active_accounts}
        running_emails = set(self._loops.keys())

        # Stop loops for removed accounts
        for email in running_emails - active_emails:
            logger.info("Account %s no longer active; stopping loop", email)
            await self._stop_loop(email)

        # Spawn loops for new accounts
        for email, metadata_calendar in active_accounts:
            if email not in self._loops or not self._loops[email].is_running:
                await self._spawn_loop(email, metadata_calendar)

    async def _rescan_loop(self) -> None:
        """Periodic account rescan task."""
        interval = self._process_config.gcal_account_rescan_interval_s
        while self._running:
            try:
                await asyncio.sleep(interval)
                await self._rescan_accounts()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Rescan loop error: %s", exc, exc_info=True)

    def _build_health_app(self) -> FastAPI:
        """Build the health/metrics FastAPI app."""
        app = FastAPI(title="Google Calendar Connector Health")

        @app.get("/health")
        async def health() -> MultiAccountHealthStatus:
            return self._get_health()

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest(REGISTRY)

        return app

    def _get_health(self) -> MultiAccountHealthStatus:
        """Return aggregated health across all account loops."""
        account_health = [loop.get_health() for loop in self._loops.values()]

        # Worst-case status
        if any(h.status == "error" for h in account_health):
            overall: Literal["healthy", "degraded", "error"] = "error"
        elif any(h.status == "degraded" for h in account_health):
            overall = "degraded"
        elif not account_health:
            overall = "degraded"  # No active accounts
        else:
            overall = "healthy"

        return MultiAccountHealthStatus(
            status=overall,
            uptime_seconds=time.time() - self._start_time,
            active_accounts=sum(1 for loop in self._loops.values() if loop.is_running),
            account_health=account_health,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def _start_health_server(self) -> None:
        """Start the health server in a background thread."""
        app = self._build_health_app()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self._process_config.connector_health_port,
            log_level="warning",
        )
        self._health_server = uvicorn.Server(config)

        def _run() -> None:
            import asyncio as _asyncio

            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            loop.run_until_complete(self._health_server.serve())

        self._health_thread = Thread(target=_run, daemon=True, name="gcal-health-server")
        self._health_thread.start()
        logger.info(
            "Health server started on port %d",
            self._process_config.connector_health_port,
        )

    def _stop_health_server(self) -> None:
        """Signal the health server to stop."""
        if self._health_server is not None:
            self._health_server.should_exit = True

    async def start(self) -> None:
        """Start the connector manager.

        1. Wait for Switchboard readiness.
        2. Discover qualifying accounts.
        3. Spawn per-account loops.
        4. Start health server and periodic rescan.
        5. Wait for SIGTERM/SIGINT.
        """
        self._running = True
        self._main_loop = asyncio.get_running_loop()

        logger.info("Google Calendar connector manager starting")

        # Wait for Switchboard to be ready
        try:
            await wait_for_switchboard_ready(self._process_config.switchboard_mcp_url)
        except TimeoutError as exc:
            logger.error("Switchboard not ready: %s", exc)

        # Initial account discovery
        await self._rescan_accounts()

        if not self._loops:
            logger.warning(
                "No qualifying Google Calendar accounts found; starting in idle/degraded mode"
            )

        # Start health server
        self._start_health_server()

        # Start periodic rescan
        self._rescan_task = asyncio.create_task(self._rescan_loop(), name="gcal-rescan")

        # Set up signal handlers
        stop_event = asyncio.Event()

        def _handle_signal(sig: int) -> None:
            logger.info("Received signal %d; initiating shutdown", sig)
            if self._main_loop is not None:
                self._main_loop.call_soon_threadsafe(stop_event.set)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                self._main_loop.add_signal_handler(sig, _handle_signal, sig)
            except (NotImplementedError, RuntimeError):
                pass

        logger.info("Google Calendar connector running (%d account(s))", len(self._loops))
        await stop_event.wait()

        await self._shutdown()

    async def _shutdown(self) -> None:
        """Gracefully shut down all account loops and cleanup."""
        logger.info("Google Calendar connector shutting down")
        self._running = False

        # Cancel rescan
        if self._rescan_task is not None and not self._rescan_task.done():
            self._rescan_task.cancel()
            try:
                await self._rescan_task
            except asyncio.CancelledError:
                pass

        # Stop all account loops
        for email in list(self._loops.keys()):
            await self._stop_loop(email)

        # Stop health server
        self._stop_health_server()

        # Close MCP client
        await self._mcp_client.aclose()

        logger.info("Google Calendar connector shutdown complete")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_google_calendar_connector() -> None:
    """Top-level entry point for the Google Calendar connector process."""
    configure_logging()
    logger.info("Starting Google Calendar connector")

    process_config = GCalProcessConfig.from_env()

    # Set up DB pools
    db_params = db_params_from_env()
    butler_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"
    shared_db_name = shared_db_name_from_env()

    db_pool = await _create_shared_db_pool(db_params, butler_db_name)
    shared_pool = await _create_shared_db_pool(db_params, shared_db_name)
    cursor_pool = db_pool  # Reuse butler DB pool for cursor writes

    try:
        manager = GCalConnectorManager(
            process_config=process_config,
            db_pool=db_pool,
            cursor_pool=cursor_pool,
            shared_pool=shared_pool,
        )
        await manager.start()
    finally:
        await db_pool.close()
        if shared_pool is not db_pool:
            await shared_pool.close()


def main() -> None:
    """Synchronous entry point for pyproject.toml console_scripts."""
    asyncio.run(run_google_calendar_connector())


if __name__ == "__main__":
    main()
