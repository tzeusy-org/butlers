"""Model catalog CRUD and per-butler override endpoints.

Provides:

- ``catalog_router`` — catalog management at ``/api/settings/models``
- ``butler_model_router`` — per-butler override endpoints at
  ``/api/butlers/{name}/model-overrides`` and ``/api/butlers/{name}/resolve-model``

All reads query ``public.model_catalog`` and ``public.butler_model_overrides``
directly via the shared credential pool.  Writes mutate those tables directly
(the catalog is managed via the API, not via butler MCP tools).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import butlers.api.routers.audit as audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_pricing
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.core.model_routing import (
    RoutingScore,
    get_breaker_states,
    get_routing_scores,
    resolve_model,
)
from butlers.core.pricing import ModelPricing, PricingConfig, PricingTier, TieredModelPricing
from butlers.core.runtime_probe_control.activation import probe_client
from butlers.core.runtime_probe_control.coordinator import HTTP_STATUS, ProbeResult, ProbeStatus
from butlers.metrics_registry import get_or_create_counter

logger = logging.getLogger(__name__)

catalog_router = APIRouter(prefix="/api/settings/models", tags=["model-catalog"])
pricing_router = APIRouter(prefix="/api/settings", tags=["pricing"])
butler_model_router = APIRouter(prefix="/api/butlers", tags=["butlers", "model-overrides"])
dispatch_router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])

_COMPLEXITY_TIERS = ("reasoning", "workhorse", "cheap", "specialty", "local", "legacy")

# Rate-limit sentinel for POST /verify-all: seconds since epoch of last accepted run.
# Process-global; resets on daemon restart. Sufficient for once-per-minute gate.
_verify_all_last_run: float = 0.0
_VERIFY_ALL_MIN_INTERVAL_S = 60.0
_VERIFY_ALL_CONCURRENCY = 8
# This flag is checked and set before the first await in the route, so a
# concurrent request is rejected immediately instead of waiting behind a
# long-running verification sweep.
_verify_all_in_flight = False

model_attention_operator_total = get_or_create_counter(
    "model_attention_operator_total",
    "Sanitized Models runtime-attention observation and manual-reissue outcomes.",
    labelnames=["operation", "outcome"],
)


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ModelCatalogEntry(BaseModel):
    """A single entry in the shared model catalog."""

    id: UUID
    alias: str
    runtime_type: str
    model_id: str
    extra_args: list[str] = Field(default_factory=list)
    complexity_tier: str
    enabled: bool = True
    priority: int = 0
    session_timeout_s: int = Field(default=1800, gt=0)
    # Token usage + limits (populated by list endpoint CTE aggregation)
    usage_24h: int = 0
    usage_30d: int = 0
    limit_24h: int | None = None
    limit_30d: int | None = None
    # Verification fields (added by core_093 migration)
    last_verified_at: datetime | None = None
    last_verified_latency_ms: int | None = None
    last_verified_ok: bool | None = None
    # Stored verification error text (core_167); None when never verified or
    # the last verification succeeded.
    last_verified_error: str | None = None
    # Dispatch-outcome circuit breaker state (bu-hmdqz.2), fully derived from
    # public.model_dispatch_attempts — see model_routing.get_breaker_states.
    # breaker_open=True is the routing consequence the Models tab surfaces:
    # this entry is currently excluded from resolution regardless of
    # last_verified_ok / enabled.
    breaker_open: bool = False
    breaker_consecutive_failures: int = 0
    # Evidence-based routing score (bu-ep4ks.13), fully derived from recent
    # public.model_dispatch_attempts rows — see model_routing.get_routing_scores.
    # routing_score is None (never a fabricated number) whenever
    # routing_score_insufficient_data is True; render "insufficient data",
    # not a 0 or an all-clear, when that flag is set.
    routing_score: float | None = None
    routing_score_insufficient_data: bool = True
    routing_score_reason: str | None = None
    routing_success_rate: float | None = None
    routing_p95_duration_ms: float | None = None
    routing_sample_count: int = 0


class ModelPriorityDelta(BaseModel):
    """Request body for PUT /api/settings/models/{id}/priority."""

    delta: int


class VerifyAllResult(BaseModel):
    """Response from POST /api/settings/models/verify-all.

    ``failed`` counts models that were probed and did not answer.  Models the
    control plane could not probe at all land in ``unavailable`` instead, and
    keep whatever verification evidence they already had --- an outage is not
    a verdict about a model.
    """

    accepted: bool
    total: int = 0
    ok: int = 0
    failed: int = 0
    unavailable: int = 0


class FailureEntry(BaseModel):
    """A single model failure record from GET /api/settings/models/{id}/failures."""

    ts: datetime
    error_code: str | None = None
    error_message: str | None = None
    butler: str | None = None
    session_id: str | None = None


class DispatchAttemptEntry(BaseModel):
    """A single attempt record from GET /api/settings/models/{id}/attempts.

    Tracks intermediate failover provenance: quota skips, runtime failures,
    suppressed failovers, exhaustion markers, and successful fallbacks.
    """

    ts: datetime
    butler: str
    outcome: str
    attempt_index: int
    failure_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    tool_call_count: int | None = None
    session_id: str | None = None
    logical_session_id: str | None = None
    duration_ms: int | None = None


class ModelCatalogCreate(BaseModel):
    """Request body for creating a catalog entry."""

    alias: str
    runtime_type: str
    model_id: str
    extra_args: list[str] = Field(default_factory=list)
    complexity_tier: str = "workhorse"
    enabled: bool = True
    priority: int = 0
    session_timeout_s: int = Field(default=1800, gt=0)


class ModelCatalogUpdate(BaseModel):
    """Request body for updating a catalog entry (all fields optional)."""

    alias: str | None = None
    runtime_type: str | None = None
    model_id: str | None = None
    extra_args: list[str] | None = None
    complexity_tier: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    session_timeout_s: int | None = Field(default=None, gt=0)


class ButlerModelOverride(BaseModel):
    """A single per-butler model override joined with catalog alias."""

    id: UUID
    butler_name: str
    catalog_entry_id: UUID
    alias: str
    enabled: bool
    priority: int | None = None
    complexity_tier: str | None = None


class ButlerModelOverrideUpsert(BaseModel):
    """One item in a batch upsert request for butler model overrides."""

    catalog_entry_id: UUID
    enabled: bool = True
    priority: int | None = None
    complexity_tier: str | None = None


class ResolveModelResponse(BaseModel):
    """Response from the resolve-model preview endpoint."""

    butler_name: str
    complexity: str
    runtime_type: str | None = None
    model_id: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    session_timeout_s: int | None = None
    resolved: bool
    # Quota fields (populated when resolved=True)
    quota_blocked: bool = False
    usage_24h: int = 0
    limit_24h: int | None = None
    usage_30d: int = 0
    limit_30d: int | None = None


class TokenLimitsRequest(BaseModel):
    """Request body for PUT /api/settings/models/{entry_id}/limits."""

    limit_24h: int | None = None
    limit_30d: int | None = None

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        if self.limit_24h is not None and self.limit_24h < 1:
            raise ValueError("limit_24h must be >= 1 when not null")
        if self.limit_30d is not None and self.limit_30d < 1:
            raise ValueError("limit_30d must be >= 1 when not null")


class TokenLimitsResponse(BaseModel):
    """Response for PUT /api/settings/models/{entry_id}/limits."""

    catalog_entry_id: UUID
    limit_24h: int | None = None
    limit_30d: int | None = None
    deleted: bool = False


class ResetUsageRequest(BaseModel):
    """Request body for POST /api/settings/models/{entry_id}/reset-usage."""

    window: str  # "24h" | "30d" | "both"

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        if self.window not in ("24h", "30d", "both"):
            raise ValueError("window must be '24h', '30d', or 'both'")


class TokenUsageResponse(BaseModel):
    """Response for GET /api/settings/models/{entry_id}/usage."""

    catalog_entry_id: UUID
    usage_24h: int = 0
    usage_30d: int = 0
    limit_24h: int | None = None
    limit_30d: int | None = None
    reset_24h_at: Any | None = None
    reset_30d_at: Any | None = None
    percent_24h: float | None = None
    percent_30d: float | None = None


class ModelTestResult(BaseModel):
    """Response from the model test endpoint.

    Carries no provider reply text: the probe runs on Switchboard and the
    control plane returns a verdict and a latency, never the model's words.
    """

    success: bool
    error: str | None = None
    duration_ms: int = 0


class ModelAttentionEpisode(BaseModel):
    """Content-blind lifecycle projection for one model attention episode."""

    episode_id: UUID
    lifecycle_state: str
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None = None
    safe_reason: str | None = None
    manual_reissue_of: UUID | None = None
    successor_id: UUID | None = None
    reissue_eligible: bool = False


class ModelAttentionObservation(BaseModel):
    """Batched attention source state, distinct from a proven empty result."""

    available: bool
    episodes: dict[UUID, ModelAttentionEpisode] = Field(default_factory=dict)


class ModelAttentionReissueResult(BaseModel):
    """The one atomic successor result returned for an original episode."""

    original_episode_id: UUID
    successor_episode_id: UUID
    successor_state: str
    created: bool


class ModelPricingResponse(BaseModel):
    """Per-model pricing entry (USD per 1M tokens).

    Cache fields are ``None`` when the model has no configured discount
    (cache reads/writes then bill at the full input rate).
    """

    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None
    cache_creation_per_million: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    """Read a mapping key with compatibility for asyncpg Record and plain dict."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _validate_complexity_tier(tier: str) -> None:
    """Raise HTTPException(422) if tier is not a valid complexity value."""
    if tier not in _COMPLEXITY_TIERS:
        valid = ", ".join(_COMPLEXITY_TIERS)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid complexity_tier '{tier}'. Must be one of: {valid}",
        )


def _coerce_extra_args(raw: Any) -> list[str]:
    """Safely coerce asyncpg JSONB result to list[str]."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _apply_routing_score(entry: ModelCatalogEntry, score: RoutingScore) -> None:
    """Merge a computed RoutingScore onto a ModelCatalogEntry in place.

    ``score.score`` is only ever ``None`` when ``insufficient_data`` is
    True — the fields are copied verbatim so a caller reading
    ``routing_score_insufficient_data`` never has to guess.
    """
    entry.routing_score = score.score
    entry.routing_score_insufficient_data = score.insufficient_data
    entry.routing_score_reason = score.reason
    entry.routing_success_rate = score.success_rate
    entry.routing_p95_duration_ms = score.latency_p95_ms
    entry.routing_sample_count = score.sample_count


def _row_to_catalog_entry(row: Any) -> ModelCatalogEntry:
    """Convert an asyncpg Record to a ModelCatalogEntry."""
    raw_usage_24h = _row_value(row, "usage_24h", 0)
    raw_usage_30d = _row_value(row, "usage_30d", 0)
    raw_limit_24h = _row_value(row, "limit_24h", None)
    raw_limit_30d = _row_value(row, "limit_30d", None)
    raw_latency = _row_value(row, "last_verified_latency_ms", None)
    return ModelCatalogEntry(
        id=row["id"],
        alias=row["alias"],
        runtime_type=row["runtime_type"],
        model_id=row["model_id"],
        extra_args=_coerce_extra_args(_row_value(row, "extra_args")),
        complexity_tier=row["complexity_tier"],
        enabled=bool(_row_value(row, "enabled", True)),
        priority=int(_row_value(row, "priority", 0)),
        session_timeout_s=int(_row_value(row, "session_timeout_s", 1800)),
        usage_24h=int(raw_usage_24h) if raw_usage_24h is not None else 0,
        usage_30d=int(raw_usage_30d) if raw_usage_30d is not None else 0,
        limit_24h=int(raw_limit_24h) if raw_limit_24h is not None else None,
        limit_30d=int(raw_limit_30d) if raw_limit_30d is not None else None,
        last_verified_at=_row_value(row, "last_verified_at", None),
        last_verified_latency_ms=int(raw_latency) if raw_latency is not None else None,
        last_verified_ok=_row_value(row, "last_verified_ok", None),
        last_verified_error=_row_value(row, "last_verified_error", None),
        breaker_open=bool(_row_value(row, "breaker_open", False)),
        breaker_consecutive_failures=int(_row_value(row, "breaker_consecutive_failures", 0) or 0),
    )


def _row_to_override(row: Any) -> ButlerModelOverride:
    """Convert an asyncpg Record to a ButlerModelOverride."""
    raw_priority = _row_value(row, "priority")
    priority = int(raw_priority) if raw_priority is not None else None
    return ButlerModelOverride(
        id=row["id"],
        butler_name=row["butler_name"],
        catalog_entry_id=row["catalog_entry_id"],
        alias=_row_value(row, "alias", ""),
        enabled=bool(_row_value(row, "enabled", True)),
        priority=priority,
        complexity_tier=_row_value(row, "complexity_tier"),
    )


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool, raising 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
        )


_ATTENTION_REASON_COPY = {
    ("pre_transport", "recipient_unavailable"): "Recipient unavailable before delivery",
    ("pre_transport", "policy_denied"): "Delivery policy denied the alert",
    ("transport_rejected", "provider_rejected"): "Transport rejected the alert",
    ("transport_uncertain", "transport_timeout"): "Delivery timed out; outcome is uncertain",
    (
        "transport_uncertain",
        "transport_connection_lost",
    ): "Delivery connection was lost; outcome is uncertain",
    ("transport_uncertain", "worker_recovery"): "A dead delivery claim was fenced as uncertain",
}


def _attention_safe_reason(row: Any) -> str | None:
    return _ATTENTION_REASON_COPY.get(
        (
            _row_value(row, "delivery_error_class"),
            _row_value(row, "delivery_error_detail"),
        )
    )


def _attention_episode(row: Any) -> ModelAttentionEpisode:
    successor_id = _row_value(row, "successor_id")
    state = str(row["lifecycle_state"])
    return ModelAttentionEpisode(
        episode_id=row["episode_id"],
        lifecycle_state=state,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        delivered_at=_row_value(row, "delivered_at"),
        safe_reason=_attention_safe_reason(row),
        manual_reissue_of=_row_value(row, "manual_reissue_of"),
        successor_id=successor_id,
        reissue_eligible=(
            state == "uncertain"
            and _row_value(row, "manual_reissue_of") is None
            and successor_id is None
        ),
    )


def _attention_metric(operation: str, outcome: str) -> None:
    """Best-effort categorical counter; never alter an operator outcome."""
    try:
        model_attention_operator_total.labels(operation=operation, outcome=outcome).inc()
    except Exception:
        logger.debug(
            "Models attention counter unavailable operation=%s outcome=%s",
            operation,
            outcome,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# GET /api/settings/models — list catalog entries
# ---------------------------------------------------------------------------


@catalog_router.get("", response_model=ApiResponse[list[ModelCatalogEntry]])
async def list_catalog_entries(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ModelCatalogEntry]]:
    """Return all catalog entries ordered by complexity_tier, priority, alias.

    Usage aggregation is done via a single CTE across all catalog entries
    to avoid N+1 queries.
    """
    pool = _shared_pool(db)
    rows = await pool.fetch(
        """
        WITH usage_agg AS (
            SELECT
                mc.id AS catalog_entry_id,
                COALESCE(SUM(tul.input_tokens + tul.output_tokens)
                    FILTER (WHERE tul.recorded_at > GREATEST(
                        COALESCE(tl.reset_24h_at, '-infinity'::timestamptz),
                        now() - interval '24 hours'
                    )), 0) AS usage_24h,
                COALESCE(SUM(tul.input_tokens + tul.output_tokens)
                    FILTER (WHERE tul.recorded_at > GREATEST(
                        COALESCE(tl.reset_30d_at, '-infinity'::timestamptz),
                        now() - interval '30 days'
                    )), 0) AS usage_30d
            FROM public.model_catalog mc
            LEFT JOIN public.token_limits tl ON tl.catalog_entry_id = mc.id
            LEFT JOIN public.token_usage_ledger tul ON tul.catalog_entry_id = mc.id
                AND tul.recorded_at > now() - interval '30 days'
            GROUP BY mc.id, tl.reset_24h_at, tl.reset_30d_at
        )
        SELECT
            mc.id, mc.alias, mc.runtime_type, mc.model_id, mc.extra_args,
            mc.complexity_tier, mc.enabled, mc.priority, mc.session_timeout_s,
            mc.last_verified_at, mc.last_verified_latency_ms, mc.last_verified_ok,
            mc.last_verified_error,
            COALESCE(ua.usage_24h, 0) AS usage_24h,
            COALESCE(ua.usage_30d, 0) AS usage_30d,
            tl.limit_24h,
            tl.limit_30d
        FROM public.model_catalog mc
        LEFT JOIN usage_agg ua ON ua.catalog_entry_id = mc.id
        LEFT JOIN public.token_limits tl ON tl.catalog_entry_id = mc.id
        ORDER BY
            CASE mc.complexity_tier
                WHEN 'reasoning' THEN 1
                WHEN 'workhorse' THEN 2
                WHEN 'cheap'     THEN 3
                WHEN 'specialty' THEN 4
                WHEN 'local'     THEN 5
                WHEN 'legacy'    THEN 6
                ELSE 7
            END,
            mc.priority DESC,
            mc.enabled DESC,
            mc.alias ASC
        """
    )
    # Breaker state (bu-hmdqz.2) and routing score (bu-ep4ks.13) are both fully
    # derived from public.model_dispatch_attempts, not model_catalog columns, so
    # each is batch-fetched separately and merged in Python — two extra round
    # trips for the whole list, not N+1 per row.
    breaker_states = await get_breaker_states(pool, [row["id"] for row in rows])
    routing_scores = await get_routing_scores(pool, [(row["id"], row["model_id"]) for row in rows])
    entries = []
    for row in rows:
        entry = _row_to_catalog_entry(row)
        state = breaker_states.get(row["id"])
        if state is not None:
            entry.breaker_open = state.open
            entry.breaker_consecutive_failures = state.consecutive_failures
        score = routing_scores.get(row["id"])
        if score is not None:
            _apply_routing_score(entry, score)
        entries.append(entry)
    return ApiResponse[list[ModelCatalogEntry]](data=entries)


# ---------------------------------------------------------------------------
# Owner-only runtime-attention observation and deliberate uncertain reissue
# ---------------------------------------------------------------------------


@catalog_router.get("/attention", response_model=ApiResponse[ModelAttentionObservation])
async def observe_model_attention(
    _owner: str = Depends(require_dashboard_owner_control),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelAttentionObservation]:
    """Return the latest sanitized episode for each model in one batch.

    Source failure is a successful response with ``available=false`` so the
    Models catalog can retain its independently available verification and
    breaker facts without inventing a no-alert state.
    """
    pool = _shared_pool(db)
    try:
        rows = await pool.fetch("SELECT * FROM public.observe_runtime_attention_models()")
    except Exception:
        logger.warning("Models runtime-attention observation unavailable")
        _attention_metric("observe", "unavailable")
        return ApiResponse[ModelAttentionObservation](
            data=ModelAttentionObservation(available=False)
        )

    episodes = {row["catalog_entry_id"]: _attention_episode(row) for row in rows}
    _attention_metric("observe", "present" if episodes else "empty")
    return ApiResponse[ModelAttentionObservation](
        data=ModelAttentionObservation(available=True, episodes=episodes)
    )


@catalog_router.post(
    "/attention/{episode_id}/reissue",
    response_model=ApiResponse[ModelAttentionReissueResult],
)
async def reissue_model_attention(
    episode_id: UUID,
    _owner: str = Depends(require_dashboard_owner_control),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelAttentionReissueResult]:
    """Create or return the one pending successor for an uncertain episode."""
    pool = _shared_pool(db)
    try:
        row = await pool.fetchrow(
            "SELECT * FROM public.reissue_runtime_attention_episode($1)", episode_id
        )
    except asyncpg.PostgresError as exc:
        if exc.sqlstate in {"55000", "23514"}:
            _attention_metric("reissue", "ineligible")
            raise HTTPException(
                status_code=409, detail="Attention episode is not eligible for reissue"
            ) from None
        if exc.sqlstate == "P0002":
            _attention_metric("reissue", "not_found")
            raise HTTPException(status_code=404, detail="Attention episode not found") from None
        logger.warning("Models attention reissue database operation unavailable")
        _attention_metric("reissue", "unavailable")
        raise HTTPException(status_code=503, detail="Attention reissue is unavailable") from None
    except (asyncpg.InterfaceError, OSError, RuntimeError, TimeoutError):
        logger.warning("Models attention reissue database connection unavailable")
        _attention_metric("reissue", "unavailable")
        raise HTTPException(status_code=503, detail="Attention reissue is unavailable") from None

    if row is None:
        _attention_metric("reissue", "unavailable")
        raise HTTPException(status_code=503, detail="Attention reissue is unavailable")
    result = ModelAttentionReissueResult(**dict(row))
    _attention_metric("reissue", "created" if result.created else "existing")
    logger.info(
        "Models attention reissue completed outcome=%s",
        "created" if result.created else "existing",
    )
    return ApiResponse[ModelAttentionReissueResult](data=result)


# ---------------------------------------------------------------------------
# POST /api/settings/models — create catalog entry
# ---------------------------------------------------------------------------


@catalog_router.post("", response_model=ApiResponse[ModelCatalogEntry], status_code=201)
async def create_catalog_entry(
    body: ModelCatalogCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelCatalogEntry]:
    """Create a new catalog entry. Returns 409 on duplicate alias."""
    _validate_complexity_tier(body.complexity_tier)
    pool = _shared_pool(db)

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO public.model_catalog
                (
                    alias, runtime_type, model_id, extra_args, complexity_tier,
                    enabled, priority, session_timeout_s
                )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, alias, runtime_type, model_id, extra_args,
                      complexity_tier, enabled, priority, session_timeout_s
            """,
            body.alias,
            body.runtime_type,
            body.model_id,
            body.extra_args,
            body.complexity_tier,
            body.enabled,
            body.priority,
            body.session_timeout_s,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Catalog entry with alias '{body.alias}' already exists",
        )
    except Exception as exc:
        logger.error("Failed to create catalog entry: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create catalog entry")

    if row is None:
        raise HTTPException(status_code=500, detail="Insert returned no row")

    return ApiResponse[ModelCatalogEntry](data=_row_to_catalog_entry(row))


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{entry_id} — update catalog entry
# ---------------------------------------------------------------------------


@catalog_router.put("/{entry_id}", response_model=ApiResponse[ModelCatalogEntry])
async def update_catalog_entry(
    entry_id: UUID,
    body: ModelCatalogUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelCatalogEntry]:
    """Update a catalog entry by ID. Only provided fields are changed."""
    if body.complexity_tier is not None:
        _validate_complexity_tier(body.complexity_tier)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided to update")

    pool = _shared_pool(db)

    # Build SET clause dynamically
    set_parts = []
    params: list[Any] = []
    idx = 1

    for field, value in updates.items():
        if field == "extra_args":
            set_parts.append(f"extra_args = ${idx}")
            params.append(value)
        else:
            set_parts.append(f"{field} = ${idx}")
            params.append(value)
        idx += 1

    set_parts.append("updated_at = now()")
    params.append(entry_id)

    sql = (
        f"UPDATE public.model_catalog SET {', '.join(set_parts)} "
        f"WHERE id = ${idx} "
        "RETURNING id, alias, runtime_type, model_id, extra_args, "
        "complexity_tier, enabled, priority, session_timeout_s, "
        "last_verified_at, last_verified_latency_ms, last_verified_ok, last_verified_error"
    )

    try:
        row = await pool.fetchrow(sql, *params)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Catalog entry with alias '{updates.get('alias')}' already exists",
        )
    except Exception as exc:
        logger.error("Failed to update catalog entry %s: %s", entry_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update catalog entry")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    changed_fields = ", ".join(updates.keys())
    await audit.append(
        pool,
        "owner",
        "model.update",
        target=str(entry_id),
        note=changed_fields,
        result="success",
    )

    return ApiResponse[ModelCatalogEntry](data=_row_to_catalog_entry(row))


# ---------------------------------------------------------------------------
# DELETE /api/settings/models/{entry_id} — delete catalog entry with cascade
# ---------------------------------------------------------------------------


@catalog_router.delete("/{entry_id}", response_model=ApiResponse[dict])
async def delete_catalog_entry(
    entry_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Delete a catalog entry by ID. Cascades to butler_model_overrides."""
    pool = _shared_pool(db)
    result = await pool.execute(
        "DELETE FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    # asyncpg returns "DELETE N" where N is the row count
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    return ApiResponse[dict](data={"deleted": True, "id": str(entry_id)})


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{entry_id}/priority — adjust priority by delta
# ---------------------------------------------------------------------------


@catalog_router.put("/{entry_id}/priority", response_model=ApiResponse[ModelCatalogEntry])
async def update_model_priority(
    entry_id: UUID,
    body: ModelPriorityDelta,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelCatalogEntry]:
    """Adjust a model's priority by ``delta``.

    Stores ``max(0, current + delta)`` — priority is clamped at zero; no error is raised
    when clamping occurs.  On success, ``audit.append("model.priority", ...)`` is called.
    """
    pool = _shared_pool(db)

    row = await pool.fetchrow(
        """
        UPDATE public.model_catalog
           SET priority   = GREATEST(0, priority + $1),
               updated_at = now()
         WHERE id = $2
        RETURNING id, alias, runtime_type, model_id, extra_args,
                  complexity_tier, enabled, priority, session_timeout_s,
                  last_verified_at, last_verified_latency_ms, last_verified_ok,
                  last_verified_error
        """,
        body.delta,
        entry_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    await audit.append(
        pool,
        "owner",
        "model.priority",
        target=str(entry_id),
        note=str(body.delta),
        result="success",
    )

    return ApiResponse[ModelCatalogEntry](data=_row_to_catalog_entry(row))


# ---------------------------------------------------------------------------
# Shared verify-all core (bu-hmdqz.2) — used by both the manual POST endpoint
# and the hourly automated sweep (butlers.jobs.model_verify), so the two can
# never drift in what "verified" means or how the result is persisted.
#
# Since bu-0uqgo.11 neither of them builds a runtime adapter.  A verification
# is a signed request to Switchboard's private runtime-probe control plane,
# and the *coordinator* --- not this process --- writes the catalog's
# verification columns through the migration-owned definer function.  That is
# what makes REQ-core-credentials-002's central promise checkable: a Dashboard
# that holds the private signer holds no runtime child, and a control-plane
# outage cannot write a model failure, because this module has no way to write
# one at all.
# ---------------------------------------------------------------------------

#: The one caller class the dashboard's owner-initiated paths sign as.  The
#: hourly sweep signs as ``scheduler`` (``butlers.jobs.model_verify``); the
#: capability grammar accepts no other value.
DASHBOARD_CALLER = "dashboard"


def _probe_client(caller: str) -> Any:
    """The signed control client for *caller*.

    Indirected through a module-level function purely so a test can substitute
    a scripted client without reaching into the activation cache.
    """
    return probe_client(caller)


async def run_verify_all_models(
    pool: asyncpg.Pool,
    *,
    audit_actor: str = "owner",
    caller: str = DASHBOARD_CALLER,
) -> VerifyAllResult:
    """Probe every enabled model in parallel through the signed control plane.

    Bounded concurrency = ``_VERIFY_ALL_CONCURRENCY``, matching the
    coordinator's own global limit so a full sweep does not shed its own
    requests.  Each entry's outcome falls into exactly one bucket:

    * ``ok`` --- the probe ran and the model answered;
    * ``failed`` --- the probe ran and the model did not;
    * ``unavailable`` --- the control plane could not run a probe at all
      (no signer, no readiness, busy, timed out, replayed, or refused);
    Only the first two are evidence about a model, and only the coordinator
    writes them.  Everything else leaves the catalog's verification history
    exactly as it was, which is the difference between "this model is broken"
    and "we could not ask".  Calls ``audit.append("models.verify_all",
    actor=audit_actor)`` once per run so the trail distinguishes an
    owner-initiated verification from the hourly automated sweep.

    Does NOT enforce the manual endpoint's once-per-minute rate limit — that
    is a caller concern specific to the HTTP surface (``verify_all_models``
    below); the hourly job calls this directly on its own cadence.
    """
    rows = await pool.fetch(
        """
        SELECT id
        FROM public.model_catalog
        WHERE enabled = true
        """
    )

    if not rows:
        await audit.append(pool, audit_actor, "models.verify_all", result="success")
        return VerifyAllResult(accepted=True, total=0, ok=0, failed=0)

    client = _probe_client(caller)
    sem = asyncio.Semaphore(_VERIFY_ALL_CONCURRENCY)

    async def _verify_one(row: Any) -> ProbeResult:
        async with sem:
            return await client.probe(row["id"])

    results = await asyncio.gather(*[_verify_one(row) for row in rows])

    completed = [result for result in results if result.status is ProbeStatus.COMPLETED]
    ok_count = sum(1 for result in completed if result.ok)
    failed_count = len(completed) - ok_count
    unavailable_count = len(results) - len(completed)

    audit_result = "error" if failed_count else "success"
    audit_error = (
        f"{failed_count} of {len(results)} model verifications failed" if failed_count else None
    )
    audit_note = (
        f"{unavailable_count} verification(s) could not run: runtime-probe control unavailable"
        if unavailable_count
        else None
    )
    await audit.append(
        pool,
        audit_actor,
        "models.verify_all",
        result=audit_result,
        error=audit_error,
        note=audit_note,
    )

    return VerifyAllResult(
        accepted=True,
        total=len(results),
        ok=ok_count,
        failed=failed_count,
        unavailable=unavailable_count,
    )


# ---------------------------------------------------------------------------
# POST /api/settings/models/verify-all — parallel signed runtime-probe requests
# ---------------------------------------------------------------------------


@catalog_router.post("/verify-all", response_model=ApiResponse[VerifyAllResult])
async def verify_all_models(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[VerifyAllResult]:
    """Request a signed Switchboard runtime probe for every enabled model in parallel.

    Rate-limited to once per minute system-wide: subsequent calls within the
    window return HTTP 429. Delegates the actual verification work to
    ``run_verify_all_models`` (shared with the hourly automated sweep).
    Switchboard performs runtime construction and verification persistence; this
    route returns only the coordinator's summary.
    """
    global _verify_all_in_flight, _verify_all_last_run  # noqa: PLW0603

    now = time.monotonic()
    if _verify_all_in_flight or now - _verify_all_last_run < _VERIFY_ALL_MIN_INTERVAL_S:
        raise HTTPException(
            status_code=429,
            detail="verify-all was called recently — wait at least 60 seconds between runs",
        )

    # Admission is synchronous on the event loop, so no second request can
    # pass this check before the first one marks itself in flight.
    _verify_all_in_flight = True
    try:
        pool = _shared_pool(db)
        result = await run_verify_all_models(pool, audit_actor="owner")
        _verify_all_last_run = now
        return ApiResponse[VerifyAllResult](data=result)
    finally:
        _verify_all_in_flight = False


# ---------------------------------------------------------------------------
# GET /api/settings/models/{entry_id}/failures — recent failure tail
# ---------------------------------------------------------------------------


@catalog_router.get(
    "/{entry_id}/failures",
    response_model=PaginatedResponse[FailureEntry],
)
async def get_model_failures(
    entry_id: UUID,
    since: str = Query(
        "24h",
        description="Lookback window. Accepts '24h', '7d', '30d', or an ISO-8601 timestamp.",
    ),
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[FailureEntry]:
    """Return recent failure entries for a model catalog entry.

    Queries ``public.dispatch_failures`` (if present) ordered ``ts DESC``.
    When the table does not exist (migration not yet applied) returns an empty
    page rather than 503 — failure history is informational and not critical.
    """
    pool = _shared_pool(db)

    # Verify the catalog entry exists
    exists = await pool.fetchval(
        "SELECT 1 FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    # Resolve the ``since`` window to a UTC datetime
    since_ts: datetime
    _WINDOW_MAP = {
        "24h": "24 hours",
        "7d": "7 days",
        "30d": "30 days",
    }
    if since in _WINDOW_MAP:
        since_ts_row = await pool.fetchval(f"SELECT now() - interval '{_WINDOW_MAP[since]}'")
        since_ts = since_ts_row
    else:
        try:
            since_ts = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid 'since' value '{since}'. Use '24h', '7d', '30d', or ISO-8601.",
            )

    try:
        rows = await pool.fetch(
            """
            SELECT ts, error_code, error_message, butler, session_id
            FROM public.dispatch_failures
            WHERE catalog_entry_id = $1
              AND ts >= $2
            ORDER BY ts DESC
            LIMIT $3
            """,
            entry_id,
            since_ts,
            limit,
        )
        total = await pool.fetchval(
            "SELECT count(*) FROM public.dispatch_failures"
            " WHERE catalog_entry_id = $1 AND ts >= $2",
            entry_id,
            since_ts,
        )
    except asyncpg.exceptions.UndefinedTableError:
        # Table not yet migrated — return empty page gracefully
        return PaginatedResponse[FailureEntry](
            data=[],
            meta=PaginationMeta(total=0, offset=0, limit=limit),
        )

    entries = [
        FailureEntry(
            ts=row["ts"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            butler=row["butler"],
            session_id=str(row["session_id"]) if row["session_id"] else None,
        )
        for row in rows
    ]

    return PaginatedResponse[FailureEntry](
        data=entries,
        meta=PaginationMeta(total=int(total or 0), offset=0, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/settings/models/{entry_id}/attempts — failover attempt provenance
# ---------------------------------------------------------------------------


@catalog_router.get(
    "/{entry_id}/attempts",
    response_model=PaginatedResponse[DispatchAttemptEntry],
)
async def get_model_attempts(
    entry_id: UUID,
    since: str = Query(
        "24h",
        description="Lookback window. Accepts '24h', '7d', '30d', or an ISO-8601 timestamp.",
    ),
    limit: int = Query(50, ge=1, le=500, description="Max records to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[DispatchAttemptEntry]:
    """Return failover attempt provenance for a model catalog entry.

    Queries ``public.model_dispatch_attempts`` (if present) ordered ``ts DESC, id DESC``.
    Each row represents one attempt in a logical session: a quota skip, a runtime
    failure, a suppressed failover, a failover exhaustion marker, or a successful
    fallback.

    When the table does not exist (migration not yet applied) returns an empty
    page rather than 503 — attempt history is informational and not critical.
    """
    pool = _shared_pool(db)

    # Verify the catalog entry exists
    exists = await pool.fetchval(
        "SELECT 1 FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    # Resolve the ``since`` window to a UTC datetime
    since_ts: datetime
    _WINDOW_MAP = {
        "24h": "24 hours",
        "7d": "7 days",
        "30d": "30 days",
    }
    if since in _WINDOW_MAP:
        since_ts_row = await pool.fetchval(f"SELECT now() - interval '{_WINDOW_MAP[since]}'")
        since_ts = since_ts_row
    else:
        try:
            since_ts = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid 'since' value '{since}'. Use '24h', '7d', '30d', or ISO-8601.",
            )

    try:
        rows = await pool.fetch(
            """
            SELECT ts, butler, outcome, attempt_index,
                   failure_reason, error_code, error_message,
                   tool_call_count, session_id, logical_session_id, duration_ms
            FROM public.model_dispatch_attempts
            WHERE catalog_entry_id = $1
              AND ts >= $2
            ORDER BY ts DESC, id DESC
            LIMIT $3
            """,
            entry_id,
            since_ts,
            limit,
        )
        total = await pool.fetchval(
            "SELECT count(*) FROM public.model_dispatch_attempts"
            " WHERE catalog_entry_id = $1 AND ts >= $2",
            entry_id,
            since_ts,
        )
    except asyncpg.exceptions.UndefinedTableError:
        # Table not yet migrated — return empty page gracefully
        return PaginatedResponse[DispatchAttemptEntry](
            data=[],
            meta=PaginationMeta(total=0, offset=0, limit=limit),
        )

    entries = [
        DispatchAttemptEntry(
            ts=row["ts"],
            butler=row["butler"],
            outcome=row["outcome"],
            attempt_index=row["attempt_index"],
            failure_reason=row["failure_reason"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            tool_call_count=row["tool_call_count"],
            session_id=str(row["session_id"]) if row["session_id"] else None,
            logical_session_id=row["logical_session_id"],
            duration_ms=_row_value(row, "duration_ms"),
        )
        for row in rows
    ]

    return PaginatedResponse[DispatchAttemptEntry](
        data=entries,
        meta=PaginationMeta(total=int(total or 0), offset=0, limit=limit),
    )


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{entry_id}/limits — upsert token limits
# ---------------------------------------------------------------------------


@catalog_router.put("/{entry_id}/limits", response_model=ApiResponse[TokenLimitsResponse])
async def upsert_token_limits(
    entry_id: UUID,
    body: TokenLimitsRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TokenLimitsResponse]:
    """Set or update 24h/30d token limits for a catalog entry.

    Setting both limits to null deletes the token_limits row.
    Limit values must be >= 1 (null means unlimited for that window).
    """
    pool = _shared_pool(db)

    # Verify the catalog entry exists
    exists = await pool.fetchval(
        "SELECT 1 FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    # If both limits are null, delete the row
    if body.limit_24h is None and body.limit_30d is None:
        await pool.execute(
            "DELETE FROM public.token_limits WHERE catalog_entry_id = $1",
            entry_id,
        )
        return ApiResponse[TokenLimitsResponse](
            data=TokenLimitsResponse(
                catalog_entry_id=entry_id,
                limit_24h=None,
                limit_30d=None,
                deleted=True,
            )
        )

    # Upsert the limits row
    await pool.execute(
        """
        INSERT INTO public.token_limits (catalog_entry_id, limit_24h, limit_30d)
        VALUES ($1, $2, $3)
        ON CONFLICT (catalog_entry_id) DO UPDATE
            SET limit_24h  = EXCLUDED.limit_24h,
                limit_30d  = EXCLUDED.limit_30d,
                updated_at = now()
        """,
        entry_id,
        body.limit_24h,
        body.limit_30d,
    )

    return ApiResponse[TokenLimitsResponse](
        data=TokenLimitsResponse(
            catalog_entry_id=entry_id,
            limit_24h=body.limit_24h,
            limit_30d=body.limit_30d,
            deleted=False,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/settings/models/{entry_id}/reset-usage — reset usage window(s)
# ---------------------------------------------------------------------------


@catalog_router.post("/{entry_id}/reset-usage", response_model=ApiResponse[dict])
async def reset_token_usage(
    entry_id: UUID,
    body: ResetUsageRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Reset the 24h, 30d, or both usage windows for a catalog entry.

    Creates the token_limits row (with null limits) if it does not exist.
    Sets the appropriate reset_*_at to now().
    """
    pool = _shared_pool(db)

    # Verify the catalog entry exists
    exists = await pool.fetchval(
        "SELECT 1 FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    if body.window == "24h":
        sql = """
            INSERT INTO public.token_limits (catalog_entry_id, reset_24h_at)
            VALUES ($1, now())
            ON CONFLICT (catalog_entry_id) DO UPDATE
                SET reset_24h_at = now(),
                    updated_at   = now()
        """
    elif body.window == "30d":
        sql = """
            INSERT INTO public.token_limits (catalog_entry_id, reset_30d_at)
            VALUES ($1, now())
            ON CONFLICT (catalog_entry_id) DO UPDATE
                SET reset_30d_at = now(),
                    updated_at   = now()
        """
    else:  # "both"
        sql = """
            INSERT INTO public.token_limits (catalog_entry_id, reset_24h_at, reset_30d_at)
            VALUES ($1, now(), now())
            ON CONFLICT (catalog_entry_id) DO UPDATE
                SET reset_24h_at = now(),
                    reset_30d_at = now(),
                    updated_at   = now()
        """

    await pool.execute(sql, entry_id)

    return ApiResponse[dict](
        data={"catalog_entry_id": str(entry_id), "window": body.window, "reset": True}
    )


# ---------------------------------------------------------------------------
# GET /api/settings/models/{entry_id}/usage — detailed usage for single entry
# ---------------------------------------------------------------------------


@catalog_router.get("/{entry_id}/usage", response_model=ApiResponse[TokenUsageResponse])
async def get_token_usage(
    entry_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TokenUsageResponse]:
    """Return detailed token usage for a single catalog entry.

    Includes actual usage aggregation (respecting reset markers), configured
    limits, and percentage fields (null when no limit is set).
    """
    pool = _shared_pool(db)

    # Verify the catalog entry exists
    exists = await pool.fetchval(
        "SELECT 1 FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    row = await pool.fetchrow(
        """
        WITH limits AS (
            SELECT
                limit_24h,
                limit_30d,
                reset_24h_at,
                reset_30d_at,
                COALESCE(reset_24h_at, '-infinity'::timestamptz) AS eff_reset_24h,
                COALESCE(reset_30d_at, '-infinity'::timestamptz) AS eff_reset_30d
            FROM public.token_limits
            WHERE catalog_entry_id = $1
        ),
        usage AS (
            SELECT
                COALESCE(SUM(input_tokens + output_tokens)
                    FILTER (WHERE recorded_at > GREATEST(
                        (SELECT eff_reset_24h FROM limits),
                        now() - interval '24 hours'
                    )), 0) AS usage_24h,
                COALESCE(SUM(input_tokens + output_tokens)
                    FILTER (WHERE recorded_at > GREATEST(
                        (SELECT eff_reset_30d FROM limits),
                        now() - interval '30 days'
                    )), 0) AS usage_30d
            FROM public.token_usage_ledger
            WHERE catalog_entry_id = $1
              AND recorded_at > now() - interval '30 days'
        )
        SELECT
            (SELECT limit_24h FROM limits)                    AS limit_24h,
            (SELECT limit_30d FROM limits)                    AS limit_30d,
            (SELECT reset_24h_at FROM limits)                 AS reset_24h_at,
            (SELECT reset_30d_at FROM limits)                 AS reset_30d_at,
            COALESCE((SELECT usage_24h FROM usage), 0)        AS usage_24h,
            COALESCE((SELECT usage_30d FROM usage), 0)        AS usage_30d
        """,
        entry_id,
    )

    if row is None:
        # No limits row and no usage — return zeros
        return ApiResponse[TokenUsageResponse](
            data=TokenUsageResponse(
                catalog_entry_id=entry_id,
            )
        )

    usage_24h = int(row["usage_24h"]) if row["usage_24h"] is not None else 0
    usage_30d = int(row["usage_30d"]) if row["usage_30d"] is not None else 0
    limit_24h = int(row["limit_24h"]) if row["limit_24h"] is not None else None
    limit_30d = int(row["limit_30d"]) if row["limit_30d"] is not None else None

    percent_24h = (usage_24h / limit_24h * 100.0) if limit_24h is not None else None
    percent_30d = (usage_30d / limit_30d * 100.0) if limit_30d is not None else None

    return ApiResponse[TokenUsageResponse](
        data=TokenUsageResponse(
            catalog_entry_id=entry_id,
            usage_24h=usage_24h,
            usage_30d=usage_30d,
            limit_24h=limit_24h,
            limit_30d=limit_30d,
            reset_24h_at=row["reset_24h_at"],
            reset_30d_at=row["reset_30d_at"],
            percent_24h=percent_24h,
            percent_30d=percent_30d,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/model-overrides — list overrides
# ---------------------------------------------------------------------------


@butler_model_router.get(
    "/{name}/model-overrides",
    response_model=ApiResponse[list[ButlerModelOverride]],
)
async def list_butler_model_overrides(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerModelOverride]]:
    """Return all model overrides for a butler, joined with catalog alias."""
    pool = _shared_pool(db)
    rows = await pool.fetch(
        """
        SELECT bmo.id, bmo.butler_name, bmo.catalog_entry_id,
               mc.alias, bmo.enabled, bmo.priority, bmo.complexity_tier
        FROM public.butler_model_overrides bmo
        JOIN public.model_catalog mc ON mc.id = bmo.catalog_entry_id
        WHERE bmo.butler_name = $1
        ORDER BY mc.alias ASC
        """,
        name,
    )
    overrides = [_row_to_override(row) for row in rows]
    return ApiResponse[list[ButlerModelOverride]](data=overrides)


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/model-overrides — batch upsert overrides
# ---------------------------------------------------------------------------


@butler_model_router.put(
    "/{name}/model-overrides",
    response_model=ApiResponse[list[ButlerModelOverride]],
)
async def upsert_butler_model_overrides(
    name: str,
    body: list[ButlerModelOverrideUpsert],
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerModelOverride]]:
    """Batch upsert model overrides for a butler.

    Each item is upserted by (butler_name, catalog_entry_id).
    Returns the full list of updated override rows.
    """
    if not body:
        raise HTTPException(status_code=422, detail="Request body must contain at least one item")

    for item in body:
        if item.complexity_tier is not None:
            _validate_complexity_tier(item.complexity_tier)

    pool = _shared_pool(db)

    upserted_ids: list[UUID] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in body:
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.butler_model_overrides
                        (butler_name, catalog_entry_id, enabled, priority, complexity_tier)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (butler_name, catalog_entry_id) DO UPDATE
                        SET enabled = EXCLUDED.enabled,
                            priority = EXCLUDED.priority,
                            complexity_tier = EXCLUDED.complexity_tier
                    RETURNING id
                    """,
                    name,
                    item.catalog_entry_id,
                    item.enabled,
                    item.priority,
                    item.complexity_tier,
                )
                if row is not None:
                    upserted_ids.append(row["id"])

    if not upserted_ids:
        return ApiResponse[list[ButlerModelOverride]](data=[])

    rows = await pool.fetch(
        """
        SELECT bmo.id, bmo.butler_name, bmo.catalog_entry_id,
               mc.alias, bmo.enabled, bmo.priority, bmo.complexity_tier
        FROM public.butler_model_overrides bmo
        JOIN public.model_catalog mc ON mc.id = bmo.catalog_entry_id
        WHERE bmo.id = ANY($1::uuid[])
        ORDER BY mc.alias ASC
        """,
        upserted_ids,
    )
    return ApiResponse[list[ButlerModelOverride]](data=[_row_to_override(r) for r in rows])


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/model-overrides/{override_id}
# ---------------------------------------------------------------------------


@butler_model_router.delete(
    "/{name}/model-overrides/{override_id}",
    response_model=ApiResponse[dict],
)
async def delete_butler_model_override(
    name: str,
    override_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a single butler model override by ID."""
    pool = _shared_pool(db)
    result = await pool.execute(
        "DELETE FROM public.butler_model_overrides WHERE id = $1 AND butler_name = $2",
        override_id,
        name,
    )
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Override not found: {override_id} for butler '{name}'",
        )
    return ApiResponse[dict](data={"deleted": True, "id": str(override_id)})


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/resolve-model?complexity=X — preview endpoint
# ---------------------------------------------------------------------------


@butler_model_router.get(
    "/{name}/resolve-model",
    response_model=ApiResponse[ResolveModelResponse],
)
async def resolve_model_preview(
    name: str,
    complexity: str = Query(default="workhorse", description="Complexity tier to resolve"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ResolveModelResponse]:
    """Preview which model would be selected for a butler + complexity tier.

    Calls the shared ``resolve_model()`` function (same evidence-based /
    round-robin selection the spawner uses, see model_routing.resolve_model).
    Also returns actual quota status by querying the ledger directly (does
    not use the check_token_quota fast-path that returns zeroes for
    unlimited entries).
    """
    _validate_complexity_tier(complexity)
    pool = _shared_pool(db)

    result = await resolve_model(pool, name, complexity)

    if result is None:
        return ApiResponse[ResolveModelResponse](
            data=ResolveModelResponse(
                butler_name=name,
                complexity=complexity,
                resolved=False,
            )
        )

    runtime_type, model_id, extra_args, catalog_entry_id, session_timeout_s = result

    # Query actual usage from ledger (always, even for unlimited entries).
    # Only possible when we have a catalog_entry_id from the row.
    quota_row = None
    if catalog_entry_id is not None:
        quota_row = await pool.fetchrow(
            """
            WITH limits AS (
                SELECT
                    limit_24h,
                    limit_30d,
                    COALESCE(reset_24h_at, '-infinity'::timestamptz) AS eff_reset_24h,
                    COALESCE(reset_30d_at, '-infinity'::timestamptz) AS eff_reset_30d
                FROM public.token_limits
                WHERE catalog_entry_id = $1
            ),
            usage AS (
                SELECT
                    COALESCE(SUM(input_tokens + output_tokens)
                        FILTER (WHERE recorded_at > GREATEST(
                            (SELECT eff_reset_24h FROM limits),
                            now() - interval '24 hours'
                        )), 0) AS usage_24h,
                    COALESCE(SUM(input_tokens + output_tokens)
                        FILTER (WHERE recorded_at > GREATEST(
                            (SELECT eff_reset_30d FROM limits),
                            now() - interval '30 days'
                        )), 0) AS usage_30d
                FROM public.token_usage_ledger
                WHERE catalog_entry_id = $1
                  AND recorded_at > now() - interval '30 days'
            )
            SELECT
                COALESCE((SELECT limit_24h FROM limits), NULL) AS limit_24h,
                COALESCE((SELECT limit_30d FROM limits), NULL) AS limit_30d,
                COALESCE((SELECT usage_24h FROM usage), 0)    AS usage_24h,
                COALESCE((SELECT usage_30d FROM usage), 0)    AS usage_30d
            """,
            catalog_entry_id,
        )

    usage_24h = 0
    usage_30d = 0
    limit_24h = None
    limit_30d = None
    quota_blocked = False

    if quota_row is not None:
        usage_24h = int(quota_row["usage_24h"]) if quota_row["usage_24h"] is not None else 0
        usage_30d = int(quota_row["usage_30d"]) if quota_row["usage_30d"] is not None else 0
        raw_lim_24h = quota_row["limit_24h"]
        raw_lim_30d = quota_row["limit_30d"]
        limit_24h = int(raw_lim_24h) if raw_lim_24h is not None else None
        limit_30d = int(raw_lim_30d) if raw_lim_30d is not None else None
        if (limit_24h is not None and usage_24h >= limit_24h) or (
            limit_30d is not None and usage_30d >= limit_30d
        ):
            quota_blocked = True

    return ApiResponse[ResolveModelResponse](
        data=ResolveModelResponse(
            butler_name=name,
            complexity=complexity,
            runtime_type=runtime_type,
            model_id=model_id,
            extra_args=extra_args,
            session_timeout_s=session_timeout_s,
            resolved=True,
            quota_blocked=quota_blocked,
            usage_24h=usage_24h,
            limit_24h=limit_24h,
            usage_30d=usage_30d,
            limit_30d=limit_30d,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/settings/models/{entry_id}/test — test a model config
# ---------------------------------------------------------------------------

#: Every control outcome that is not a completed probe keeps its own HTTP
#: status on this route, reusing the control plane's own mapping so the two
#: cannot drift.  Collapsing any of them into a model failure would show an
#: outage to the operator as a broken model --- and, worse, a 200 with
#: ``success: false`` would read as verified evidence that this route never
#: gathered.
_TEST_UNAVAILABLE_DETAIL = {
    ProbeStatus.UNAUTHORIZED: "runtime-probe control rejected this request",
    ProbeStatus.REPLAY: "this verification capability was already used",
    ProbeStatus.BUSY: "runtime-probe control is at capacity — retry shortly",
    ProbeStatus.UNAVAILABLE: "runtime-probe control is unavailable",
    ProbeStatus.TIMEOUT: "the model did not answer within the probe deadline",
}


@catalog_router.post(
    "/{entry_id}/test",
    response_model=ApiResponse[ModelTestResult],
)
async def test_catalog_entry(
    entry_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelTestResult]:
    """Probe one catalog entry through the signed runtime-probe control plane.

    The probe is a one-turn completion with no MCP servers, run by Switchboard
    inside the shared runtime home.  This process builds no adapter, spawns no
    child, and writes no verification evidence; it only asks, and reports what
    came back.

    Provider text never crosses the control plane, so a successful probe
    returns latency rather than the model's words.  That is deliberate: a raw
    provider reply in an API payload is exactly the disclosure this plane was
    built to remove.
    """
    pool = _shared_pool(db)
    exists = await pool.fetchval(
        "SELECT 1 FROM public.model_catalog WHERE id = $1",
        entry_id,
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    result = await _probe_client(DASHBOARD_CALLER).probe(entry_id)

    if result.status is not ProbeStatus.COMPLETED:
        raise HTTPException(
            status_code=HTTP_STATUS[result.status],
            detail=_TEST_UNAVAILABLE_DETAIL[result.status],
        )

    duration_ms = result.latency_ms or 0
    if result.ok:
        return ApiResponse[ModelTestResult](
            data=ModelTestResult(success=True, duration_ms=duration_ms)
        )
    return ApiResponse[ModelTestResult](
        data=ModelTestResult(
            success=False,
            error="the model did not complete the verification probe",
            duration_ms=duration_ms,
        )
    )


# ---------------------------------------------------------------------------
# GET /api/settings/pricing — per-model pricing (USD per 1M tokens)
# ---------------------------------------------------------------------------


@pricing_router.get("/pricing", response_model=ApiResponse[dict[str, ModelPricingResponse]])
async def get_model_pricing(
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[dict[str, ModelPricingResponse]]:
    """Return a flat dict mapping model_id to per-million-token prices.

    For tiered models, returns the base tier (context_threshold=0).
    """
    result: dict[str, ModelPricingResponse] = {}
    for model_id in pricing.model_ids:
        entry = pricing.get_model_pricing(model_id)
        if entry is None:
            continue

        rates: ModelPricing | PricingTier
        if isinstance(entry, TieredModelPricing):
            # Use the lowest tier (context_threshold=0)
            rates = entry.tiers[0]
        elif isinstance(entry, ModelPricing):
            rates = entry
        else:
            continue

        result[model_id] = ModelPricingResponse(
            input_per_million=rates.input_price_per_token * 1_000_000,
            output_per_million=rates.output_price_per_token * 1_000_000,
            cached_input_per_million=(
                rates.cached_input_price_per_token * 1_000_000
                if rates.cached_input_price_per_token is not None
                else None
            ),
            cache_creation_per_million=(
                rates.cache_creation_price_per_token * 1_000_000
                if rates.cache_creation_price_per_token is not None
                else None
            ),
        )

    return ApiResponse[dict[str, ModelPricingResponse]](data=result)


# ---------------------------------------------------------------------------
# GET /api/dispatch/attempts — query failover provenance by session or
#                              logical_session_id (cross-model view)
# ---------------------------------------------------------------------------


@dispatch_router.get("/attempts", response_model=PaginatedResponse[DispatchAttemptEntry])
async def get_dispatch_attempts(
    session_id: UUID | None = Query(
        None,
        description=(
            "Filter by the UUID of the session row. Returns all attempt rows tied to this session."
        ),
    ),
    logical_session_id: str | None = Query(
        None,
        description=(
            "Filter by logical_session_id. "
            "Groups all attempts (quota-skip, failure, success) for one logical dispatch "
            "cycle, even when multiple session rows exist."
        ),
    ),
    outcome: str | None = Query(
        None,
        description=(
            "Fleet-wide mode (bu-7o89u.3): filter by outcome (e.g. 'quota_skip') across ALL "
            "sessions instead of one session/logical_session_id. Mutually exclusive with "
            "session_id/logical_session_id. Powers the /spend fleet-halt state, which needs "
            "'recent denied dispatches' without knowing a session id up front."
        ),
    ),
    reason_prefix: str | None = Query(
        None,
        description=(
            "Only valid with 'outcome'. Narrows to rows whose failure_reason starts with this "
            "prefix, e.g. 'Monthly spend ceiling reached' to isolate ceiling hard-blocks from "
            "routine same-tier quota_skip failovers (both share outcome='quota_skip')."
        ),
    ),
    since: datetime | None = Query(
        None,
        description="Only valid with 'outcome'. Restricts to rows with ts >= this timestamp.",
    ),
    order: str = Query(
        "desc",
        pattern="^(asc|desc)$",
        description="Only valid with 'outcome'. Sort direction for ts ('asc' or 'desc').",
    ),
    limit: int = Query(100, ge=1, le=500, description="Max records to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[DispatchAttemptEntry]:
    """Return failover attempt provenance rows.

    Two mutually exclusive query modes:

    - **Session mode** (``session_id`` and/or ``logical_session_id``): rows for one
      logical dispatch cycle, ordered by ``attempt_index ASC`` so callers can
      reconstruct the ordered sequence of candidates.
    - **Fleet mode** (``outcome``, optionally narrowed by ``reason_prefix``/``since``):
      rows across ALL sessions matching the outcome, ordered by ``ts`` (``order``,
      default ``desc``). Used by the dashboard to answer "how many dispatches has
      the fleet denied recently" without a session id in hand (bu-7o89u.3).

    Callers choose exactly one mode: session mode accepts ``session_id`` and/or
    ``logical_session_id``, while fleet mode requires ``outcome``. Fleet mode
    cannot be combined with either session selector.

    Each row answers one of these questions:
    - Which model was skipped before invocation (``quota_skip``)?
    - Which model failed with a failover-eligible error (``runtime_failure``)?
    - Why was failover suppressed (``suppressed``)?
    - Which model finally succeeded (``success``)?

    When the table does not exist (migration not yet applied) returns an empty
    page rather than 503.
    """
    if outcome is not None and (session_id is not None or logical_session_id is not None):
        raise HTTPException(
            status_code=422,
            detail=(
                "'outcome' fleet mode cannot be combined with 'session_id' or 'logical_session_id'."
            ),
        )

    if session_id is None and logical_session_id is None and outcome is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "At least one of 'session_id', 'logical_session_id', or 'outcome' must be provided."
            ),
        )

    pool = _shared_pool(db)

    try:
        if outcome is not None:
            order_sql = "ASC" if order == "asc" else "DESC"
            where_clauses = ["outcome = $1"]
            params: list[Any] = [outcome]
            if reason_prefix is not None:
                params.append(reason_prefix)
                # True prefix match, not a LIKE pattern -- reason_prefix is caller-supplied
                # (query param), and `%`/`_` are LIKE wildcards. `left(...) = $n` avoids
                # having to escape those characters and keeps the "prefix" contract exact.
                n = len(params)
                where_clauses.append(f"left(failure_reason, length(${n})) = ${n}")
            if since is not None:
                params.append(since)
                where_clauses.append(f"ts >= ${len(params)}")
            where_sql = " AND ".join(where_clauses)

            rows = await pool.fetch(
                f"""
                SELECT ts, butler, outcome, attempt_index,
                       failure_reason, error_code, error_message,
                       tool_call_count, session_id, logical_session_id, duration_ms
                FROM public.model_dispatch_attempts
                WHERE {where_sql}
                ORDER BY ts {order_sql}, id {order_sql}
                LIMIT ${len(params) + 1}
                """,
                *params,
                limit,
            )
            total = await pool.fetchval(
                f"SELECT count(*) FROM public.model_dispatch_attempts WHERE {where_sql}",
                *params,
            )
        elif session_id is not None and logical_session_id is not None:
            rows = await pool.fetch(
                """
                SELECT ts, butler, outcome, attempt_index,
                       failure_reason, error_code, error_message,
                       tool_call_count, session_id, logical_session_id, duration_ms
                FROM public.model_dispatch_attempts
                WHERE session_id = $1::uuid
                   OR logical_session_id = $2
                ORDER BY attempt_index ASC, ts ASC, id ASC
                LIMIT $3
                """,
                session_id,
                logical_session_id,
                limit,
            )
            total = await pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts"
                " WHERE session_id = $1::uuid OR logical_session_id = $2",
                session_id,
                logical_session_id,
            )
        elif session_id is not None:
            rows = await pool.fetch(
                """
                SELECT ts, butler, outcome, attempt_index,
                       failure_reason, error_code, error_message,
                       tool_call_count, session_id, logical_session_id, duration_ms
                FROM public.model_dispatch_attempts
                WHERE session_id = $1::uuid
                ORDER BY attempt_index ASC, ts ASC, id ASC
                LIMIT $2
                """,
                session_id,
                limit,
            )
            total = await pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE session_id = $1::uuid",
                session_id,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT ts, butler, outcome, attempt_index,
                       failure_reason, error_code, error_message,
                       tool_call_count, session_id, logical_session_id, duration_ms
                FROM public.model_dispatch_attempts
                WHERE logical_session_id = $1
                ORDER BY attempt_index ASC, ts ASC, id ASC
                LIMIT $2
                """,
                logical_session_id,
                limit,
            )
            total = await pool.fetchval(
                "SELECT count(*) FROM public.model_dispatch_attempts WHERE logical_session_id = $1",
                logical_session_id,
            )
    except asyncpg.exceptions.UndefinedTableError:
        return PaginatedResponse[DispatchAttemptEntry](
            data=[],
            meta=PaginationMeta(total=0, offset=0, limit=limit),
        )

    entries = [
        DispatchAttemptEntry(
            ts=row["ts"],
            butler=row["butler"],
            outcome=row["outcome"],
            attempt_index=row["attempt_index"],
            failure_reason=row["failure_reason"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            tool_call_count=row["tool_call_count"],
            session_id=str(row["session_id"]) if row["session_id"] else None,
            logical_session_id=row["logical_session_id"],
            duration_ms=_row_value(row, "duration_ms"),
        )
        for row in rows
    ]

    return PaginatedResponse[DispatchAttemptEntry](
        data=entries,
        meta=PaginationMeta(total=int(total or 0), offset=0, limit=limit),
    )
