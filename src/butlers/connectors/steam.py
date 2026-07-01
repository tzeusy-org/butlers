"""Steam connector — multi-account polling with delta detection.

Polls connected Steam accounts for gaming activity and submits normalized
ingest.v1 events to the Switchboard. Implements the connector base contract:
filtered event batch flush, replay queue drain, source filter gate, heartbeat,
and Prometheus metrics.

Key design choices:
- Multi-account: queries public.steam_accounts for active accounts at startup,
  spawns independent asyncio tasks per account.
- 2 independent per-account pollers, each with its own interval and backoff:
  recently_played (5 min) and online_status (5 min). Friends, game_library,
  and achievements polling are disabled — they produced noise without
  matching the owner's event goals (game start/stop). The
  `steam_get_achievements`, `steam_list_friends`, and `steam_list_owned_games`
  MCP tools remain available for on-demand queries.
- Delta detection via SHA-256 state hashing: stores last-known snapshot in
  connectors.steam_cursors; emits events only when state changes.
- Crash-safe cursor persistence: cursors survive restarts, preventing duplicate
  event emission.
- Dynamic account discovery: re-scans public.steam_accounts every 300 s to
  pick up new accounts and retire revoked ones.
- Per-account error isolation: one account's rate limit does not affect others.

Environment variables:
- SWITCHBOARD_MCP_URL (required): Switchboard MCP SSE endpoint
- CONNECTOR_HEALTH_PORT (default: 40089): health/metrics HTTP port
- CONNECTOR_BUTLER_DB_NAME (optional): butler DB name for cursor/policy
- BUTLER_SHARED_DB_NAME (optional): shared DB name (defaults to 'butlers')
- STEAM_ACCOUNT_RESCAN_S (default: 300): account discovery interval
- STEAM_HEARTBEAT_INTERVAL_S (default: 60): heartbeat interval override
- STEAM_MAX_TRACKED_GAMES (default: 10): achievement tracking limit

Metrics exported (Steam-specific):
  connector_steam_polls_total{data_type, endpoint_identity, status}
  connector_steam_events_submitted_total{event_type, endpoint_identity}
  connector_steam_events_filtered_total{filter_reason, endpoint_identity}
  connector_steam_api_errors_total{endpoint_identity, http_status}
  connector_steam_api_latency_seconds{data_type, endpoint_identity}
  connector_steam_rate_limit_backoffs_total{endpoint_identity}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal

from prometheus_client import Counter, Histogram, generate_latest

from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics
from butlers.steam.client import SteamAPIClient, SteamAPIError, SteamRateLimitError

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "steam"
_CONNECTOR_CHANNEL = "gaming"
_CONNECTOR_PROVIDER = "steam"

_DEFAULT_HEALTH_PORT = 40089
_DEFAULT_ACCOUNT_RESCAN_S = 300
_DEFAULT_HEARTBEAT_INTERVAL_S = 60
_DEFAULT_MAX_TRACKED_GAMES = 10

# Default poll intervals per data type (seconds).
# Note: achievements, friends, and game_library polling are intentionally
# omitted. The connector targets game-session signals (start/stop), so the
# remaining background pollers are recently_played and online_status.
# Dropped data types still have poll/envelope code present; their MCP tools
# (`steam_get_achievements`, `steam_list_friends`, `steam_list_owned_games`)
# remain available for on-demand queries from butlers.
_DEFAULT_INTERVALS: dict[str, int] = {
    "recently_played": 300,  # 5 min
    "online_status": 300,  # 5 min
}

# Transient error backoff: initial 5 s, max 300 s
_TRANSIENT_BACKOFF_INITIAL_S = 5.0
_TRANSIENT_BACKOFF_MAX_S = 300.0
_TRANSIENT_CONSECUTIVE_ERROR_THRESHOLD = 5

# Rate limit backoff: initial 60 s, max 3600 s
_RATE_BACKOFF_INITIAL_S = 60.0
_RATE_BACKOFF_MAX_S = 3600.0

# Cursor table DDL — created by migration; just reference the schema.
_CURSOR_SCHEMA = "connectors"
_CURSOR_TABLE = "steam_cursors"

# Persona state codes → human-readable labels
_PERSONA_STATE: dict[int, str] = {
    0: "offline",
    1: "online",
    2: "busy",
    3: "away",
    4: "snooze",
    5: "looking_to_trade",
    6: "looking_to_play",
}

# ---------------------------------------------------------------------------
# Steam-specific Prometheus metrics
# ---------------------------------------------------------------------------

steam_polls_total = Counter(
    "connector_steam_polls_total",
    "Total Steam polls by data type and outcome",
    labelnames=["data_type", "endpoint_identity", "status"],
)

steam_events_submitted_total = Counter(
    "connector_steam_events_submitted_total",
    "Total Steam events submitted to Switchboard",
    labelnames=["event_type", "endpoint_identity"],
)

steam_events_filtered_total = Counter(
    "connector_steam_events_filtered_total",
    "Total Steam events blocked by source filters",
    labelnames=["filter_reason", "endpoint_identity"],
)

steam_api_errors_total = Counter(
    "connector_steam_api_errors_total",
    "Total Steam API errors by HTTP status",
    labelnames=["endpoint_identity", "http_status"],
)

steam_api_latency_seconds = Histogram(
    "connector_steam_api_latency_seconds",
    "Steam API call latency",
    labelnames=["data_type", "endpoint_identity"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

steam_rate_limit_backoffs_total = Counter(
    "connector_steam_rate_limit_backoffs_total",
    "Total Steam rate limit backoff events",
    labelnames=["endpoint_identity"],
)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

AccountHealth = Literal["healthy", "degraded", "error"]


@dataclass
class SteamCursor:
    """Per-account, per-data-type polling cursor stored in connectors.steam_cursors."""

    endpoint_identity: str
    data_type: str
    last_poll_at: datetime | None = None
    state_hash: str | None = None
    state_snapshot: dict[str, Any] | None = None


@dataclass
class AccountPollerState:
    """Runtime state for one account's polling loop."""

    steam_id: int
    endpoint_identity: str
    api_key: str

    # UUID PK from public.steam_accounts — used for play_history FK upserts.
    # None for legacy/test pollers that were created before core_011.
    steam_account_id: uuid.UUID | None = None

    # Per-data-type poll intervals (seconds)
    intervals: dict[str, int] = field(default_factory=dict)

    # Per-data-type health
    health: dict[str, AccountHealth] = field(default_factory=dict)
    last_poll_at: dict[str, datetime | None] = field(default_factory=dict)
    consecutive_errors: dict[str, int] = field(default_factory=dict)
    backoff_until: dict[str, float] = field(default_factory=dict)

    # Cursors loaded from DB
    cursors: dict[str, SteamCursor] = field(default_factory=dict)

    # Overall account health
    account_health: AccountHealth = "healthy"
    account_error: str | None = None

    # Tracked games for achievement polling (app_ids as strings)
    tracked_games: list[str] = field(default_factory=list)

    # Tasks for per-data-type pollers
    tasks: dict[str, asyncio.Task] = field(default_factory=dict)

    # Shutdown signal
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def effective_health(self) -> AccountHealth:
        """Worst-case health across all data types."""
        statuses = list(self.health.values())
        if not statuses:
            return self.account_health
        if "error" in statuses:
            return "error"
        if "degraded" in statuses or self.account_health == "degraded":
            return "degraded"
        return "healthy"


# ---------------------------------------------------------------------------
# Play history delta computation (pure, testable)
# ---------------------------------------------------------------------------


def _compute_play_delta(
    app_id: int,
    playtime_2weeks: int,
    prev_snapshot: dict[str, Any] | None,
) -> int | None:
    """Return the minutes to write to play_history for one game, or None to skip.

    Rules:
    - ``prev_snapshot is None`` → first ever poll; skip (no baseline yet).
    - Game not in ``prev_snapshot`` → new game appeared; skip (establish baseline,
      avoid writing up to 14 days of prior cumulative play as a single delta).
    - ``playtime_2weeks <= prev_playtime`` → no new play; skip.
    - Otherwise → return ``playtime_2weeks - prev_playtime`` (positive delta only).

    Returns ``None`` when the game should be skipped; a positive integer otherwise.
    """
    if prev_snapshot is None:
        return None

    prev_entry = prev_snapshot.get(str(app_id), {})
    # Empty dict means the game was not in the prior snapshot — treat as new game.
    if prev_entry:
        prev_playtime: int | None = prev_entry.get("playtime_2weeks", 0)
    else:
        prev_playtime = None

    if prev_playtime is None:
        return None  # New game — establish baseline, never write cumulative

    delta = playtime_2weeks - prev_playtime
    return delta if delta > 0 else None


# ---------------------------------------------------------------------------
# Play history persistence
# ---------------------------------------------------------------------------

_PLAY_HISTORY_SCHEMA = "connectors"
_PLAY_HISTORY_TABLE = "steam_play_history"

# Upsert into connectors.steam_play_history using the post-core_011 schema.
# Conflicts on (steam_account_id, app_id, date); accumulates per-poll deltas
# so multiple polls on the same day sum to the day's total playtime.
_PLAY_HISTORY_UPSERT_SQL = f"""
INSERT INTO {_PLAY_HISTORY_SCHEMA}.{_PLAY_HISTORY_TABLE}
    (steam_id, steam_account_id, app_id, app_name, date, playtime_minutes, recorded_at)
VALUES ($1, $2, $3, $4, $5, $6, now())
ON CONFLICT (steam_account_id, app_id, date)
DO UPDATE SET
    playtime_minutes = {_PLAY_HISTORY_SCHEMA}.{_PLAY_HISTORY_TABLE}.playtime_minutes
                       + EXCLUDED.playtime_minutes,
    app_name     = COALESCE(EXCLUDED.app_name,
                            {_PLAY_HISTORY_SCHEMA}.{_PLAY_HISTORY_TABLE}.app_name),
    recorded_at  = now()
"""

# Fallback upsert for rows where steam_account_id is NULL (backfill failures or
# pre-core_011 schema). The old unique constraint on (steam_id, app_id, play_date)
# was dropped by migration core_011, so we cannot name it as a conflict target.
# We use ON CONFLICT DO NOTHING to avoid duplicates via the partial unique index
# uq_steam_play_history_steam_id_app_date_null_account added by core_011 on
# (steam_id, app_id, date) WHERE steam_account_id IS NULL.
# NOTE: the column was renamed from play_date → date by core_011, so post-migration
# this path uses the 'date' column via the renamed schema.
_PLAY_HISTORY_UPSERT_SQL_LEGACY = f"""
INSERT INTO {_PLAY_HISTORY_SCHEMA}.{_PLAY_HISTORY_TABLE}
    (steam_id, app_id, date, playtime_minutes, recorded_at)
VALUES ($1, $2, $3, $4, now())
ON CONFLICT DO NOTHING
"""


async def _upsert_play_history(
    pool: asyncpg.Pool,
    *,
    steam_id: int,
    steam_account_id: uuid.UUID | None,
    app_id: int,
    app_name: str,
    play_date: datetime,
    playtime_minutes: int,
) -> None:
    """Upsert a play-history row, falling back to the legacy schema if needed."""
    date_only = play_date.date()
    try:
        async with pool.acquire() as conn:
            if steam_account_id is not None:
                await conn.execute(
                    _PLAY_HISTORY_UPSERT_SQL,
                    steam_id,
                    steam_account_id,
                    app_id,
                    app_name or None,
                    date_only,
                    playtime_minutes,
                )
            else:
                # No account UUID available — use legacy path (steam_id + play_date key).
                await conn.execute(
                    _PLAY_HISTORY_UPSERT_SQL_LEGACY,
                    steam_id,
                    app_id,
                    date_only,
                    playtime_minutes,
                )
    except Exception:
        logger.warning(
            "Failed to upsert play history: steam_id=%s app_id=%s date=%s",
            steam_id,
            app_id,
            date_only,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Cursor persistence
# ---------------------------------------------------------------------------

_CURSOR_UPSERT_SQL = f"""
INSERT INTO {_CURSOR_SCHEMA}.{_CURSOR_TABLE}
    (endpoint_identity, data_type, last_poll_at, state_hash, state_snapshot, updated_at)
VALUES ($1, $2, $3, $4, $5, now())
ON CONFLICT (endpoint_identity, data_type)
DO UPDATE SET
    last_poll_at    = EXCLUDED.last_poll_at,
    state_hash      = EXCLUDED.state_hash,
    state_snapshot  = EXCLUDED.state_snapshot,
    updated_at      = now()
"""

_CURSOR_SELECT_SQL = f"""
SELECT endpoint_identity, data_type, last_poll_at, state_hash, state_snapshot
FROM {_CURSOR_SCHEMA}.{_CURSOR_TABLE}
WHERE endpoint_identity = $1
"""


async def _save_steam_cursor(pool: asyncpg.Pool, cursor: SteamCursor) -> None:
    """Upsert a Steam cursor row."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                _CURSOR_UPSERT_SQL,
                cursor.endpoint_identity,
                cursor.data_type,
                cursor.last_poll_at,
                cursor.state_hash,
                cursor.state_snapshot,
            )
    except Exception:
        logger.warning(
            "Failed to save Steam cursor: endpoint=%s data_type=%s",
            cursor.endpoint_identity,
            cursor.data_type,
            exc_info=True,
        )


async def _load_steam_cursors(pool: asyncpg.Pool, endpoint_identity: str) -> dict[str, SteamCursor]:
    """Load all cursors for an endpoint identity."""
    cursors: dict[str, SteamCursor] = {}
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_CURSOR_SELECT_SQL, endpoint_identity)
        for row in rows:
            snapshot = row["state_snapshot"]
            if isinstance(snapshot, str):
                try:
                    snapshot = json.loads(snapshot)
                except Exception:
                    snapshot = None
            cursors[row["data_type"]] = SteamCursor(
                endpoint_identity=row["endpoint_identity"],
                data_type=row["data_type"],
                last_poll_at=row["last_poll_at"],
                state_hash=row["state_hash"],
                state_snapshot=snapshot,
            )
    except Exception:
        logger.warning("Failed to load Steam cursors for %s", endpoint_identity, exc_info=True)
    return cursors


# Retention period for cursors belonging to revoked accounts (30 days).
_REVOKED_CURSOR_RETENTION_DAYS = 30

_CURSOR_PURGE_SQL = f"""
DELETE FROM {_CURSOR_SCHEMA}.{_CURSOR_TABLE} c
USING public.steam_accounts sa
WHERE c.endpoint_identity = 'steam:user:' || sa.steam_id::text
  AND sa.status = 'revoked'
  AND sa.revoked_at IS NOT NULL
  AND sa.revoked_at < now() - INTERVAL '{_REVOKED_CURSOR_RETENTION_DAYS} days'
"""


async def purge_revoked_cursors(pool: asyncpg.Pool) -> int:
    """Delete cursors for Steam accounts revoked more than 30 days ago.

    Cursors are keyed by ``endpoint_identity`` (``steam:user:<steam_id>``).
    Only accounts with a non-NULL ``revoked_at`` are considered — accounts
    revoked before migration core_044 (which added the column) are excluded
    unless they are re-revoked after the migration runs.

    Parameters
    ----------
    pool:
        asyncpg pool connected to the shared database.

    Returns
    -------
    int
        Number of cursor rows deleted.
    """
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(_CURSOR_PURGE_SQL)
        # asyncpg returns a status string like "DELETE N"
        deleted = int(result.split()[-1]) if result else 0
        if deleted:
            logger.info("Steam cursor cleanup: purged %d cursor rows for revoked accounts", deleted)
        return deleted
    except Exception:
        logger.warning("Failed to purge revoked Steam cursors", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Delta detection helpers
# ---------------------------------------------------------------------------


def _state_hash(data: Any) -> str:
    """Return a stable SHA-256 hex digest of the JSON-serialized data."""
    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


def _redact_steam_id(steam_id: int | str) -> str:
    """Partially redact a SteamID for health output.

    Example: 76561198000000000 → "7656***0000"
    """
    s = str(steam_id)
    if len(s) <= 8:
        return s
    return s[:4] + "***" + s[-4:]


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def build_play_session_envelope(
    *,
    steam_id: int,
    endpoint_identity: str,
    app_id: int,
    game_name: str,
    playtime_2weeks: int,
    playtime_delta: int,
    poll_ts: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a play_session event."""
    minutes = playtime_delta
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        duration_label = f"{hours}h {mins}m" if mins > 0 else f"{hours}h"
    else:
        duration_label = f"{mins} minutes"

    normalized_text = f"Played {game_name} for {duration_label}"
    external_event_id = f"steam:play:{steam_id}:{app_id}:{poll_ts}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "type": "play_session",
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": poll_ts,
        },
        "sender": {
            "identity": f"steam:{steam_id}",
        },
        "payload": {
            "raw": {
                **raw,
                "app_id": app_id,
                "game_name": game_name,
                "playtime_2weeks": playtime_2weeks,
                "playtime_delta_minutes": playtime_delta,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": external_event_id,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_achievement_unlock_envelope(
    *,
    steam_id: int,
    endpoint_identity: str,
    app_id: int,
    game_name: str,
    achievement_api_name: str,
    achievement_display_name: str,
    achievement_description: str,
    unlock_time: int,
    poll_ts: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for an achievement_unlock event."""
    normalized_text = f"Unlocked '{achievement_display_name}' in {game_name}"
    external_event_id = f"steam:achievement:{steam_id}:{app_id}:{achievement_api_name}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "type": "achievement_unlock",
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": poll_ts,
        },
        "sender": {
            "identity": f"steam:{steam_id}",
        },
        "payload": {
            "raw": {
                "steam_id": steam_id,
                "app_id": app_id,
                "game_name": game_name,
                "achievement_api_name": achievement_api_name,
                "achievement_display_name": achievement_display_name,
                "achievement_description": achievement_description,
                "unlock_time": unlock_time,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": external_event_id,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_status_change_envelope(
    *,
    steam_id: int,
    endpoint_identity: str,
    persona_state: int,
    game_extra_info: str | None,
    prev_persona_state: int | None,
    prev_game_extra_info: str | None,
    poll_ts: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a status_change event."""
    state_label = _PERSONA_STATE.get(persona_state, f"state_{persona_state}")
    if game_extra_info:
        normalized_text = f"Now playing {game_extra_info}"
    elif prev_game_extra_info and not game_extra_info:
        normalized_text = f"Stopped playing {prev_game_extra_info}"
    elif persona_state == 0:
        normalized_text = "Went offline"
    elif persona_state == 1:
        normalized_text = "Came online"
    else:
        normalized_text = f"Status changed to {state_label}"

    external_event_id = f"steam:status:{steam_id}:{poll_ts}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "type": "status_change",
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": poll_ts,
        },
        "sender": {
            "identity": f"steam:{steam_id}",
        },
        "payload": {
            "raw": {
                "steam_id": steam_id,
                "persona_state": persona_state,
                "persona_state_label": state_label,
                "game_extra_info": game_extra_info,
                "prev_persona_state": prev_persona_state,
                "prev_game_extra_info": prev_game_extra_info,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": external_event_id,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_game_purchase_envelope(
    *,
    steam_id: int,
    endpoint_identity: str,
    app_id: int,
    game_name: str,
    playtime_forever: int,
    poll_ts: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a game_purchase event."""
    normalized_text = f"Added '{game_name}' to library"
    external_event_id = f"steam:purchase:{steam_id}:{app_id}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "type": "game_purchase",
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": poll_ts,
        },
        "sender": {
            "identity": f"steam:{steam_id}",
        },
        "payload": {
            "raw": {
                "steam_id": steam_id,
                "app_id": app_id,
                "game_name": game_name,
                "playtime_forever": playtime_forever,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": external_event_id,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_friend_change_envelope(
    *,
    steam_id: int,
    endpoint_identity: str,
    friend_steam_id: str,
    friend_name: str | None,
    direction: Literal["added", "removed"],
    relationship: str,
    poll_ts: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a friend_change event."""
    name_label = friend_name or friend_steam_id
    if direction == "added":
        normalized_text = f"Added friend '{name_label}'"
    else:
        normalized_text = f"Removed friend '{name_label}'"

    external_event_id = f"steam:friend:{steam_id}:{friend_steam_id}:{direction}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "type": "friend_change",
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": poll_ts,
        },
        "sender": {
            "identity": f"steam:{steam_id}",
        },
        "payload": {
            "raw": {
                "steam_id": steam_id,
                "friend_steam_id": friend_steam_id,
                "friend_name": friend_name,
                "direction": direction,
                "relationship": relationship,
            },
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": external_event_id,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


# ---------------------------------------------------------------------------
# Filter key extraction (for IngestionPolicyEvaluator)
# ---------------------------------------------------------------------------


def _make_ingestion_envelope_for_filter(
    event: dict[str, Any],
) -> Any:
    """Build an IngestionEnvelope for the policy evaluator from an ingest.v1 event dict.

    Steam uses raw_key to carry app_id (for app_id rule type), event type, and
    sender identity for policy matching.
    """
    from butlers.ingestion_policy import IngestionEnvelope

    event_section = event.get("event", {})
    event_type = event_section.get("type", "")
    sender_identity = event.get("sender", {}).get("identity", "")
    raw_payload = event.get("payload", {}).get("raw", {})

    # raw_key carries comma-separated keys for multi-key matching:
    # "<event_type>,<sender_identity>,app_id:<app_id>"
    app_id_str = str(raw_payload.get("app_id", "")) if raw_payload else ""
    raw_key = f"{event_type},{sender_identity}"
    if app_id_str:
        raw_key += f",app_id:{app_id_str}"

    return IngestionEnvelope(
        source_channel=_CONNECTOR_CHANNEL,
        sender_address=sender_identity,
        raw_key=raw_key,
    )


# ---------------------------------------------------------------------------
# Per-account poller
# ---------------------------------------------------------------------------


class SteamAccountPoller:
    """Manages all polling tasks for a single Steam account.

    Spawns 5 asyncio tasks (one per data type), handles per-type backoff,
    delta detection, event emission, and cursor persistence.
    """

    def __init__(
        self,
        state: AccountPollerState,
        db_pool: asyncpg.Pool,
        mcp_client: CachedMCPClient,
        metrics: ConnectorMetrics,
        max_tracked_games: int = _DEFAULT_MAX_TRACKED_GAMES,
        ingestion_policy: Any | None = None,
    ) -> None:
        self._state = state
        self._db_pool = db_pool
        self._mcp_client = mcp_client
        self._metrics = metrics
        self._max_tracked_games = max_tracked_games
        self._policy = ingestion_policy

        self._steam_client = SteamAPIClient(api_key=state.api_key)

        # Per-type filtered event buffers
        self._filtered_bufs: dict[str, FilteredEventBuffer] = {
            dt: FilteredEventBuffer(
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=state.endpoint_identity,
            )
            for dt in _DEFAULT_INTERVALS
        }

        logger.info("SteamAccountPoller created: endpoint=%s", state.endpoint_identity)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the Steam API client and spawn per-data-type polling tasks."""
        await self._steam_client.open()

        for data_type in _DEFAULT_INTERVALS:
            interval = self._state.intervals.get(data_type, _DEFAULT_INTERVALS[data_type])
            task = asyncio.create_task(
                self._poller_loop(data_type, interval),
                name=f"steam-{self._state.steam_id}-{data_type}",
            )
            self._state.tasks[data_type] = task
            self._state.health[data_type] = "healthy"
            self._state.consecutive_errors[data_type] = 0
            self._state.backoff_until[data_type] = 0.0
            self._state.last_poll_at[data_type] = None

    async def stop(self) -> None:
        """Signal shutdown and wait for all polling tasks to finish."""
        self._state.shutdown_event.set()

        for data_type, task in list(self._state.tasks.items()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        await self._steam_client.close()
        logger.info("SteamAccountPoller stopped: endpoint=%s", self._state.endpoint_identity)

    # ------------------------------------------------------------------
    # Generic poller loop
    # ------------------------------------------------------------------

    async def _poller_loop(self, data_type: str, interval: int) -> None:
        """Run indefinitely, calling the appropriate poll method every interval seconds."""
        poll_fn = {
            "recently_played": self._poll_recently_played,
            "online_status": self._poll_online_status,
            "achievements": self._poll_achievements,
            "friends": self._poll_friends,
            "game_library": self._poll_game_library,
        }[data_type]

        try:
            while not self._state.shutdown_event.is_set():
                # Respect backoff
                now = time.monotonic()
                wait_until = self._state.backoff_until.get(data_type, 0.0)
                if wait_until > now:
                    sleep_s = min(wait_until - now, interval)
                    await asyncio.sleep(sleep_s)
                    continue

                # Poll
                t_start = time.monotonic()
                try:
                    await poll_fn()
                    self._state.consecutive_errors[data_type] = 0
                    self._state.health[data_type] = "healthy"
                    steam_polls_total.labels(
                        data_type=data_type,
                        endpoint_identity=self._state.endpoint_identity,
                        status="success",
                    ).inc()
                    steam_api_latency_seconds.labels(
                        data_type=data_type,
                        endpoint_identity=self._state.endpoint_identity,
                    ).observe(time.monotonic() - t_start)
                except SteamRateLimitError as exc:
                    backoff = exc.retry_after_s
                    self._state.backoff_until[data_type] = time.monotonic() + backoff
                    self._state.health[data_type] = "degraded"
                    steam_polls_total.labels(
                        data_type=data_type,
                        endpoint_identity=self._state.endpoint_identity,
                        status="rate_limited",
                    ).inc()
                    steam_rate_limit_backoffs_total.labels(
                        endpoint_identity=self._state.endpoint_identity,
                    ).inc()
                    steam_api_errors_total.labels(
                        endpoint_identity=self._state.endpoint_identity,
                        http_status=str(exc.status_code),
                    ).inc()
                    logger.warning(
                        "Steam rate limited: endpoint=%s data_type=%s backoff=%.1fs",
                        self._state.endpoint_identity,
                        data_type,
                        backoff,
                    )
                except SteamAPIError as exc:
                    errors = self._state.consecutive_errors.get(data_type, 0) + 1
                    self._state.consecutive_errors[data_type] = errors
                    if errors >= _TRANSIENT_CONSECUTIVE_ERROR_THRESHOLD:
                        self._state.health[data_type] = "error"
                    else:
                        self._state.health[data_type] = "degraded"

                    # Exponential backoff for transient errors
                    backoff = min(
                        _TRANSIENT_BACKOFF_INITIAL_S * (2 ** min(errors - 1, 10)),
                        _TRANSIENT_BACKOFF_MAX_S,
                    )
                    self._state.backoff_until[data_type] = time.monotonic() + backoff

                    steam_polls_total.labels(
                        data_type=data_type,
                        endpoint_identity=self._state.endpoint_identity,
                        status="error",
                    ).inc()
                    steam_api_errors_total.labels(
                        endpoint_identity=self._state.endpoint_identity,
                        http_status=str(exc.status_code),
                    ).inc()
                    logger.warning(
                        "Steam API error: endpoint=%s data_type=%s status=%d errors=%d",
                        self._state.endpoint_identity,
                        data_type,
                        exc.status_code,
                        errors,
                    )

                    # Record in filtered_events
                    buf = self._filtered_bufs[data_type]
                    buf.record(
                        external_message_id=f"error:{self._state.steam_id}:{data_type}:{_now_iso()}",
                        source_channel=_CONNECTOR_CHANNEL,
                        sender_identity=f"steam:{self._state.steam_id}",
                        subject_or_preview=None,
                        filter_reason=FilteredEventBuffer.reason_submission_error(),
                        full_payload=FilteredEventBuffer.full_payload(
                            channel=_CONNECTOR_CHANNEL,
                            provider=_CONNECTOR_PROVIDER,
                            endpoint_identity=self._state.endpoint_identity,
                            external_event_id=f"error:{self._state.steam_id}:{data_type}:{_now_iso()}",
                            external_thread_id=None,
                            observed_at=_now_iso(),
                            sender_identity=f"steam:{self._state.steam_id}",
                            raw={
                                "data_type": data_type,
                                "http_status": exc.status_code,
                                "error": str(exc),
                            },
                        ),
                        status="error",
                        error_detail=f"HTTP {exc.status_code}: {exc.body[:200]}",
                    )
                    await buf.flush(self._db_pool)
                except Exception:
                    logger.exception(
                        "Unexpected error in Steam poller: endpoint=%s data_type=%s",
                        self._state.endpoint_identity,
                        data_type,
                    )
                    steam_polls_total.labels(
                        data_type=data_type,
                        endpoint_identity=self._state.endpoint_identity,
                        status="error",
                    ).inc()

                # Post-poll: drain replay queue
                try:
                    await drain_replay_pending(
                        pool=self._db_pool,
                        connector_type=_CONNECTOR_TYPE,
                        endpoint_identity=self._state.endpoint_identity,
                        submit_fn=self._submit_envelope,
                        drain_logger=logger,
                    )
                except Exception:
                    logger.warning(
                        "Replay drain failed: endpoint=%s", self._state.endpoint_identity
                    )

                # Update last_poll_at
                self._state.last_poll_at[data_type] = datetime.now(UTC)

                # Sleep until next poll
                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            logger.debug(
                "Polling task cancelled: endpoint=%s data_type=%s",
                self._state.endpoint_identity,
                data_type,
            )
            raise

    # ------------------------------------------------------------------
    # Recently played poller
    # ------------------------------------------------------------------

    async def _poll_recently_played(self) -> None:
        """Poll IPlayerService/GetRecentlyPlayedGames and emit play_session events."""
        data = await self._steam_client.request(
            "IPlayerService",
            "GetRecentlyPlayedGames",
            params={"steamid": str(self._state.steam_id), "count": 10},
        )

        games = data.get("games", [])
        if not games:
            # Privacy or no recent play — not an error
            logger.debug(
                "No recent games (privacy or none played): endpoint=%s",
                self._state.endpoint_identity,
            )
            return

        # Update tracked games from recently played (for achievement polling).
        # Achievement cursors are keyed "achievements:<app_id>", not "achievements",
        # so we check whether tracked_games was explicitly pre-configured via metadata.
        recent_app_ids = [str(g["appid"]) for g in games[: self._max_tracked_games]]
        # Only override if no explicit tracked_games config was provided at startup.
        if not self._state.tracked_games:
            self._state.tracked_games = recent_app_ids

        # Build state for delta detection.
        # Keys are stored as strings to survive JSON round-trip through the DB:
        # json.dumps({730: ...}) → {"730": ...} → json.loads → {"730": ...}.
        # Using str keys here keeps prev_snapshot lookups consistent after restart.
        current_state: dict[str, Any] = {
            str(g["appid"]): {
                "playtime_2weeks": g.get("playtime_2weeks", 0),
                "playtime_forever": g.get("playtime_forever", 0),
            }
            for g in games
        }

        cursor = self._state.cursors.get("recently_played")
        prev_hash = cursor.state_hash if cursor else None
        prev_snapshot = cursor.state_snapshot if cursor else None

        new_hash = _state_hash(current_state)
        if new_hash == prev_hash:
            # No change
            return

        poll_ts = _now_iso()
        # poll_dt is the wall-clock time at which this poll ran, not an actual
        # play-session date. Steam's GetRecentlyPlayedGames API only returns a
        # rolling 14-day aggregate (playtime_2weeks); it does not expose
        # per-session timestamps. The 'date' column in steam_play_history
        # therefore records when the poller detected the playtime, not when the
        # user actually played. Do not use this column for per-day analytics.
        poll_dt = datetime.now(UTC)
        events_emitted = 0

        for game in games:
            app_id = game["appid"]
            game_name = game.get("name", f"App {app_id}")
            playtime_2weeks = game.get("playtime_2weeks", 0)

            # _compute_play_delta encapsulates all baseline-skip and delta rules.
            # Returns None when the game should be skipped (first poll, new game,
            # or no new play); returns a positive integer delta otherwise.
            delta = _compute_play_delta(app_id, playtime_2weeks, prev_snapshot)
            if delta is None:
                continue

            envelope = build_play_session_envelope(
                steam_id=self._state.steam_id,
                endpoint_identity=self._state.endpoint_identity,
                app_id=app_id,
                game_name=game_name,
                playtime_2weeks=playtime_2weeks,
                playtime_delta=delta,
                poll_ts=poll_ts,
                raw=game,
            )
            await self._maybe_submit(envelope, data_type="recently_played")
            events_emitted += 1

            # Persist detected play session in play_history. Write the
            # per-poll delta (not the rolling 14-day total), so the row
            # for this date reflects only minutes played since the prior
            # poll. Same-day re-polls accumulate via the additive upsert.
            await _upsert_play_history(
                self._db_pool,
                steam_id=self._state.steam_id,
                steam_account_id=self._state.steam_account_id,
                app_id=app_id,
                app_name=game_name,
                play_date=poll_dt,
                playtime_minutes=delta,
            )

        # Update cursor
        new_cursor = SteamCursor(
            endpoint_identity=self._state.endpoint_identity,
            data_type="recently_played",
            last_poll_at=datetime.now(UTC),
            state_hash=new_hash,
            state_snapshot=current_state,
        )
        self._state.cursors["recently_played"] = new_cursor
        await _save_steam_cursor(self._db_pool, new_cursor)

        if events_emitted:
            logger.info(
                "Steam recently_played: %d events emitted for endpoint=%s",
                events_emitted,
                self._state.endpoint_identity,
            )

    # ------------------------------------------------------------------
    # Online status poller
    # ------------------------------------------------------------------

    async def _poll_online_status(self) -> None:
        """Poll ISteamUser/GetPlayerSummaries and emit status_change events."""
        data = await self._steam_client.get_player_summaries([str(self._state.steam_id)])
        if not data:
            logger.debug(
                "No player summary (private profile): endpoint=%s",
                self._state.endpoint_identity,
            )
            return

        player = data[0]
        persona_state = player.get("personastate", 0)
        game_extra_info = player.get("gameextrainfo")

        current_state = {
            "persona_state": persona_state,
            "game_extra_info": game_extra_info,
        }

        cursor = self._state.cursors.get("online_status")
        prev_hash = cursor.state_hash if cursor else None
        prev_snapshot = cursor.state_snapshot if cursor else None

        new_hash = _state_hash(current_state)
        if new_hash == prev_hash:
            return

        poll_ts = _now_iso()

        if prev_snapshot is not None:
            # Detect change
            prev_persona = prev_snapshot.get("persona_state")
            prev_game = prev_snapshot.get("game_extra_info")

            if prev_persona != persona_state or prev_game != game_extra_info:
                envelope = build_status_change_envelope(
                    steam_id=self._state.steam_id,
                    endpoint_identity=self._state.endpoint_identity,
                    persona_state=persona_state,
                    game_extra_info=game_extra_info,
                    prev_persona_state=prev_persona,
                    prev_game_extra_info=prev_game,
                    poll_ts=poll_ts,
                )
                await self._maybe_submit(envelope, data_type="online_status")

        new_cursor = SteamCursor(
            endpoint_identity=self._state.endpoint_identity,
            data_type="online_status",
            last_poll_at=datetime.now(UTC),
            state_hash=new_hash,
            state_snapshot=current_state,
        )
        self._state.cursors["online_status"] = new_cursor
        await _save_steam_cursor(self._db_pool, new_cursor)

    # ------------------------------------------------------------------
    # Achievements poller
    # ------------------------------------------------------------------

    async def _poll_achievements(self) -> None:
        """Poll ISteamUserStats/GetPlayerAchievements for tracked games."""
        # Determine which games to track
        tracked = self._state.tracked_games[: self._max_tracked_games]
        if not tracked:
            logger.debug(
                "No tracked games for achievements: endpoint=%s",
                self._state.endpoint_identity,
            )
            return

        poll_ts = _now_iso()

        for app_id_str in tracked:
            app_id = int(app_id_str)
            data_type_key = f"achievements:{app_id}"

            try:
                data = await self._steam_client.request(
                    "ISteamUserStats",
                    "GetPlayerAchievements",
                    params={
                        "steamid": str(self._state.steam_id),
                        "appid": app_id,
                        "l": "en",
                    },
                )
            except SteamAPIError:
                # Privacy or no achievements for this game — skip silently
                logger.debug(
                    "Achievement poll skipped (privacy/no stats): endpoint=%s app_id=%s",
                    self._state.endpoint_identity,
                    app_id,
                )
                continue
            except SteamRateLimitError:
                raise  # Let outer loop handle backoff

            player_stats = data.get("playerstats", {})
            game_name = player_stats.get("gameName", f"App {app_id}")
            achievements = player_stats.get("achievements", [])

            # Build set of currently unlocked achievements
            current_unlocked: dict[str, dict[str, Any]] = {
                a["apiname"]: a for a in achievements if a.get("achieved", 0) == 1
            }

            current_state = {k: v.get("unlocktime", 0) for k, v in current_unlocked.items()}

            cursor = self._state.cursors.get(data_type_key)
            prev_hash = cursor.state_hash if cursor else None
            prev_snapshot = cursor.state_snapshot if cursor else None

            new_hash = _state_hash(current_state)
            if new_hash == prev_hash:
                continue

            if prev_snapshot is not None:
                # Find newly unlocked achievements
                for api_name, a_data in current_unlocked.items():
                    if api_name not in prev_snapshot:
                        envelope = build_achievement_unlock_envelope(
                            steam_id=self._state.steam_id,
                            endpoint_identity=self._state.endpoint_identity,
                            app_id=app_id,
                            game_name=game_name,
                            achievement_api_name=api_name,
                            achievement_display_name=a_data.get("name", api_name),
                            achievement_description=a_data.get("description", ""),
                            unlock_time=a_data.get("unlocktime", 0),
                            poll_ts=poll_ts,
                        )
                        await self._maybe_submit(envelope, data_type="achievements")

            new_cursor = SteamCursor(
                endpoint_identity=self._state.endpoint_identity,
                data_type=data_type_key,
                last_poll_at=datetime.now(UTC),
                state_hash=new_hash,
                state_snapshot=current_state,
            )
            self._state.cursors[data_type_key] = new_cursor
            await _save_steam_cursor(self._db_pool, new_cursor)

    # ------------------------------------------------------------------
    # Friends poller
    # ------------------------------------------------------------------

    async def _poll_friends(self) -> None:
        """Poll ISteamUser/GetFriendList and emit friend_change events."""
        try:
            data = await self._steam_client.request(
                "ISteamUser",
                "GetFriendList",
                params={
                    "steamid": str(self._state.steam_id),
                    "relationship": "friend",
                },
            )
        except SteamAPIError:
            # Private profile
            logger.debug(
                "Friend list not accessible (private): endpoint=%s",
                self._state.endpoint_identity,
            )
            return

        friends = data.get("friendslist", {}).get("friends", [])
        current_state: dict[str, str] = {
            f["steamid"]: f.get("relationship", "friend") for f in friends
        }

        cursor = self._state.cursors.get("friends")
        prev_hash = cursor.state_hash if cursor else None
        prev_snapshot = cursor.state_snapshot if cursor else None

        new_hash = _state_hash(current_state)
        if new_hash == prev_hash:
            return

        poll_ts = _now_iso()

        if prev_snapshot is not None:
            prev_ids = set(prev_snapshot.keys())
            current_ids = set(current_state.keys())

            for added_id in current_ids - prev_ids:
                envelope = build_friend_change_envelope(
                    steam_id=self._state.steam_id,
                    endpoint_identity=self._state.endpoint_identity,
                    friend_steam_id=added_id,
                    friend_name=None,
                    direction="added",
                    relationship=current_state.get(added_id, "friend"),
                    poll_ts=poll_ts,
                )
                await self._maybe_submit(envelope, data_type="friends")

            for removed_id in prev_ids - current_ids:
                envelope = build_friend_change_envelope(
                    steam_id=self._state.steam_id,
                    endpoint_identity=self._state.endpoint_identity,
                    friend_steam_id=removed_id,
                    friend_name=None,
                    direction="removed",
                    relationship=prev_snapshot.get(removed_id, "friend"),
                    poll_ts=poll_ts,
                )
                await self._maybe_submit(envelope, data_type="friends")

        new_cursor = SteamCursor(
            endpoint_identity=self._state.endpoint_identity,
            data_type="friends",
            last_poll_at=datetime.now(UTC),
            state_hash=new_hash,
            state_snapshot=current_state,
        )
        self._state.cursors["friends"] = new_cursor
        await _save_steam_cursor(self._db_pool, new_cursor)

    # ------------------------------------------------------------------
    # Game library poller
    # ------------------------------------------------------------------

    async def _poll_game_library(self) -> None:
        """Poll IPlayerService/GetOwnedGames and emit game_purchase events."""
        data = await self._steam_client.request(
            "IPlayerService",
            "GetOwnedGames",
            params={
                "steamid": str(self._state.steam_id),
                "include_appinfo": 1,
                "include_played_free_games": 1,
            },
        )

        games = data.get("games", [])
        current_state: dict[int, dict[str, Any]] = {
            g["appid"]: {
                "name": g.get("name", f"App {g['appid']}"),
                "playtime_forever": g.get("playtime_forever", 0),
            }
            for g in games
        }

        cursor = self._state.cursors.get("game_library")
        prev_hash = cursor.state_hash if cursor else None
        prev_snapshot = cursor.state_snapshot if cursor else None

        new_hash = _state_hash(current_state)
        if new_hash == prev_hash:
            return

        poll_ts = _now_iso()

        if prev_snapshot is not None:
            prev_app_ids = set(int(k) for k in prev_snapshot.keys())
            current_app_ids = set(current_state.keys())

            for new_app_id in current_app_ids - prev_app_ids:
                game_info = current_state[new_app_id]
                envelope = build_game_purchase_envelope(
                    steam_id=self._state.steam_id,
                    endpoint_identity=self._state.endpoint_identity,
                    app_id=new_app_id,
                    game_name=game_info["name"],
                    playtime_forever=game_info["playtime_forever"],
                    poll_ts=poll_ts,
                )
                await self._maybe_submit(envelope, data_type="game_library")
        # First poll: establish baseline without emitting events

        new_cursor = SteamCursor(
            endpoint_identity=self._state.endpoint_identity,
            data_type="game_library",
            last_poll_at=datetime.now(UTC),
            state_hash=new_hash,
            state_snapshot={str(k): v for k, v in current_state.items()},
        )
        self._state.cursors["game_library"] = new_cursor
        await _save_steam_cursor(self._db_pool, new_cursor)

    # ------------------------------------------------------------------
    # Event submission with filter gate
    # ------------------------------------------------------------------

    async def _maybe_submit(self, envelope: dict[str, Any], data_type: str) -> None:
        """Evaluate source filter gate, then submit or record in filtered_events."""
        event_type = envelope.get("event", {}).get("type", "unknown")

        if self._policy is not None:
            try:
                fe = _make_ingestion_envelope_for_filter(envelope)
                decision = self._policy.evaluate(fe)
                if not decision.allowed:
                    filter_reason = (
                        f"policy:{decision.matched_rule_type}:{decision.matched_rule_id}"
                    )
                    logger.debug(
                        "Steam event blocked: endpoint=%s event_type=%s reason=%s",
                        self._state.endpoint_identity,
                        event_type,
                        filter_reason,
                    )
                    steam_events_filtered_total.labels(
                        filter_reason=filter_reason,
                        endpoint_identity=self._state.endpoint_identity,
                    ).inc()
                    buf = self._filtered_bufs.get(
                        data_type, self._filtered_bufs.get("recently_played")
                    )
                    if buf:
                        buf.record(
                            external_message_id=envelope["event"]["external_event_id"],
                            source_channel=_CONNECTOR_CHANNEL,
                            sender_identity=envelope["sender"]["identity"],
                            subject_or_preview=envelope["payload"].get("normalized_text"),
                            filter_reason=filter_reason,
                            full_payload=FilteredEventBuffer.full_payload(
                                channel=_CONNECTOR_CHANNEL,
                                provider=_CONNECTOR_PROVIDER,
                                endpoint_identity=self._state.endpoint_identity,
                                external_event_id=envelope["event"]["external_event_id"],
                                external_thread_id=envelope["event"].get("external_thread_id"),
                                observed_at=envelope["event"]["observed_at"],
                                sender_identity=envelope["sender"]["identity"],
                                raw=envelope["payload"].get("raw"),
                                normalized_text=envelope["payload"].get("normalized_text"),
                                policy_tier=envelope.get("control", {}).get("policy_tier"),
                            ),
                            status="filtered",
                        )
                        await buf.flush(self._db_pool)
                    return
            except Exception:
                logger.warning(
                    "Policy evaluation error: endpoint=%s", self._state.endpoint_identity
                )

        await self._submit_envelope(envelope)
        steam_events_submitted_total.labels(
            event_type=event_type,
            endpoint_identity=self._state.endpoint_identity,
        ).inc()

    async def _submit_envelope(self, envelope: dict[str, Any]) -> None:
        """Submit an ingest.v1 envelope to the Switchboard via MCP."""
        try:
            await self._mcp_client.call_tool("ingest", envelope)
            self._metrics.record_ingest_submission(status="success")
            self._metrics.record_source_api_call(api_method="ingest", status="success")
        except Exception as exc:
            self._metrics.record_ingest_submission(status="error")
            logger.warning(
                "Failed to submit envelope: endpoint=%s error=%s",
                self._state.endpoint_identity,
                exc,
            )
            raise


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class SteamConnector:
    """Steam polling connector — multi-account, polling-based gaming activity ingestion.

    Manages the full lifecycle: startup, account discovery, per-account poller
    spawning, heartbeat, health endpoint, and graceful shutdown.
    """

    def __init__(
        self,
        switchboard_mcp_url: str,
        db_pool: asyncpg.Pool,
        health_port: int = _DEFAULT_HEALTH_PORT,
        account_rescan_s: int = _DEFAULT_ACCOUNT_RESCAN_S,
        max_tracked_games: int = _DEFAULT_MAX_TRACKED_GAMES,
        heartbeat_interval_s: int = _DEFAULT_HEARTBEAT_INTERVAL_S,
    ) -> None:
        self._switchboard_mcp_url = switchboard_mcp_url
        self._db_pool = db_pool
        self._health_port = health_port
        self._account_rescan_s = account_rescan_s
        self._max_tracked_games = max_tracked_games
        self._heartbeat_interval_s = heartbeat_interval_s

        self._mcp_client = CachedMCPClient(switchboard_mcp_url, client_name="steam-connector")
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity="steam:multi",
        )

        # Effective poll intervals — start from compiled-in defaults; refreshed from
        # connector_registry.settings on each rescan cycle.
        self._effective_poll_intervals: dict[str, int] = dict(_DEFAULT_INTERVALS)

        # Active pollers: endpoint_identity → SteamAccountPoller
        self._pollers: dict[str, SteamAccountPoller] = {}
        self._poller_states: dict[str, AccountPollerState] = {}

        self._start_time = time.time()
        self._shutdown_event = asyncio.Event()
        self._running = False

        # Heartbeat
        self._heartbeat: ConnectorHeartbeat | None = None

        # Health server
        self._health_server_thread: Thread | None = None

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the connector: wait for Switchboard, discover accounts, begin polling."""
        logger.info("Steam connector starting up")
        self._running = True

        await wait_for_switchboard_ready(self._switchboard_mcp_url)

        # Load settings from config store before first account discovery.
        await self._load_config_from_store()

        # Initial account discovery
        await self._discover_accounts()

        # Start heartbeat
        heartbeat_config = HeartbeatConfig(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=",".join(self._pollers.keys()) or "steam:no_accounts",
            interval_s=self._heartbeat_interval_s,
            enabled=True,
        )
        self._heartbeat = ConnectorHeartbeat(
            config=heartbeat_config,
            mcp_client=self._mcp_client,
            metrics=self._metrics,
            get_health_state=self._get_health_state,
        )
        self._heartbeat.start()

        # Start health server in background thread
        self._health_server_thread = Thread(target=self._run_health_server, daemon=True)
        self._health_server_thread.start()

        # Main loop: periodic account re-scan
        await self._rescan_loop()

    async def stop(self) -> None:
        """Gracefully stop all pollers and background tasks."""
        logger.info("Steam connector stopping")
        self._shutdown_event.set()
        self._running = False

        if self._heartbeat:
            await self._heartbeat.stop()

        # Stop all pollers
        for poller in self._pollers.values():
            await poller.stop()

        self._pollers.clear()
        self._poller_states.clear()
        logger.info("Steam connector stopped")

    # ------------------------------------------------------------------
    # Account discovery
    # ------------------------------------------------------------------

    async def _discover_accounts(self) -> None:
        """Query public.steam_accounts for active accounts and sync pollers."""
        try:
            async with self._db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT
                        sa.id,
                        sa.steam_id,
                        sa.status,
                        sa.metadata,
                        ei.value AS api_key
                    FROM public.steam_accounts sa
                    LEFT JOIN public.entities e ON e.id = sa.entity_id
                    LEFT JOIN public.entity_info ei
                        ON ei.entity_id = sa.entity_id
                       AND ei.type = 'steam_api_key'
                    WHERE sa.status = 'active'
                    """
                )
        except Exception:
            logger.warning("Failed to query steam_accounts", exc_info=True)
            return

        active_identities: set[str] = set()

        for row in rows:
            steam_id = row["steam_id"]
            steam_account_id: uuid.UUID | None = row["id"]
            api_key = row["api_key"]
            raw_metadata = row["metadata"]
            if raw_metadata is None:
                metadata: dict[str, Any] = {}
            elif isinstance(raw_metadata, str):
                metadata = json.loads(raw_metadata) if raw_metadata else {}
            else:
                metadata = dict(raw_metadata)
            endpoint_identity = f"steam:user:{steam_id}"

            if not api_key:
                logger.warning("No API key for steam_id=%s — skipping (degraded mode)", steam_id)
                continue

            active_identities.add(endpoint_identity)

            if endpoint_identity in self._pollers:
                continue  # Already polling

            # Build per-account intervals from effective global intervals + metadata overrides
            intervals: dict[str, int] = dict(self._effective_poll_intervals)
            overrides = metadata.get("poll_intervals", {})
            for dt_key, val in overrides.items():
                if dt_key in intervals and isinstance(val, int) and val > 0:
                    intervals[dt_key] = val

            # Load cursors
            cursors = await _load_steam_cursors(self._db_pool, endpoint_identity)

            # Determine tracked games from metadata or cursors
            tracked_games = metadata.get("tracked_games", [])
            if not tracked_games:
                # Auto-detect from recently played cursor state
                rp_cursor = cursors.get("recently_played")
                if rp_cursor and rp_cursor.state_snapshot:
                    tracked_games = list(str(k) for k in rp_cursor.state_snapshot.keys())
                    tracked_games = tracked_games[: self._max_tracked_games]

            state = AccountPollerState(
                steam_id=steam_id,
                steam_account_id=steam_account_id,
                endpoint_identity=endpoint_identity,
                api_key=api_key,
                intervals=intervals,
                cursors=cursors,
                tracked_games=tracked_games,
            )

            # Load ingestion policy for this account
            policy = await self._load_policy(endpoint_identity)

            poller = SteamAccountPoller(
                state=state,
                db_pool=self._db_pool,
                mcp_client=self._mcp_client,
                metrics=self._metrics,
                max_tracked_games=self._max_tracked_games,
                ingestion_policy=policy,
            )
            self._pollers[endpoint_identity] = poller
            self._poller_states[endpoint_identity] = state

            await poller.start()
            logger.info("Started Steam poller for endpoint=%s", endpoint_identity)

        # Shut down pollers for accounts that are no longer active
        revoked = set(self._pollers.keys()) - active_identities
        for endpoint_identity in revoked:
            logger.info("Steam account revoked/suspended — stopping poller: %s", endpoint_identity)
            poller = self._pollers.pop(endpoint_identity)
            self._poller_states.pop(endpoint_identity, None)
            await poller.stop()

        if not self._pollers:
            logger.info("Steam connector: no active accounts — running in idle/degraded mode")

        # Purge cursors for accounts revoked more than 30 days ago.
        await purge_revoked_cursors(self._db_pool)

    async def _load_config_from_store(self) -> None:
        """Read connector settings from switchboard.connector_registry.settings.

        Updates ``_account_rescan_s``, ``_heartbeat_interval_s``, and
        ``_max_tracked_games`` from the dashboard-stored values (if any).
        Falls back to compiled-in defaults for missing keys.

        This is called on each rescan cycle so settings take effect without a
        connector restart.
        """
        _CONFIG_ENDPOINT = "steam:config"
        try:
            from butlers.connectors.cursor_store import load_connector_settings

            settings = await load_connector_settings(self._db_pool, "steam", _CONFIG_ENDPOINT)
        except Exception:
            logger.debug("Failed to load Steam connector settings from store (non-fatal)")
            return

        if not settings:
            return

        if "account_rescan_s" in settings:
            val = settings["account_rescan_s"]
            if isinstance(val, int) and val > 0:
                self._account_rescan_s = val

        if "heartbeat_interval_s" in settings:
            val = settings["heartbeat_interval_s"]
            if isinstance(val, int) and val > 0:
                self._heartbeat_interval_s = val

        if "max_tracked_games" in settings:
            val = settings["max_tracked_games"]
            if isinstance(val, int) and val > 0:
                self._max_tracked_games = val

        # Update effective poll intervals used for new account pollers.
        pi_val = settings.get("poll_intervals")
        pi: dict = pi_val if isinstance(pi_val, dict) else {}
        if pi:
            for dt_key in list(_DEFAULT_INTERVALS):
                if dt_key in pi and isinstance(pi[dt_key], int) and pi[dt_key] > 0:
                    self._effective_poll_intervals[dt_key] = pi[dt_key]

        logger.debug(
            "Steam connector config reloaded: rescan=%ds heartbeat=%ds max_tracked=%d",
            self._account_rescan_s,
            self._heartbeat_interval_s,
            self._max_tracked_games,
        )

    async def _rescan_loop(self) -> None:
        """Periodically re-discover accounts while running."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=self._account_rescan_s)
                break  # shutdown requested
            except TimeoutError:
                pass  # rescan interval elapsed

            # Reload settings from config store before discovering accounts.
            await self._load_config_from_store()
            await self._discover_accounts()

    # ------------------------------------------------------------------
    # Policy loader
    # ------------------------------------------------------------------

    async def _load_policy(self, endpoint_identity: str) -> Any | None:
        """Initialize and load an IngestionPolicyEvaluator for a connector scope."""
        try:
            from butlers.ingestion_policy import IngestionPolicyEvaluator

            scope = f"connector:{_CONNECTOR_TYPE}:{endpoint_identity}"
            policy = IngestionPolicyEvaluator(scope=scope, db_pool=self._db_pool)
            await policy.ensure_loaded()
            return policy
        except Exception:
            logger.warning(
                "Failed to load ingestion policy for %s", endpoint_identity, exc_info=True
            )
            return None

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def _get_health_state(self) -> tuple[str, str | None]:
        """Return (status, error_message) for heartbeat."""
        if not self._pollers:
            return ("degraded", "No active Steam accounts")

        all_healths = []
        for state in self._poller_states.values():
            all_healths.append(state.effective_health)

        if "error" in all_healths:
            return ("error", "One or more accounts in error state")
        if "degraded" in all_healths:
            return ("degraded", None)
        return ("healthy", None)

    def get_health_report(self) -> dict[str, Any]:
        """Return a structured health report (for the health endpoint)."""
        uptime_s = int(time.time() - self._start_time)
        overall_status, _ = self._get_health_state()

        account_health = []
        for state in self._poller_states.values():
            data_types = {}
            for dt in _DEFAULT_INTERVALS:
                last_poll = state.last_poll_at.get(dt)
                data_types[dt] = {
                    "status": state.health.get(dt, "healthy"),
                    "last_poll_at": last_poll.isoformat() if last_poll else None,
                }

            account_health.append(
                {
                    "steam_id": _redact_steam_id(state.steam_id),
                    "endpoint_identity": state.endpoint_identity,
                    "status": state.effective_health,
                    "error": state.account_error,
                    "data_types": data_types,
                }
            )

        return {
            "status": overall_status,
            "uptime_seconds": uptime_s,
            "active_accounts": len(self._pollers),
            "account_health": account_health,
        }

    # ------------------------------------------------------------------
    # Health server
    # ------------------------------------------------------------------

    def _run_health_server(self) -> None:
        """Run a minimal health + metrics HTTP server in a background thread."""
        import http.server
        import socketserver

        connector_ref = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    body = json.dumps(connector_ref.get_health_report()).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/metrics":
                    body = generate_latest()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args: Any) -> None:  # noqa: ANN401
                pass  # silence access log

        try:
            with socketserver.TCPServer(("", self._health_port), _Handler) as httpd:
                httpd.serve_forever()
        except Exception:
            logger.warning("Health server failed to start on port %d", self._health_port)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def _run() -> None:
    """Main coroutine — create pool, start connector, handle signals."""
    import asyncpg

    from butlers.db import db_params_from_env, register_jsonb_codec, should_retry_with_ssl_disable

    switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL", "").strip()
    if not switchboard_mcp_url:
        raise ValueError("SWITCHBOARD_MCP_URL is required")

    health_port = int(os.environ.get("CONNECTOR_HEALTH_PORT", str(_DEFAULT_HEALTH_PORT)))
    rescan_s = int(os.environ.get("STEAM_ACCOUNT_RESCAN_S", str(_DEFAULT_ACCOUNT_RESCAN_S)))
    max_tracked = int(os.environ.get("STEAM_MAX_TRACKED_GAMES", str(_DEFAULT_MAX_TRACKED_GAMES)))
    heartbeat_s = int(
        os.environ.get("STEAM_HEARTBEAT_INTERVAL_S", str(_DEFAULT_HEARTBEAT_INTERVAL_S))
    )

    params = db_params_from_env()
    pool_kwargs: dict[str, Any] = {
        "host": params.get("host", "localhost"),
        "port": int(params.get("port", 5432)),
        "user": params.get("user", "butlers"),
        "password": params.get("password", "butlers"),
        "database": params.get("database", "butlers"),
        "min_size": 2,
        "max_size": 10,
    }
    if params.get("ssl"):
        pool_kwargs["ssl"] = params["ssl"]
    pool_kwargs["setup"] = connector_setup_role
    pool_kwargs["init"] = register_jsonb_codec

    try:
        pool = await asyncpg.create_pool(**pool_kwargs)
    except Exception as exc:
        if should_retry_with_ssl_disable(exc, pool_kwargs.get("ssl")):
            pool_kwargs["ssl"] = "disable"
            pool = await asyncpg.create_pool(**pool_kwargs)
        else:
            raise

    connector = SteamConnector(
        switchboard_mcp_url=switchboard_mcp_url,
        db_pool=pool,
        health_port=health_port,
        account_rescan_s=rescan_s,
        max_tracked_games=max_tracked,
        heartbeat_interval_s=heartbeat_s,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(connector.stop()))

    try:
        await connector.start()
    finally:
        await pool.close()


def main() -> None:
    """CLI entrypoint."""
    from butlers.core.logging import configure_logging

    configure_logging()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
