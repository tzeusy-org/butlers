"""Spotify connector runtime for listening context ingestion via adaptive polling.

This connector polls the Spotify Web API for current playback state and recently-played
tracks, detects listening state transitions, aggregates logical listening sessions, and
submits normalized ingest.v1 envelopes to the Switchboard.

Unlike messaging connectors, the Spotify connector has no discretion layer (all events
are the user's own activity), no per-chat buffering, and no interactive routing. It is
a pure polling-and-ingest connector.

Key behaviors:
- Credential resolution via CredentialStore (SPOTIFY_CLIENT_ID, SPOTIFY_ACCESS_TOKEN,
  SPOTIFY_REFRESH_TOKEN, SPOTIFY_TOKEN_EXPIRES_AT)
- Endpoint identity auto-resolution via GET /me at startup
- Adaptive polling loop: SPOTIFY_POLL_ACTIVE_S (60s) when playing, exponential backoff
  to SPOTIFY_POLL_IDLE_S (300s) when idle
- Recently-played gap-filling via GET /me/player/recently-played with `after` cursor
- ListeningSessionTracker state machine (idle → active → draining → idle)
- ingest.v1 envelope construction for spotify.track_change and spotify.session_summary
- IngestionPolicyEvaluator source filter gate with scope=connector:spotify:<identity>
- Filtered event batch flush to connectors.filtered_events
- Checkpoint persistence via cursor_store keyed by ("spotify", "<endpoint_identity>")
- Switchboard MCP submission via CachedMCPClient
- Credential error recovery: stop polling on auth failure, re-check every 60s
- Graceful shutdown on SIGTERM/SIGINT: complete poll, persist checkpoint, final heartbeat
- Prometheus metrics (standard + Spotify-specific)
- Health/metrics HTTP server on CONNECTOR_HEALTH_PORT (default 40083)

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- CONNECTOR_PROVIDER=spotify (required)
- CONNECTOR_CHANNEL=spotify (required)
- SPOTIFY_POLL_ACTIVE_S (optional, default 60): polling interval during active playback
- SPOTIFY_POLL_IDLE_S (optional, default 300): maximum polling interval during idle
- SPOTIFY_SESSION_IDLE_TIMEOUT_S (optional, default 300): idle timeout before session close
- CONNECTOR_HEALTH_PORT (optional, default 40083): health/metrics HTTP port
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120): heartbeat interval
- CONNECTOR_MAX_INFLIGHT (optional, default 8): max concurrent ingest submissions
- CONNECTOR_BUTLER_DB_NAME (optional; local butler DB for cursor/policy)
- BUTLER_SHARED_DB_NAME (optional; shared credential DB, defaults to 'butlers')

Security requirements:
- Never commit credentials or session artifacts to version control
- OAuth tokens resolved exclusively from CredentialStore (DB), not environment variables
"""

from __future__ import annotations

import asyncio
import json
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
from prometheus_client import Counter, Histogram, generate_latest

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import db_params_from_env, schema_search_path, should_retry_with_ssl_disable
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "spotify"
_CONNECTOR_CHANNEL = "spotify_user_client"

# Durable evidence table for Chronicler projection (RFC 0014 §D9).
_SESSION_EVIDENCE_TABLE = "connectors.spotify_listening_sessions"
_CONNECTOR_PROVIDER = "spotify"

# Spotify API URLs
_SPOTIFY_API_BASE = "https://api.spotify.com/v1"
_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

# Default configuration
_DEFAULT_POLL_ACTIVE_S = 60
_DEFAULT_POLL_IDLE_S = 300
_DEFAULT_SESSION_IDLE_TIMEOUT_S = 300
_DEFAULT_DIGEST_INTERVAL_S = 3600
_DEFAULT_GAP_FILL_IDLE_INTERVAL_S = 10800  # 3 hrs — batch recently-played during idle
_DEFAULT_HEALTH_PORT = 40083
_DEFAULT_MAX_INFLIGHT = 8

# Credential keys in CredentialStore
_CRED_CLIENT_ID = "SPOTIFY_CLIENT_ID"
_CRED_ACCESS_TOKEN = "SPOTIFY_ACCESS_TOKEN"
_CRED_REFRESH_TOKEN = "SPOTIFY_REFRESH_TOKEN"
_CRED_TOKEN_EXPIRES_AT = "SPOTIFY_TOKEN_EXPIRES_AT"

# Token proactive-refresh buffer: refresh 5 minutes before expiry
_TOKEN_REFRESH_BUFFER_S = 300

# Rate-limit backoff config
_RATE_LIMIT_INITIAL_S = 30.0
_RATE_LIMIT_MAX_S = 600.0

# Credential re-check interval when in auth-error state
_CREDENTIAL_RECHECK_S = 60

# Idle polling backoff step multiplier
_IDLE_BACKOFF_MULTIPLIER = 2.0

# ---------------------------------------------------------------------------
# Spotify-specific Prometheus metrics
# ---------------------------------------------------------------------------

spotify_polls_total = Counter(
    "connector_spotify_polls_total",
    "Total number of Spotify poll cycles",
    labelnames=["endpoint_identity", "status"],
)

spotify_context_starts_total = Counter(
    "connector_spotify_context_starts_total",
    "Total number of context start events emitted",
    labelnames=["endpoint_identity"],
)

spotify_digests_total = Counter(
    "connector_spotify_digests_total",
    "Total number of listening digest events emitted",
    labelnames=["endpoint_identity"],
)

spotify_sessions_total = Counter(
    "connector_spotify_sessions_total",
    "Total number of listening sessions closed",
    labelnames=["endpoint_identity"],
)

spotify_session_duration_seconds = Histogram(
    "connector_spotify_session_duration_seconds",
    "Duration of listening sessions in seconds",
    labelnames=["endpoint_identity"],
    buckets=(60, 300, 600, 1200, 1800, 3600, 7200, 14400),
)

spotify_token_refreshes_total = Counter(
    "connector_spotify_token_refreshes_total",
    "Total number of Spotify token refresh attempts",
    labelnames=["endpoint_identity", "status"],
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SpotifyConnectorConfig:
    """Configuration for the Spotify connector runtime."""

    switchboard_mcp_url: str
    provider: str = _CONNECTOR_PROVIDER
    channel: str = _CONNECTOR_CHANNEL

    # Polling
    poll_active_s: int = _DEFAULT_POLL_ACTIVE_S
    poll_idle_s: int = _DEFAULT_POLL_IDLE_S
    session_idle_timeout_s: int = _DEFAULT_SESSION_IDLE_TIMEOUT_S
    digest_interval_s: int = _DEFAULT_DIGEST_INTERVAL_S
    gap_fill_idle_interval_s: int = _DEFAULT_GAP_FILL_IDLE_INTERVAL_S

    # Concurrency / health
    max_inflight: int = _DEFAULT_MAX_INFLIGHT
    health_port: int = _DEFAULT_HEALTH_PORT

    @classmethod
    def from_env(cls) -> SpotifyConnectorConfig:
        """Load non-credential configuration from environment variables."""
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL", "").strip()
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL is required")

        def _int(key: str, default: int) -> int:
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Invalid value for %s=%r, using default %d", key, raw, default)
                return default

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            provider=os.environ.get("CONNECTOR_PROVIDER", _CONNECTOR_PROVIDER),
            channel=os.environ.get("CONNECTOR_CHANNEL", _CONNECTOR_CHANNEL),
            poll_active_s=_int("SPOTIFY_POLL_ACTIVE_S", _DEFAULT_POLL_ACTIVE_S),
            poll_idle_s=_int("SPOTIFY_POLL_IDLE_S", _DEFAULT_POLL_IDLE_S),
            session_idle_timeout_s=_int(
                "SPOTIFY_SESSION_IDLE_TIMEOUT_S", _DEFAULT_SESSION_IDLE_TIMEOUT_S
            ),
            digest_interval_s=_int("SPOTIFY_DIGEST_INTERVAL_S", _DEFAULT_DIGEST_INTERVAL_S),
            gap_fill_idle_interval_s=_int(
                "SPOTIFY_GAP_FILL_IDLE_INTERVAL_S", _DEFAULT_GAP_FILL_IDLE_INTERVAL_S
            ),
            max_inflight=_int("CONNECTOR_MAX_INFLIGHT", _DEFAULT_MAX_INFLIGHT),
            health_port=_int("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
        )


# ---------------------------------------------------------------------------
# Listening session state machine
# ---------------------------------------------------------------------------

SessionState = Literal["idle", "active", "draining"]


@dataclass
class ListeningSession:
    """A single aggregated listening session.

    A session spans contiguous playback within the same playlist/album context.
    """

    context_uri: str | None  # playlist:xxx / album:xxx / None
    started_at: datetime
    context_name: str | None = None
    track_names: list[str] = field(default_factory=list)
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    drain_started_at: datetime | None = None
    last_digest_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_digest_track_index: int = 0

    @property
    def track_count(self) -> int:
        return len(self.track_names)

    @property
    def duration_seconds(self) -> float:
        end = self.drain_started_at or datetime.now(UTC)
        return max(0.0, (end - self.started_at).total_seconds())


class ListeningSessionTracker:
    """State machine for aggregating Spotify playback into listening sessions.

    Emits batched events to minimise downstream token costs:
    - context_start: when playback begins from idle (user starts listening)
    - session_summary: when a session ends (drain timeout after playback stops)

    All track and context changes during continuous playback are accumulated
    silently — no events are emitted for them.  Sessions end ONLY when playback
    stops long enough for the drain timeout to expire.  This prevents autoplay,
    radio, and DJ mode from generating per-track LLM sessions when Spotify
    cycles through different context URIs.

    The connector checks for periodic digest emission separately (hourly).

    States:
        idle     — no active playback
        active   — currently playing (context changes are accumulated, not split)
        draining — playback stopped; waiting for idle timeout before closing session
    """

    def __init__(
        self,
        idle_timeout_s: int = _DEFAULT_SESSION_IDLE_TIMEOUT_S,
        digest_interval_s: int = _DEFAULT_DIGEST_INTERVAL_S,
    ) -> None:
        self._idle_timeout_s = idle_timeout_s
        self._digest_interval = timedelta(seconds=digest_interval_s)
        self._state: SessionState = "idle"
        self._session: ListeningSession | None = None
        self._last_track_id: str | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def current_session(self) -> ListeningSession | None:
        return self._session

    def process_playback(
        self,
        *,
        track_id: str,
        track_name: str,
        context_uri: str | None,
        context_name: str | None = None,
        now: datetime | None = None,
    ) -> tuple[list[str], list[ListeningSession]]:
        """Process an active playback event.

        Returns:
            (events, closed_sessions) where events is a list of event type strings
            ("context_start") and closed_sessions is a list of sessions that were
            closed (each should emit a session_summary).

        Track changes during continuous playback are accumulated silently —
        no per-track or per-context events are emitted.  Sessions end only
        when playback stops (drain timeout).
        """
        if now is None:
            now = datetime.now(UTC)

        events: list[str] = []
        closed_sessions: list[ListeningSession] = []

        if self._state == "idle":
            # Start a new session
            self._session = ListeningSession(
                context_uri=context_uri,
                started_at=now,
                context_name=context_name,
                track_names=[track_name],
                last_activity_at=now,
                last_digest_at=now,
            )
            self._last_track_id = track_id
            self._state = "active"
            events.append("context_start")

        elif self._state == "active":
            assert self._session is not None
            if (
                self._session.context_name is None
                and context_name
                and self._session.context_uri == context_uri
            ):
                self._session.context_name = context_name
            if track_id == self._last_track_id:
                # Same track: update last activity, no event
                self._session.last_activity_at = now
            else:
                # Track changed — accumulate silently regardless of context.
                # Context changes during continuous playback (autoplay, radio,
                # DJ) are not reliable session boundaries.  Sessions end only
                # when playback stops (drain timeout).
                self._last_track_id = track_id
                self._session.track_names.append(track_name)
                self._session.last_activity_at = now

        elif self._state == "draining":
            assert self._session is not None
            # Playback resumed: continue existing session, clear drain timer.
            # Context may have changed (user switched playlists during brief
            # pause) but the session is still alive — accumulate silently.
            self._session.drain_started_at = None
            self._session.last_activity_at = now
            if (
                self._session.context_name is None
                and context_name
                and self._session.context_uri == context_uri
            ):
                self._session.context_name = context_name
            if track_id != self._last_track_id:
                self._last_track_id = track_id
                self._session.track_names.append(track_name)
            self._state = "active"

        return events, closed_sessions

    def check_digest_due(self, now: datetime | None = None) -> bool:
        """Return True if a listening_digest should be emitted."""
        if self._state != "active" or self._session is None:
            return False
        if now is None:
            now = datetime.now(UTC)
        elapsed = now - self._session.last_digest_at
        return elapsed >= self._digest_interval and self._session.track_count > 0

    def mark_digest_emitted(self, now: datetime | None = None) -> None:
        """Record that a digest was just emitted, resetting the timer."""
        if self._session is not None:
            self._session.last_digest_at = now or datetime.now(UTC)
            self._session.last_digest_track_index = self._session.track_count

    def process_no_playback(self, now: datetime | None = None) -> list[ListeningSession]:
        """Process a poll result with no active playback.

        Returns list of sessions that were closed (at most one).
        """
        if now is None:
            now = datetime.now(UTC)

        closed_sessions: list[ListeningSession] = []

        if self._state == "idle":
            pass  # Nothing to do

        elif self._state == "active":
            assert self._session is not None
            # Begin drain timeout
            self._session.drain_started_at = now
            self._state = "draining"

        elif self._state == "draining":
            assert self._session is not None
            # Check if idle timeout exceeded
            drain_start = self._session.drain_started_at or now
            elapsed = (now - drain_start).total_seconds()
            if elapsed >= self._idle_timeout_s:
                closed_sessions.append(self._session)
                self._session = None
                self._last_track_id = None
                self._state = "idle"

        return closed_sessions


# ---------------------------------------------------------------------------
# Spotify API client helpers
# ---------------------------------------------------------------------------


class SpotifyCredentialError(Exception):
    """Raised when Spotify credentials are missing or invalid."""


class SpotifyRateLimitError(Exception):
    """Raised when Spotify API returns HTTP 429 with Retry-After."""

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"Rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def _clean_context_name(value: Any) -> str | None:
    """Return a non-empty Spotify context name, or None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _parse_spotify_context_uri(context_uri: str | None) -> tuple[str | None, str | None]:
    """Split a Spotify context URI into (kind, id)."""
    if not context_uri:
        return None, None
    parts = context_uri.split(":", 2)
    if len(parts) != 3 or parts[0] != "spotify":
        return None, None
    return parts[1], parts[2]


def _context_name_from_item(context_uri: str | None, item: dict[str, Any]) -> str | None:
    """Resolve a context name directly from the current track payload when possible."""
    context_type, context_id = _parse_spotify_context_uri(context_uri)
    if not context_type or not context_id:
        return None

    if context_type == "album":
        album = item.get("album") or {}
        album_id = str(album.get("id") or "").strip()
        album_uri = str(album.get("uri") or "").strip()
        if album_id == context_id or album_uri == context_uri:
            return _clean_context_name(album.get("name"))

    if context_type == "artist":
        for artist in item.get("artists", []) or []:
            artist_id = str(artist.get("id") or "").strip()
            artist_uri = str(artist.get("uri") or "").strip()
            if artist_id == context_id or artist_uri == context_uri:
                return _clean_context_name(artist.get("name"))

    return None


def _resolve_context_label(
    *,
    context_uri: str | None,
    context_name: str | None = None,
    raw_payload: dict[str, Any] | None = None,
) -> str | None:
    """Choose the best human-readable label for a Spotify listening context."""
    explicit_name = _clean_context_name(context_name)
    if explicit_name:
        return explicit_name

    if raw_payload:
        embedded_name = _clean_context_name(raw_payload.get("context_name"))
        if embedded_name:
            return embedded_name

    if context_uri:
        return context_uri.split(":")[-1]
    return None


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def build_context_start_envelope(
    *,
    endpoint_identity: str,
    spotify_user_id: str,
    track_id: str,
    track_name: str,
    artist_names: list[str],
    album_name: str,
    duration_ms: int,
    context_uri: str | None,
    context_name: str | None = None,
    device_name: str | None,
    timestamp_ms: int,
    raw_payload: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a spotify.context_start event.

    Emitted once when a new listening context begins (playlist, album, etc.).
    """
    artist_str = ", ".join(artist_names) if artist_names else "unknown artist"
    context_label = _resolve_context_label(
        context_uri=context_uri,
        context_name=context_name,
        raw_payload=raw_payload,
    )
    if context_label:
        normalized_text = (
            f"Started listening to {context_label} — first track: {track_name} by {artist_str}"
        )
    else:
        normalized_text = f"Started listening to {track_name} by {artist_str}"

    payload_raw = dict(raw_payload)
    if context_label:
        payload_raw["context_name"] = context_label

    idempotency_key = f"spotify:{endpoint_identity}:ctx:{timestamp_ms}:{context_uri or track_id}"
    external_event_id = f"spotify:ctx:{timestamp_ms}:{context_uri or track_id}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": external_event_id,
            "external_thread_id": context_uri,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": spotify_user_id,
        },
        "payload": {
            "raw": payload_raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_listening_digest_envelope(
    *,
    endpoint_identity: str,
    spotify_user_id: str,
    session: ListeningSession,
    observed_at: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a spotify.listening_digest event.

    Emitted periodically (default every 60 min) during active listening.
    Shows only tracks played since the last digest (or session start).
    """
    context_label = _resolve_context_label(
        context_uri=session.context_uri,
        context_name=session.context_name,
    )
    period_tracks = session.track_names[session.last_digest_track_index :]
    period_count = len(period_tracks)
    track_list = ", ".join(period_tracks)

    if context_label:
        normalized_text = (
            f"Listening digest: {period_count} tracks on {context_label} — {track_list}"
        )
    else:
        normalized_text = f"Listening digest: {period_count} tracks — {track_list}"

    digest_start_ms = int(session.last_digest_at.timestamp() * 1000)
    idempotency_key = f"spotify:{endpoint_identity}:digest:{digest_start_ms}"
    external_event_id = f"spotify:digest:{digest_start_ms}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": external_event_id,
            "external_thread_id": session.context_uri,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": spotify_user_id,
        },
        "payload": {
            "raw": {
                "digest_start": session.last_digest_at.isoformat(),
                "period_track_count": period_count,
                "total_track_count": session.track_count,
                "context_uri": session.context_uri,
                "context_name": context_label,
                "period_tracks": period_tracks,
                "tracks": session.track_names,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_session_summary_envelope(
    *,
    endpoint_identity: str,
    spotify_user_id: str,
    session: ListeningSession,
    observed_at: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a spotify.session_summary event."""
    duration_s = int(session.duration_seconds)
    minutes = duration_s // 60
    seconds = duration_s % 60
    duration_label = f"{minutes}m{seconds}s" if minutes > 0 else f"{seconds}s"

    context_label = _resolve_context_label(
        context_uri=session.context_uri,
        context_name=session.context_name,
    )
    if context_label:
        normalized_text = (
            f"Listening session: {session.track_count} tracks over "
            f"{duration_label} from {context_label}"
        )
    else:
        normalized_text = f"Listening session: {session.track_count} tracks over {duration_label}"

    session_start_ms = int(session.started_at.timestamp() * 1000)
    external_event_id = f"spotify:session:{session_start_ms}"
    idempotency_key = f"spotify:{endpoint_identity}:session:{session_start_ms}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": external_event_id,
            "external_thread_id": session.context_uri,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": spotify_user_id,
        },
        "payload": {
            "raw": {
                "session_start": session.started_at.isoformat(),
                "session_end": (session.drain_started_at or datetime.now(UTC)).isoformat(),
                "duration_seconds": duration_s,
                "track_count": session.track_count,
                "context_uri": session.context_uri,
                "context_name": context_label,
                "tracks": session.track_names,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


# ---------------------------------------------------------------------------
# Durable session evidence persistence (Chronicler read surface, RFC 0014)
# ---------------------------------------------------------------------------


async def persist_session_summary(
    pool: asyncpg.Pool,
    *,
    endpoint_identity: str,
    spotify_user_id: str,
    session: ListeningSession,
) -> bool:
    """Upsert a listening session row into the durable evidence table.

    Works for both in-progress and closed sessions:
      - In-progress: ``session.drain_started_at`` is None → ``ended_at`` is
        set to ``last_activity_at`` (or now), so the Music lane shows a
        live-extending bar instead of staying empty until the 5-minute
        idle-drain closes the session.
      - Closed: ``session.drain_started_at`` is set → final ``ended_at``.

    On conflict (same ``idempotency_key`` from a prior poll) the mutable
    fields are updated in place: ``ended_at``, ``duration_seconds``,
    ``track_count``, ``track_names``, ``context_name``, ``recorded_at``.
    The Chronicler adapter watermarks on ``recorded_at`` so updates flow
    through to ``chronicler.episodes`` via ``upsert_episode`` on the next
    projection run.

    This table is the Chronicler-readable evidence surface for
    ``spotify.session_summary`` (RFC 0014 §D9).  Errors are caught by
    callers and do NOT abort the ingest submission path.

    Returns True for both insert and update (the row exists with current
    state). The boolean is retained for backward-compat with callers that
    log on insert; updates are indistinguishable from inserts at this
    layer by design.
    """
    session_start_ms = int(session.started_at.timestamp() * 1000)
    idempotency_key = f"spotify:{endpoint_identity}:session:{session_start_ms}"
    ended_at = session.drain_started_at or session.last_activity_at or datetime.now(UTC)
    duration_s = int(max(0.0, (ended_at - session.started_at).total_seconds()))

    result = await pool.fetchval(
        f"""
        INSERT INTO {_SESSION_EVIDENCE_TABLE} (
            idempotency_key,
            endpoint_identity,
            spotify_user_id,
            started_at,
            ended_at,
            duration_seconds,
            track_count,
            track_names,
            context_uri,
            context_name
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
        ON CONFLICT (idempotency_key) DO UPDATE SET
            ended_at         = EXCLUDED.ended_at,
            duration_seconds = EXCLUDED.duration_seconds,
            track_count      = EXCLUDED.track_count,
            track_names      = EXCLUDED.track_names,
            context_name     = COALESCE(
                EXCLUDED.context_name,
                {_SESSION_EVIDENCE_TABLE}.context_name
            ),
            recorded_at      = now()
        RETURNING id
        """,
        idempotency_key,
        endpoint_identity,
        spotify_user_id,
        session.started_at,
        ended_at,
        duration_s,
        session.track_count,
        json.dumps(list(session.track_names)),
        session.context_uri,
        session.context_name,
    )
    return result is not None


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class SpotifyConnector:
    """Spotify polling connector.

    Single-account connector (one Spotify user per process). Manages the
    full lifecycle: startup, polling loop, session tracking, ingest submission,
    checkpoint persistence, heartbeat, health endpoint, and graceful shutdown.
    """

    def __init__(
        self,
        config: SpotifyConnectorConfig,
        db_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool

        # Will be set during startup
        self._endpoint_identity: str = ""
        self._spotify_user_id: str = ""
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._client_id: str | None = None
        self._token_expires_at: datetime | None = None

        # HTTP client (created in start())
        self._http_client: httpx.AsyncClient | None = None

        # MCP client
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url,
            client_name="spotify-connector",
        )

        # Polling state
        self._current_poll_interval_s: float = config.poll_active_s
        self._last_recently_played_cursor: str | None = None  # after= timestamp (ms)
        self._last_gap_fill_poll_at: float = 0.0  # monotonic; 0 = never polled
        self._context_name_cache: dict[str, str] = {}

        # Session tracking
        self._session_tracker = ListeningSessionTracker(
            idle_timeout_s=config.session_idle_timeout_s,
            digest_interval_s=config.digest_interval_s,
        )

        # Auth error state
        self._auth_error: bool = False
        self._auth_error_message: str | None = None

        # Checkpoint
        self._last_checkpoint_cursor: str | None = None
        self._last_checkpoint_save: float | None = None

        # Shutdown event
        self._shutdown_event = asyncio.Event()
        self._running = False

        # Metrics
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity="",  # Updated after identity resolution
        )

        # Health tracking
        self._start_time = time.time()
        self._last_ingest_submit: float | None = None
        self._source_api_ok: bool | None = None

        # Heartbeat (initialized after identity resolution)
        self._heartbeat: ConnectorHeartbeat | None = None

        # Ingestion policy (initialized after identity resolution)
        self._ingestion_policy: IngestionPolicyEvaluator | None = None

        # Filtered event buffer (initialized after identity resolution)
        self._filtered_event_buffer: FilteredEventBuffer | None = None

        # Semaphore for inflight requests
        self._semaphore = asyncio.Semaphore(config.max_inflight)

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Full startup sequence followed by the main poll loop."""
        logger.info("SpotifyConnector starting")

        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._running = True

        try:
            # Set up signal handlers
            try:
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, self._handle_signal)
            except (NotImplementedError, OSError):
                logger.debug("SpotifyConnector: signal handlers not supported on this platform")

            # Phase 1: Resolve credentials
            await self._resolve_credentials()

            # Phase 2: Resolve endpoint identity via GET /me
            await self._resolve_identity()

            # Phase 3: Post-identity initialization
            self._endpoint_identity_ready()

            # Phase 4: Load checkpoint
            await self._load_checkpoint()

            # Phase 5: Wait for Switchboard readiness
            try:
                await wait_for_switchboard_ready(self._config.switchboard_mcp_url)
            except TimeoutError:
                logger.warning(
                    "SpotifyConnector: Switchboard readiness probe timed out; proceeding."
                )

            # Phase 6: Start health server
            self._start_health_server()

            # Phase 7: Start heartbeat
            assert self._heartbeat is not None
            self._heartbeat.start()

            # Phase 8: Send initial heartbeat
            try:
                await self._heartbeat._send_heartbeat()
            except Exception as exc:
                logger.debug("SpotifyConnector: initial heartbeat failed (non-fatal): %s", exc)

            # Phase 9: Run main poll loop
            await self._poll_loop()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Request graceful shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("SpotifyConnector: stop() called, requesting shutdown")
            self._shutdown_event.set()

    def _handle_signal(self) -> None:
        """Handle SIGTERM/SIGINT: request graceful shutdown."""
        logger.info("SpotifyConnector: received shutdown signal")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        """Graceful shutdown: persist checkpoint, final heartbeat, clean up."""
        logger.info("SpotifyConnector: shutting down")
        self._running = False

        # Persist checkpoint
        await self._save_checkpoint()

        # Send final heartbeat
        if self._heartbeat is not None:
            try:
                await self._heartbeat._send_heartbeat()
            except Exception as exc:
                logger.debug("SpotifyConnector: final heartbeat failed (non-fatal): %s", exc)
            await self._heartbeat.stop()

        # Stop health server
        if self._health_server is not None:
            self._health_server.should_exit = True

        # Close HTTP client
        if self._http_client is not None:
            await self._http_client.aclose()

        logger.info("SpotifyConnector: shutdown complete")

    # ------------------------------------------------------------------
    # Credential resolution
    # ------------------------------------------------------------------

    async def _resolve_credentials(self) -> None:
        """Resolve Spotify OAuth credentials from CredentialStore.

        Blocks until credentials are available or raises SpotifyCredentialError.
        """
        if self._db_pool is None:
            raise SpotifyCredentialError(
                "No DB pool available — cannot resolve Spotify credentials"
            )

        store = CredentialStore(self._db_pool)

        client_id = await store.resolve(_CRED_CLIENT_ID)
        access_token = await store.resolve(_CRED_ACCESS_TOKEN)
        refresh_token = await store.resolve(_CRED_REFRESH_TOKEN)
        expires_at_str = await store.resolve(_CRED_TOKEN_EXPIRES_AT)

        if not client_id or not refresh_token:
            raise SpotifyCredentialError(
                "Spotify credentials not configured. "
                "Please connect your Spotify account via the dashboard settings."
            )

        self._client_id = client_id
        self._refresh_token = refresh_token

        if access_token:
            self._access_token = access_token

        if expires_at_str:
            try:
                self._token_expires_at = datetime.fromisoformat(
                    expires_at_str.replace("Z", "+00:00")
                )
            except ValueError:
                logger.warning(
                    "SpotifyConnector: could not parse SPOTIFY_TOKEN_EXPIRES_AT=%r",
                    expires_at_str,
                )

        logger.info("SpotifyConnector: credentials resolved from CredentialStore")

    async def _reload_credentials(self) -> bool:
        """Attempt to reload credentials. Returns True if credentials are now valid."""
        if self._db_pool is None:
            return False
        try:
            await self._resolve_credentials()
            return bool(self._client_id and self._refresh_token)
        except Exception as exc:
            logger.debug("SpotifyConnector: credential reload failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        """Return a valid access token, refreshing proactively if near expiry."""
        now = datetime.now(UTC)
        if (
            self._access_token is not None
            and self._token_expires_at is not None
            and now < self._token_expires_at - timedelta(seconds=_TOKEN_REFRESH_BUFFER_S)
        ):
            return self._access_token

        # Need refresh
        return await self._refresh_access_token()

    async def _refresh_access_token(self) -> str:
        """Refresh the Spotify access token via POST to the token endpoint.

        Updates CredentialStore with new tokens.
        Raises SpotifyCredentialError on invalid_grant or permanent failure.
        """
        if not self._refresh_token or not self._client_id:
            raise SpotifyCredentialError("Missing refresh_token or client_id for token refresh")

        assert self._http_client is not None

        logger.info("SpotifyConnector: refreshing access token")

        try:
            resp = await self._http_client.post(
                _SPOTIFY_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
        except httpx.TransportError as exc:
            if self._endpoint_identity:
                spotify_token_refreshes_total.labels(
                    endpoint_identity=self._endpoint_identity, status="error"
                ).inc()
            raise RuntimeError(f"Token refresh transport error: {exc}") from exc

        if resp.status_code == 200:
            data = resp.json()
            self._access_token = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))
            self._token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

            # Rotate refresh token if provided
            if "refresh_token" in data:
                self._refresh_token = data["refresh_token"]

            # Persist to CredentialStore
            await self._persist_tokens()

            if self._endpoint_identity:
                spotify_token_refreshes_total.labels(
                    endpoint_identity=self._endpoint_identity, status="success"
                ).inc()

            logger.info("SpotifyConnector: access token refreshed successfully")
            return self._access_token

        # Auth failure — invalid_grant means re-authorization required
        body_text = resp.text[:200]
        if resp.status_code == 400:
            if self._endpoint_identity:
                spotify_token_refreshes_total.labels(
                    endpoint_identity=self._endpoint_identity, status="error"
                ).inc()
            raise SpotifyCredentialError(
                f"Spotify authorization expired (HTTP 400): {body_text}. "
                "Re-connect via dashboard settings."
            )

        if self._endpoint_identity:
            spotify_token_refreshes_total.labels(
                endpoint_identity=self._endpoint_identity, status="error"
            ).inc()
        raise RuntimeError(f"Token refresh failed: HTTP {resp.status_code}: {body_text}")

    async def _persist_tokens(self) -> None:
        """Write current tokens back to CredentialStore."""
        if self._db_pool is None:
            return
        try:
            store = CredentialStore(self._db_pool)
            if self._access_token:
                await store.store(
                    _CRED_ACCESS_TOKEN,
                    self._access_token,
                    category="spotify",
                    is_sensitive=True,
                )
            if self._refresh_token:
                await store.store(
                    _CRED_REFRESH_TOKEN,
                    self._refresh_token,
                    category="spotify",
                    is_sensitive=True,
                )
            if self._token_expires_at:
                await store.store(
                    _CRED_TOKEN_EXPIRES_AT,
                    self._token_expires_at.isoformat(),
                    category="spotify",
                    is_sensitive=False,
                )
        except Exception as exc:
            logger.warning("SpotifyConnector: failed to persist tokens to CredentialStore: %s", exc)

    # ------------------------------------------------------------------
    # Identity resolution
    # ------------------------------------------------------------------

    async def _resolve_identity(self) -> None:
        """Auto-resolve endpoint identity via GET /me.

        Retries with exponential backoff until successful.
        """
        delay = 2.0
        attempt = 0
        while True:
            try:
                me = await self._spotify_get("/me")
                self._spotify_user_id = me["id"]
                self._endpoint_identity = f"spotify:{self._spotify_user_id}"
                logger.info(
                    "SpotifyConnector: identity resolved — endpoint_identity=%s",
                    self._endpoint_identity,
                )
                return
            except SpotifyCredentialError:
                raise
            except Exception as exc:
                attempt += 1
                logger.warning(
                    "SpotifyConnector: identity resolution failed (attempt %d): %s"
                    " — retrying in %.1fs",
                    attempt,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60.0)

    def _endpoint_identity_ready(self) -> None:
        """Initialize components that depend on endpoint_identity."""
        # Update metrics connector with resolved identity
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        # Init ingestion policy
        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=f"connector:{_CONNECTOR_TYPE}:{self._endpoint_identity}",
            db_pool=self._db_pool,
        )

        # Init filtered event buffer
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        # Init heartbeat
        hb_config = HeartbeatConfig.from_env(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )
        self._heartbeat = ConnectorHeartbeat(
            config=hb_config,
            mcp_client=self._mcp_client,
            metrics=self._metrics,
            get_health_state=self._get_health_state,
            get_checkpoint=self._get_checkpoint_info,
        )

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        """Load the last recently-played cursor from the checkpoint store."""
        if self._cursor_pool is None or not self._endpoint_identity:
            return
        try:
            cursor = await load_cursor(self._cursor_pool, _CONNECTOR_TYPE, self._endpoint_identity)
            if cursor:
                self._last_recently_played_cursor = cursor
                self._last_checkpoint_cursor = cursor
                logger.info(
                    "SpotifyConnector: loaded checkpoint cursor=%s endpoint=%s",
                    cursor,
                    self._endpoint_identity,
                )
        except Exception as exc:
            logger.warning("SpotifyConnector: failed to load checkpoint: %s", exc)

    async def _save_checkpoint(self) -> None:
        """Persist the current recently-played cursor to the checkpoint store."""
        if self._cursor_pool is None or not self._endpoint_identity:
            return
        cursor = self._last_recently_played_cursor
        if cursor is None:
            return
        # Skip write if cursor hasn't changed since last save
        if cursor == self._last_checkpoint_cursor:
            return
        try:
            await save_cursor(self._cursor_pool, _CONNECTOR_TYPE, self._endpoint_identity, cursor)
            self._last_checkpoint_cursor = cursor
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save("success")
            logger.debug(
                "SpotifyConnector: saved checkpoint cursor=%s endpoint=%s",
                cursor,
                self._endpoint_identity,
            )
        except Exception as exc:
            self._metrics.record_checkpoint_save("error")
            logger.warning("SpotifyConnector: failed to save checkpoint: %s", exc)

    # ------------------------------------------------------------------
    # Spotify API
    # ------------------------------------------------------------------

    async def _spotify_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        api_method: str | None = None,
    ) -> dict[str, Any]:
        """Call a Spotify API GET endpoint with token refresh and rate-limit handling.

        Returns the parsed JSON response body.
        Raises SpotifyCredentialError on unrecoverable auth failure.
        Raises SpotifyRateLimitError on HTTP 429.
        Raises RuntimeError on other unrecoverable errors.
        """
        if api_method is None:
            api_method = path.lstrip("/").replace("/", ".")

        assert self._http_client is not None
        url = f"{_SPOTIFY_API_BASE}{path}"

        for attempt in range(1, 3):  # retry once after token refresh
            token = await self._get_access_token()
            headers = {"Authorization": f"Bearer {token}"}

            try:
                resp = await self._http_client.get(
                    url, headers=headers, params=params or {}, timeout=30
                )
            except httpx.TransportError as exc:
                if self._endpoint_identity:
                    self._metrics.record_source_api_call(api_method, "error")
                raise RuntimeError(f"Spotify API transport error: {exc}") from exc

            if self._endpoint_identity:
                self._metrics.record_source_api_call(api_method, str(resp.status_code))

            if resp.status_code == 200:
                self._source_api_ok = True
                return resp.json()

            if resp.status_code == 204:
                # No content — e.g. nothing currently playing
                self._source_api_ok = True
                return {}

            if resp.status_code == 401 and attempt == 1:
                # Token expired: refresh and retry once
                logger.info("SpotifyConnector: received 401, refreshing token and retrying")
                self._access_token = None
                self._token_expires_at = None
                try:
                    await self._refresh_access_token()
                except SpotifyCredentialError:
                    self._source_api_ok = False
                    raise
                continue

            if resp.status_code == 401:
                self._source_api_ok = False
                raise SpotifyCredentialError(
                    "Spotify authorization failed after token refresh. "
                    "Re-connect via dashboard settings."
                )

            if resp.status_code == 429:
                self._source_api_ok = False
                retry_after = float(resp.headers.get("Retry-After", _RATE_LIMIT_INITIAL_S))
                raise SpotifyRateLimitError(retry_after)

            self._source_api_ok = False
            raise RuntimeError(f"Spotify API error: HTTP {resp.status_code}: {resp.text[:200]}")

        # Should not be reachable
        raise RuntimeError("Spotify API: exhausted retry attempts")

    async def _get_currently_playing(self) -> dict[str, Any] | None:
        """Call GET /me/player/currently-playing.

        Returns the response dict, or None if nothing is playing or the endpoint
        returned 204 No Content.
        """
        try:
            data = await self._spotify_get(
                "/me/player/currently-playing",
                api_method="currently_playing",
            )
            # 204 returns {}; also check is_playing
            if not data:
                return None
            return data
        except SpotifyRateLimitError:
            raise
        except Exception:
            raise

    async def _get_recently_played(self, after_ms: str | None) -> list[dict[str, Any]]:
        """Call GET /me/player/recently-played with after cursor.

        Returns a list of track items (most recent first from API, but we process oldest first).
        """
        params: dict[str, Any] = {"limit": 50}
        if after_ms:
            params["after"] = after_ms

        data = await self._spotify_get(
            "/me/player/recently-played",
            params=params,
            api_method="recently_played",
        )
        return data.get("items", [])

    async def _resolve_context_name(
        self,
        context_uri: str | None,
        item: dict[str, Any] | None = None,
    ) -> str | None:
        """Best-effort resolve a Spotify context URI to a human-readable name."""
        normalized_uri = (context_uri or "").strip()
        if not normalized_uri:
            return None

        cached = self._context_name_cache.get(normalized_uri)
        if cached:
            return cached

        inline_name = _context_name_from_item(normalized_uri, item or {})
        if inline_name:
            self._context_name_cache[normalized_uri] = inline_name
            return inline_name

        context_type, context_id = _parse_spotify_context_uri(normalized_uri)
        if not context_type or not context_id:
            return None

        try:
            if context_type == "playlist":
                payload = await self._spotify_get(
                    f"/playlists/{context_id}",
                    params={"fields": "name"},
                    api_method="playlist_name",
                )
            elif context_type == "album":
                payload = await self._spotify_get(
                    f"/albums/{context_id}",
                    api_method="album_name",
                )
            elif context_type == "artist":
                payload = await self._spotify_get(
                    f"/artists/{context_id}",
                    api_method="artist_name",
                )
            else:
                return None
        except Exception as exc:
            logger.debug(
                "SpotifyConnector: could not resolve context name for %s: %s",
                normalized_uri,
                exc,
            )
            return None

        resolved_name = _clean_context_name(payload.get("name"))
        if resolved_name:
            self._context_name_cache[normalized_uri] = resolved_name
        return resolved_name

    # ------------------------------------------------------------------
    # Main polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Main adaptive polling loop.

        Polls currently-playing at active interval or exponential backoff when idle.
        Also polls recently-played for gap-filling after each cycle.
        """
        logger.info(
            "SpotifyConnector: entering poll loop (active=%ds, idle_max=%ds) endpoint=%s",
            self._config.poll_active_s,
            self._config.poll_idle_s,
            self._endpoint_identity,
        )

        while self._running and not self._shutdown_event.is_set():
            # Drain replay queue once per cycle
            await self._drain_replay()

            # If in auth-error state, wait for credential re-check
            if self._auth_error:
                logger.info(
                    "SpotifyConnector: in auth-error state — waiting %ds"
                    " before re-checking credentials",
                    _CREDENTIAL_RECHECK_S,
                )
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), timeout=_CREDENTIAL_RECHECK_S
                    )
                    break  # Shutdown requested
                except TimeoutError:
                    pass

                if await self._reload_credentials():
                    logger.info("SpotifyConnector: credentials reloaded — resuming polling")
                    self._auth_error = False
                    self._auth_error_message = None
                continue

            # === Poll cycle ===
            poll_start = time.monotonic()
            try:
                await self._execute_poll_cycle()
            except SpotifyCredentialError as exc:
                logger.error(
                    "SpotifyConnector: auth error — stopping poll, will re-check in %ds: %s",
                    _CREDENTIAL_RECHECK_S,
                    exc,
                )
                self._auth_error = True
                self._auth_error_message = str(exc)
                self._source_api_ok = False
                continue
            except SpotifyRateLimitError as exc:
                logger.warning("SpotifyConnector: rate limited — sleeping %.1fs", exc.retry_after)
                if self._endpoint_identity:
                    spotify_polls_total.labels(
                        endpoint_identity=self._endpoint_identity, status="rate_limited"
                    ).inc()
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=exc.retry_after)
                    break
                except TimeoutError:
                    pass
                continue
            except Exception as exc:
                logger.warning(
                    "SpotifyConnector: poll cycle error (non-fatal): %s", exc, exc_info=True
                )
                if self._endpoint_identity:
                    spotify_polls_total.labels(
                        endpoint_identity=self._endpoint_identity, status="error"
                    ).inc()
                self._metrics.record_error("poll_error", "poll_cycle")

            # Flush filtered events after each cycle
            await self._flush_filtered_events()

            # Save checkpoint after successful cycle
            await self._save_checkpoint()

            # Wait for next poll interval
            poll_elapsed = time.monotonic() - poll_start
            wait_time = max(0.0, self._current_poll_interval_s - poll_elapsed)
            logger.debug(
                "SpotifyConnector: poll cycle complete in %.2fs — sleeping %.1fs",
                poll_elapsed,
                wait_time,
            )
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=wait_time)
                break  # Shutdown requested during wait
            except TimeoutError:
                pass  # Normal: timeout means it's time for next poll

    async def _execute_poll_cycle(self) -> None:
        """Execute a single poll cycle: currently-playing + recently-played gap-fill."""
        now = datetime.now(UTC)
        observed_at = now.isoformat()

        # --- Poll currently-playing ---
        currently_playing = await self._get_currently_playing()

        is_playing = False
        if currently_playing:
            item = currently_playing.get("item")
            is_playing_flag = currently_playing.get("is_playing", False)
            # Ignore podcasts (type == "episode") — track only
            item_type = item.get("type", "") if item else ""
            if item and is_playing_flag and item_type == "track":
                is_playing = True
                await self._handle_active_playback(currently_playing, item, now, observed_at)

        if not is_playing:
            await self._handle_no_playback(now, observed_at)

        # --- Gap-fill via recently-played ---
        # Throttle gap-fill to gap_fill_idle_interval_s (default 3 hrs) so
        # recently-played tracks are batched into infrequent bulk digests
        # rather than one ingestion per track every few minutes.
        # Active playback detection via currently-playing is unaffected.
        gap_fill_due = self._last_gap_fill_poll_at == 0.0 or (
            time.monotonic() - self._last_gap_fill_poll_at >= self._config.gap_fill_idle_interval_s
        )
        if gap_fill_due:
            await self._poll_recently_played(now, observed_at)
            self._last_gap_fill_poll_at = time.monotonic()

    async def _handle_active_playback(
        self,
        payload: dict[str, Any],
        item: dict[str, Any],
        now: datetime,
        observed_at: str,
    ) -> None:
        """Handle active playback: update session tracker, emit track_change if needed."""
        track_id = item.get("id", "")
        track_name = item.get("name", "unknown")
        album = item.get("album", {}) or {}
        album_name = album.get("name", "unknown")
        artists = item.get("artists", []) or []
        artist_names = [a.get("name", "") for a in artists if a.get("name")]
        duration_ms = int(item.get("duration_ms", 0))

        context = payload.get("context") or {}
        context_uri = context.get("uri") if context else None
        context_name = await self._resolve_context_name(context_uri, item)

        timestamp_ms = int(payload.get("timestamp", now.timestamp() * 1000))
        device = payload.get("device") or {}
        device_name = device.get("name") if device else None

        # Reset to active poll interval
        self._current_poll_interval_s = self._config.poll_active_s

        # Update session tracker
        events, closed_sessions = self._session_tracker.process_playback(
            track_id=track_id,
            track_name=track_name,
            context_uri=context_uri,
            context_name=context_name,
            now=now,
        )

        # Emit session summaries for closed sessions
        for closed in closed_sessions:
            await self._emit_session_summary(closed, observed_at)

        # Emit context_start if a new context began
        if "context_start" in events:
            envelope = build_context_start_envelope(
                endpoint_identity=self._endpoint_identity,
                spotify_user_id=self._spotify_user_id,
                track_id=track_id,
                track_name=track_name,
                artist_names=artist_names,
                album_name=album_name,
                duration_ms=duration_ms,
                context_uri=context_uri,
                context_name=context_name,
                device_name=device_name,
                timestamp_ms=timestamp_ms,
                raw_payload=payload,
                observed_at=observed_at,
            )
            await self._submit_envelope(envelope)
            if self._endpoint_identity:
                spotify_context_starts_total.labels(endpoint_identity=self._endpoint_identity).inc()

        # Emit periodic listening digest if due
        if self._session_tracker.check_digest_due(now):
            session = self._session_tracker.current_session
            if session is not None:
                digest_envelope = build_listening_digest_envelope(
                    endpoint_identity=self._endpoint_identity,
                    spotify_user_id=self._spotify_user_id,
                    session=session,
                    observed_at=observed_at,
                )
                await self._submit_envelope(digest_envelope)
                self._session_tracker.mark_digest_emitted(now)
                if self._endpoint_identity:
                    spotify_digests_total.labels(endpoint_identity=self._endpoint_identity).inc()

        if self._endpoint_identity:
            spotify_polls_total.labels(
                endpoint_identity=self._endpoint_identity, status="success"
            ).inc()

        # Persist in-progress session evidence so the Music lane shows the
        # current session immediately instead of waiting for idle-drain
        # close. Survives container restart on the next active poll.
        await self._persist_in_progress_session()

        # Update recently-played cursor to current timestamp
        self._last_recently_played_cursor = str(timestamp_ms)

    async def _handle_no_playback(self, now: datetime, observed_at: str) -> None:
        """Handle no active playback: advance session tracker, emit summaries if closed."""
        # Exponential backoff toward idle interval
        if self._current_poll_interval_s < self._config.poll_idle_s:
            self._current_poll_interval_s = min(
                self._current_poll_interval_s * _IDLE_BACKOFF_MULTIPLIER,
                self._config.poll_idle_s,
            )

        closed_sessions = self._session_tracker.process_no_playback(now=now)
        for closed in closed_sessions:
            await self._emit_session_summary(closed, observed_at)

        if self._endpoint_identity:
            spotify_polls_total.labels(
                endpoint_identity=self._endpoint_identity, status="idle"
            ).inc()

    async def _poll_recently_played(self, now: datetime, observed_at: str) -> None:
        """Poll recently-played for gap-filling using the stored cursor.

        Gap-fill tracks are batched into a single digest envelope rather than
        emitting per-track events, to reduce downstream token costs.
        """
        try:
            items = await self._get_recently_played(self._last_recently_played_cursor)
        except Exception as exc:
            logger.debug("SpotifyConnector: recently-played poll failed (non-fatal): %s", exc)
            return

        if not items:
            return

        # Items are returned most-recent-first; process oldest-first for proper ordering
        items_ordered = list(reversed(items))

        # Accumulate gap-fill tracks by context for batched submission
        gap_tracks: list[dict[str, Any]] = []
        last_cursor_ms = 0

        for item_wrapper in items_ordered:
            track = item_wrapper.get("track") or {}
            track_id = track.get("id", "")
            track_name = track.get("name", "unknown")

            # Parse played_at timestamp
            played_at_str = item_wrapper.get("played_at", "")
            try:
                played_at_dt = datetime.fromisoformat(played_at_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                played_at_dt = now
            played_at_ms = int(played_at_dt.timestamp() * 1000)

            # Only process tracks not already observed via currently-playing
            if self._last_recently_played_cursor:
                try:
                    cursor_ms = int(self._last_recently_played_cursor)
                except ValueError:
                    cursor_ms = 0
                if played_at_ms <= cursor_ms:
                    continue

            if not track_id:
                continue

            gap_tracks.append({"name": track_name, "played_at_ms": played_at_ms})
            last_cursor_ms = max(last_cursor_ms, played_at_ms)

        # Emit a single batched gap-fill digest if we found any tracks
        if gap_tracks:
            track_names = [t["name"] for t in gap_tracks]
            track_count = len(gap_tracks)
            track_list = ", ".join(track_names)

            normalized_text = f"Gap-fill digest: {track_count} recently played — {track_list}"

            first_ms = gap_tracks[0]["played_at_ms"]
            last_ms = gap_tracks[-1]["played_at_ms"]
            idempotency_key = f"spotify:{self._endpoint_identity}:gapfill:{first_ms}:{last_ms}"
            external_event_id = f"spotify:gapfill:{first_ms}:{last_ms}"

            envelope = {
                "schema_version": "ingest.v1",
                "source": {
                    "channel": _CONNECTOR_CHANNEL,
                    "provider": _CONNECTOR_PROVIDER,
                    "endpoint_identity": self._endpoint_identity,
                },
                "event": {
                    "external_event_id": external_event_id,
                    "external_thread_id": None,
                    "observed_at": observed_at,
                },
                "sender": {
                    "identity": self._spotify_user_id,
                },
                "payload": {
                    "raw": {
                        "gap_fill": True,
                        "track_count": track_count,
                        "tracks": track_names,
                        "first_played_at_ms": first_ms,
                        "last_played_at_ms": last_ms,
                    },
                    "normalized_text": normalized_text,
                },
                "control": {
                    "idempotency_key": idempotency_key,
                    "policy_tier": "default",
                    "ingestion_tier": "full",
                },
            }
            await self._submit_envelope(envelope)

            # Advance cursor to the latest gap-fill track
            self._last_recently_played_cursor = str(last_cursor_ms)

    async def _persist_in_progress_session(self) -> None:
        """Upsert the currently-active session into the evidence table.

        Called from ``_handle_active_playback`` on every active poll so the
        Music lane shows a live-extending bar within ~60s of playback start
        instead of waiting for the 5-minute idle-drain to close the session.

        Survives container restart: the next active poll re-upserts the same
        idempotency_key and the previously-projected episode's ``end_at``
        moves forward via the chronicler adapter on its next run.

        No-ops when (a) cursor_pool / identity not yet wired, (b) no session
        is open, or (c) the session is empty (zero tracks). Failures are
        logged at debug and never propagated.
        """
        if self._cursor_pool is None or not self._endpoint_identity or not self._spotify_user_id:
            return
        session = self._session_tracker.current_session
        if session is None or session.track_count == 0:
            return
        try:
            await persist_session_summary(
                self._cursor_pool,
                endpoint_identity=self._endpoint_identity,
                spotify_user_id=self._spotify_user_id,
                session=session,
            )
        except Exception as exc:
            logger.debug(
                "SpotifyConnector: in-progress session persist failed (non-fatal): %s",
                exc,
            )

    async def _emit_session_summary(self, session: ListeningSession, observed_at: str) -> None:
        """Emit a session summary ingest envelope and persist to evidence table.

        The ingest submission path (Switchboard) is unchanged.  In addition,
        the session is persisted to ``connectors.spotify_listening_sessions``
        which is the durable evidence surface for Chronicler (RFC 0014 §D9).
        Evidence persistence failures are logged and do not abort the ingest.
        """
        if session.track_count == 0:
            return

        # ── Durable evidence write (Chronicler read surface) ──────────────
        if self._cursor_pool is not None and self._endpoint_identity and self._spotify_user_id:
            try:
                inserted = await persist_session_summary(
                    self._cursor_pool,
                    endpoint_identity=self._endpoint_identity,
                    spotify_user_id=self._spotify_user_id,
                    session=session,
                )
                if inserted:
                    logger.debug(
                        "SpotifyConnector: persisted session evidence start=%s tracks=%d",
                        session.started_at.isoformat(),
                        session.track_count,
                    )
                else:
                    logger.debug(
                        "SpotifyConnector: duplicate session evidence skipped start=%s",
                        session.started_at.isoformat(),
                    )
            except Exception as exc:
                logger.warning(
                    "SpotifyConnector: failed to persist session evidence (non-fatal): %s",
                    exc,
                )

        # ── Ingest submission (Switchboard) ───────────────────────────────
        envelope = build_session_summary_envelope(
            endpoint_identity=self._endpoint_identity,
            spotify_user_id=self._spotify_user_id,
            session=session,
            observed_at=observed_at,
        )
        await self._submit_envelope(envelope)

        if self._endpoint_identity:
            spotify_sessions_total.labels(endpoint_identity=self._endpoint_identity).inc()
            spotify_session_duration_seconds.labels(
                endpoint_identity=self._endpoint_identity
            ).observe(session.duration_seconds)

    # ------------------------------------------------------------------
    # Ingest submission (with filter gate)
    # ------------------------------------------------------------------

    async def _submit_envelope(self, envelope: dict[str, Any]) -> None:
        """Evaluate filter gate, then submit to Switchboard.

        Filtered events go to the filtered_event_buffer.
        """
        source = envelope.get("source", {})
        event = envelope.get("event", {})
        sender = envelope.get("sender", {})
        payload = envelope.get("payload", {})
        control = envelope.get("control", {})

        # Build IngestionEnvelope for policy evaluation
        # Spotify events use source_channel as raw_key (no email/chat_id matching)
        ing_env = IngestionEnvelope(
            source_channel=source.get("channel", ""),
            raw_key=sender.get("identity", ""),
            thread_id=event.get("external_thread_id"),
        )

        # Evaluate policy (synchronous; triggers background refresh if stale)
        if self._ingestion_policy is not None:
            try:
                decision = self._ingestion_policy.evaluate(ing_env)
                if not decision.allowed:
                    logger.debug(
                        "SpotifyConnector: event blocked by policy: %s (action=%s)",
                        event.get("external_event_id"),
                        decision.action,
                    )
                    if self._filtered_event_buffer is not None:
                        self._filtered_event_buffer.record(
                            external_message_id=event.get("external_event_id", ""),
                            source_channel=source.get("channel", ""),
                            sender_identity=sender.get("identity", ""),
                            subject_or_preview=payload.get("normalized_text", "")[:100],
                            filter_reason=FilteredEventBuffer.reason_policy_rule(
                                scope=self._ingestion_policy.scope,
                                action=decision.action,
                                rule_type=decision.matched_rule_type or "unknown",
                            ),
                            full_payload=FilteredEventBuffer.full_payload(
                                channel=source.get("channel", ""),
                                provider=source.get("provider", ""),
                                endpoint_identity=source.get("endpoint_identity", ""),
                                external_event_id=event.get("external_event_id", ""),
                                external_thread_id=event.get("external_thread_id"),
                                observed_at=event.get("observed_at", ""),
                                sender_identity=sender.get("identity", ""),
                                raw=payload.get("raw"),
                                normalized_text=payload.get("normalized_text", ""),
                                policy_tier=control.get("policy_tier", "default"),
                            ),
                        )
                    return
            except Exception as exc:
                # Fail-open: log and proceed with submission
                logger.debug("SpotifyConnector: policy evaluation error (fail-open): %s", exc)

        # Submit to Switchboard
        async with self._semaphore:
            start_t = time.perf_counter()
            try:
                result = await self._mcp_client.call_tool("ingest", envelope)
                latency = time.perf_counter() - start_t

                status = "success"
                if isinstance(result, dict):
                    resp_status = result.get("status", "")
                    if resp_status == "duplicate":
                        status = "duplicate"
                    elif resp_status not in ("accepted", "queued", "duplicate"):
                        logger.warning("SpotifyConnector: unexpected ingest response: %s", result)

                self._metrics.record_ingest_submission(status, latency)
                self._last_ingest_submit = time.time()
                self._source_api_ok = True

            except Exception as exc:
                latency = time.perf_counter() - start_t
                self._metrics.record_ingest_submission("error", latency)
                self._metrics.record_error("ingest_error", "submit")
                logger.warning("SpotifyConnector: ingest submission failed: %s", exc)
                # Buffer for replay with status="error" so it's distinguishable from policy-filtered
                if self._filtered_event_buffer is not None:
                    self._filtered_event_buffer.record(
                        external_message_id=event.get("external_event_id", ""),
                        source_channel=source.get("channel", ""),
                        sender_identity=sender.get("identity", ""),
                        subject_or_preview=payload.get("normalized_text", "")[:100],
                        filter_reason=FilteredEventBuffer.reason_submission_error(),
                        full_payload=envelope,
                        status="error",
                        error_detail=str(exc),
                    )

    # ------------------------------------------------------------------
    # Filtered events
    # ------------------------------------------------------------------

    async def _flush_filtered_events(self) -> None:
        """Flush accumulated filtered events to the DB."""
        if self._db_pool is None or self._filtered_event_buffer is None:
            return
        if len(self._filtered_event_buffer) == 0:
            return
        try:
            await self._filtered_event_buffer.flush(self._db_pool)
        except Exception as exc:
            logger.warning("SpotifyConnector: filtered event flush failed: %s", exc)

    async def _drain_replay(self) -> None:
        """Drain replay_pending filtered events."""
        if self._db_pool is None or not self._endpoint_identity:
            return
        try:
            await drain_replay_pending(
                pool=self._db_pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=self._endpoint_identity,
                submit_fn=self._submit_to_ingest_direct,
                drain_logger=logger,
            )
        except Exception as exc:
            logger.warning("SpotifyConnector: replay drain failed: %s", exc)

    async def _submit_to_ingest_direct(self, envelope: dict[str, Any]) -> None:
        """Submit directly to Switchboard (used by replay drain — skips filter gate)."""
        await self._mcp_client.call_tool("ingest", envelope)

    # ------------------------------------------------------------------
    # Health state callbacks
    # ------------------------------------------------------------------

    def _get_health_state(self) -> tuple[str, str | None]:
        """Return (state, error_message) for heartbeat."""
        if self._auth_error:
            return "error", self._auth_error_message
        if self._source_api_ok is None:
            return "starting", None
        if self._source_api_ok:
            return "healthy", None
        return "degraded", "Spotify API not reachable"

    def _get_checkpoint_info(self) -> tuple[str | None, datetime | None]:
        """Return (cursor, updated_at) for heartbeat."""
        checkpoint_ts: datetime | None = None
        if self._last_checkpoint_save is not None:
            checkpoint_ts = datetime.fromtimestamp(self._last_checkpoint_save, UTC)
        return self._last_checkpoint_cursor, checkpoint_ts

    # ------------------------------------------------------------------
    # Health HTTP server
    # ------------------------------------------------------------------

    def _start_health_server(self) -> None:
        """Start the health/metrics HTTP server in a background thread."""
        app = FastAPI(title="spotify-connector-health")

        @app.get("/health")
        async def health() -> dict[str, Any]:
            state, error = self._get_health_state()
            uptime_s = int(time.time() - self._start_time)
            return {
                "status": state,
                "connector_type": _CONNECTOR_TYPE,
                "endpoint_identity": self._endpoint_identity,
                "uptime_seconds": uptime_s,
                "session_state": self._session_tracker.state,
                "error": error,
            }

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest()

        port = self._config.health_port
        try:
            sock = make_health_socket("127.0.0.1", port)
        except Exception as exc:
            logger.warning(
                "SpotifyConnector: could not bind health socket on port %d: %s", port, exc
            )
            return

        uvicorn_config = uvicorn.Config(
            app=app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)
        self._health_server = server

        def _run() -> None:
            asyncio.run(server.serve(sockets=[sock]))

        thread = Thread(target=_run, daemon=True, name="spotify-health-server")
        thread.start()
        self._health_thread = thread
        logger.info("SpotifyConnector: health server started on port %d", port)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_spotify_connector() -> None:
    """Main async entry point for the Spotify connector."""
    configure_logging()
    logger.info("Spotify connector starting")

    config = SpotifyConnectorConfig.from_env()

    import asyncpg

    db_params = db_params_from_env()
    shared_db_name = shared_db_name_from_env()
    shared_schema = os.environ.get("BUTLER_SHARED_DB_SCHEMA", "public")
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"

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
        if shared_schema:
            try:
                pool_kwargs["server_settings"] = {"search_path": schema_search_path(shared_schema)}
            except ValueError:
                pass
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

        logger.info("Spotify connector: DB pool established (db=%s)", shared_db_name)
    except Exception as exc:
        logger.warning(
            "Spotify connector: DB pool failed (credentials and policy unavailable): %s", exc
        )
        db_pool = None

    # Create cursor pool
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
        logger.info("Spotify connector: cursor pool established (db=%s)", local_db_name)
    except Exception as exc:
        logger.warning(
            "Spotify connector: cursor pool failed (checkpoint persistence unavailable): %s", exc
        )
        cursor_pool = None

    connector = SpotifyConnector(
        config=config,
        db_pool=db_pool,
        cursor_pool=cursor_pool,
    )

    try:
        await connector.start()
    finally:
        if cursor_pool is not None:
            await cursor_pool.close()
        if db_pool is not None:
            await db_pool.close()


def main() -> None:
    """Synchronous entry point for use as a console script or __main__."""
    asyncio.run(run_spotify_connector())


if __name__ == "__main__":
    main()
