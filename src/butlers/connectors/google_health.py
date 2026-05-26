"""Google Health connector runtime — passive per-owner wellness ingestion.

This connector polls the Google Health API at ``https://health.googleapis.com/v4/``
for the primary Google account's wellness data (sleep, heart-rate, HRV, SpO2,
breathing-rate, steps, active-minutes, VO2 max), detects new or changed
records via state diffing, normalises events into ``ingest.v1`` envelopes,
and submits them to the Switchboard.

Archetype: passive signal connector (mirror of ``connector-spotify`` and
``connector-steam``). No per-chat buffering, no discretion layer, no
interactive replies. Single-owner: polls only the primary Google account
regardless of how many non-primary accounts exist.

Key behaviours
--------------

- Owner resolution via :func:`butlers.google_account_registry.get_google_account`
  (primary account only). The Google user identifier recorded in
  ``source.endpoint_identity`` and ``sender.identity`` is the account's
  *email* — it is the stable identifier Google Health persists in
  ``public.google_accounts`` today, and RFC 0004 identity resolution hinges
  on a matching ``public.contact_info`` row being upserted during OAuth
  callback for ``scope_set=health``.
- Scope gate: connector stays in ``degraded`` until all three
  RESTRICTED Google Health scope URLs are present on the primary account's
  ``granted_scopes``. Scope checks run every 300 s and on each poll-loop
  entry.
- OAuth tokens: refresh-token access is delegated to the shared Google
  credential pipeline via :func:`butlers.google_credentials.load_google_credentials`.
  Access tokens live only in memory. No ``CredentialStore.resolve()`` for
  refresh tokens. No environment-variable fallbacks. Per
  ``about/heart-and-soul/security.md`` Tier-2 contract.
- Per-resource polling loops: sleep sessions / daily activity / resting HR
  at 30-min cadence, HRV / SpO2 / breathing rate at 60-min, VO2 max daily.
  First-run backfill walks the trailing ``GOOGLE_HEALTH_BACKFILL_DAYS``
  days (default 30) of daily summaries.
- Cursor persistence: each resource's cursor is stored via
  :mod:`butlers.connectors.cursor_store` under
  ``(connector_type="google_health", endpoint_identity="google_health:user:<id>:<resource>")``.
  The envelope's ``source.endpoint_identity`` remains the 3-segment
  canonical ``google_health:user:<id>``; the ``:<resource>`` suffix is used
  ONLY in the cursor key.
- Base-spec obligations: source filter gate via
  :class:`butlers.ingestion_policy.IngestionPolicyEvaluator` scoped
  ``connector:google_health:<endpoint_identity>``, filtered-events batch
  flush to ``connectors.filtered_events``, replay-queue drain at the top of
  each poll cycle.
- Rate-limit handling: honours ``Retry-After`` on HTTP 429; falls back to
  exponential backoff with jitter. Cursors never advance on a failed
  request.
- Heartbeat states: ``healthy`` | ``degraded`` | ``error`` (no ``broken``
  state, per base-spec v2).

Environment variables
---------------------

``SWITCHBOARD_MCP_URL`` (required)
    URL of the Switchboard's MCP endpoint.
``GOOGLE_HEALTH_BACKFILL_DAYS`` (optional, default 30)
    First-run backfill window in days.
``GOOGLE_HEALTH_POLL_SLEEP_S`` (optional, default 1800)
    Per-resource cadence for sleep sessions. Overrides the default.
``GOOGLE_HEALTH_POLL_ACTIVITY_S`` (optional, default 1800)
    Cadence for daily activity / steps / resting HR.
``GOOGLE_HEALTH_POLL_HEALTH_METRICS_S`` (optional, default 3600)
    Cadence for HRV / SpO2 / breathing rate.
``GOOGLE_HEALTH_POLL_VO2_MAX_S`` (optional, default 86400)
    Cadence for VO2 max.
``GOOGLE_HEALTH_SCOPE_RECHECK_S`` (optional, default 300)
    Scope re-check cadence when running degraded.
``CONNECTOR_HEALTH_PORT`` (optional, default 40086)
    Health/metrics HTTP port.
``CONNECTOR_HEARTBEAT_INTERVAL_S`` (optional, default 120)
    Heartbeat submission cadence.
``CONNECTOR_MAX_INFLIGHT`` (optional, default 8)
    Max concurrent envelope submissions.
``CONNECTOR_BUTLER_DB_NAME`` (optional)
    Local butler DB — hosts ``switchboard.connector_registry`` for cursors.
``BUTLER_SHARED_DB_NAME`` (optional, default ``butlers``)
    Shared DB — hosts ``public.google_accounts``, ``public.entity_info``,
    ``public.contact_info``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from threading import Thread
from typing import TYPE_CHECKING, Any

import httpx
import uvicorn
from fastapi import FastAPI
from prometheus_client import Counter, Gauge, generate_latest

from butlers.connectors.cursor_store import load_cursor, save_cursor
from butlers.connectors.db_role import connector_setup_role
from butlers.connectors.filtered_event_buffer import FilteredEventBuffer, drain_replay_pending
from butlers.connectors.google_health_client import (
    GoogleHealthClient,
    GoogleHealthCredentialError,
    GoogleHealthError,
    GoogleHealthRateLimitError,
    GoogleHealthSourcePreconditionError,
    exponential_backoff_delay,
)
from butlers.connectors.health_socket import make_health_socket
from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.mcp_client import CachedMCPClient, wait_for_switchboard_ready
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging
from butlers.credential_store import CredentialStore, shared_db_name_from_env
from butlers.db import db_params_from_env, schema_search_path, should_retry_with_ssl_disable
from butlers.google_account_registry import (
    GoogleAccount,
    HealthScopedAccount,
    MissingGoogleCredentialsError,
    get_google_account,
    list_health_scoped_accounts,
)
from butlers.google_credentials import (
    InvalidGoogleCredentialsError,
    load_google_credentials,
)
from butlers.ingestion_policy import IngestionEnvelope, IngestionPolicyEvaluator

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CONNECTOR_TYPE = "google_health"
_CONNECTOR_CHANNEL = "wellness"
_CONNECTOR_PROVIDER = "google_health"

# Full Google Health RESTRICTED scope URLs (per openspec spec and the OAuth
# scope-set registry in src/butlers/api/routers/oauth.py). Scope check is an
# AND across all three — partial grants leave the connector degraded.
GOOGLE_HEALTH_SCOPES: frozenset[str] = frozenset(
    {
        "https://www.googleapis.com/auth/googlehealth.sleep",
        "https://www.googleapis.com/auth/googlehealth.activity_and_fitness",
        "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements",
    }
)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TOKEN_REFRESH_BUFFER_S = 60  # refresh access token 60s before expiry

# Default per-resource polling cadences (seconds).
_DEFAULT_POLL_INTERVALS: dict[str, int] = {
    "sleep": 1800,
    "activity": 1800,
    "resting_hr": 1800,
    "hrv": 3600,
    "spo2": 3600,
    "breathing_rate": 3600,
    "vo2_max": 86400,
}

# Default first-run backfill window in days.
_DEFAULT_BACKFILL_DAYS = 30

# Default scope re-check cadence when in degraded.
_DEFAULT_SCOPE_RECHECK_S = 300

_DEFAULT_HEALTH_PORT = 40086
_DEFAULT_MAX_INFLIGHT = 8


# ---------------------------------------------------------------------------
# Google Health-specific Prometheus metrics
# ---------------------------------------------------------------------------

google_health_polls_total = Counter(
    "connector_google_health_polls_total",
    "Total Google Health per-resource poll cycles",
    labelnames=["endpoint_identity", "resource", "outcome"],
)

google_health_envelopes_total = Counter(
    "connector_google_health_envelopes_total",
    "Ingest.v1 envelopes emitted per resource",
    labelnames=["endpoint_identity", "resource"],
)

google_health_rate_limit_remaining = Gauge(
    "connector_google_health_rate_limit_remaining",
    "Google Health API X-RateLimit-Remaining observed on the last call per resource",
    labelnames=["endpoint_identity", "resource"],
)

google_health_rate_limit_events_total = Counter(
    "connector_google_health_rate_limit_events_total",
    "Total HTTP 429 responses observed per resource",
    labelnames=["endpoint_identity", "resource"],
)

google_health_scope_missing_total = Counter(
    "connector_google_health_scope_missing_total",
    "Count of startup / re-check cycles that found missing Google Health scopes",
    labelnames=["endpoint_identity"],
)


# ---------------------------------------------------------------------------
# Resource bundle definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceBundle:
    """Metadata for a single Google Health data-type bundle.

    Attributes
    ----------
    resource:
        Short key used in cursor identities, idempotency keys, metric
        labels, and external_event_ids. Must match the predicate-taxonomy
        keys in ``openspec/changes/google-health-connector/design.md`` §D5.
    endpoint_path:
        Relative path on ``health.googleapis.com/v4`` that is polled.
        Paths follow the public v4 discovery document's
        ``users.dataTypes.dataPoints`` methods.
    data_type:
        Google Health data type id in kebab-case.
    filter_field:
        AIP-160 filter field used for GET/reconcile resources.
    secondary_endpoint_path:
        Optional second daily-rollup endpoint; currently used to merge
        ``active-minutes`` into the activity envelope alongside ``steps``.
    poll_interval_key:
        Environment-variable suffix controlling cadence overrides.
    default_interval_s:
        Fallback cadence when no override is present.
    category:
        ``"sleep"`` or ``"daily"`` — affects external_event_id shape.
    normalized_summary:
        Human-readable summary template used in ``payload.normalized_text``.
    """

    resource: str
    endpoint_path: str
    data_type: str
    filter_field: str
    poll_interval_key: str
    default_interval_s: int
    category: str
    normalized_summary: str
    request_method: str = "GET"
    secondary_endpoint_path: str | None = None


# Per-resource bundles. Endpoint paths follow the documented v4 structure
# (users/me/dataTypes/...); the final segment is confirmed during reconciliation
# in .2.4 and recorded in research-notes.md. Polling respects
# `_DEFAULT_POLL_INTERVALS` overrides per resource.
RESOURCE_BUNDLES: tuple[ResourceBundle, ...] = (
    ResourceBundle(
        resource="sleep",
        endpoint_path="/users/me/dataTypes/sleep/dataPoints:reconcile",
        data_type="sleep",
        filter_field="sleep.interval.end_time",
        poll_interval_key="GOOGLE_HEALTH_POLL_SLEEP_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["sleep"],
        category="sleep",
        normalized_summary="Slept {duration_label} ({efficiency}% efficiency)",
    ),
    ResourceBundle(
        resource="activity",
        endpoint_path="/users/me/dataTypes/steps/dataPoints:dailyRollUp",
        data_type="steps",
        filter_field="steps.interval.start_time",
        poll_interval_key="GOOGLE_HEALTH_POLL_ACTIVITY_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["activity"],
        category="daily",
        normalized_summary="Steps: {value}",
        request_method="POST",
        secondary_endpoint_path="/users/me/dataTypes/active-minutes/dataPoints:dailyRollUp",
    ),
    ResourceBundle(
        resource="resting_hr",
        endpoint_path="/users/me/dataTypes/daily-resting-heart-rate/dataPoints:reconcile",
        data_type="daily-resting-heart-rate",
        filter_field="daily_resting_heart_rate.date",
        poll_interval_key="GOOGLE_HEALTH_POLL_ACTIVITY_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["resting_hr"],
        category="daily",
        normalized_summary="Resting HR: {value} bpm",
    ),
    ResourceBundle(
        resource="hrv",
        endpoint_path="/users/me/dataTypes/daily-heart-rate-variability/dataPoints:reconcile",
        data_type="daily-heart-rate-variability",
        filter_field="daily_heart_rate_variability.date",
        poll_interval_key="GOOGLE_HEALTH_POLL_HEALTH_METRICS_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["hrv"],
        category="daily",
        normalized_summary="HRV: {value} ms",
    ),
    ResourceBundle(
        resource="spo2",
        endpoint_path="/users/me/dataTypes/daily-oxygen-saturation/dataPoints:reconcile",
        data_type="daily-oxygen-saturation",
        filter_field="daily_oxygen_saturation.date",
        poll_interval_key="GOOGLE_HEALTH_POLL_HEALTH_METRICS_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["spo2"],
        category="daily",
        normalized_summary="SpO2: avg {value}%",
    ),
    ResourceBundle(
        resource="breathing_rate",
        endpoint_path="/users/me/dataTypes/daily-respiratory-rate/dataPoints:reconcile",
        data_type="daily-respiratory-rate",
        filter_field="daily_respiratory_rate.date",
        poll_interval_key="GOOGLE_HEALTH_POLL_HEALTH_METRICS_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["breathing_rate"],
        category="daily",
        normalized_summary="Breathing: {value} bpm",
    ),
    ResourceBundle(
        resource="vo2_max",
        endpoint_path="/users/me/dataTypes/daily-vo2-max/dataPoints:reconcile",
        data_type="daily-vo2-max",
        filter_field="daily_vo2_max.date",
        poll_interval_key="GOOGLE_HEALTH_POLL_VO2_MAX_S",
        default_interval_s=_DEFAULT_POLL_INTERVALS["vo2_max"],
        category="daily",
        normalized_summary="VO2 Max: {value}",
    ),
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GoogleHealthConnectorConfig:
    """Configuration for the Google Health connector runtime."""

    switchboard_mcp_url: str
    channel: str = _CONNECTOR_CHANNEL
    provider: str = _CONNECTOR_PROVIDER

    backfill_days: int = _DEFAULT_BACKFILL_DAYS
    scope_recheck_s: int = _DEFAULT_SCOPE_RECHECK_S

    max_inflight: int = _DEFAULT_MAX_INFLIGHT
    health_port: int = _DEFAULT_HEALTH_PORT

    # Resolved per-resource intervals (seconds).
    poll_intervals: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> GoogleHealthConnectorConfig:
        """Load configuration from process environment.

        ``SWITCHBOARD_MCP_URL`` is required. Everything else falls back to
        the documented defaults — see the module-level docstring for the
        full list of variables.
        """
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

        poll_intervals: dict[str, int] = {}
        for bundle in RESOURCE_BUNDLES:
            poll_intervals[bundle.resource] = _int(
                bundle.poll_interval_key, bundle.default_interval_s
            )

        return cls(
            switchboard_mcp_url=switchboard_mcp_url,
            backfill_days=_int("GOOGLE_HEALTH_BACKFILL_DAYS", _DEFAULT_BACKFILL_DAYS),
            scope_recheck_s=_int("GOOGLE_HEALTH_SCOPE_RECHECK_S", _DEFAULT_SCOPE_RECHECK_S),
            max_inflight=_int("CONNECTOR_MAX_INFLIGHT", _DEFAULT_MAX_INFLIGHT),
            health_port=_int("CONNECTOR_HEALTH_PORT", _DEFAULT_HEALTH_PORT),
            poll_intervals=poll_intervals,
        )


# ---------------------------------------------------------------------------
# Envelope builders
# ---------------------------------------------------------------------------


def _endpoint_identity_for_user(google_user_id: str) -> str:
    """Canonical 3-segment endpoint identity for a Google Health envelope."""
    return f"google_health:user:{google_user_id}"


def _cursor_endpoint_identity(google_user_id: str, account_uuid: uuid.UUID, resource: str) -> str:
    """Per-resource cursor key — embeds ``account_uuid`` between email and resource.

    The ``cursor_store`` primitive uses a 2-tuple
    ``(connector_type, endpoint_identity)`` so per-resource dimension is
    encoded into the endpoint_identity suffix.  The envelope's
    ``source.endpoint_identity`` (returned by
    :func:`_endpoint_identity_for_user`) remains the canonical 3-segment form —
    only cursors carry the ``account_uuid`` and resource suffixes.

    Key shape: ``google_health:user:<email>:<account_uuid>:<resource>``

    The ``account_uuid`` segment is required even when the email is unique
    because an owner can re-add the same Google account (rotation, force_consent)
    and receive a new ``google_accounts.id``.  The cursor must follow the DB row,
    not the email address.
    """
    return f"google_health:user:{google_user_id}:{account_uuid}:{resource}"


def _format_daily_summary_value(resource: str, record: dict[str, Any]) -> str:
    """Extract a human-readable scalar from a daily-summary record.

    Falls back to ``"?"`` when the raw record lacks a recognised value
    field. Used only for ``payload.normalized_text`` — the structured
    ``payload.raw`` carries the full response.
    """
    for key in ("value", "count", "avg", "average", "midpoint"):
        val = record.get(key)
        if val is not None:
            return str(val)
    return "?"


def _format_sleep_duration_label(duration_ms: int) -> str:
    """Render a sleep-session duration as e.g. ``"7h 23m"``."""
    minutes = max(0, duration_ms // 60000)
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0:
        return f"{hours}h {mins}m"
    return f"{mins}m"


def build_sleep_session_envelope(
    *,
    endpoint_identity: str,
    google_user_id: str,
    session_id: str,
    session_record: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a Google Health sleep session."""
    duration_ms = int(
        session_record.get("durationMillis") or session_record.get("duration_ms") or 0
    )
    efficiency = session_record.get("efficiency") or session_record.get("efficiencyPercent") or "?"
    duration_label = _format_sleep_duration_label(duration_ms)
    normalized_text = f"Slept {duration_label} ({efficiency}% efficiency)"

    external_event_id = f"google_health:sleep_session:{session_id}"
    idempotency_key = f"google_health:sleep:{session_id}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": google_user_id,
        },
        "payload": {
            "raw": dict(session_record),
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


def build_daily_summary_envelope(
    *,
    endpoint_identity: str,
    google_user_id: str,
    resource: str,
    record_date: str,
    record: dict[str, Any],
    normalized_summary_template: str,
    observed_at: str,
) -> dict[str, Any]:
    """Build an ingest.v1 envelope for a daily-summary Google Health record.

    ``record_date`` is the YYYY-MM-DD date the summary applies to (the
    ``valid_at`` axis in downstream Health butler facts).
    """
    value_str = _format_daily_summary_value(resource, record)
    normalized_text = normalized_summary_template.format(value=value_str)

    external_event_id = f"google_health:{resource}:{record_date}"
    idempotency_key = f"google_health:{resource}:{record_date}"

    return {
        "schema_version": "ingest.v1",
        "source": {
            "channel": _CONNECTOR_CHANNEL,
            "provider": _CONNECTOR_PROVIDER,
            "endpoint_identity": endpoint_identity,
        },
        "event": {
            "external_event_id": external_event_id,
            "external_thread_id": None,
            "observed_at": observed_at,
        },
        "sender": {
            "identity": google_user_id,
        },
        "payload": {
            "raw": dict(record),
            "normalized_text": normalized_text,
        },
        "control": {
            "idempotency_key": idempotency_key,
            "policy_tier": "default",
            "ingestion_tier": "full",
        },
    }


# ---------------------------------------------------------------------------
# Contact-info registration (called from OAuth callback for scope_set=health)
# ---------------------------------------------------------------------------


async def upsert_google_health_contact_info(
    pool: asyncpg.Pool,
    *,
    google_user_id: str,
    owner_entity_id: uuid.UUID,
) -> None:
    """Upsert the owner's ``public.contact_info`` row for Google Health.

    RFC 0004 identity resolution looks up ``sender.identity`` via
    ``public.contact_info(type, value)`` and expects a single matching row
    linked to a contact whose entity has the ``owner`` role. This helper
    is idempotent — re-running pairing produces no duplicate row.

    The connector does NOT call this at ingestion time; it is called by
    the OAuth callback when ``scope_set=health`` completes successfully.
    The function is exposed from the connector module because the contact
    shape is owned by the connector's contract.
    """
    insert_status: str | None = None
    owner_contact_id_for_shim: uuid.UUID | None = None

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Ensure an owner contact exists linked to the owner entity.
            owner_contact = await conn.fetchrow(
                """
                SELECT id
                FROM public.contacts
                WHERE entity_id = $1
                ORDER BY created_at ASC
                LIMIT 1
                """,
                owner_entity_id,
            )

            if owner_contact is None:
                # Create a minimal contact row for the owner if none exists.
                owner_contact = await conn.fetchrow(
                    """
                    INSERT INTO public.contacts (name, entity_id, metadata)
                    VALUES ($1, $2, '{}'::jsonb)
                    RETURNING id
                    """,
                    "Owner",
                    owner_entity_id,
                )
            contact_id = owner_contact["id"]

            insert_status = await conn.execute(
                """
                INSERT INTO public.contact_info (contact_id, type, value, secured)
                VALUES ($1, 'google_health', $2, false)
                ON CONFLICT (type, value) DO NOTHING
                """,
                contact_id,
                google_user_id,
            )
            owner_contact_id_for_shim = contact_id

    # Dual-write shim (Group F): best-effort post-commit triple emission (Amendment 14).
    # Only emit when the INSERT actually created a row (asyncpg status == "INSERT 0 1").
    # When ON CONFLICT DO NOTHING silently skips because the (type, value) pair is already
    # claimed by a different contact, we must not assert a triple for the owner entity —
    # that would contradict the authoritative SQL state.
    # Note: google_health is currently unmapped in _CI_TYPE_TO_PREDICATE, so emit_contact_info_fact
    # will no-op internally. The gate is kept as a correctness safeguard so that if the
    # predicate mapping is added in the future, spurious triples on conflict paths are prevented.
    if insert_status == "INSERT 0 1" and owner_contact_id_for_shim is not None:
        try:
            from butlers.tools.relationship.dual_write import emit_contact_info_fact

            await emit_contact_info_fact(
                pool,
                contact_id=owner_contact_id_for_shim,
                ci_type="google_health",
                value=google_user_id,
                is_primary=False,
                src="dual-write",
            )
        except Exception:  # noqa: BLE001 — best-effort: never block the legacy commit
            logger.warning(
                "upsert_google_health_contact_info: emit_contact_info_fact failed for "
                "contact %s (ci_type='google_health', value=%r) — dual-write failure swallowed",
                owner_contact_id_for_shim,
                google_user_id,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Per-resource state
# ---------------------------------------------------------------------------


@dataclass
class ResourceState:
    """Runtime state per polled resource bundle."""

    bundle: ResourceBundle
    next_poll_monotonic: float = 0.0  # time.monotonic() of next scheduled poll
    last_poll_at: datetime | None = None
    last_cursor: str | None = None
    backfill_done: bool = False


# ---------------------------------------------------------------------------
# Per-account owner context
# ---------------------------------------------------------------------------


@dataclass
class OwnerContext:
    """Per-account state for the multi-account poll engine.

    Holds the identity and credential metadata resolved from the account
    registry for one health-scoped Google account.  Per design.md ADR-4,
    the refresh token is NOT stored here — callers resolve it on-demand via
    ``google_credentials._resolve_entity_refresh_token``.

    Attributes
    ----------
    account_id:
        UUID primary key of the ``public.google_accounts`` row.
    email:
        Authenticated Google email address — used as the stable
        ``google_user_id`` in envelopes and endpoint identities.
    entity_id:
        UUID of the companion entity (anchor for refresh-token lookup).
    refresh_token_present:
        Snapshot of whether a refresh token existed at the last registry
        query.  Informational; not a live credential check.
    endpoint_identity:
        3-segment canonical identity string, e.g.
        ``"google_health:user:foo@gmail.com"``.
    cached_access_token:
        Per-account in-memory access token (never persisted).
    token_expires_at:
        Expiry timestamp for ``cached_access_token``.
    """

    account_id: uuid.UUID
    email: str
    entity_id: uuid.UUID
    refresh_token_present: bool
    endpoint_identity: str
    cached_access_token: str | None = None
    token_expires_at: datetime | None = None

    @classmethod
    def from_registry(cls, account: HealthScopedAccount) -> OwnerContext:
        """Construct an OwnerContext from a registry account row."""
        return cls(
            account_id=account.id,
            email=account.email,
            entity_id=account.entity_id,
            refresh_token_present=account.refresh_token_present,
            endpoint_identity=_endpoint_identity_for_user(account.email),
        )

    def refresh_from(self, account: HealthScopedAccount) -> None:
        """Update mutable fields in-place from a fresh registry row."""
        self.email = account.email
        self.entity_id = account.entity_id
        self.refresh_token_present = account.refresh_token_present
        self.endpoint_identity = _endpoint_identity_for_user(account.email)


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class GoogleHealthConnector:
    """Single-owner Google Health polling connector.

    Responsible for:

    - Owner account discovery + scope verification.
    - Access-token acquisition via the shared Google credential pipeline.
    - Per-resource polling loops with 2-tuple cursor persistence.
    - Envelope construction, filter-gate evaluation, and Switchboard submission.
    - Heartbeat, metrics, health endpoint, filtered-event flush, replay drain.
    """

    def __init__(
        self,
        config: GoogleHealthConnectorConfig,
        shared_pool: asyncpg.Pool | None = None,
        cursor_pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config
        self._shared_pool = shared_pool
        self._cursor_pool = cursor_pool

        # Resolved on startup
        self._google_account: GoogleAccount | None = None
        self._google_user_id: str = ""  # email (stable primary-account identifier)
        self._endpoint_identity: str = ""

        # Multi-account registry: keyed by account UUID, populated by
        # _resolve_owner_and_scopes from list_health_scoped_accounts.
        # _accounts_added / _accounts_removed track diff across scope-recheck cycles.
        self._accounts: dict[uuid.UUID, OwnerContext] = {}
        self._accounts_added: list[uuid.UUID] = []  # newly discovered in last cycle
        self._accounts_removed: list[uuid.UUID] = []  # lost scopes/removed in last cycle

        # Cached OAuth app credentials (client_id / client_secret) from shared pipeline.
        # The refresh token is per-account; resolved on-demand via _resolve_entity_refresh_token.
        self._client_id: str | None = None
        self._client_secret: str | None = None
        # Legacy single-account refresh token — kept for _resolve_credentials compat only.
        self._refresh_token: str | None = None

        # HTTP clients
        self._http_client: httpx.AsyncClient | None = None
        self._api_client: GoogleHealthClient | None = None

        # Switchboard MCP client
        self._mcp_client = CachedMCPClient(
            config.switchboard_mcp_url,
            client_name="google-health-connector",
        )

        # Per-(account, resource) state: keyed by (account_uuid, resource_name).
        # Initialised when an account is added; torn down when an account is removed.
        self._resources: dict[tuple[uuid.UUID, str], ResourceState] = {}

        # Per-account heartbeat tasks: keyed by account UUID.
        self._heartbeats: dict[uuid.UUID, ConnectorHeartbeat] = {}

        # Degraded / error flags
        self._scope_missing: bool = True  # start degraded until verified
        self._account_missing: bool = True
        self._auth_error: bool = False
        self._auth_error_message: str | None = None
        self._last_source_api_ok: bool | None = None
        self._source_api_error_message: str | None = None

        # Shared infra (created after identity resolution)
        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity="",
        )
        # Connector-level degraded heartbeat (emitted before any account is resolved).
        # Once accounts are discovered, per-account heartbeats in _heartbeats take over.
        self._degraded_heartbeat: ConnectorHeartbeat | None = None
        self._ingestion_policy: IngestionPolicyEvaluator | None = None
        self._filtered_event_buffer: FilteredEventBuffer | None = None

        self._semaphore = asyncio.Semaphore(config.max_inflight)

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None

        # Shutdown / lifecycle
        self._shutdown_event = asyncio.Event()
        self._running = False
        self._start_time = time.time()

        # Checkpoint (aggregated across resources — latest poll timestamp)
        self._last_checkpoint_save: float | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Full startup sequence followed by the main poll loop."""
        logger.info("GoogleHealthConnector starting")

        self._http_client = httpx.AsyncClient(timeout=30.0)
        self._running = True

        try:
            # Signal handlers
            try:
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGTERM, signal.SIGINT):
                    loop.add_signal_handler(sig, self._handle_signal)
            except (NotImplementedError, OSError):
                logger.debug("GoogleHealthConnector: signal handlers unsupported")

            # Phase 1: Resolve owner account + scopes. Non-fatal if missing — we
            # simply stay in degraded and re-check.
            await self._resolve_owner_and_scopes(initial=True)

            # Phase 2: Post-identity init (metrics, heartbeat, policy, filter buffer).
            # Uses a sentinel identity "degraded" when owner not yet resolved so
            # the heartbeat can still report state.
            self._post_identity_init()

            # Phase 3: Load per-resource cursors (only when identity resolved).
            await self._load_all_cursors()

            # Phase 4: Wait for Switchboard.
            try:
                await wait_for_switchboard_ready(self._config.switchboard_mcp_url)
            except TimeoutError:
                logger.warning(
                    "GoogleHealthConnector: Switchboard readiness probe timed out; proceeding"
                )

            # Phase 5: Health server.
            self._start_health_server()

            # Phase 6: Heartbeat — start the degraded-state heartbeat and any
            # per-account heartbeats that _post_identity_init may have spawned.
            if self._degraded_heartbeat is not None:
                self._degraded_heartbeat.start()
                try:
                    await self._degraded_heartbeat._send_heartbeat()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "GoogleHealthConnector: initial degraded heartbeat failed: %s", exc
                    )
            for hb in self._heartbeats.values():
                hb.start()
                try:
                    await hb._send_heartbeat()
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "GoogleHealthConnector: initial per-account heartbeat failed: %s", exc
                    )

            # Phase 7: Main loop.
            await self._main_loop()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Request a graceful shutdown."""
        if not self._shutdown_event.is_set():
            logger.info("GoogleHealthConnector: stop() requested")
            self._shutdown_event.set()

    def _handle_signal(self) -> None:
        logger.info("GoogleHealthConnector: received shutdown signal")
        self._shutdown_event.set()

    async def _shutdown(self) -> None:
        logger.info("GoogleHealthConnector: shutting down")
        self._running = False

        # Final per-account heartbeats + stop.
        for hb in list(self._heartbeats.values()):
            try:
                await hb._send_heartbeat()
            except Exception as exc:  # noqa: BLE001
                logger.debug("GoogleHealthConnector: final per-account heartbeat failed: %s", exc)
            await hb.stop()

        # Degraded-state heartbeat.
        if self._degraded_heartbeat is not None:
            try:
                await self._degraded_heartbeat._send_heartbeat()
            except Exception as exc:  # noqa: BLE001
                logger.debug("GoogleHealthConnector: final degraded heartbeat failed: %s", exc)
            await self._degraded_heartbeat.stop()

        if self._health_server is not None:
            self._health_server.should_exit = True

        if self._api_client is not None:
            await self._api_client.aclose()

        if self._http_client is not None:
            await self._http_client.aclose()

        logger.info("GoogleHealthConnector: shutdown complete")

    # ------------------------------------------------------------------
    # Owner / scope resolution
    # ------------------------------------------------------------------

    async def _resolve_owner_and_scopes(self, *, initial: bool = False) -> None:
        """Re-query all health-scoped Google accounts and diff against the previous cycle.

        Populates ``_accounts`` (keyed by account UUID → :class:`OwnerContext`) from
        :func:`list_health_scoped_accounts`.  On each cycle:

        - **Adds** (newly discovered accounts) are recorded in ``_accounts_added``.
        - **Removals** (scope revocation or account deletion) are recorded in
          ``_accounts_removed`` and the heartbeat row is closed via
          :meth:`_teardown_account` with ``state='unknown',
          error_message='account_removed'``.
        - **Existing** accounts have their mutable fields refreshed in-place.

        The legacy single-account fields (``_google_account``, ``_google_user_id``,
        ``_endpoint_identity``) are kept in sync with the *primary* health-scoped
        account for backward compatibility with callers that have not yet been
        updated to the multi-account model.  When no accounts qualify, the
        connector stays in degraded mode.

        Non-fatal — missing accounts simply leave the connector in degraded mode.
        """
        self._accounts_added = []
        self._accounts_removed = []

        if self._shared_pool is None:
            self._account_missing = True
            self._scope_missing = True
            if initial:
                logger.warning(
                    "GoogleHealthConnector: no shared DB pool — starting in degraded mode"
                )
            return

        try:
            health_accounts = await list_health_scoped_accounts(self._shared_pool)
        except Exception as exc:  # noqa: BLE001
            self._account_missing = True
            self._scope_missing = True
            logger.warning("GoogleHealthConnector: account discovery failed (non-fatal): %s", exc)
            return

        new_ids = {a.id for a in health_accounts}
        cur_ids = set(self._accounts)

        # ----------------------------------------------------------------
        # Tear down accounts that lost scopes or were deleted.
        # ----------------------------------------------------------------
        for gone_id in cur_ids - new_ids:
            self._accounts_removed.append(gone_id)
            ctx = self._accounts.pop(gone_id)
            logger.info(
                "GoogleHealthConnector: account removed (scope revoked or deleted): %s (%s)",
                ctx.email,
                gone_id,
            )
            # Remove per-(account, resource) poll state.
            for bundle in RESOURCE_BUNDLES:
                self._resources.pop((gone_id, bundle.resource), None)
            await self._teardown_account(ctx)

        # ----------------------------------------------------------------
        # Spin up new accounts.
        # ----------------------------------------------------------------
        for added in health_accounts:
            if added.id not in cur_ids:
                self._accounts_added.append(added.id)
                ctx = OwnerContext.from_registry(added)
                self._accounts[added.id] = ctx
                logger.info(
                    "GoogleHealthConnector: new health-scoped account discovered: %s (%s)",
                    added.email,
                    added.id,
                )
                if not added.refresh_token_present:
                    logger.warning(
                        "GoogleHealthConnector: account %s has health scopes but no refresh token",
                        added.email,
                    )
                # Initialise per-(account, resource) poll state.
                for bundle in RESOURCE_BUNDLES:
                    self._resources[(added.id, bundle.resource)] = ResourceState(bundle=bundle)
                # Spawn a per-account heartbeat task.
                self._spinup_account_heartbeat(ctx)

        # ----------------------------------------------------------------
        # Refresh existing accounts (email / entity_id may have changed).
        # ----------------------------------------------------------------
        for existing in health_accounts:
            if existing.id in cur_ids:
                self._accounts[existing.id].refresh_from(existing)

        # ----------------------------------------------------------------
        # Update legacy single-account fields + scope/account missing flags.
        # ----------------------------------------------------------------
        if not self._accounts:
            self._account_missing = True
            self._scope_missing = True

            # Try to provide a more specific degraded reason: check whether any
            # active account exists at all (even without the required scopes).
            try:
                primary_account = await get_google_account(self._shared_pool, account=None)
                # A primary account exists but lacks health scopes.
                self._google_account = primary_account
                self._account_missing = False
                granted = frozenset(primary_account.granted_scopes or [])
                missing_scopes = GOOGLE_HEALTH_SCOPES - granted
                self._scope_missing = bool(missing_scopes)
                if self._scope_missing:
                    google_health_scope_missing_total.labels(
                        endpoint_identity=_endpoint_identity_for_user(
                            primary_account.email or "unknown"
                        )
                    ).inc()
                    if initial:
                        logger.warning(
                            "GoogleHealthConnector: primary account %s missing health scopes: %s",
                            primary_account.email,
                            sorted(missing_scopes),
                        )
                user_id = primary_account.email or str(primary_account.id)
                self._google_user_id = user_id
                self._endpoint_identity = _endpoint_identity_for_user(user_id)
            except MissingGoogleCredentialsError:
                self._google_account = None
                self._account_missing = True
                self._scope_missing = True
                if initial:
                    logger.warning(
                        "GoogleHealthConnector: no primary Google account — degraded mode"
                    )
            except Exception as exc:  # noqa: BLE001
                self._account_missing = True
                self._scope_missing = True
                logger.warning(
                    "GoogleHealthConnector: fallback account check failed (non-fatal): %s", exc
                )
        else:
            # At least one health-scoped account is present — connector is healthy.
            self._account_missing = False
            self._scope_missing = False

            # Keep legacy single-account fields pointing at the first account
            # (primary if present, otherwise oldest by connected_at which is the
            # registry's ordering guarantee).
            first_ctx = next(iter(self._accounts.values()))
            self._google_user_id = first_ctx.email
            self._endpoint_identity = first_ctx.endpoint_identity
            self._google_account = GoogleAccount(
                id=first_ctx.account_id,
                entity_id=first_ctx.entity_id,
                email=first_ctx.email,
                display_name=None,
                is_primary=False,
                granted_scopes=list(GOOGLE_HEALTH_SCOPES),
                status="active",
                connected_at=datetime.now(UTC),
                last_token_refresh_at=None,
            )

    async def _teardown_account(self, ctx: OwnerContext) -> None:
        """Close the heartbeat row for a removed account.

        Submits a final heartbeat envelope with ``state='unknown'`` and
        ``error_message='account_removed'`` so the dashboard reflects the
        account's departure and the connector_registry row transitions out of
        the ``healthy`` state.  Non-fatal — failure is logged and swallowed.
        """
        # Stop the per-account heartbeat task if running.
        hb = self._heartbeats.pop(ctx.account_id, None)
        if hb is not None:
            try:
                await hb.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "GoogleHealthConnector: error stopping heartbeat for %s (non-fatal): %s",
                    ctx.email,
                    exc,
                )

        # Send a final "unknown" heartbeat to close the connector_registry row.
        envelope = {
            "schema_version": "connector.heartbeat.v1",
            "connector": {
                "connector_type": _CONNECTOR_TYPE,
                "endpoint_identity": ctx.endpoint_identity,
                "instance_id": str(uuid.uuid4()),
                "version": "1",
            },
            "status": {
                "state": "unknown",
                "error_message": "account_removed",
                "uptime_s": 0,
            },
            "counters": {},
            "sent_at": datetime.now(UTC).isoformat(),
        }
        try:
            await self._mcp_client.call_tool("connector.heartbeat", envelope)
            logger.info(
                "GoogleHealthConnector: closed heartbeat row for removed account %s",
                ctx.email,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthConnector: failed to close heartbeat row for %s (non-fatal): %s",
                ctx.email,
                exc,
            )

    def _spinup_account_heartbeat(self, ctx: OwnerContext) -> None:
        """Create and register a per-account heartbeat task.

        The heartbeat's ``get_health_state`` callback is scoped to the specific
        account: it returns the account's auth-error state if present, or falls
        back to the connector-level aggregate.  The task is started immediately
        if the connector is already running (i.e. start() has been called),
        otherwise it will be started by start() during Phase 6.
        """
        hb_config = HeartbeatConfig.from_env(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=ctx.endpoint_identity,
        )
        # Capture account_id in a closure so the lambda stays bound to this account.
        acct_id = ctx.account_id

        def _account_health_state() -> tuple[str, str | None]:
            return self._get_account_health_state(acct_id)

        hb = ConnectorHeartbeat(
            config=hb_config,
            mcp_client=self._mcp_client,
            metrics=ConnectorMetrics(
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=ctx.endpoint_identity,
            ),
            get_health_state=_account_health_state,
        )
        self._heartbeats[ctx.account_id] = hb

        # If the connector is already past Phase 6 (running), start immediately.
        if self._running:
            hb.start()

    # ------------------------------------------------------------------
    # Post-identity initialization
    # ------------------------------------------------------------------

    def _post_identity_init(self) -> None:
        """Initialise metrics/policy/filter-buffer and spawn per-account heartbeats.

        Also maintains a connector-level degraded heartbeat (used before any
        account is resolved) so operators can observe the degraded signal.
        """
        identity_label = self._endpoint_identity or "google_health:degraded"

        self._metrics = ConnectorMetrics(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=identity_label,
        )

        self._ingestion_policy = IngestionPolicyEvaluator(
            scope=f"connector:{_CONNECTOR_TYPE}:{identity_label}",
            db_pool=self._shared_pool,
        )

        self._filtered_event_buffer = FilteredEventBuffer(
            connector_type=_CONNECTOR_TYPE,
            endpoint_identity=identity_label,
        )

        # If no accounts are known yet, maintain a degraded connector-level heartbeat.
        # Once accounts are resolved, per-account heartbeats take precedence.
        if not self._accounts:
            hb_config = HeartbeatConfig.from_env(
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=identity_label,
            )
            self._degraded_heartbeat = ConnectorHeartbeat(
                config=hb_config,
                mcp_client=self._mcp_client,
                metrics=self._metrics,
                get_health_state=self._get_health_state,
            )
        else:
            # Accounts already known — stop any degraded heartbeat and ensure
            # per-account heartbeats exist.
            if self._degraded_heartbeat is not None:
                asyncio.create_task(self._degraded_heartbeat.stop())
                self._degraded_heartbeat = None
            for ctx in self._accounts.values():
                if ctx.account_id not in self._heartbeats:
                    self._spinup_account_heartbeat(ctx)

    # ------------------------------------------------------------------
    # Credential resolution — shared Google pipeline
    # ------------------------------------------------------------------

    async def _resolve_credentials(self) -> bool:
        """Resolve (client_id, client_secret, refresh_token) via the shared pipeline.

        Returns ``True`` if credentials are now available, ``False`` otherwise.
        Never raises on missing credentials — the connector stays in degraded
        state until OAuth callback populates the required rows.
        """
        if self._shared_pool is None or self._google_account is None:
            return False

        try:
            store = CredentialStore(self._shared_pool)
            creds = await load_google_credentials(
                store, pool=self._shared_pool, account=self._google_account.email
            )
        except InvalidGoogleCredentialsError as exc:
            logger.warning("GoogleHealthConnector: stored credentials invalid: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthConnector: credential resolution failed (non-fatal): %s", exc
            )
            return False

        if creds is None:
            return False

        self._client_id = creds.client_id
        self._client_secret = creds.client_secret
        self._refresh_token = creds.refresh_token
        return True

    async def _resolve_app_credentials(self) -> bool:
        """Resolve OAuth app credentials (client_id, client_secret) from the shared pipeline.

        The per-account refresh tokens are fetched separately by
        :meth:`_mint_access_token` via ``_resolve_entity_refresh_token``.
        This method only populates ``_client_id`` and ``_client_secret``.

        Returns ``True`` if credentials are now available, ``False`` otherwise.
        """
        if self._client_id and self._client_secret:
            return True

        # Fall back to the legacy single-account credential resolver to get
        # client_id and client_secret (they are shared across accounts).
        if self._shared_pool is None:
            return False

        try:
            store = CredentialStore(self._shared_pool)
            account_email = self._google_user_id or None
            creds = await load_google_credentials(
                store, pool=self._shared_pool, account=account_email
            )
        except InvalidGoogleCredentialsError as exc:
            logger.warning("GoogleHealthConnector: stored app credentials invalid: %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthConnector: app credential resolution failed (non-fatal): %s", exc
            )
            return False

        if creds is None:
            return False

        self._client_id = creds.client_id
        self._client_secret = creds.client_secret
        return True

    async def _mint_access_token(self, account_uuid: uuid.UUID) -> str:
        """Mint a fresh access token for the given account.

        Per design.md ADR-4: uses ``_resolve_entity_refresh_token`` to fetch
        the per-account refresh token directly, keyed by ``companion_entity_id``.
        Tokens live in ``OwnerContext.cached_access_token`` — never written to
        disk, logs, or DB.  Per ``about/heart-and-soul/security.md`` Tier-2
        contract for Google integrations.

        Mint failure for one account leaves other accounts' polls untouched.
        """
        from butlers.google_credentials import _resolve_entity_refresh_token

        ctx = self._accounts.get(account_uuid)
        if ctx is None:
            raise GoogleHealthCredentialError(
                f"Account {account_uuid} not in _accounts — cannot mint token"
            )

        # Resolve OAuth app credentials (client_id, client_secret) once.
        if not (self._client_id and self._client_secret):
            if not await self._resolve_app_credentials():
                raise GoogleHealthCredentialError(
                    "Google Health app credentials (client_id/client_secret) are not configured. "
                    "Run OAuth with scope_set=health to authorize."
                )

        # Fetch the per-account refresh token via the entity_info anchor.
        if self._shared_pool is None:
            raise GoogleHealthCredentialError(
                f"No shared DB pool — cannot resolve refresh token for account {ctx.email}"
            )
        refresh_token = await _resolve_entity_refresh_token(self._shared_pool, ctx.entity_id)
        if not refresh_token:
            raise GoogleHealthCredentialError(
                f"No refresh token found for account {ctx.email} (entity {ctx.entity_id}). "
                "Run OAuth with scope_set=health to authorize."
            )

        assert self._http_client is not None
        try:
            resp = await self._http_client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                timeout=15,
            )
        except httpx.TransportError as exc:
            raise GoogleHealthError(f"Token refresh transport error: {exc}") from exc

        if resp.status_code != 200:
            body = resp.text[:200]
            # Treat any non-200 as terminal for this account.
            raise GoogleHealthCredentialError(
                f"Google token refresh failed for {ctx.email}: HTTP {resp.status_code}: {body}"
            )

        data = resp.json()
        access_token = data.get("access_token")
        if not access_token:
            raise GoogleHealthCredentialError(
                f"Token response missing access_token for account {ctx.email}"
            )

        expires_in = int(data.get("expires_in", 3600))
        ctx.cached_access_token = access_token
        ctx.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        return access_token

    async def _get_access_token(self, account_uuid: uuid.UUID) -> str:
        """Return a usable access token for the given account, refreshing if near expiry."""
        ctx = self._accounts.get(account_uuid)
        if ctx is not None:
            now = datetime.now(UTC)
            if (
                ctx.cached_access_token
                and ctx.token_expires_at
                and now < ctx.token_expires_at - timedelta(seconds=_TOKEN_REFRESH_BUFFER_S)
            ):
                return ctx.cached_access_token
        return await self._mint_access_token(account_uuid)

    async def _mark_account_revoked(self, account_uuid: uuid.UUID) -> None:
        """Mark a google account row as revoked.

        Called when the connector observes a persistent 401 after token refresh
        for a specific account.  The dashboard picks up the new status and
        surfaces a re-consent CTA.  Non-fatal — failure is logged and swallowed.
        """
        if self._shared_pool is None:
            return
        try:
            async with self._shared_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE public.google_accounts
                    SET status = 'revoked'
                    WHERE id = $1
                    """,
                    account_uuid,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthConnector: failed to mark account %s revoked (non-fatal): %s",
                account_uuid,
                exc,
            )

    # ------------------------------------------------------------------
    # API client
    # ------------------------------------------------------------------

    def _make_account_api_client(self, account_uuid: uuid.UUID) -> GoogleHealthClient:
        """Create a per-account Google Health API client.

        Each account gets its own ``token_fetcher`` bound to its UUID so token
        mints are isolated — a failure for account A does not affect account B.
        """
        assert self._http_client is not None

        async def _token_fetcher() -> str:
            return await self._get_access_token(account_uuid)

        return GoogleHealthClient(
            token_fetcher=_token_fetcher,
            client=self._http_client,
        )

    def _ensure_api_client(self) -> GoogleHealthClient:
        """Lazily instantiate a legacy fallback API client (single-account compat).

        Prefer :meth:`_make_account_api_client` when iterating across accounts.
        This method exists for test fixtures and callers that predate the
        per-account model.
        """
        if self._api_client is None:
            assert self._http_client is not None
            # Use the first available account's token fetcher, or a no-op if
            # no accounts are resolved yet.
            first_uuid = next(iter(self._accounts), None)
            if first_uuid is not None:

                async def _token_fetcher() -> str:
                    return await self._get_access_token(first_uuid)

                self._api_client = GoogleHealthClient(
                    token_fetcher=_token_fetcher,
                    client=self._http_client,
                )
            else:
                # Fallback: no accounts yet — build a client that will always error.
                async def _no_account_fetcher() -> str:
                    raise GoogleHealthCredentialError("No accounts resolved yet")

                self._api_client = GoogleHealthClient(
                    token_fetcher=_no_account_fetcher,
                    client=self._http_client,
                )
        return self._api_client

    # ------------------------------------------------------------------
    # Cursor persistence
    # ------------------------------------------------------------------

    async def _load_all_cursors(self) -> None:
        """Load per-(account, resource) cursors from the switchboard registry."""
        if self._cursor_pool is None:
            return
        for (acct_id, _resource), state in self._resources.items():
            ctx = self._accounts.get(acct_id)
            if ctx is None:
                continue
            endpoint = _cursor_endpoint_identity(ctx.email, acct_id, state.bundle.resource)
            try:
                cursor = await load_cursor(self._cursor_pool, _CONNECTOR_TYPE, endpoint)
                if cursor is not None:
                    state.last_cursor = cursor
                    state.backfill_done = True
                    logger.info(
                        "GoogleHealthConnector: loaded cursor account=%s resource=%s cursor=%s",
                        ctx.email,
                        state.bundle.resource,
                        cursor,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "GoogleHealthConnector: failed to load cursor account=%s resource=%s: %s",
                    ctx.email,
                    state.bundle.resource,
                    exc,
                )

    async def _save_cursor(
        self, account_uuid: uuid.UUID, state: ResourceState, cursor_value: str
    ) -> None:
        """Persist the cursor for one (account, resource) pair after a successful poll."""
        if self._cursor_pool is None:
            return
        ctx = self._accounts.get(account_uuid)
        if ctx is None:
            return
        endpoint = _cursor_endpoint_identity(ctx.email, account_uuid, state.bundle.resource)
        try:
            await save_cursor(self._cursor_pool, _CONNECTOR_TYPE, endpoint, cursor_value)
            state.last_cursor = cursor_value
            self._last_checkpoint_save = time.time()
            self._metrics.record_checkpoint_save("success")
        except Exception as exc:  # noqa: BLE001
            self._metrics.record_checkpoint_save("error")
            logger.warning(
                "GoogleHealthConnector: failed to save cursor account=%s resource=%s: %s",
                ctx.email,
                state.bundle.resource,
                exc,
            )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _main_loop(self) -> None:
        """Top-level poll scheduler.

        Runs per-resource polls when due. When the connector is in degraded
        state (missing scopes / account / credentials), sleeps on
        ``scope_recheck_s`` and re-resolves owner + scopes. Base-spec
        obligations (replay drain, filtered-event flush) fire every cycle.
        """
        logger.info(
            "GoogleHealthConnector: entering main loop (intervals=%s backfill_days=%d)",
            self._config.poll_intervals,
            self._config.backfill_days,
        )

        while self._running and not self._shutdown_event.is_set():
            # Always drain replay queue — runs even in degraded mode so a
            # backlog does not build up while scopes are missing.
            await self._drain_replay()

            # Degraded-mode wait path.
            if self._scope_missing or self._account_missing or self._auth_error:
                await self._resolve_owner_and_scopes()
                if self._scope_missing or self._account_missing or self._auth_error:
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=self._config.scope_recheck_s,
                        )
                        break
                    except TimeoutError:
                        pass
                    continue
                # Scopes acquired — re-init identity-dependent state.
                logger.info("GoogleHealthConnector: scopes granted — transitioning healthy")
                self._post_identity_init()
                await self._load_all_cursors()

            now = time.monotonic()
            next_due: float = now + self._shortest_interval()

            for (acct_id, _resource), state in list(self._resources.items()):
                ctx = self._accounts.get(acct_id)
                if ctx is None:
                    # Account was removed mid-loop; skip.
                    continue
                if state.next_poll_monotonic > now:
                    next_due = min(next_due, state.next_poll_monotonic)
                    continue
                try:
                    await self._poll_resource(acct_id, state)
                except GoogleHealthCredentialError as exc:
                    logger.error(
                        "GoogleHealthConnector: credential error account=%s — "
                        "marking revoked and skipping: %s",
                        ctx.email,
                        exc,
                    )
                    # Isolate: only this account's polls stop; others continue.
                    ctx.cached_access_token = None
                    ctx.token_expires_at = None
                    ctx.refresh_token_present = False
                    await self._mark_account_revoked(acct_id)
                    # Reschedule after one full recheck cycle so we don't tight-loop.
                    state.next_poll_monotonic = time.monotonic() + self._config.scope_recheck_s
                    next_due = min(next_due, state.next_poll_monotonic)
                    hb = self._heartbeats.get(acct_id)
                    if hb is not None:
                        try:
                            await hb._send_heartbeat()
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                except GoogleHealthRateLimitError as exc:
                    delay = exc.retry_after
                    if delay is None:
                        delay = exponential_backoff_delay(1)
                    google_health_rate_limit_events_total.labels(
                        endpoint_identity=ctx.endpoint_identity,
                        resource=state.bundle.resource,
                    ).inc()
                    logger.warning(
                        "GoogleHealthConnector: 429 account=%s resource=%s — retry after %.1fs",
                        ctx.email,
                        state.bundle.resource,
                        delay,
                    )
                    # Do NOT advance the cursor — reschedule after the delay.
                    state.next_poll_monotonic = time.monotonic() + delay
                    next_due = min(next_due, state.next_poll_monotonic)
                    continue
                except GoogleHealthSourcePreconditionError as exc:
                    self._last_source_api_ok = False
                    self._source_api_error_message = exc.reason.lower()
                    logger.warning(
                        "GoogleHealthConnector: source precondition failed "
                        "account=%s resource=%s reason=%s redirect_uri=%s",
                        ctx.email,
                        state.bundle.resource,
                        exc.reason,
                        exc.redirect_uri,
                    )
                    google_health_polls_total.labels(
                        endpoint_identity=ctx.endpoint_identity,
                        resource=state.bundle.resource,
                        outcome="error",
                    ).inc()
                    interval = self._config.poll_intervals.get(
                        state.bundle.resource, state.bundle.default_interval_s
                    )
                    state.next_poll_monotonic = time.monotonic() + interval
                    next_due = min(next_due, state.next_poll_monotonic)
                    hb = self._heartbeats.get(acct_id)
                    if hb is not None:
                        try:
                            await hb._send_heartbeat()
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                except Exception as exc:  # noqa: BLE001
                    self._last_source_api_ok = False
                    self._source_api_error_message = "source_api_unreachable"
                    logger.warning(
                        "GoogleHealthConnector: poll error account=%s resource=%s (non-fatal): %s",
                        ctx.email,
                        state.bundle.resource,
                        exc,
                    )
                    google_health_polls_total.labels(
                        endpoint_identity=ctx.endpoint_identity,
                        resource=state.bundle.resource,
                        outcome="error",
                    ).inc()
                    interval = self._config.poll_intervals.get(
                        state.bundle.resource, state.bundle.default_interval_s
                    )
                    state.next_poll_monotonic = time.monotonic() + interval
                    next_due = min(next_due, state.next_poll_monotonic)
                    continue
                else:
                    interval = self._config.poll_intervals.get(
                        state.bundle.resource, state.bundle.default_interval_s
                    )
                    state.next_poll_monotonic = time.monotonic() + interval
                    next_due = min(next_due, state.next_poll_monotonic)

            # Base-spec obligation — flush filtered events every cycle.
            await self._flush_filtered_events()

            # Sleep until the next resource is due.
            sleep_for = max(1.0, next_due - time.monotonic())
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=sleep_for)
                break
            except TimeoutError:
                pass

    def _shortest_interval(self) -> float:
        """Return the shortest cadence across all resources (seconds)."""
        if not self._config.poll_intervals:
            return _DEFAULT_POLL_INTERVALS["sleep"]
        return float(min(self._config.poll_intervals.values()))

    # ------------------------------------------------------------------
    # Per-resource poll
    # ------------------------------------------------------------------

    async def _poll_resource(self, account_uuid: uuid.UUID, state: ResourceState) -> None:
        """Poll a single (account, resource), emit envelopes, advance cursor.

        Args:
            account_uuid: The account whose refresh token and endpoint identity to use.
            state: Per-(account, resource) poll state (cursor, backfill flag, etc.).
        """
        ctx = self._accounts.get(account_uuid)
        if ctx is None:
            raise GoogleHealthCredentialError(f"Account {account_uuid} not found — skipping poll")

        client = self._make_account_api_client(account_uuid)
        bundle = state.bundle
        endpoint_identity = ctx.endpoint_identity

        since, until = self._compute_window(state)
        if bundle.request_method == "POST":
            body = self._build_post_body(bundle, state, since, until)
            data = await client.post_json(bundle.endpoint_path, json_body=body)
            if bundle.secondary_endpoint_path:
                secondary_data = await client.post_json(
                    bundle.secondary_endpoint_path,
                    json_body=body,
                )
                records = _build_activity_records(data, secondary_data)
            else:
                records = [
                    _normalize_google_health_record(bundle, record)
                    for record in _extract_records(data)
                ]
        else:
            params = self._build_params(bundle, state, since, until)
            data = await client.get_json(bundle.endpoint_path, params=params)
            records = [
                _normalize_google_health_record(bundle, record) for record in _extract_records(data)
            ]

        # Observe rate-limit headers per (account, resource) for metrics visibility.
        headers = client.last_rate_limit_headers
        remaining_raw = headers.get("X-RateLimit-Remaining")
        if remaining_raw is not None:
            try:
                google_health_rate_limit_remaining.labels(
                    endpoint_identity=endpoint_identity,
                    resource=bundle.resource,
                ).set(float(remaining_raw))
            except ValueError:
                pass

        now = datetime.now(UTC)
        observed_at = now.isoformat()
        emitted = 0
        latest_cursor = state.last_cursor

        for record in records:
            record_id = _record_identity(bundle, record)
            if record_id is None:
                continue
            if state.last_cursor and record_id == state.last_cursor:
                # Same record as cursor — spec mandates no duplicate emission.
                # Cursor value stays unchanged; poll timestamp updated below.
                continue

            if bundle.category == "sleep":
                envelope = build_sleep_session_envelope(
                    endpoint_identity=endpoint_identity,
                    google_user_id=ctx.email,
                    session_id=record_id,
                    session_record=record,
                    observed_at=observed_at,
                )
            else:
                envelope = build_daily_summary_envelope(
                    endpoint_identity=endpoint_identity,
                    google_user_id=ctx.email,
                    resource=bundle.resource,
                    record_date=record_id,
                    record=record,
                    normalized_summary_template=bundle.normalized_summary,
                    observed_at=observed_at,
                )
            await self._submit_envelope(envelope, resource=bundle.resource)
            emitted += 1
            latest_cursor = record_id

        google_health_envelopes_total.labels(
            endpoint_identity=endpoint_identity,
            resource=bundle.resource,
        ).inc(emitted)

        if latest_cursor and latest_cursor != state.last_cursor:
            await self._save_cursor(account_uuid, state, latest_cursor)

        state.last_poll_at = now
        state.backfill_done = True

        google_health_polls_total.labels(
            endpoint_identity=endpoint_identity,
            resource=bundle.resource,
            outcome="success",
        ).inc()
        self._last_source_api_ok = True
        self._source_api_error_message = None

    def _compute_window(self, state: ResourceState) -> tuple[datetime, datetime]:
        """Compute [since, until] for the next poll.

        On first run (no cursor), backfills ``backfill_days`` days.
        Otherwise starts from the last observed record's date (or the
        last poll timestamp, whichever is available).
        """
        now = datetime.now(UTC)
        if state.backfill_done and state.last_poll_at:
            # Re-poll from a tight trailing window (1 day) so we catch late
            # device syncs while still converging on the cursor quickly.
            since = max(state.last_poll_at - timedelta(days=1), now - timedelta(days=7))
        else:
            since = now - timedelta(days=self._config.backfill_days)
        return since, now

    def _build_params(
        self,
        bundle: ResourceBundle,
        state: ResourceState,
        since: datetime,
        until: datetime,
    ) -> dict[str, Any]:
        """Build the Google Health query params for the resource.

        Google Health v4 uses AIP-160 ``filter`` query expressions on
        ``dataPoints:list`` / ``dataPoints:reconcile``.
        """
        if bundle.category == "sleep":
            start = since.isoformat()
            end = until.isoformat()
        else:
            start = since.date().isoformat()
            end = until.date().isoformat()
        return {
            "filter": f'{bundle.filter_field} >= "{start}" AND {bundle.filter_field} < "{end}"',
            "pageSize": 25 if bundle.category == "sleep" else 10000,
        }

    def _build_post_body(
        self,
        bundle: ResourceBundle,
        state: ResourceState,
        since: datetime,
        until: datetime,
    ) -> dict[str, Any]:
        """Build a daily-rollup JSON body for POST resources."""
        del bundle, state
        # Google caps active-minutes rollups at 14 days; activity envelopes
        # merge active-minutes with steps, so clamp that source to avoid a
        # whole-poll 400 on first-run backfills.
        since = max(since, until - timedelta(days=14))
        return {
            "range": {
                "start": {"date": _date_message(since.date())},
                "end": {"date": _date_message(until.date())},
            },
            "windowSizeDays": 1,
            "pageSize": 10000,
        }

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    async def _submit_envelope(self, envelope: dict[str, Any], *, resource: str) -> None:
        """Evaluate filter gate and submit to Switchboard.

        Filtered events land in :class:`FilteredEventBuffer` (status=filtered).
        Submission errors land in the same buffer (status=error).
        """
        source = envelope.get("source", {})
        event = envelope.get("event", {})
        sender = envelope.get("sender", {})
        payload = envelope.get("payload", {})
        control = envelope.get("control", {})

        ing_env = IngestionEnvelope(
            source_channel=source.get("channel", ""),
            raw_key=sender.get("identity", ""),
            thread_id=event.get("external_thread_id"),
        )

        if self._ingestion_policy is not None:
            try:
                decision = self._ingestion_policy.evaluate(ing_env)
                if not decision.allowed:
                    logger.debug(
                        "GoogleHealthConnector: event blocked by policy: %s",
                        event.get("external_event_id"),
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
            except Exception as exc:  # noqa: BLE001
                logger.debug("GoogleHealthConnector: policy evaluation error (fail-open): %s", exc)

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
                        logger.warning(
                            "GoogleHealthConnector: unexpected ingest response: %s", result
                        )
                self._metrics.record_ingest_submission(status, latency)
                self._last_source_api_ok = True
            except Exception as exc:  # noqa: BLE001
                latency = time.perf_counter() - start_t
                self._metrics.record_ingest_submission("error", latency)
                self._metrics.record_error("ingest_error", "submit")
                logger.warning("GoogleHealthConnector: ingest submission failed: %s", exc)
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

    async def _submit_to_ingest_direct(self, envelope: dict[str, Any]) -> None:
        """Submit directly to Switchboard — bypasses filter gate (replay path)."""
        await self._mcp_client.call_tool("ingest", envelope)

    async def _flush_filtered_events(self) -> None:
        if self._shared_pool is None or self._filtered_event_buffer is None:
            return
        if len(self._filtered_event_buffer) == 0:
            return
        try:
            await self._filtered_event_buffer.flush(self._shared_pool)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GoogleHealthConnector: filtered event flush failed: %s", exc)

    async def _drain_replay(self) -> None:
        if self._shared_pool is None or not self._endpoint_identity:
            return
        try:
            await drain_replay_pending(
                pool=self._shared_pool,
                connector_type=_CONNECTOR_TYPE,
                endpoint_identity=self._endpoint_identity,
                submit_fn=self._submit_to_ingest_direct,
                drain_logger=logger,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("GoogleHealthConnector: replay drain failed: %s", exc)

    # ------------------------------------------------------------------
    # Health / heartbeat callbacks
    # ------------------------------------------------------------------

    def _get_account_health_state(self, account_uuid: uuid.UUID) -> tuple[str, str | None]:
        """Return (state, error_message) for a specific account's heartbeat.

        Per AC-3 / spec §Aggregate state computation: error > degraded > healthy.
        A revoked/missing token for this account yields ``error``; missing scopes
        or source API failures yield ``degraded``; otherwise ``healthy``.
        """
        ctx = self._accounts.get(account_uuid)
        if ctx is None:
            return "error", "account_not_found"
        # If this account's token was explicitly cleared (revoked), mark error.
        if ctx.cached_access_token is None and not ctx.refresh_token_present:
            return "error", "token_invalid"
        # Connector-level degraded signals still apply.
        if self._scope_missing:
            return "degraded", "scope_missing"
        if self._last_source_api_ok is False:
            return "degraded", self._source_api_error_message or "source_api_unreachable"
        return "healthy", None

    def _get_health_state(self) -> tuple[str, str | None]:
        """Return worst-of (state, error_message) across all per-account heartbeats.

        Aggregate order: error > degraded > healthy (spec §Aggregate state computation).
        Falls back to connector-level flags when no accounts are resolved.
        """
        if not self._accounts:
            # Degraded: no accounts resolved yet.
            if self._auth_error:
                return "error", self._auth_error_message or "token_invalid"
            if self._account_missing:
                return "degraded", "no_primary_account"
            if self._scope_missing:
                return "degraded", "scope_missing"
            if self._last_source_api_ok is False:
                return "degraded", self._source_api_error_message or "source_api_unreachable"
            return "healthy", None

        # Worst-of across all accounts.
        worst_state = "healthy"
        worst_error: str | None = None
        _priority = {"error": 2, "degraded": 1, "healthy": 0}
        for acct_id in self._accounts:
            acct_state, acct_error = self._get_account_health_state(acct_id)
            if _priority.get(acct_state, 0) > _priority.get(worst_state, 0):
                worst_state = acct_state
                worst_error = acct_error
        return worst_state, worst_error

    # ------------------------------------------------------------------
    # Health HTTP server
    # ------------------------------------------------------------------

    def _start_health_server(self) -> None:
        app = FastAPI(title="google-health-connector-health")

        @app.get("/health")
        async def health() -> dict[str, Any]:
            state, error = self._get_health_state()
            uptime_s = int(time.time() - self._start_time)
            # Snapshot mutable state once to avoid cross-thread dict-size races.
            resources_snapshot = list(self._resources.items())
            accounts_snapshot = dict(self._accounts)
            # Per-account resource state for observability.
            resources_by_account: dict[str, dict[str, Any]] = {}
            for (acct_id, resource_name), state_ in resources_snapshot:
                ctx = accounts_snapshot.get(acct_id)
                acct_label = ctx.email if ctx else str(acct_id)
                resources_by_account.setdefault(acct_label, {})[resource_name] = {
                    "last_poll_at": state_.last_poll_at.isoformat()
                    if state_.last_poll_at
                    else None,
                    "last_cursor": state_.last_cursor,
                    "backfill_done": state_.backfill_done,
                }
            return {
                "status": state,
                "connector_type": _CONNECTOR_TYPE,
                "endpoint_identity": self._endpoint_identity,
                "uptime_seconds": uptime_s,
                "scope_missing": self._scope_missing,
                "account_missing": self._account_missing,
                "auth_error": self._auth_error,
                "accounts": list(accounts_snapshot.keys()),
                "resources_by_account": resources_by_account,
                "error": error,
            }

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest()

        port = self._config.health_port
        try:
            sock = make_health_socket("127.0.0.1", port)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GoogleHealthConnector: could not bind health socket on %d: %s", port, exc
            )
            return

        uvicorn_config = uvicorn.Config(
            app=app, host="127.0.0.1", port=port, log_level="warning", access_log=False
        )
        server = uvicorn.Server(uvicorn_config)
        self._health_server = server

        def _run() -> None:
            asyncio.run(server.serve(sockets=[sock]))

        thread = Thread(target=_run, daemon=True, name="google-health-health-server")
        thread.start()
        self._health_thread = thread
        logger.info("GoogleHealthConnector: health server started on port %d", port)


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------


_DAILY_UNION_FIELDS: dict[str, tuple[str, str]] = {
    "resting_hr": ("dailyRestingHeartRate", "beatsPerMinute"),
    "hrv": ("dailyHeartRateVariability", "averageHeartRateVariabilityMilliseconds"),
    "spo2": ("dailyOxygenSaturation", "averagePercentage"),
    "breathing_rate": ("dailyRespiratoryRate", "breathsPerMinute"),
    "vo2_max": ("dailyVo2Max", "vo2Max"),
}


def _extract_records(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract an iterable of record dicts from a Google Health response.

    Google Health API responses typically nest records under one of
    ``sessions``, ``dataPoints``, ``rollupDataPoints``, or ``items`` depending on the endpoint.
    This helper returns the first list it finds (falling back to ``[]``).
    """
    if not isinstance(data, dict):
        return []
    for key in ("sessions", "dataPoints", "rollupDataPoints", "items", "records"):
        value = data.get(key)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    return []


def _normalize_google_health_record(
    bundle: ResourceBundle,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Normalize Google Health v4 union records to the wellness ingest raw contract."""
    if bundle.category == "sleep":
        return _normalize_sleep_record(record)
    union = _DAILY_UNION_FIELDS.get(bundle.resource)
    if union is None:
        return dict(record)
    union_key, value_key = union
    payload = record.get(union_key)
    if not isinstance(payload, dict):
        return dict(record)

    date_value = _format_date_value(payload.get("date"))
    value = payload.get(value_key)
    normalized = dict(payload)
    if date_value:
        normalized["date"] = date_value
    if value is not None:
        normalized["value"] = value
    return normalized


def _normalize_sleep_record(record: dict[str, Any]) -> dict[str, Any]:
    sleep = record.get("sleep")
    if not isinstance(sleep, dict):
        return dict(record)

    interval = sleep.get("interval") if isinstance(sleep.get("interval"), dict) else {}
    summary = sleep.get("summary") if isinstance(sleep.get("summary"), dict) else {}

    session_id = _data_point_id(record) or sleep.get("id") or interval.get("startTime")
    start_time = interval.get("startTime")
    end_time = interval.get("endTime")
    minutes_period = _to_int(summary.get("minutesInSleepPeriod"))
    minutes_asleep = _to_int(summary.get("minutesAsleep"))
    duration_ms = 0
    if minutes_period is not None:
        duration_ms = minutes_period * 60_000
    elif start_time and end_time:
        duration_ms = _duration_ms(str(start_time), str(end_time))
    elif minutes_asleep is not None:
        duration_ms = minutes_asleep * 60_000

    efficiency: int | None = None
    if minutes_asleep is not None and minutes_period:
        efficiency = round((minutes_asleep / minutes_period) * 100)

    normalized: dict[str, Any] = dict(sleep)
    if session_id:
        normalized["session_id"] = str(session_id)
    if start_time:
        normalized["startTime"] = start_time
    if end_time:
        normalized["endTime"] = end_time
    normalized["durationMillis"] = duration_ms
    if efficiency is not None:
        normalized["efficiency"] = efficiency

    stages = _stage_summary(summary.get("stagesSummary"))
    if stages:
        normalized["stages"] = stages
    return normalized


def _build_activity_records(
    steps_data: dict[str, Any],
    active_minutes_data: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge steps and active-minutes daily rollups into activity records by date."""
    by_date: dict[str, dict[str, Any]] = {}

    for record in _extract_records(steps_data):
        record_date = _rollup_record_date(record)
        if not record_date:
            continue
        value = _to_int((record.get("steps") or {}).get("countSum"))
        if value is None:
            continue
        by_date.setdefault(record_date, {"date": record_date})
        by_date[record_date]["steps"] = value
        by_date[record_date]["value"] = value

    for record in _extract_records(active_minutes_data):
        record_date = _rollup_record_date(record)
        if not record_date:
            continue
        active = record.get("activeMinutes")
        if not isinstance(active, dict):
            continue
        total = 0
        for item in active.get("activeMinutesRollupByActivityLevel") or []:
            if isinstance(item, dict):
                total += _to_int(item.get("activeMinutes")) or 0
        by_date.setdefault(record_date, {"date": record_date})
        by_date[record_date]["activeMinutes"] = total
        by_date[record_date]["active_minutes"] = total

    return [by_date[key] for key in sorted(by_date)]


def _record_identity(bundle: ResourceBundle, record: dict[str, Any]) -> str | None:
    """Return the stable identifier for a record, or ``None`` if unavailable.

    For sleep sessions this is the ``session_id``. For daily summaries it
    is the date portion of the ``start_time`` / ``date`` field, normalised
    to YYYY-MM-DD.
    """
    if bundle.category == "sleep":
        session_id = record.get("session_id") or record.get("id")
        if session_id:
            return str(session_id)
        # Fallback: synthesise from startTime so idempotency holds.
        start = record.get("startTime") or record.get("start_time")
        return str(start) if start else None

    # Daily summary: key on the date the record applies to.
    for key in ("date", "startTime", "start_time"):
        raw = record.get(key)
        if not raw:
            continue
        raw_str = str(raw)
        if "T" in raw_str:
            return raw_str.split("T", 1)[0]
        return raw_str[:10]
    return None


def _rollup_record_date(record: dict[str, Any]) -> str | None:
    for key in ("civilStartTime", "startTime", "date"):
        value = record.get(key)
        formatted = _format_date_value(value)
        if formatted:
            return formatted
    return None


def _format_date_value(value: Any) -> str | None:
    if isinstance(value, str):
        return value.split("T", 1)[0]
    if isinstance(value, dict):
        date_part = value.get("date") if "date" in value else value
        if isinstance(date_part, dict):
            year = date_part.get("year")
            month = date_part.get("month")
            day = date_part.get("day")
            if year and month and day:
                return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    return None


def _date_message(value: date) -> dict[str, int]:
    return {"year": value.year, "month": value.month, "day": value.day}


def _data_point_id(record: dict[str, Any]) -> str | None:
    name = record.get("name")
    if not isinstance(name, str) or "/dataPoints/" not in name:
        return None
    return name.rsplit("/dataPoints/", 1)[-1] or None


def _duration_ms(start: str, end: str) -> int:
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((end_dt - start_dt).total_seconds() * 1000))


def _stage_summary(value: Any) -> dict[str, int]:
    if not isinstance(value, list):
        return {}
    stages: dict[str, int] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        stage_type = item.get("type") or item.get("stage")
        if not stage_type:
            continue
        minutes = _to_int(item.get("minutes"))
        if minutes is None:
            minutes = _duration_string_to_minutes(item.get("totalDuration"))
        if minutes is not None:
            stages[str(stage_type).lower()] = minutes
    return stages


def _duration_string_to_minutes(value: Any) -> int | None:
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return round(float(value[:-1]) / 60)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_google_health_connector() -> None:
    """Main async entry point for the Google Health connector."""
    configure_logging()
    logger.info("Google Health connector starting")

    config = GoogleHealthConnectorConfig.from_env()

    import asyncpg

    db_params = db_params_from_env()
    shared_db_name = shared_db_name_from_env()
    shared_schema = os.environ.get("BUTLER_SHARED_DB_SCHEMA", "public")
    local_db_name = os.environ.get("CONNECTOR_BUTLER_DB_NAME", "butlers").strip() or "butlers"

    shared_pool: asyncpg.Pool | None = None
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
            shared_pool = await asyncpg.create_pool(**pool_kwargs)
        except Exception as exc:  # noqa: BLE001
            if should_retry_with_ssl_disable(exc, pool_kwargs.get("ssl")):
                pool_kwargs["ssl"] = "disable"
                shared_pool = await asyncpg.create_pool(**pool_kwargs)
            else:
                raise

        logger.info("Google Health connector: shared pool established (db=%s)", shared_db_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Google Health connector: shared pool failed (degraded mode): %s", exc)
        shared_pool = None

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
        logger.info("Google Health connector: cursor pool established (db=%s)", local_db_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Google Health connector: cursor pool failed (checkpoint persistence disabled): %s",
            exc,
        )
        cursor_pool = None

    connector = GoogleHealthConnector(
        config=config,
        shared_pool=shared_pool,
        cursor_pool=cursor_pool,
    )

    try:
        await connector.start()
    finally:
        if cursor_pool is not None:
            await cursor_pool.close()
        if shared_pool is not None:
            await shared_pool.close()


def main() -> None:
    """Synchronous entry point used by ``python -m butlers.connectors.google_health``."""
    asyncio.run(run_google_health_connector())


if __name__ == "__main__":
    main()
