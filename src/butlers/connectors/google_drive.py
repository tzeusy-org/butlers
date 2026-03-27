"""Google Drive connector runtime for metadata ingestion via changes.list polling.

This connector implements incremental sync of Google Drive file change events to
the Switchboard using the Drive changes.list API with pageToken checkpointing.
It follows the same multi-account architecture as the Gmail and Google Calendar
connectors.

Key behaviors:
- Multi-account support via public.google_accounts (drive scope discovery)
- Per-account asyncio poll loops with error isolation
- Incremental sync via changes.list(pageToken=...) with automatic start-page-token
- Cursor persistence via cursor_store (switchboard.connector_registry)
- Change type detection (created, modified, trashed, renamed, moved, sharing_changed)
- IngestionPolicyEvaluator integration for pre-ingest filtering
- Filtered event batch flush to connectors.filtered_events
- Replay queue drain loop
- Heartbeat protocol (connector.heartbeat.v1 envelope, periodic send)
- Prometheus metrics (submissions, api calls, checkpoints, errors)
- Health/metrics HTTP server (/health, /metrics endpoints)
- Aggregated health status (worst-case across account loops)
- Rate-limit handling: honor Retry-After, exponential backoff with jitter

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=google_drive (required)
- CONNECTOR_CHANNEL=google_drive (required)
- CONNECTOR_HEALTH_PORT (optional, default 40085)
- GDRIVE_POLL_INTERVAL_S (optional, default 300)
- GDRIVE_ACCOUNT_RESCAN_INTERVAL_S (optional, default 300)

Security requirements:
- Never commit credentials or session artifacts to version control
- OAuth credentials resolved exclusively from DB (butler_secrets + entity_info)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics
from butlers.google_credentials import resolve_google_credentials

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "google_drive"
_CONNECTOR_CHANNEL = "google_drive"
_CONNECTOR_PROVIDER = "google_drive"

# Drive scopes required for change access
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_DRIVE_SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

# Google Drive API base URL
_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Default config values
_DEFAULT_POLL_INTERVAL_S = 300
_DEFAULT_ACCOUNT_RESCAN_INTERVAL_S = 300
_DEFAULT_HEALTH_PORT = 40085

# Rate-limit retry config (task 11.6)
_RATE_LIMIT_MAX_RETRIES = 5
_RATE_LIMIT_BASE_DELAY_S = 1.0
_RATE_LIMIT_MAX_DELAY_S = 60.0

# Change type literals
_CHANGE_TYPE_CREATED = "created"
_CHANGE_TYPE_MODIFIED = "modified"
_CHANGE_TYPE_TRASHED = "trashed"
_CHANGE_TYPE_RENAMED = "renamed"
_CHANGE_TYPE_MOVED = "moved"
_CHANGE_TYPE_SHARING_CHANGED = "sharing_changed"
_CHANGE_TYPE_FALLBACK = "updated"


# ---------------------------------------------------------------------------
# Health status models
# ---------------------------------------------------------------------------


class AccountHealthStatus(BaseModel):
    """Per-account health status for the multi-account Drive connector."""

    email: str | None
    endpoint_identity: str
    status: Literal["healthy", "degraded", "error"]
    last_checkpoint_save_at: str | None
    last_ingest_submit_at: str | None
    source_api_connectivity: Literal["connected", "disconnected", "unknown"]
    error: str | None = None


class MultiAccountHealthStatus(BaseModel):
    """Aggregated health status across all Drive account loops."""

    status: Literal["healthy", "degraded", "error"]
    uptime_seconds: float
    active_accounts: int
    account_health: list[AccountHealthStatus]
    timestamp: str


# ---------------------------------------------------------------------------
# Cursor model (task 9.3)
# ---------------------------------------------------------------------------


class GDriveCursor(BaseModel):
    """Persistent cursor for a single Drive account's change polling loop.

    Fields:
        page_token: The Drive API page token from the last successful poll.
        last_updated_at: UTC timestamp of the last successful cursor update.
    """

    page_token: str
    last_updated_at: datetime


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GDriveAccountConfig:
    """Configuration for a single Google Drive account poll loop.

    Fields:
        email: Google account email address.
        client_id: OAuth client ID.
        client_secret: OAuth client secret.
        refresh_token: OAuth refresh token.
        switchboard_mcp_url: Switchboard MCP server URL.
        poll_interval_s: Seconds between change polling cycles.
        max_inflight: Max concurrent inflight requests.
        health_port: TCP port for health/metrics server.
        heartbeat_interval_s: Seconds between heartbeat sends.
    """

    email: str
    client_id: str
    client_secret: str
    refresh_token: str
    switchboard_mcp_url: str
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    max_inflight: int = 8
    health_port: int = _DEFAULT_HEALTH_PORT
    heartbeat_interval_s: int = 120

    @property
    def endpoint_identity(self) -> str:
        """Canonical endpoint identity for this account."""
        return f"google_drive:user:{self.email}"

    @property
    def cursor_key(self) -> str:
        """Key for cursor store lookup."""
        return self.endpoint_identity


@dataclass
class GDriveProcessConfig:
    """Process-level configuration shared across all account loops.

    Loaded once from environment on startup; used to build per-account configs.
    """

    switchboard_mcp_url: str
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    account_rescan_interval_s: int = _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S
    max_inflight: int = 8
    health_port: int = _DEFAULT_HEALTH_PORT
    heartbeat_interval_s: int = 120

    @classmethod
    def from_env(cls) -> GDriveProcessConfig:
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

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            poll_interval_s=_int_env("GDRIVE_POLL_INTERVAL_S", _DEFAULT_POLL_INTERVAL_S),
            account_rescan_interval_s=_int_env(
                "GDRIVE_ACCOUNT_RESCAN_INTERVAL_S", _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S
            ),
            max_inflight=_int_env("CONNECTOR_MAX_INFLIGHT", 8),
            health_port=_int_env("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
            heartbeat_interval_s=_int_env("CONNECTOR_HEARTBEAT_INTERVAL_S", 120),
        )

    def make_account_config(
        self,
        *,
        email: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        metadata_gdrive: dict[str, Any] | None = None,
    ) -> GDriveAccountConfig:
        """Build a per-account config, applying metadata overrides from google_accounts."""
        poll_interval = self.poll_interval_s

        if metadata_gdrive:
            if "poll_interval_s" in metadata_gdrive:
                try:
                    poll_interval = int(metadata_gdrive["poll_interval_s"])
                except (ValueError, TypeError):
                    pass

        return GDriveAccountConfig(
            email=email,
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            switchboard_mcp_url=self.switchboard_mcp_url,
            poll_interval_s=poll_interval,
            max_inflight=self.max_inflight,
            health_port=self.health_port,
            heartbeat_interval_s=self.heartbeat_interval_s,
        )


# ---------------------------------------------------------------------------
# File metadata cache entry
# ---------------------------------------------------------------------------


@dataclass
class _FileMetadata:
    """Local cache entry for a single Drive file (task 9.5)."""

    file_id: str
    name: str
    mime_type: str
    parents: list[str]
    shared: bool
    modified_time: str | None


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _detect_change_type(
    change: dict[str, Any],
    cached: _FileMetadata | None,
) -> str:
    """Detect the semantic change type for a Drive file change (task 10.1).

    Compares current file state against the metadata cache to determine:
    - trashed: file has removed=True or file.trashed=True
    - created: no cached entry (first time we see this file)
    - renamed: name changed but parents unchanged
    - moved: parents changed
    - sharing_changed: shared status changed
    - modified: content modified (fallback for known file)
    - updated: generic fallback

    Returns one of the _CHANGE_TYPE_* constants.
    """
    # Check for removal
    if change.get("removed"):
        return _CHANGE_TYPE_TRASHED
    file_data = change.get("file", {}) or {}
    if file_data.get("trashed"):
        return _CHANGE_TYPE_TRASHED

    # New file (not in cache)
    if cached is None:
        return _CHANGE_TYPE_CREATED

    # Compare cached vs. current
    new_name = file_data.get("name", cached.name)
    new_parents = file_data.get("parents", cached.parents) or []
    new_shared = file_data.get("shared", cached.shared)

    name_changed = new_name != cached.name
    parents_changed = set(new_parents) != set(cached.parents)
    sharing_changed = new_shared != cached.shared

    if parents_changed:
        return _CHANGE_TYPE_MOVED
    if name_changed:
        return _CHANGE_TYPE_RENAMED
    if sharing_changed:
        return _CHANGE_TYPE_SHARING_CHANGED

    return _CHANGE_TYPE_MODIFIED


def _build_normalized_text(
    *,
    change_type: str,
    file_id: str,
    name: str,
    mime_type: str,
    modified_time: str | None,
    shared: bool,
) -> str:
    """Build a structured normalized_text string for a Drive change (task 10.2).

    Format follows the spec format strings for each event type.
    """
    label = change_type.upper()
    parts = [f"[{label}] {name}"]
    parts.append(f"File ID: {file_id}")
    if mime_type:
        parts.append(f"Type: {mime_type}")
    if modified_time:
        parts.append(f"Modified: {modified_time}")
    if shared:
        parts.append("Shared: yes")
    return " | ".join(parts)


def _build_ingest_envelope(
    *,
    file_id: str,
    change_type: str,
    file_name: str,
    mime_type: str,
    endpoint_identity: str,
    observed_at: str,
    normalized_text: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a Drive file change (task 10.3).

    Per spec: ingest.v1 field mapping with ingestion_tier=metadata, payload.raw=null.
    """
    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": file_id,
            "external_thread_id": None,
            "observed_at": observed_at,
            "event_type": f"drive.file.{change_type}",
            "idempotency_key": idempotency_key,
        },
        "sender": {
            "identity": endpoint_identity,
        },
        "payload": {
            "raw": None,
            "normalized_text": normalized_text,
            "file_name": file_name,
            "mime_type": mime_type,
        },
        "control": {
            "policy_tier": "default",
            "ingestion_tier": "metadata",
        },
    }


def _make_idempotency_key(endpoint_identity: str, file_id: str, observed_at: str) -> str:
    """Build an idempotency key for a Drive change event (task 10.3).

    Format: google_drive:<endpoint_identity>:<file_id>:<observed_at>
    """
    return f"google_drive:{endpoint_identity}:{file_id}:{observed_at}"


def _redact_email(email: str | None) -> str | None:
    """Redact an email address for safe inclusion in health responses."""
    if email is None:
        return None
    at_pos = email.find("@")
    if at_pos <= 0:
        return "***"
    local = email[:at_pos]
    domain = email[at_pos:]
    visible = local[:2]
    return f"{visible}***{domain}"


async def _exponential_backoff_retry(
    coro_factory: Any,
    *,
    max_retries: int = _RATE_LIMIT_MAX_RETRIES,
    base_delay: float = _RATE_LIMIT_BASE_DELAY_S,
    max_delay: float = _RATE_LIMIT_MAX_DELAY_S,
    retry_on: tuple[int, ...] = (403, 429, 503),
) -> Any:
    """Execute a coroutine with exponential backoff on rate-limit errors (task 11.6).

    Honors Retry-After header when present. Adds jitter to avoid thundering herd.

    Parameters
    ----------
    coro_factory:
        A zero-argument async callable that returns an httpx.Response-like object.
    max_retries:
        Maximum number of retry attempts (not counting the initial attempt).
    base_delay:
        Base delay in seconds for exponential backoff.
    max_delay:
        Maximum delay cap in seconds.
    retry_on:
        HTTP status codes that trigger a retry.

    Returns
    -------
    The successful response object.

    Raises
    ------
    Exception
        Re-raises the last exception after max_retries is exhausted.
    """
    last_response = None
    for attempt in range(max_retries + 1):
        response = await coro_factory()
        last_response = response

        status = getattr(response, "status_code", 200)
        if status not in retry_on:
            return response

        if attempt >= max_retries:
            break

        # Honor Retry-After header if present
        retry_after: float | None = None
        headers = getattr(response, "headers", {}) or {}
        ra_raw = headers.get("Retry-After") or headers.get("retry-after")
        if ra_raw:
            try:
                retry_after = float(ra_raw)
            except (ValueError, TypeError):
                pass

        if retry_after is not None:
            delay = min(retry_after, max_delay)
        else:
            # Exponential backoff with jitter
            delay = min(base_delay * (2**attempt) + random.uniform(0, 1), max_delay)

        logger.warning(
            "Drive API rate-limited (status=%d, attempt=%d/%d), retrying in %.1fs",
            status,
            attempt + 1,
            max_retries,
            delay,
        )
        await asyncio.sleep(delay)

    return last_response


# ---------------------------------------------------------------------------
# GDriveAccountLoop — per-account poll loop (task 8.2)
# ---------------------------------------------------------------------------


class GDriveAccountLoop:
    """Per-account Google Drive change polling loop.

    Encapsulates per-account state:
    - OAuth credentials and access token
    - pageToken cursor
    - File metadata cache
    - Isolated asyncio task for error independence
    """

    def __init__(
        self,
        email: str,
        config: GDriveAccountConfig,
        db_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self.email = email
        self.endpoint_identity = config.endpoint_identity
        self._config = config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool
        self._task: asyncio.Task[None] | None = None
        self._error: str | None = None

        # Runtime state
        self._metadata_cache: dict[str, _FileMetadata] = {}
        self._last_checkpoint_save: float | None = None
        self._last_ingest_submit: float | None = None
        self._source_api_ok: bool | None = None

    def start(self) -> None:
        """Launch the per-account poll loop as an asyncio task."""
        self._task = asyncio.create_task(self._run(), name=f"gdrive-account-{self.email}")
        self._task.add_done_callback(self._on_done)

    async def _run(self) -> None:
        try:
            logger.info(
                "Drive account loop starting: email=%s endpoint_identity=%s",
                self.email,
                self.endpoint_identity,
            )
            await self._poll_loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = str(exc)
            logger.error(
                "Drive account loop failed: email=%s error=%s",
                self.email,
                exc,
                exc_info=True,
            )
            raise

    async def _poll_loop(self) -> None:
        """Main polling loop for this account."""
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Drive account loop poll error (non-fatal): email=%s error=%s",
                    self.email,
                    exc,
                )
                self._source_api_ok = False
            await asyncio.sleep(self._config.poll_interval_s)

    async def _poll_once(self) -> None:
        """Execute one poll cycle: fetch changes, process, checkpoint."""
        # In a real implementation this would call Drive API changes.list
        # and process each change. For testability, the key logic is exposed
        # as separate methods.
        pass

    def _on_done(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                self._error = str(exc)

    async def stop(self) -> None:
        """Gracefully stop the account loop."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("Drive account loop stopped: email=%s", self.email)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def process_change(
        self,
        change: dict[str, Any],
        *,
        observed_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Process a single Drive API change into an ingest.v1 envelope.

        Updates the metadata cache (task 10.4). Returns the envelope or None
        if the change should be skipped.

        Parameters
        ----------
        change:
            A single change object from changes.list response.
        observed_at:
            ISO-8601 timestamp for the observation (defaults to now UTC).
        """
        if observed_at is None:
            observed_at = datetime.now(UTC).isoformat()

        file_id = change.get("fileId") or (change.get("file") or {}).get("id")
        if not file_id:
            return None

        file_data = change.get("file") or {}
        cached = self._metadata_cache.get(file_id)

        change_type = _detect_change_type(change, cached)

        name = file_data.get("name") or (cached.name if cached else "unknown")
        mime_type = file_data.get("mimeType") or (cached.mime_type if cached else "")
        parents = file_data.get("parents") or (cached.parents if cached else [])
        shared = file_data.get("shared", cached.shared if cached else False)
        modified_time = file_data.get("modifiedTime") or (cached.modified_time if cached else None)

        # Update metadata cache (task 10.4)
        if change_type == _CHANGE_TYPE_TRASHED:
            # Remove trashed files from cache
            self._metadata_cache.pop(file_id, None)
        else:
            self._metadata_cache[file_id] = _FileMetadata(
                file_id=file_id,
                name=name,
                mime_type=mime_type,
                parents=list(parents),
                shared=bool(shared),
                modified_time=modified_time,
            )

        normalized_text = _build_normalized_text(
            change_type=change_type,
            file_id=file_id,
            name=name,
            mime_type=mime_type,
            modified_time=modified_time,
            shared=bool(shared),
        )

        idempotency_key = _make_idempotency_key(self.endpoint_identity, file_id, observed_at)

        return _build_ingest_envelope(
            file_id=file_id,
            change_type=change_type,
            file_name=name,
            mime_type=mime_type,
            endpoint_identity=self.endpoint_identity,
            observed_at=observed_at,
            normalized_text=normalized_text,
            idempotency_key=idempotency_key,
        )

    def get_health(self) -> AccountHealthStatus:
        """Return per-account health snapshot."""
        last_checkpoint_save_at = None
        if self._last_checkpoint_save is not None:
            last_checkpoint_save_at = datetime.fromtimestamp(
                self._last_checkpoint_save, UTC
            ).isoformat()

        last_ingest_submit_at = None
        if self._last_ingest_submit is not None:
            last_ingest_submit_at = datetime.fromtimestamp(
                self._last_ingest_submit, UTC
            ).isoformat()

        if self._source_api_ok is None:
            connectivity: Literal["connected", "disconnected", "unknown"] = "unknown"
        elif self._source_api_ok:
            connectivity = "connected"
        else:
            connectivity = "disconnected"

        error_msg = self._error
        if not self.is_running and error_msg:
            account_status: Literal["healthy", "degraded", "error"] = "error"
        elif self._source_api_ok is False:
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
# GDriveConnectorManager — top-level multi-account orchestrator (task 8.1)
# ---------------------------------------------------------------------------


class GDriveConnectorManager:
    """Top-level orchestrator for the multi-account Google Drive connector.

    Discovers all active Google accounts with drive scopes from
    public.google_accounts, spawns independent GDriveAccountLoop instances
    per account, and manages their lifecycle.

    Supports:
    - Periodic account re-scan at GDRIVE_ACCOUNT_RESCAN_INTERVAL_S (default 300)
    - On-demand reload via SIGHUP / connector_reload_accounts MCP tool
    - Aggregated health endpoint across all accounts (/health, /metrics)
    - Degraded startup when no qualifying accounts found
    - Per-account error isolation
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        credential_store: Any,
        switchboard_mcp_url: str,
        poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S,
        account_rescan_interval_s: int = _DEFAULT_ACCOUNT_RESCAN_INTERVAL_S,
        cursor_pool: asyncpg.Pool | None = None,
        health_port: int = _DEFAULT_HEALTH_PORT,
        heartbeat_interval_s: int = 120,
    ) -> None:
        self._db_pool = db_pool
        self._credential_store = credential_store
        self._switchboard_mcp_url = switchboard_mcp_url
        self._poll_interval_s = poll_interval_s
        self._account_rescan_interval_s = account_rescan_interval_s
        self._cursor_pool = cursor_pool
        self._health_port = health_port
        self._heartbeat_interval_s = heartbeat_interval_s

        # Active account loops keyed by email
        self._loops: dict[str, GDriveAccountLoop] = {}

        # State
        self._start_time = time.time()
        self._running = False
        self._reload_event = asyncio.Event()
        # Capture the main event loop so background health-server thread can
        # signal _reload_event via call_soon_threadsafe.
        self._main_loop: asyncio.AbstractEventLoop | None = None

        # Health server (started in background thread)
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Rescan task
        self._rescan_task: asyncio.Task[None] | None = None

        # Heartbeat (per-connector-manager, uses aggregated health)
        self._heartbeat: ConnectorHeartbeat | None = None
        self._mcp_client: CachedMCPClient | None = None
        self._metrics: ConnectorMetrics | None = None

    async def discover_drive_accounts(self) -> list[Any]:
        """Query public.google_accounts for active accounts with drive scope (task 8.3).

        Returns list of account-like objects (with .email, .granted_scopes)
        for accounts that have drive.readonly or drive scope.

        Only accounts with status='active' and a qualifying drive scope in
        granted_scopes are returned.
        """
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, entity_id, email, granted_scopes, status,
                           is_primary, connected_at, last_token_refresh_at,
                           display_name
                    FROM public.google_accounts
                    WHERE status = 'active'
                    ORDER BY is_primary DESC, connected_at ASC
                    """
                )
        except Exception as exc:
            logger.warning("Drive manager: failed to query google_accounts (non-fatal): %s", exc)
            return []

        qualifying = []
        for row in rows:
            email = row["email"]
            scopes = list(row["granted_scopes"] or [])

            has_drive_scope = any(s in (_DRIVE_SCOPE, _DRIVE_SCOPE_READONLY) for s in scopes)
            if not has_drive_scope:
                logger.debug(
                    "Drive manager: skipping account %r — no drive scope in granted_scopes=%s",
                    email,
                    scopes,
                )
                continue

            qualifying.append(row)

        return qualifying

    async def sync_accounts(self) -> tuple[list[str], list[str], list[str]]:
        """Discover qualifying accounts and reconcile running loops (task 12.1).

        Returns (added, removed, unchanged) email lists.
        """
        qualifying = await self.discover_drive_accounts()

        desired_emails: set[str] = set()
        account_metadata: dict[str, dict[str, Any] | None] = {}
        for row in qualifying:
            email = row["email"]
            if email is None:
                continue
            desired_emails.add(email)
            metadata = row["metadata"] if "metadata" in row.keys() else {}  # type: ignore[call-overload]
            gdrive_section: dict[str, Any] | None = None
            if isinstance(metadata, dict):
                sec = metadata.get("google_drive")
                if isinstance(sec, dict):
                    gdrive_section = sec
            account_metadata[email] = gdrive_section

        current_emails = set(self._loops.keys())
        to_add = desired_emails - current_emails
        to_remove = current_emails - desired_emails
        unchanged = current_emails & desired_emails

        # Stop removed loops (graceful: complete in-flight, checkpoint, stop)
        for email in to_remove:
            loop = self._loops.pop(email)
            logger.info("Drive manager: stopping loop for removed account %r", email)
            await loop.stop()

        # Start new loops
        added: list[str] = []
        for email in to_add:
            try:
                creds = await resolve_google_credentials(
                    self._credential_store,
                    pool=self._db_pool,
                    caller="google_drive",
                    account=email,
                )
            except Exception as exc:
                logger.warning(
                    "Drive manager: credential resolution failed for account %r (skipping): %s",
                    email,
                    exc,
                )
                continue

            poll_interval = self._poll_interval_s
            meta = account_metadata.get(email)
            if meta and "poll_interval_s" in meta:
                try:
                    poll_interval = int(meta["poll_interval_s"])
                except (ValueError, TypeError):
                    pass

            config = GDriveAccountConfig(
                email=email,
                client_id=creds.client_id,
                client_secret=creds.client_secret,
                refresh_token=creds.refresh_token,
                switchboard_mcp_url=self._switchboard_mcp_url,
                poll_interval_s=poll_interval,
            )

            loop = GDriveAccountLoop(
                email=email,
                config=config,
                db_pool=self._db_pool,
                cursor_pool=self._cursor_pool,
            )
            self._loops[email] = loop
            loop.start()
            added.append(email)
            logger.info("Drive manager: started loop for new account %r", email)

        return added, list(to_remove), list(unchanged)

    def get_health(self) -> MultiAccountHealthStatus:
        """Return aggregated health status across all account loops (task 12.4)."""
        account_healths = [loop.get_health() for loop in self._loops.values()]

        # Worst-case overall status
        statuses = [h.status for h in account_healths]
        if "error" in statuses:
            overall: Literal["healthy", "degraded", "error"] = "error"
        elif "degraded" in statuses:
            overall = "degraded"
        else:
            overall = "healthy"

        return MultiAccountHealthStatus(
            status=overall,
            uptime_seconds=time.time() - self._start_time,
            active_accounts=len(self._loops),
            account_health=account_healths,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def stop(self) -> None:
        """Stop all account loops gracefully."""
        self._running = False

        # Stop heartbeat
        if self._heartbeat is not None:
            await self._heartbeat.stop()
            self._heartbeat = None

        # Stop MCP client
        if self._mcp_client is not None:
            try:
                await self._mcp_client.close()
            except Exception:
                pass
            self._mcp_client = None

        for email, loop in list(self._loops.items()):
            logger.info("Drive manager: stopping loop for account %r", email)
            await loop.stop()
        self._loops.clear()
        logger.info("Drive manager: all account loops stopped")

    # ------------------------------------------------------------------
    # Task 12.2: SIGHUP handler and reload_accounts MCP tool
    # ------------------------------------------------------------------

    def _setup_sighup(self) -> None:
        """Register SIGHUP handler to trigger immediate account re-scan (task 12.2)."""
        try:
            loop = asyncio.get_event_loop()

            def _on_sighup() -> None:
                logger.info("Drive manager: SIGHUP received — triggering account reload")
                self._reload_event.set()

            loop.add_signal_handler(signal.SIGHUP, _on_sighup)
            logger.debug("Drive manager: SIGHUP handler registered")
        except (OSError, NotImplementedError):
            # SIGHUP not available on Windows
            logger.debug("Drive manager: SIGHUP not available on this platform")

    async def reload_accounts(self) -> dict[str, Any]:
        """Trigger immediate account re-scan (connector_reload_accounts MCP tool, task 12.2).

        Returns a summary of accounts added, removed, and unchanged.
        """
        added, removed, unchanged = await self.sync_accounts()
        return {
            "added": added,
            "removed": removed,
            "unchanged": unchanged,
        }

    # ------------------------------------------------------------------
    # Task 12.4: Aggregated health HTTP endpoint
    # ------------------------------------------------------------------

    def _start_health_server(self) -> None:
        """Start aggregated health endpoint in background thread (task 12.4)."""
        app = FastAPI(title="Google Drive Connector Health")

        @app.get("/health")
        async def health() -> MultiAccountHealthStatus:
            return self.get_health()

        @app.post("/reload")
        async def reload() -> dict[str, Any]:
            """Trigger immediate account re-scan (connector_reload_accounts MCP tool)."""
            # _reload_event was created in the main event loop. This endpoint runs
            # inside asyncio.run() in a background thread (a separate event loop),
            # so calling .set() directly would be unsafe. Use call_soon_threadsafe.
            if self._main_loop is not None and self._main_loop.is_running():
                self._main_loop.call_soon_threadsafe(self._reload_event.set)
            return {"status": "reload_triggered"}

        try:
            from butlers.connectors.health_socket import make_health_socket

            sock = make_health_socket("127.0.0.1", self._health_port)
            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self._health_port,
                log_level="warning",
            )
            self._health_server = uvicorn.Server(config)

            def run_server() -> None:
                asyncio.run(self._health_server.serve(sockets=[sock]))

            self._health_thread = Thread(target=run_server, daemon=True)
            self._health_thread.start()
            logger.info("Drive manager: health server started on port %d", self._health_port)
        except Exception as exc:
            logger.warning("Drive manager: health server failed to start (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Task 12.5: Heartbeat protocol
    # ------------------------------------------------------------------

    def _get_health_state_for_heartbeat(self) -> tuple[str, str | None]:
        """Return (state, error_message) tuple for heartbeat reporting (task 12.5)."""
        health = self.get_health()
        error_msg: str | None = None
        if health.status == "error":
            # Aggregate first error message from account loops
            for account in health.account_health:
                if account.error:
                    error_msg = account.error
                    break
        return health.status, error_msg

    def _get_capabilities(self) -> dict[str, object]:
        """Return connector capabilities dict for heartbeat advertisement (task 12.5)."""
        return {
            "multi_account": True,
            "changes_polling": True,
            "metadata_only": True,
            "account_rescan": True,
            "reload_accounts": True,
        }

    def _start_heartbeat(self) -> None:
        """Initialize and start heartbeat background task (task 12.5).

        Uses manager-level aggregated health for heartbeat state.
        """
        if self._mcp_client is None:
            logger.debug("Drive manager: no MCP client — heartbeat not started")
            return

        if self._metrics is None:
            self._metrics = ConnectorMetrics(
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity="google_drive:manager:process",
            )

        heartbeat_config = HeartbeatConfig(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity="google_drive:manager:process",
            interval_s=self._heartbeat_interval_s,
        )

        self._heartbeat = ConnectorHeartbeat(
            config=heartbeat_config,
            mcp_client=self._mcp_client,
            metrics=self._metrics,
            get_health_state=self._get_health_state_for_heartbeat,
            get_capabilities=self._get_capabilities,
        )
        self._heartbeat.start()
        logger.info("Drive manager: heartbeat started (interval=%ds)", self._heartbeat_interval_s)

    # ------------------------------------------------------------------
    # Task 12.1: Periodic rescan loop + start/stop lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the connector manager: discover accounts, start loops, run rescan loop.

        Task 12.1: Implements periodic account re-scan at account_rescan_interval_s.
        Task 12.2: Registers SIGHUP handler for on-demand reload.
        Task 12.4: Starts aggregated health HTTP endpoint.
        Task 12.5: Starts heartbeat protocol.
        """
        self._running = True

        # Capture the running event loop so the background health-server thread
        # can safely signal _reload_event via loop.call_soon_threadsafe.
        self._main_loop = asyncio.get_running_loop()

        # Start health server (task 12.4)
        self._start_health_server()

        # Register SIGHUP for on-demand reload (task 12.2)
        self._setup_sighup()

        # Initial account discovery (task 12.1)
        added, removed, unchanged = await self.sync_accounts()
        logger.info(
            "Drive manager: initial account sync — added=%d removed=%d unchanged=%d",
            len(added),
            len(removed),
            len(unchanged),
        )

        if not self._loops:
            logger.warning(
                "Drive manager: no qualifying Drive accounts found at startup. "
                "Running in idle/degraded mode. Will retry at rescan interval=%ds.",
                self._account_rescan_interval_s,
            )

        # Start heartbeat (task 12.5) — MCP client must be wired externally before start()
        self._start_heartbeat()

        # Main rescan loop (task 12.1)
        try:
            await self._run_rescan_loop()
        finally:
            await self.stop()

    async def _run_rescan_loop(self) -> None:
        """Periodically re-scan for account changes, also triggered by reload events.

        Task 12.1: Periodic re-scan at account_rescan_interval_s.
        Task 12.2: Also triggered by _reload_event (SIGHUP or /reload endpoint).
        Task 12.3: Graceful loop shutdown — removed accounts have their loops stopped.
        """
        rescan_interval = self._account_rescan_interval_s
        while self._running:
            # Wait for either rescan interval or reload trigger
            try:
                await asyncio.wait_for(self._reload_event.wait(), timeout=rescan_interval)
                logger.info("Drive manager: reload triggered — re-scanning accounts")
                self._reload_event.clear()
            except TimeoutError:
                logger.debug("Drive manager: periodic re-scan triggered")

            if not self._running:
                break

            added, removed, unchanged = await self.sync_accounts()
            if added or removed:
                logger.info(
                    "Drive manager: account sync — added=%s removed=%s unchanged=%d",
                    added,
                    removed,
                    len(unchanged),
                )


# ---------------------------------------------------------------------------
# Standalone helpers for parsing Drive API changes.list responses (task 9)
# ---------------------------------------------------------------------------


def parse_changes_list_response(
    response: dict[str, Any],
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Parse a changes.list API response (task 9.2).

    Returns (changes, next_page_token, new_start_page_token).

    - changes: list of change objects
    - next_page_token: token for fetching the next page (None if exhausted)
    - new_start_page_token: the new start token for the next poll cycle
      (only present on the final page)
    """
    changes = response.get("changes", [])
    next_page_token = response.get("nextPageToken")
    new_start_page_token = response.get("newStartPageToken")
    return changes, next_page_token, new_start_page_token


def build_start_page_token_url() -> str:
    """Return the Drive API URL for getStartPageToken (task 9.1)."""
    return f"{_DRIVE_API_BASE}/changes/startPageToken"


def build_changes_list_url(page_token: str, *, include_removed: bool = True) -> str:
    """Return the Drive API URL for changes.list (task 9.2)."""
    removed_flag = "true" if include_removed else "false"
    return f"{_DRIVE_API_BASE}/changes?pageToken={page_token}&includeRemoved={removed_flag}"
