"""ActivityWatch connector runtime for desktop-activity ingestion via polling.

ActivityWatch (https://activitywatch.net/) is an open-source, local-first
active-window/AFK tracker. This connector polls the local (or Tailscale-
reachable) ActivityWatch REST API for window-focus and AFK-status events,
classifies each focused application into a coarse app-class bucket
(``ide`` / ``terminal`` / ``browser`` / ``other``), normalizes them into
ingest.v1 envelopes, and submits them to the Switchboard. It is the desktop
work-activity ingestion pathway into the butler ecosystem (bu-whhll.6, epic
bu-whhll Tier 1 "gold standard": no connector observes any computer today —
the single largest coverage gap in weekday work visibility).

Key behaviors:
- Single-machine, poll-based connector (one connector instance per machine;
  no OAuth/account registry — the machine is identified via
  ``ACTIVITYWATCH_MACHINE_ID``, mirroring the Home Assistant connector's
  env-only, no-DB-registry pattern for local-network services)
- Discovers the ``aw-watcher-window`` (type ``currentwindow``) and
  ``aw-watcher-afk`` (type ``afkstatus``) buckets via GET /api/0/buckets
- Polls window-focus events since the last checkpoint via
  GET /api/0/buckets/{bucket_id}/events
- Bucketing app classes into ``ide`` / ``terminal`` / ``browser`` / ``other``
  via a static substring-match table (see ``classify_app``)
- Matches each window event's start timestamp against the AFK bucket's
  status intervals to derive ``is_afk`` (best-effort; AFK bucket is optional)
- For browser window events, best-effort correlates the matching
  ``aw-watcher-web`` event by timestamp and derives a hostname-only
  ``browser_domain`` sub-bucket. Raw web URLs and tab titles stay in the
  sensitive evidence JSON and never enter the ingest envelope.
- Bounded first-run backfill (``ACTIVITYWATCH_MAX_BACKFILL_DAYS``, default 30)
  so a long-running local AW install does not flood the system on first
  connect (RFC per "first poll baseline" connector obligation)
- Durable evidence persistence to ``connectors.activitywatch_events``
  (idempotent via ON CONFLICT DO NOTHING on ``idempotency_key``)
- Timestamp-based checkpoint via cursor_store keyed by
  ("activitywatch", "activitywatch:<machine_id>")
- Heartbeat protocol with connector_type="activitywatch"
- Prometheus metrics (standard connector metrics)
- Health endpoint returning state/uptime/last_event_at/events_today
- Filtered event batch flush to connectors.filtered_events
- Replay queue drain loop each poll cycle
- IngestionPolicyEvaluator source filter gate
- Scheduled data retention purge (every 6 hours, DELETE from
  connectors.activitywatch_events) — mirrors the OwnTracks connector's
  retention task (bu-il04h). Window titles are privacy=sensitive durable
  evidence with no other TTL, so this table defaults to a shorter retention
  window than OwnTracks' 30-day location default.
- Graceful shutdown on SIGTERM/SIGINT

Privacy:
- Window titles are captured into the durable evidence table for forensic /
  future-reclassification use, but are NEVER included in the ingest.v1
  envelope's normalized text, and NEVER projected by the Chronicler adapter
  (see ``src/butlers/chronicler/adapters/activitywatch.py``). A validated
  hostname is the sole web-watcher detail allowed into a normal projection;
  raw URLs and titles remain in the evidence table's ``raw_payload`` only.

Environment variables:
- SWITCHBOARD_MCP_URL (required)
- ACTIVITYWATCH_MACHINE_ID (required): stable identifier for this machine
  (e.g. "desktop", "work-laptop"); becomes endpoint_identity
  "activitywatch:<machine_id>"
- ACTIVITYWATCH_BASE_URL (optional, default "http://localhost:5600"): base
  URL of the ActivityWatch REST API. For a remote machine reachable over
  Tailscale, set this to the Tailscale MagicDNS hostname/IP.
- ACTIVITYWATCH_POLL_INTERVAL_S (optional, default 60)
- ACTIVITYWATCH_MAX_BACKFILL_DAYS (optional, default 30): bound on how far
  back the very first poll (no checkpoint yet) looks for history.
- ACTIVITYWATCH_MIN_EVENT_DURATION_S (optional, default 0): skip window
  events shorter than this many seconds (noise reduction).
- ACTIVITYWATCH_RETENTION_DAYS (optional, default 14): data retention in
  days for connectors.activitywatch_events. Rows older than this are
  purged every 6 hours. Defaults shorter than OwnTracks' 30-day location
  retention because this table durably stores window titles (sensitive).
- CONNECTOR_INGESTION_TIER (optional, default "metadata"): "metadata" or
  "full" — controls whether the raw ``app`` process name is included in
  ``payload.raw``. Window titles are never included regardless of tier.
- CONNECTOR_HEALTH_PORT (optional, default 40092)
- CONNECTOR_HEARTBEAT_INTERVAL_S (optional, default 120)
- BUTLER_SHARED_DB_NAME (optional; shared butler DB, defaults to 'butlers')

Security requirements:
- No authentication layer: the trust boundary is localhost + Tailscale
  (matches project security doctrine — see
  ``about/heart-and-soul/security.md``). The AW REST API itself has no
  built-in auth; do not expose ``ACTIVITYWATCH_BASE_URL`` beyond that
  boundary.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Thread
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import FastAPI
from prometheus_client import Counter, generate_latest

from butlers.connectors.cursor_store import NO_PARENT, load_cursor, save_cursor
from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging
from butlers.credential_store import shared_db_name_from_env
from butlers.db import (
    db_params_from_env,
    register_jsonb_codec,
    schema_search_path,
    should_retry_with_ssl_disable,
)
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "activitywatch"
_CONNECTOR_CHANNEL = "activitywatch"
_CONNECTOR_PROVIDER = "activitywatch"

_EVIDENCE_TABLE = "connectors.activitywatch_events"

_DEFAULT_BASE_URL = "http://localhost:5600"
_DEFAULT_POLL_INTERVAL_S = 60
_DEFAULT_HEALTH_PORT = 40092
_DEFAULT_MAX_BACKFILL_DAYS = 30
_DEFAULT_MIN_EVENT_DURATION_S = 0.0
_DEFAULT_EVENT_LIMIT = 2000

# Retention (bu-il04h): connectors.activitywatch_events durably stores
# window titles (privacy=sensitive) with no other TTL. Default is shorter
# than OwnTracks' 30-day location retention (owntracks.py) since title text
# is more sensitive than app-class buckets; owner can widen/narrow via
# ACTIVITYWATCH_RETENTION_DAYS.
_DEFAULT_RETENTION_DAYS = 14
_MIN_RETENTION_DAYS = 1
_RETENTION_PURGE_INTERVAL_S = 6 * 60 * 60  # 6 hours

# Public aliases for use by standalone ActivityWatchRetentionConfig / ActivityWatchRetention
DEFAULT_RETENTION_DAYS = _DEFAULT_RETENTION_DAYS
MIN_RETENTION_DAYS = _MIN_RETENTION_DAYS
RETENTION_PURGE_INTERVAL_S = _RETENTION_PURGE_INTERVAL_S

_TIER_METADATA = "metadata"
_TIER_FULL = "full"

# AW bucket "type" field values (aw-client convention).
_BUCKET_TYPE_WINDOW = "currentwindow"
_BUCKET_TYPE_AFK = "afkstatus"
_BUCKET_TYPE_WEB = "web.tab.current"

# App-class buckets. Browser events may carry an additional hostname-only
# sub-bucket after correlation with aw-watcher-web (see ``match_browser_domain``).
AppClass = Literal["ide", "terminal", "browser", "other"]

_IDE_APPS = frozenset(
    {
        "code",
        "visual studio code",
        "vscodium",
        "cursor",
        "pycharm",
        "intellij idea",
        "webstorm",
        "goland",
        "rubymine",
        "clion",
        "sublime_text",
        "sublime text",
        "vim",
        "nvim",
        "neovim",
        "macvim",
        "emacs",
        "android studio",
        "xcode",
    }
)

_TERMINAL_APPS = frozenset(
    {
        "iterm2",
        "iterm",
        "terminal",
        "alacritty",
        "wezterm",
        "kitty",
        "konsole",
        "gnome-terminal",
        "warp",
        "hyper",
        "windows terminal",
        "cmd.exe",
        "powershell",
        "tmux",
    }
)

_BROWSER_APPS = frozenset(
    {
        "google chrome",
        "chrome",
        "firefox",
        "firefox developer edition",
        "safari",
        "microsoft edge",
        "msedge",
        "brave browser",
        "brave",
        "arc",
        "chromium",
        "opera",
        "vivaldi",
    }
)


def classify_app(app: str | None) -> AppClass:
    """Bucket a raw ActivityWatch ``app`` process/window-class name.

    Case-insensitive substring match against static keyword tables, checked
    in order IDE -> terminal -> browser -> other. This is a heuristic: it
    will misclassify unusual app names, and does not attempt browser-domain
    sub-bucketing (see module docstring). Empty/None input classifies as
    ``"other"``.
    """
    if not app:
        return "other"
    normalized = app.strip().lower()
    for suffix in (".exe", ".app"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    if any(keyword in normalized for keyword in _IDE_APPS):
        return "ide"
    if any(keyword in normalized for keyword in _TERMINAL_APPS):
        return "terminal"
    if any(keyword in normalized for keyword in _BROWSER_APPS):
        return "browser"
    return "other"


# ---------------------------------------------------------------------------
# ActivityWatch-specific Prometheus metrics
# ---------------------------------------------------------------------------

activitywatch_polls_total = Counter(
    "connector_activitywatch_polls_total",
    "Total number of ActivityWatch poll cycles",
    labelnames=["endpoint_identity", "status"],
)

activitywatch_events_received_total = Counter(
    "connector_activitywatch_events_received_total",
    "Total number of ActivityWatch window-focus events received by app_class",
    labelnames=["endpoint_identity", "app_class"],
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class ActivityWatchConnectorConfig:
    """Configuration for the ActivityWatch connector runtime."""

    switchboard_mcp_url: str
    machine_id: str
    provider: str = _CONNECTOR_PROVIDER
    channel: str = _CONNECTOR_CHANNEL

    base_url: str = _DEFAULT_BASE_URL
    poll_interval_s: int = _DEFAULT_POLL_INTERVAL_S
    max_backfill_days: int = _DEFAULT_MAX_BACKFILL_DAYS
    min_event_duration_s: float = _DEFAULT_MIN_EVENT_DURATION_S
    ingestion_tier: Literal["metadata", "full"] = _TIER_METADATA
    retention_days: int = _DEFAULT_RETENTION_DAYS

    health_port: int = _DEFAULT_HEALTH_PORT

    @classmethod
    def from_env(cls) -> ActivityWatchConnectorConfig:
        """Load configuration from environment variables.

        Raises:
            ValueError: If ``SWITCHBOARD_MCP_URL`` or
                ``ACTIVITYWATCH_MACHINE_ID`` is missing (fail-closed — the
                machine id is required to build a stable endpoint identity
                and evidence-table partition key).
        """
        switchboard_mcp_url = os.environ.get("SWITCHBOARD_MCP_URL", "").strip()
        if not switchboard_mcp_url:
            raise ValueError("SWITCHBOARD_MCP_URL is required")

        machine_id = os.environ.get("ACTIVITYWATCH_MACHINE_ID", "").strip()
        if not machine_id:
            raise ValueError(
                "ACTIVITYWATCH_MACHINE_ID is required (identifies which machine this "
                "connector instance polls, e.g. 'desktop', 'work-laptop')"
            )

        def _int(key: str, default: int) -> int:
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                logger.warning("Invalid value for %s=%r, using default %d", key, raw, default)
                return default

        def _float(key: str, default: float) -> float:
            raw = os.environ.get(key, "").strip()
            if not raw:
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning("Invalid value for %s=%r, using default %s", key, raw, default)
                return default

        raw_tier = os.environ.get("CONNECTOR_INGESTION_TIER", _TIER_METADATA).strip().lower()
        if raw_tier not in (_TIER_METADATA, _TIER_FULL):
            logger.warning("Unknown CONNECTOR_INGESTION_TIER=%r; using 'metadata'", raw_tier)
            raw_tier = _TIER_METADATA
        ingestion_tier: Literal["metadata", "full"] = raw_tier  # type: ignore[assignment]

        retention_days = _int("ACTIVITYWATCH_RETENTION_DAYS", _DEFAULT_RETENTION_DAYS)
        if retention_days < _MIN_RETENTION_DAYS:
            raise ValueError(
                f"ACTIVITYWATCH_RETENTION_DAYS={retention_days} is below minimum "
                f"{_MIN_RETENTION_DAYS}"
            )

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            machine_id=machine_id,
            provider=os.environ.get("CONNECTOR_PROVIDER", _CONNECTOR_PROVIDER),
            channel=os.environ.get("CONNECTOR_CHANNEL", _CONNECTOR_CHANNEL),
            base_url=os.environ.get("ACTIVITYWATCH_BASE_URL", _DEFAULT_BASE_URL).rstrip("/"),
            poll_interval_s=_int("ACTIVITYWATCH_POLL_INTERVAL_S", _DEFAULT_POLL_INTERVAL_S),
            max_backfill_days=_int("ACTIVITYWATCH_MAX_BACKFILL_DAYS", _DEFAULT_MAX_BACKFILL_DAYS),
            min_event_duration_s=_float(
                "ACTIVITYWATCH_MIN_EVENT_DURATION_S", _DEFAULT_MIN_EVENT_DURATION_S
            ),
            ingestion_tier=ingestion_tier,
            retention_days=retention_days,
            health_port=_int("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
        )


# ---------------------------------------------------------------------------
# Retention configuration (standalone, reusable)
# ---------------------------------------------------------------------------


@dataclass
class ActivityWatchRetentionConfig:
    """Configuration for ActivityWatch data retention.

    Attributes:
        retention_days: Number of days to retain window-focus events (including
            sensitive window titles). Rows older than this threshold are deleted
            from connectors.activitywatch_events on each purge cycle. Must be an
            integer >= ``MIN_RETENTION_DAYS`` (1).
    """

    retention_days: int = DEFAULT_RETENTION_DAYS

    def __post_init__(self) -> None:
        """Validate retention_days on construction regardless of how the config is built.

        Raises:
            TypeError: If ``retention_days`` is not an ``int``.
            ValueError: If ``retention_days`` is less than ``MIN_RETENTION_DAYS``.
        """
        if not isinstance(self.retention_days, int):
            raise TypeError(
                f"retention_days must be an int, got {type(self.retention_days).__name__!r}: "
                f"{self.retention_days!r}"
            )
        if self.retention_days < MIN_RETENTION_DAYS:
            raise ValueError(
                f"retention_days must be >= {MIN_RETENTION_DAYS}, got {self.retention_days}. "
                "A value of 0 or negative would delete all activity history immediately."
            )

    @classmethod
    def from_env(cls) -> ActivityWatchRetentionConfig:
        """Load retention configuration from environment variables.

        Reads ``ACTIVITYWATCH_RETENTION_DAYS``. If the value is set to a number
        less than ``MIN_RETENTION_DAYS`` (1), a ``ValueError`` is raised to
        prevent accidental mass-deletion of fresh data.

        Returns:
            ActivityWatchRetentionConfig with resolved settings.

        Raises:
            ValueError: If ``ACTIVITYWATCH_RETENTION_DAYS`` is set to a value < 1.
        """
        raw = os.environ.get("ACTIVITYWATCH_RETENTION_DAYS")
        if raw is None:
            return cls(retention_days=DEFAULT_RETENTION_DAYS)

        try:
            days = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"ACTIVITYWATCH_RETENTION_DAYS must be a positive integer, got: {raw!r}"
            ) from exc

        if days < MIN_RETENTION_DAYS:
            raise ValueError(
                f"ACTIVITYWATCH_RETENTION_DAYS must be >= {MIN_RETENTION_DAYS}, got {days}. "
                "Setting 0 or a negative value would delete all activity history immediately."
            )

        return cls(retention_days=days)


# ---------------------------------------------------------------------------
# Retention purge SQL (parameterized — no interpolation footgun)
# ---------------------------------------------------------------------------

_PURGE_SQL = f"""\
DELETE FROM {_EVIDENCE_TABLE}
WHERE ts < NOW() - $1 * INTERVAL '1 day'
"""


# ---------------------------------------------------------------------------
# Retention background task (standalone, reusable)
# ---------------------------------------------------------------------------


class ActivityWatchRetention:
    """Background data retention task for the ActivityWatch connector.

    Runs a purge cycle every ``RETENTION_PURGE_INTERVAL_S`` seconds (6 hours)
    that deletes expired rows from ``connectors.activitywatch_events`` where
    ``ts`` is older than the configured retention period. This is the durable
    evidence table that stores window titles (privacy=sensitive); see the
    module docstring.

    Purge failures are logged at WARNING level and never crash the connector.

    Usage::

        pool = await asyncpg.create_pool(...)
        config = ActivityWatchRetentionConfig.from_env()
        retention = ActivityWatchRetention(config, pool)
        retention.start()
        ...
        await retention.stop()
    """

    def __init__(
        self,
        config: ActivityWatchRetentionConfig,
        pool: asyncpg.Pool,
        *,
        purge_interval_s: int = RETENTION_PURGE_INTERVAL_S,
    ) -> None:
        """Initialise the retention task.

        Args:
            config: Retention configuration (retention_days, etc.).
            pool: asyncpg connection pool that can reach the ``connectors`` schema.
            purge_interval_s: Interval between purge cycles in seconds.
                Defaults to ``RETENTION_PURGE_INTERVAL_S`` (6 hours). Exposed as
                a parameter for unit testing so tests do not have to wait 6 hours.
                Must be >= 1; a value of 0 would spin the purge loop without pause
                and hammer the DB. A negative value would cause ``asyncio.sleep``
                to raise immediately, killing the background task.
        """
        if purge_interval_s < 1:
            raise ValueError(
                f"purge_interval_s must be >= 1, got {purge_interval_s}. "
                "A value of 0 would spin the purge loop without pause; "
                "a negative value would raise in asyncio.sleep."
            )
        self._config = config
        self._pool = pool
        self._purge_interval_s = purge_interval_s
        self._task: asyncio.Task | None = None
        self._consecutive_failures = 0

    @property
    def retention_days(self) -> int:
        """Return the active retention period in days."""
        return self._config.retention_days

    @property
    def health_degradation_message(self) -> str | None:
        """Return a sanitized health diagnostic for consecutive purge failures."""
        failures = self._consecutive_failures
        if failures == 0:
            return None
        occurrence = "time" if failures == 1 else "times"
        return f"ActivityWatch retention purge has failed {failures} consecutive {occurrence}"

    def start(self) -> None:
        """Schedule the background purge loop as an asyncio task.

        Must be called from within a running event loop. Calling ``start()``
        while a task is already running is a no-op with a warning log.
        """
        if self._task is not None:
            logger.warning(
                "ActivityWatch retention task already running; ignoring duplicate start call."
            )
            return

        self._task = asyncio.create_task(self._purge_loop())
        logger.info(
            "ActivityWatch retention task started: retention_days=%d, interval_s=%d",
            self._config.retention_days,
            self._purge_interval_s,
        )

    async def stop(self) -> None:
        """Cancel the background purge loop and wait for it to exit."""
        if self._task is None:
            return

        logger.info("Stopping ActivityWatch retention task.")
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

        logger.info("ActivityWatch retention task stopped.")

    async def purge_once(self) -> int:
        """Execute a single purge cycle immediately.

        Deletes rows from ``connectors.activitywatch_events`` where ``ts`` is
        older than the configured retention period.

        Returns:
            Number of rows deleted.

        Raises:
            Exception: Re-raises any database exceptions so that ``_purge_loop``
                can catch and log them without crashing the connector.
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(_PURGE_SQL, self._config.retention_days)

        # asyncpg returns a status string like "DELETE 42"
        deleted = _parse_delete_count(result)
        return deleted

    async def _purge_loop(self) -> None:
        """Repeat purge cycles forever, separated by ``_purge_interval_s``.

        Failures are logged at WARNING and the loop continues.
        """
        try:
            while True:
                await asyncio.sleep(self._purge_interval_s)
                await self._run_purge()
        except asyncio.CancelledError:
            logger.debug("ActivityWatch retention purge loop cancelled.")
            raise

    async def _run_purge(self) -> None:
        """Execute one purge cycle with error handling and logging."""
        try:
            deleted = await self.purge_once()
            self._consecutive_failures = 0
            logger.info(
                "ActivityWatch retention purge complete: deleted %d rows (retention_days=%d)",
                deleted,
                self._config.retention_days,
            )
        except Exception:
            self._consecutive_failures += 1
            logger.warning(
                "ActivityWatch retention purge failed (retention_days=%d). Will retry on next "
                "cycle.",
                self._config.retention_days,
                exc_info=True,
            )


def _parse_delete_count(status: str) -> int:
    """Parse the row count from an asyncpg DELETE status string.

    asyncpg returns a string such as ``"DELETE 42"`` after ``conn.execute()``.
    This helper extracts the integer count. Returns 0 if the string cannot
    be parsed.

    Args:
        status: Status string returned by ``asyncpg.Connection.execute()``.

    Returns:
        Number of deleted rows, or 0 if parsing fails.
    """
    try:
        parts = status.split()
        if len(parts) == 2 and parts[0] == "DELETE":
            return int(parts[1])
    except (ValueError, AttributeError):
        pass
    return 0


# ---------------------------------------------------------------------------
# ActivityWatch REST client helpers
# ---------------------------------------------------------------------------


class ActivityWatchUnavailableError(Exception):
    """Raised when the ActivityWatch REST API cannot be reached or parsed.

    This is an *expected* condition (the machine may be off, AW not running,
    or Tailscale unreachable) — callers should log at WARNING and retry on
    the next poll cycle rather than treating it as fatal.
    """


def _parse_aw_timestamp(raw: str) -> datetime:
    """Parse an ActivityWatch event timestamp as an aware UTC instant.

    ActivityWatch stores timestamps as UTC and may discard the source offset,
    so an offset-free ISO-8601 value is UTC rather than an unknown local time.
    """

    timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp


@dataclass(frozen=True)
class BrowserDomainMatch:
    """A safe browser-domain derivation plus its sensitive source event.

    ``domain`` is an HTTP(S) hostname with no path, query, fragment, port,
    credentials, or tab title. ``raw_event`` must only be stored in the
    connector's evidence surface; callers must not put it in normal ingress
    or projections.
    """

    domain: str
    raw_event: dict[str, Any]


def _safe_browser_hostname(hostname: object) -> str | None:
    """Return a plain ASCII hostname suitable for normal evidence fields."""

    if not isinstance(hostname, str):
        return None
    hostname = hostname.strip().lower().rstrip(".")
    if not hostname or len(hostname) > 253:
        return None

    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in label)
        for label in labels
    ):
        return None
    return hostname


def _normalize_browser_hostname(raw_url: object) -> str | None:
    """Return an ASCII hostname from an HTTP(S) URL, or ``None``.

    ActivityWatch's web watcher reports an entire URL. Keeping only its
    hostname is the privacy boundary for normal browser-domain projections.
    Non-web schemes and malformed values deliberately fail closed.
    """

    if not isinstance(raw_url, str) or not raw_url:
        return None

    try:
        parsed = urlsplit(raw_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None
    return _safe_browser_hostname(hostname)


def _to_utc_instant(timestamp: datetime) -> datetime | None:
    """Canonicalize an ActivityWatch timestamp to its documented UTC instant."""

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def match_browser_domain(
    window_ts: datetime,
    web_events: list[dict[str, Any]],
) -> BrowserDomainMatch | None:
    """Correlate a window-focus instant to an overlapping web-watcher event.

    ActivityWatch event intervals use a half-open ``[start, end)`` boundary,
    so an event beginning at a prior event's exact end takes precedence. If
    valid web events overlap, the latest start wins; an equal-start collision
    breaks deterministically by hostname. All comparisons use UTC instants;
    offset-free ActivityWatch timestamps are UTC by protocol, while malformed
    source data is ignored.
    """

    instant = _to_utc_instant(window_ts)
    if instant is None:
        return None

    candidates: list[tuple[datetime, str, dict[str, Any]]] = []
    for event in web_events:
        try:
            raw_timestamp = event["timestamp"]
            if not isinstance(raw_timestamp, str):
                continue
            start = _to_utc_instant(_parse_aw_timestamp(raw_timestamp))
            duration_seconds = float(event.get("duration", 0.0))
            data = event.get("data")
            raw_url = data.get("url") if isinstance(data, dict) else None
            domain = _normalize_browser_hostname(raw_url)
            if start is None or domain is None or duration_seconds <= 0:
                continue
            end = start + timedelta(seconds=duration_seconds)
        except (KeyError, OverflowError, TypeError, ValueError):
            continue

        if start <= instant < end:
            candidates.append((start, domain, event))

    if not candidates:
        return None

    _, domain, raw_event = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))
    return BrowserDomainMatch(domain=domain, raw_event=raw_event)


async def fetch_buckets(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """GET /api/0/buckets — return the raw bucket-id -> metadata mapping.

    Raises:
        ActivityWatchUnavailableError: on any network/HTTP/parse failure.
    """
    try:
        response = await client.get("/api/0/buckets")
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ActivityWatchUnavailableError(f"failed to list AW buckets: {exc}") from exc
    if not isinstance(data, dict):
        raise ActivityWatchUnavailableError(
            f"unexpected /api/0/buckets response type: {type(data)}"
        )
    return data


def find_bucket_id(buckets: dict[str, dict[str, Any]], bucket_type: str) -> str | None:
    """Return the first bucket id whose ``type`` field matches *bucket_type*."""
    for bucket_id, meta in buckets.items():
        if isinstance(meta, dict) and meta.get("type") == bucket_type:
            return bucket_id
    return None


async def fetch_events(
    client: httpx.AsyncClient,
    bucket_id: str,
    *,
    since: datetime,
    limit: int = _DEFAULT_EVENT_LIMIT,
) -> list[dict[str, Any]]:
    """GET /api/0/buckets/{bucket_id}/events since *since*, ascending order.

    Raises:
        ActivityWatchUnavailableError: on any network/HTTP/parse failure.
    """
    try:
        response = await client.get(
            f"/api/0/buckets/{bucket_id}/events",
            params={"start": since.isoformat(), "limit": limit},
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ActivityWatchUnavailableError(
            f"failed to fetch events for bucket {bucket_id}: {exc}"
        ) from exc
    if not isinstance(data, list):
        raise ActivityWatchUnavailableError(
            f"unexpected events response type for bucket {bucket_id}: {type(data)}"
        )
    # AW's REST API returns events newest-first by default; sort ascending so
    # checkpoint advancement and AFK-interval matching see monotonic order.
    events = [e for e in data if isinstance(e, dict) and "timestamp" in e]
    events.sort(key=lambda e: e["timestamp"])
    return events


def build_afk_intervals(
    afk_events: list[dict[str, Any]],
) -> list[tuple[datetime, datetime, str]]:
    """Convert raw AFK bucket events into sorted ``(start, end, status)`` tuples."""
    intervals: list[tuple[datetime, datetime, str]] = []
    for event in afk_events:
        try:
            start = _parse_aw_timestamp(event["timestamp"])
            duration = float(event.get("duration", 0.0))
            status = str(event.get("data", {}).get("status", "unknown"))
        except (KeyError, TypeError, ValueError):
            continue
        intervals.append((start, start + timedelta(seconds=duration), status))
    intervals.sort(key=lambda t: t[0])
    return intervals


def lookup_afk_status(
    intervals: list[tuple[datetime, datetime, str]],
    ts: datetime,
) -> bool | None:
    """Return True if *ts* falls within an ``"afk"`` interval, False if ``"not-afk"``.

    Returns ``None`` if no AFK bucket data covers *ts* (AFK watcher not
    installed, or gap in AFK data).
    """
    for start, end, status in intervals:
        if start <= ts <= end:
            return status == "afk"
    return None


# ---------------------------------------------------------------------------
# Envelope + evidence persistence
# ---------------------------------------------------------------------------


def build_activity_envelope(
    *,
    machine_id: str,
    endpoint_identity: str,
    bucket_id: str,
    ts: datetime,
    duration_seconds: float,
    app: str,
    app_class: AppClass,
    ingestion_tier: Literal["metadata", "full"],
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a window-focus event.

    Window titles are NEVER included, regardless of tier (privacy —
    see module docstring). In ``full`` tier the raw ``app`` process name
    is included in ``payload.raw``; in ``metadata`` tier (default)
    ``payload.raw`` is None.
    """
    ts_iso = ts.isoformat()
    idempotency_key = f"activitywatch:{machine_id}:{bucket_id}:{ts_iso}"
    normalized_text = f"{app_class} activity ({round(duration_seconds)}s)"
    raw = (
        {"app": app, "app_class": app_class, "duration_seconds": duration_seconds}
        if ingestion_tier == _TIER_FULL
        else None
    )

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": f"{bucket_id}:{ts_iso}",
            "external_thread_id": endpoint_identity,
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": endpoint_identity,
        },
        "payload": {
            "raw": raw,
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": ingestion_tier,
        },
    }


async def persist_activity_event(
    pool: asyncpg.Pool,
    *,
    machine_id: str,
    endpoint_identity: str,
    bucket_id: str,
    ts: datetime,
    duration_seconds: float,
    app: str,
    window_title: str | None,
    app_class: AppClass,
    browser_domain: str | None,
    is_afk: bool | None,
    raw_payload: dict[str, Any],
) -> bool:
    """Write a window-focus event to the durable evidence table.

    Idempotent — uses ON CONFLICT DO NOTHING on ``idempotency_key``.
    Returns True if the row was inserted, False if it already existed.
    """
    idempotency_key = f"activitywatch:{machine_id}:{bucket_id}:{ts.isoformat()}"
    browser_domain = _safe_browser_hostname(browser_domain)

    result = await pool.fetchval(
        f"""
        INSERT INTO {_EVIDENCE_TABLE} (
            idempotency_key,
            machine_id,
            endpoint_identity,
            bucket_id,
            ts,
            duration_seconds,
            app,
            window_title,
            app_class,
            browser_domain,
            is_afk,
            raw_payload
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        idempotency_key,
        machine_id,
        endpoint_identity,
        bucket_id,
        ts,
        duration_seconds,
        app,
        window_title,
        app_class,
        browser_domain,
        is_afk,
        raw_payload,
    )
    return result is not None


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class ActivityWatchConnector:
    """ActivityWatch polling connector.

    Polls the local (or Tailscale-reachable) ActivityWatch REST API for
    window-focus and AFK-status events, classifies app activity, and
    submits ingest.v1 envelopes to the Switchboard.
    """

    def __init__(
        self,
        config: ActivityWatchConnectorConfig,
        db_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config
        self._db_pool = db_pool
        self._cursor_pool = cursor_pool

        self._endpoint_identity = f"activitywatch:{config.machine_id}"

        self._http_client: httpx.AsyncClient | None = None
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url,
            client_name="activitywatch-connector",
        )

        self._start_time = time.time()
        self._running = False
        self._shutdown_event = asyncio.Event()

        self._last_event_at: datetime | None = None
        self._events_today: int = 0
        self._events_today_date: date = datetime.now(UTC).date()

        self._last_checkpoint_ts: datetime | None = None
        self._last_checkpoint_save: float | None = None

        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        self._health_error: str | None = None
        self._last_poll_ok: bool | None = None

        self._heartbeat: ConnectorHeartbeat | None = None
        self._ingestion_policy: IngestionPolicyEvaluator | None = None
        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=self._endpoint_identity,
        )

        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Retention purge (ActivityWatchRetention; initialized in start() when
        # db_pool is available)
        self._retention: ActivityWatchRetention | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Full startup sequence followed by the main poll loop."""
        logger.info(
            "ActivityWatchConnector starting: machine_id=%s, base_url=%s",
            self._config.machine_id,
            self._config.base_url,
        )
        self._running = True
        self._http_client = httpx.AsyncClient(base_url=self._config.base_url, timeout=15.0)

        try:
            try:
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, self._handle_signal)
            except (NotImplementedError, OSError):
                logger.debug(
                    "ActivityWatchConnector: signal handlers not supported on this platform"
                )

            # Phase 1: Initialize ingestion policy evaluator
            scope = f"connector:{_CONNECTOR_TYPE}:{self._endpoint_identity}"
            self._ingestion_policy = IngestionPolicyEvaluator(
                scope=scope,
                db_pool=self._db_pool,
            )
            await self._ingestion_policy.ensure_loaded()

            # Phase 2: Initialize heartbeat
            self._init_heartbeat()

            # Phase 3: Load checkpoint
            await self._load_checkpoint()

            # Phase 4: Wait for Switchboard readiness
            try:
                await wait_for_switchboard_ready(self._config.switchboard_mcp_url)
            except TimeoutError:
                logger.warning(
                    "ActivityWatchConnector: Switchboard readiness probe timed out; proceeding."
                )

            # Phase 5: Start health server
            self._start_health_server()

            # Phase 6: Start heartbeat
            assert self._heartbeat is not None
            self._heartbeat.start()
            try:
                await self._heartbeat._send_heartbeat()
            except Exception as exc:
                logger.debug(
                    "ActivityWatchConnector: initial heartbeat failed (non-fatal): %s", exc
                )

            # Phase 7: Start retention purge task
            if self._db_pool is not None:
                retention_config = ActivityWatchRetentionConfig(
                    retention_days=self._config.retention_days
                )
                self._retention = ActivityWatchRetention(retention_config, self._db_pool)
                self._retention.start()

            # Phase 8: Main poll loop
            await self._poll_loop()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Request graceful shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("ActivityWatchConnector: stop() called, requesting shutdown")
            self._shutdown_event.set()

    def _handle_signal(self) -> None:
        logger.info("ActivityWatchConnector: received shutdown signal")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        logger.info("ActivityWatchConnector: shutting down")
        self._running = False

        if self._retention is not None:
            await self._retention.stop()

        if self._db_pool is not None:
            try:
                await self._filtered_event_buffer.flush(self._db_pool)
            except Exception:
                logger.warning("ActivityWatchConnector: filtered event flush failed on shutdown")

        if self._heartbeat is not None:
            try:
                await self._heartbeat._send_heartbeat()
            except Exception as exc:
                logger.debug("ActivityWatchConnector: final heartbeat failed (non-fatal): %s", exc)
            await self._heartbeat.stop()

        if self._health_server is not None:
            self._health_server.should_exit = True

        if self._http_client is not None:
            await self._http_client.aclose()

        logger.info("ActivityWatchConnector: shutdown complete")

    def _init_heartbeat(self) -> None:
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
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running and not self._shutdown_event.is_set():
            if self._db_pool is not None:
                try:
                    await drain_replay_pending(
                        pool=self._db_pool,
                        connector_type=_CONNECTOR_TYPE,
                        endpoint_identity=self._endpoint_identity,
                        submit_fn=self._submit_envelope,
                        drain_logger=logger,
                    )
                except Exception:
                    logger.warning("ActivityWatchConnector: replay queue drain failed")

            poll_start = time.monotonic()
            try:
                await self._execute_poll_cycle()
                self._last_poll_ok = True
                self._health_error = None
                activitywatch_polls_total.labels(
                    endpoint_identity=self._endpoint_identity, status="success"
                ).inc()
            except ActivityWatchUnavailableError as exc:
                self._last_poll_ok = False
                self._health_error = str(exc)
                activitywatch_polls_total.labels(
                    endpoint_identity=self._endpoint_identity, status="unavailable"
                ).inc()
                logger.warning("ActivityWatchConnector: poll cycle failed: %s", exc)
            except Exception as exc:
                self._last_poll_ok = False
                self._health_error = str(exc)
                self._metrics.record_error(error_type="poll_cycle_error", operation="poll")
                activitywatch_polls_total.labels(
                    endpoint_identity=self._endpoint_identity, status="error"
                ).inc()
                logger.exception("ActivityWatchConnector: poll cycle raised unexpectedly")

            if self._db_pool is not None:
                try:
                    await self._filtered_event_buffer.flush(self._db_pool)
                except Exception:
                    logger.warning("ActivityWatchConnector: filtered event flush failed")

            elapsed = time.monotonic() - poll_start
            sleep_for = max(0.0, self._config.poll_interval_s - elapsed)
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=sleep_for)
            except TimeoutError:
                pass

    async def _execute_poll_cycle(self) -> None:
        """One poll cycle: discover buckets, fetch events, ingest, checkpoint."""
        assert self._http_client is not None
        buckets = await fetch_buckets(self._http_client)

        window_bucket_id = find_bucket_id(buckets, _BUCKET_TYPE_WINDOW)
        if window_bucket_id is None:
            raise ActivityWatchUnavailableError(
                f"no aw-watcher-window bucket found on {self._config.base_url} "
                f"(machine_id={self._config.machine_id}); is ActivityWatch running "
                "with the window watcher enabled?"
            )
        afk_bucket_id = find_bucket_id(buckets, _BUCKET_TYPE_AFK)
        web_bucket_id = find_bucket_id(buckets, _BUCKET_TYPE_WEB)

        since = self._last_checkpoint_ts
        if since is None:
            since = datetime.now(UTC) - timedelta(days=self._config.max_backfill_days)

        window_events = await fetch_events(self._http_client, window_bucket_id, since=since)
        self._metrics.record_source_api_call(api_method="get_events_window", status="success")

        afk_intervals: list[tuple[datetime, datetime, str]] = []
        if afk_bucket_id is not None:
            try:
                afk_events = await fetch_events(self._http_client, afk_bucket_id, since=since)
                afk_intervals = build_afk_intervals(afk_events)
                self._metrics.record_source_api_call(api_method="get_events_afk", status="success")
            except ActivityWatchUnavailableError:
                # AFK data is best-effort enrichment — proceed without it.
                logger.debug("ActivityWatchConnector: AFK bucket fetch failed; is_afk will be null")

        web_events: list[dict[str, Any]] = []
        if web_bucket_id is not None:
            try:
                web_events = await fetch_events(self._http_client, web_bucket_id, since=since)
                self._metrics.record_source_api_call(api_method="get_events_web", status="success")
            except ActivityWatchUnavailableError:
                # Browser-domain enrichment is optional: keep coarse browser
                # activity when the browser extension is absent or unreachable.
                logger.debug(
                    "ActivityWatchConnector: web bucket fetch failed; browser_domain will be null"
                )

        if not window_events:
            return

        now_date = datetime.now(UTC).date()
        if now_date != self._events_today_date:
            self._events_today = 0
            self._events_today_date = now_date

        latest_ts = since
        for event in window_events:
            try:
                ts = _parse_aw_timestamp(event["timestamp"])
                duration_seconds = float(event.get("duration", 0.0))
                data = event.get("data") or {}
                app = str(data.get("app") or "unknown")
                window_title = data.get("title")
                window_title = str(window_title) if window_title else None
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("ActivityWatchConnector: skipping malformed event: %s", exc)
                continue

            if latest_ts is None or ts > latest_ts:
                latest_ts = ts

            if duration_seconds < self._config.min_event_duration_s:
                continue

            app_class = classify_app(app)
            is_afk = lookup_afk_status(afk_intervals, ts)
            browser_match = match_browser_domain(ts, web_events) if app_class == "browser" else None

            activitywatch_events_received_total.labels(
                endpoint_identity=self._endpoint_identity, app_class=app_class
            ).inc()

            try:
                await self._process_window_event(
                    bucket_id=window_bucket_id,
                    ts=ts,
                    duration_seconds=duration_seconds,
                    app=app,
                    window_title=window_title,
                    app_class=app_class,
                    is_afk=is_afk,
                    raw_event=event,
                    browser_domain=(browser_match.domain if browser_match is not None else None),
                    raw_web_event=(browser_match.raw_event if browser_match is not None else None),
                )
                self._last_event_at = datetime.now(UTC)
                self._events_today += 1
            except Exception:
                # Log and continue — a single bad event should not block the
                # rest of the batch or wedge the checkpoint on a transient
                # per-event failure. The event's idempotency_key means a
                # future retry (if the checkpoint is re-run) stays safe.
                logger.warning(
                    "ActivityWatchConnector: failed to process event at %s (non-fatal)",
                    ts.isoformat(),
                    exc_info=True,
                )

        if latest_ts is not None and (
            self._last_checkpoint_ts is None or latest_ts > self._last_checkpoint_ts
        ):
            await self._save_checkpoint(latest_ts)

    async def _process_window_event(
        self,
        *,
        bucket_id: str,
        ts: datetime,
        duration_seconds: float,
        app: str,
        window_title: str | None,
        app_class: AppClass,
        is_afk: bool | None,
        raw_event: dict[str, Any],
        browser_domain: str | None = None,
        raw_web_event: dict[str, Any] | None = None,
    ) -> None:
        """Apply the policy gate, submit to Switchboard, and persist evidence."""
        observed_at = datetime.now(UTC).isoformat()

        if self._ingestion_policy is not None:
            envelope_for_policy = IngestionEnvelope(
                source_channel=_CONNECTOR_CHANNEL,
                raw_key=self._endpoint_identity,
            )
            decision = self._ingestion_policy.evaluate(envelope_for_policy)
            if not decision.allowed:
                external_event_id = f"{bucket_id}:{ts.isoformat()}"
                self._filtered_event_buffer.record(
                    external_message_id=external_event_id,
                    source_channel=_CONNECTOR_CHANNEL,
                    sender_identity=self._endpoint_identity,
                    subject_or_preview=f"ActivityWatch {app_class} activity",
                    filter_reason=FilteredEventBuffer.reason_policy_rule(
                        scope="connector_rule",
                        action=decision.action,
                        rule_type=decision.matched_rule_type or "unknown",
                    ),
                    full_payload=FilteredEventBuffer.full_payload(
                        channel=_CONNECTOR_CHANNEL,
                        provider=_CONNECTOR_PROVIDER,
                        endpoint_identity=self._endpoint_identity,
                        external_event_id=external_event_id,
                        external_thread_id=self._endpoint_identity,
                        observed_at=observed_at,
                        sender_identity=self._endpoint_identity,
                        raw={},
                    ),
                )
                return

        envelope = build_activity_envelope(
            machine_id=self._config.machine_id,
            endpoint_identity=self._endpoint_identity,
            bucket_id=bucket_id,
            ts=ts,
            duration_seconds=duration_seconds,
            app=app,
            app_class=app_class,
            ingestion_tier=self._config.ingestion_tier,
        )

        start_t = time.perf_counter()
        status = "success"
        try:
            await self._mcp_client.call_tool("ingest", envelope)
        except Exception:
            status = "error"
            raise
        finally:
            latency = time.perf_counter() - start_t
            self._metrics.record_ingest_submission(status=status, latency=latency)

        if self._db_pool is not None:
            try:
                # Raw URLs and tab titles are sensitive evidence. They stay in
                # this database-only JSON payload and are never passed to the
                # Switchboard envelope above.
                evidence_payload = dict(raw_event)
                if raw_web_event is not None:
                    evidence_payload["web_event"] = raw_web_event
                await persist_activity_event(
                    self._db_pool,
                    machine_id=self._config.machine_id,
                    endpoint_identity=self._endpoint_identity,
                    bucket_id=bucket_id,
                    ts=ts,
                    duration_seconds=duration_seconds,
                    app=app,
                    window_title=window_title,
                    app_class=app_class,
                    browser_domain=browser_domain,
                    is_afk=is_afk,
                    raw_payload=evidence_payload,
                )
            except Exception:
                logger.warning(
                    "ActivityWatchConnector: failed to persist activity evidence (non-fatal)",
                    exc_info=True,
                )

    async def _submit_envelope(self, envelope: dict[str, Any]) -> None:
        """Submit an ingest.v1 envelope to the Switchboard (for replay drain)."""
        await self._mcp_client.call_tool("ingest", envelope)

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    async def _load_checkpoint(self) -> None:
        pool = self._cursor_pool or self._db_pool
        if pool is None:
            logger.debug("ActivityWatchConnector: no DB pool available, skipping checkpoint load")
            return
        try:
            cursor = await load_cursor(pool, _CONNECTOR_TYPE, self._endpoint_identity)
            if cursor is not None:
                try:
                    self._last_checkpoint_ts = _parse_aw_timestamp(cursor)
                    logger.info(
                        "ActivityWatchConnector: loaded checkpoint ts=%s for %s",
                        self._last_checkpoint_ts.isoformat(),
                        self._endpoint_identity,
                    )
                except ValueError:
                    logger.warning(
                        "ActivityWatchConnector: invalid checkpoint cursor=%r, ignoring", cursor
                    )
        except Exception:
            logger.warning("ActivityWatchConnector: failed to load checkpoint", exc_info=True)

    async def _save_checkpoint(self, ts: datetime) -> None:
        pool = self._cursor_pool or self._db_pool
        if pool is None:
            return
        try:
            # One cursor per watched machine, keyed by the same identity the
            # heartbeat registers, so this row IS the runtime instance's own
            # (bu-ogs8x).
            await save_cursor(
                pool,
                _CONNECTOR_TYPE,
                self._endpoint_identity,
                ts.isoformat(),
                parent_endpoint_identity=NO_PARENT,
            )
            self._last_checkpoint_ts = ts
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save(status="success")
        except Exception:
            self._metrics.record_checkpoint_save(status="error")
            logger.warning(
                "ActivityWatchConnector: failed to save checkpoint ts=%s",
                ts.isoformat(),
                exc_info=True,
            )

    def _get_checkpoint_info(self) -> tuple[str | None, datetime | None]:
        cursor = self._last_checkpoint_ts.isoformat() if self._last_checkpoint_ts else None
        updated_at = (
            datetime.fromtimestamp(self._last_checkpoint_save, tz=UTC)
            if self._last_checkpoint_save is not None
            else None
        )
        return cursor, updated_at

    # ------------------------------------------------------------------
    # Health state
    # ------------------------------------------------------------------

    def _get_health_state(self) -> tuple[str, str | None]:
        if self._health_error:
            return "degraded", self._health_error
        if self._retention is not None:
            retention_message = self._retention.health_degradation_message
            if retention_message is not None:
                return "degraded", retention_message
        return "healthy", None

    # ------------------------------------------------------------------
    # Health/metrics HTTP server
    # ------------------------------------------------------------------

    def _build_health_app(self) -> FastAPI:
        app = FastAPI(title="activitywatch-connector-health")

        @app.get("/health")
        async def health() -> dict[str, Any]:
            state, error = self._get_health_state()
            uptime_s = int(time.time() - self._start_time)
            return {
                "state": state,
                "connector_type": _CONNECTOR_TYPE,
                "endpoint_identity": self._endpoint_identity,
                "uptime_s": uptime_s,
                "last_event_at": (self._last_event_at.isoformat() if self._last_event_at else None),
                "events_today": self._events_today,
                "error": error,
            }

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest()

        return app

    def _start_health_server(self) -> None:
        port = self._config.health_port
        try:
            sock = make_health_socket("127.0.0.1", port)
        except Exception as exc:
            logger.warning(
                "ActivityWatchConnector: could not bind health server on port %d: %s", port, exc
            )
            return

        app = self._build_health_app()
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

        thread = Thread(target=_run, daemon=True, name="activitywatch-health")
        thread.start()
        self._health_thread = thread
        logger.info("ActivityWatchConnector: health server started on 127.0.0.1:%d", port)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_activitywatch_connector() -> None:
    """Main async entry point for the ActivityWatch connector."""
    configure_logging()
    logger.info("ActivityWatch connector starting")

    config = ActivityWatchConnectorConfig.from_env()

    import asyncpg

    db_params = db_params_from_env()
    shared_db_name = shared_db_name_from_env()
    shared_schema = os.environ.get("BUTLER_SHARED_DB_SCHEMA", "public")

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
        }
        if shared_schema:
            try:
                pool_kwargs["server_settings"] = {"search_path": schema_search_path(shared_schema)}
            except ValueError:
                pass
        pool_kwargs["setup"] = connector_setup_role
        # Register the JSONB codec so dict payloads encode for jsonb columns
        # (e.g. activitywatch_events.raw_payload). Mirrors owntracks/steam.
        pool_kwargs["init"] = register_jsonb_codec
        try:
            db_pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:
            if should_retry_with_ssl_disable(exc, pool_kwargs.get("ssl")):
                logger.debug("ActivityWatchConnector: retrying DB pool without SSL")
                pool_kwargs["ssl"] = False
                db_pool = await asyncpg.create_pool(**pool_kwargs)
            else:
                raise
        logger.info("ActivityWatchConnector: DB pool connected to %s", shared_db_name)
    except Exception:
        logger.warning(
            "ActivityWatchConnector: failed to create DB pool; running without DB", exc_info=True
        )

    connector = ActivityWatchConnector(
        config=config,
        db_pool=db_pool,
        cursor_pool=db_pool,
    )

    try:
        await connector.start()
    finally:
        if db_pool is not None:
            await db_pool.close()


if __name__ == "__main__":
    asyncio.run(run_activitywatch_connector())
