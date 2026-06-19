"""Relationship/CRM endpoints.

Provides endpoints for contacts, groups, labels, notes, interactions,
gifts, loans, upcoming dates, and activity feeds. All data is queried
directly from the relationship butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerUnreachableError,
    MCPClientManager,
    get_mcp_manager,
)
from butlers.credential_store import assert_entity_info_secured
from butlers.identity import channel_value_for_storage
from butlers.tools.relationship._ef_channel_helpers import (
    TELEGRAM_HANDLE_PREFIX as _EF_TELEGRAM_HANDLE_PREFIX,
)
from butlers.tools.relationship._ef_channel_helpers import (
    ef_object_to_display_value as _ef_object_to_display_value_shared,
)
from butlers.tools.relationship._ef_channel_helpers import (
    ef_predicate_to_ci_type as _ef_predicate_to_ci_type_shared,
)
from butlers.tools.relationship._ef_channel_helpers import (
    entity_facts_channels_by_entity as _entity_facts_channels_by_entity_shared,
)
from butlers.tools.relationship.merge_review import (
    derive_shared_and_divergent_rows as _derive_shared_and_divergent_rows_shared,
)
from butlers.tools.relationship.merge_review import (
    fetch_single_cardinality_predicates as _fetch_single_cardinality_predicates_shared,
)
from butlers.tools.relationship.merge_review import (
    write_merge_review as _write_merge_review_shared,
)

# Load local models module
_api_dir = Path(__file__).parent
_models_path = _api_dir / "models.py"
if _models_path.exists():
    spec = importlib.util.spec_from_file_location("relationship_api_models_internal", _models_path)
    if spec is not None and spec.loader is not None:
        _models_module = importlib.util.module_from_spec(spec)
        sys.modules["relationship_api_models_internal"] = _models_module
        spec.loader.exec_module(_models_module)

        # Import models from the loaded module
        Group = _models_module.Group
        GroupListResponse = _models_module.GroupListResponse
        Label = _models_module.Label
        UpcomingDate = _models_module.UpcomingDate
        ContactInfoEntry = _models_module.ContactInfoEntry
        OwnerSetupStatus = _models_module.OwnerSetupStatus
        OwnerEntityInfoResponse = _models_module.OwnerEntityInfoResponse
        EntityInfoEntry = _models_module.EntityInfoEntry
        EntityDetail = _models_module.EntityDetail
        CreateEntityInfoRequest = _models_module.CreateEntityInfoRequest
        CreateEntityInfoResponse = _models_module.CreateEntityInfoResponse
        UpdateEntityInfoRequest = _models_module.UpdateEntityInfoRequest
        DunbarEntry = _models_module.DunbarEntry
        DunbarRankingResponse = _models_module.DunbarRankingResponse
        EntityNote = _models_module.EntityNote
        EntityInteraction = _models_module.EntityInteraction
        EntityGift = _models_module.EntityGift
        EntityLoan = _models_module.EntityLoan
        EntityTimelineItem = _models_module.EntityTimelineItem
        LinkedContactSummary = _models_module.LinkedContactSummary
        EntityImportantDate = _models_module.EntityImportantDate
        DunbarTierOverrideRequest = _models_module.DunbarTierOverrideRequest
        DunbarTierOverrideResponse = _models_module.DunbarTierOverrideResponse
        MessageThreadSummary = _models_module.MessageThreadSummary
        EntitySummary = _models_module.EntitySummary
        EntityListResponse = _models_module.EntityListResponse
        NeighbourEntry = _models_module.NeighbourEntry
        NeighboursResponse = _models_module.NeighboursResponse
        SearchResultEntry = _models_module.SearchResultEntry
        SearchResponse = _models_module.SearchResponse
        QueueEntry = _models_module.QueueEntry
        QueueResponse = _models_module.QueueResponse
        PredicateTab = _models_module.PredicateTab
        ConcentrationTarget = _models_module.ConcentrationTarget
        ConcentrationEntry = _models_module.ConcentrationEntry
        ConcentrationRollup = _models_module.ConcentrationRollup
        ConcentrationResponse = _models_module.ConcentrationResponse
        PromoteEntityRequest = _models_module.PromoteEntityRequest
        ContactFact = _models_module.ContactFact
        ContactsResponse = _models_module.ContactsResponse
        AddContactRequest = _models_module.AddContactRequest
        AddContactResponse = _models_module.AddContactResponse
        DeleteContactResponse = _models_module.DeleteContactResponse
        MarkContactVerifiedResponse = _models_module.MarkContactVerifiedResponse
        SetPreferredChannelRequest = _models_module.SetPreferredChannelRequest
        SetPreferredChannelResponse = _models_module.SetPreferredChannelResponse
        ClearPreferredChannelResponse = _models_module.ClearPreferredChannelResponse
        UpdateContactRequest = _models_module.UpdateContactRequest
        UpdateContactResponse = _models_module.UpdateContactResponse
        MergeEntitiesRequest = _models_module.MergeEntitiesRequest
        MergeEntitiesResponse = _models_module.MergeEntitiesResponse
        DismissQueueRequest = _models_module.DismissQueueRequest
        DismissQueueItemResult = _models_module.DismissQueueItemResult
        DismissQueueResponse = _models_module.DismissQueueResponse
        ActivityEntry = _models_module.ActivityEntry
        ActivityResponse = _models_module.ActivityResponse
        EntityFactEntry = _models_module.EntityFactEntry
        EntityFactsResponse = _models_module.EntityFactsResponse
        ActivityBin = _models_module.ActivityBin
        ActivityBinsResponse = _models_module.ActivityBinsResponse
        ViewMarkResponse = _models_module.ViewMarkResponse
        DeltaFactEntry = _models_module.DeltaFactEntry
        DeltaFactsResponse = _models_module.DeltaFactsResponse
        CoreDateEntry = _models_module.CoreDateEntry
        CoreDatesResponse = _models_module.CoreDatesResponse
        CompareRequest = _models_module.CompareRequest
        CompareFact = _models_module.CompareFact
        CompareEntitySummary = _models_module.CompareEntitySummary
        CompareEntityBlock = _models_module.CompareEntityBlock
        CompareResponse = _models_module.CompareResponse
        DismissPairRequest = _models_module.DismissPairRequest
        DismissPairResponse = _models_module.DismissPairResponse
        CreateLabelRequest = _models_module.CreateLabelRequest
        CreateLabelResponse = _models_module.CreateLabelResponse
        AssignGroupLabelResponse = _models_module.AssignGroupLabelResponse
        RemoveGroupLabelResponse = _models_module.RemoveGroupLabelResponse
        GroupLabelsResponse = _models_module.GroupLabelsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/relationship", tags=["relationship"])

BUTLER_DB = "relationship"
_CONTACTS_SYNC_TIMEOUT_S = 120.0


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the relationship butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Relationship butler database is not available",
        )


async def _table_columns(pool, table_name: str) -> set[str]:
    """Return column names for a table, resolved via the connection's search_path."""
    rows = await pool.fetch(
        """
        SELECT a.attname AS column_name
        FROM pg_attribute a
        WHERE a.attrelid = to_regclass($1)
          AND a.attnum > 0
          AND NOT a.attisdropped
        """,
        table_name,
    )
    return {row["column_name"] for row in rows}


def _group_select_fragments(group_columns: set[str]) -> tuple[str, str]:
    """Build schema-compatible select fragments for optional group fields."""
    description_sql = (
        "g.description" if "description" in group_columns else "NULL::text AS description"
    )
    updated_at_sql = (
        "g.updated_at" if "updated_at" in group_columns else "g.created_at AS updated_at"
    )
    return description_sql, updated_at_sql


def _extract_mcp_result_text(result: object) -> str | None:
    """Extract text content from an MCP tool result."""
    content = getattr(result, "content", None)
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            parts.append(text)
    if not parts:
        return None
    return "\n".join(parts)


def _parse_mcp_result_payload(raw_text: str | None) -> object:
    """Parse MCP text payload as JSON when possible, else return raw text."""
    if raw_text is None:
        return None
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return raw_text


def _extract_sync_summary(payload: object) -> dict[str, Any]:
    """Best-effort summary normalization from tool payload."""
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return summary
    return payload


def _coerce_count(value: Any) -> int | None:
    """Coerce summary counts to int when possible."""
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _is_credential_error(payload: object, raw_text: str | None) -> bool:
    """Detect credential-related failures from tool error payload."""
    samples: list[str] = []
    if raw_text:
        samples.append(raw_text.lower())
    if isinstance(payload, dict):
        for key in ("error", "detail", "message", "reason"):
            value = payload.get(key)
            if isinstance(value, str):
                samples.append(value.lower())
    joined = " ".join(samples)
    markers = (
        "credential",
        "oauth",
        "refresh token",
        "access token",
        "invalid_grant",
        "not configured",
        "missing",
    )
    return any(marker in joined for marker in markers)


# ---------------------------------------------------------------------------
# Warmth computation helper
# ---------------------------------------------------------------------------


def _compute_warmth(
    last_interaction_at: datetime | None,
    interactions_in_last_30d: int,
    tier_cadence_days: int,
) -> float:
    """Compute a 0.0–1.0 warmth score for a contact.

    Formula::

        recency_score   = max(0, 1 - days_since_last_contact / tier_cadence_days)
        tier_target_per_30d = 30 / tier_cadence_days
        frequency_score = min(1, interactions_in_last_30d / tier_target_per_30d)
        warmth = 0.6 * recency_score + 0.4 * frequency_score

    ``tier_cadence_days`` is the expected contact cadence for the contact's
    Dunbar tier (e.g. tier-5 = 14 days, tier-15 = 21 days, etc.).
    ``tier_target_per_30d`` is derived from the cadence so both components
    share the same time scale.

    Edge cases:
    - If ``last_interaction_at`` is None, recency_score = 0.
    - ``tier_cadence_days`` must be > 0 (enforced by TIER_CADENCE constants).
    - Result is clamped to [0.0, 1.0].
    """
    now = datetime.now(UTC)

    if last_interaction_at is None:
        days_since = float("inf")
    else:
        # Make tz-aware if naive
        if last_interaction_at.tzinfo is None:
            last_interaction_at = last_interaction_at.replace(tzinfo=UTC)
        days_since = max((now - last_interaction_at).total_seconds() / 86400.0, 0.0)

    recency_score = max(0.0, 1.0 - days_since / tier_cadence_days)
    tier_target_per_30d = 30.0 / tier_cadence_days
    frequency_score = min(1.0, interactions_in_last_30d / max(tier_target_per_30d, 1e-9))

    warmth = 0.6 * recency_score + 0.4 * frequency_score
    return round(max(0.0, min(1.0, warmth)), 4)


# ---------------------------------------------------------------------------
# entity_facts channel helpers — shared with tools layer (bu-twbt0)
# ---------------------------------------------------------------------------
# The low-level predicate→type and object→display-value helpers now live in
# butlers.tools.relationship._ef_channel_helpers (imported at the top of this
# file).  Local aliases preserve the original call-site names throughout
# this module.

_TELEGRAM_HANDLE_PREFIX = _EF_TELEGRAM_HANDLE_PREFIX
_ef_predicate_to_ci_type = _ef_predicate_to_ci_type_shared
_ef_object_to_display_value = _ef_object_to_display_value_shared
_entity_facts_channels_by_entity = _entity_facts_channels_by_entity_shared


def _ef_row_to_ci_entry(fr: Any) -> Any:
    """Convert a relationship.entity_facts row to a ContactInfoEntry.

    Expected keys on *fr*: ``id``, ``predicate``, ``object``, ``primary``, ``verified``.

    The returned entry carries ``source="entity_facts"`` and populates
    ``predicate`` + ``value_hash`` for entity-keyed mutation paths.
    """
    raw_obj: str = fr["object"]
    ci_type = _ef_predicate_to_ci_type(fr["predicate"], raw_obj)
    display_val = _ef_object_to_display_value(fr["predicate"], raw_obj)
    primary_raw = fr["primary"]
    verified_raw = fr.get("verified")
    return ContactInfoEntry(
        id=fr["id"],
        type=ci_type,
        value=display_val,
        is_primary=bool(primary_raw) if primary_raw is not None else False,
        secured=False,
        parent_id=None,
        context=None,
        source="entity_facts",
        predicate=fr["predicate"],
        value_hash=_contact_value_hash(raw_obj),
        verified=bool(verified_raw) if verified_raw is not None else False,
    )


def _deliverable_channels_from_facts(fact_rows: list[Any]) -> list[str]:
    """Return the deliverable channels the entity has a contact fact for.

    Used by ``GET /entities/{id}/linked-contacts`` to tell the dashboard
    channel-preference control which channels are selectable (spec scenario
    "Only reachable channels are offered").

    Restricted to the channels the dashboard preference control can offer
    (``email``, ``telegram``) — the same deliverable set group 2's notify path
    honors. Reachability proofs mirror group 1's ``_CHANNEL_REACHABILITY``:

    - ``email``    ← an active ``has-email`` fact.
    - ``telegram`` ← an active ``has-handle`` fact whose object is telegram-
      prefixed (``telegram:…``), the only handle channel with a reliable prefix.

    *fact_rows* are the entity's active ``has-*`` literal facts already fetched
    by the caller (keys ``predicate``, ``object``). Order in the returned list
    is stable (``email`` before ``telegram``) for deterministic rendering.
    """
    channels: list[str] = []
    has_email = any(fr["predicate"] == "has-email" for fr in fact_rows)
    has_telegram = any(
        fr["predicate"] == "has-handle" and str(fr["object"]).startswith(_EF_TELEGRAM_HANDLE_PREFIX)
        for fr in fact_rows
    )
    if has_email:
        channels.append("email")
    if has_telegram:
        channels.append("telegram")
    return channels


def _first_ef_value(
    facts: list[dict],
    *,
    predicate: str,
    exclude_telegram_prefix: bool = False,
) -> str | None:
    """Return the first matching object value from a list of entity_facts rows.

    Parameters
    ----------
    facts:
        Rows from :func:`_entity_facts_channels_by_entity` (already ordered
        by ``primary DESC NULLS LAST, created_at ASC``).
    predicate:
        The predicate to match (e.g. ``"has-email"``).
    exclude_telegram_prefix:
        When *True* (used for bare handle lookups), skip objects that start
        with the ``"telegram:"`` prefix so telegram IDs are not returned as
        generic handles.
    """
    for fr in facts:
        if fr["predicate"] != predicate:
            continue
        val: str = fr["object"]
        if exclude_telegram_prefix and val.startswith(_TELEGRAM_HANDLE_PREFIX):
            continue
        return _ef_object_to_display_value(predicate, val)
    return None


# ---------------------------------------------------------------------------
# GET /owner/setup-status
# ---------------------------------------------------------------------------


@router.get("/owner/setup-status", response_model=OwnerSetupStatus)
async def get_owner_setup_status(
    db: DatabaseManager = Depends(_get_db_manager),
) -> OwnerSetupStatus:
    """Return whether the owner entity has channel identifiers configured.

    Used by the dashboard to show setup prompts when the owner has not yet
    connected their communication channels.
    """
    pool = _pool(db)

    # Find the owner entity
    owner_row = await pool.fetchrow(
        """
        SELECT id, canonical_name
        FROM public.entities
        WHERE 'owner' = ANY(COALESCE(roles, '{}'))
        LIMIT 1
        """,
    )
    if owner_row is None:
        return OwnerSetupStatus(
            entity_id=None,
            has_name=False,
            has_telegram=False,
            has_telegram_chat_id=False,
            has_email=False,
        )

    owner_entity_id = owner_row["id"]
    # The bootstrap name "Owner" is a placeholder — treat it as not yet set
    canonical = owner_row["canonical_name"] or ""
    has_name = bool(canonical.strip() and canonical.strip().lower() != "owner")

    rows = await pool.fetch(
        """
        SELECT ei.type
        FROM public.entity_info ei
        WHERE ei.entity_id = $1
          AND ei.type IN ('telegram', 'telegram_chat_id', 'email')
        """,
        owner_entity_id,
    )

    found_types = {r["type"] for r in rows}

    return OwnerSetupStatus(
        entity_id=owner_entity_id,
        has_name=has_name,
        has_telegram="telegram" in found_types,
        has_telegram_chat_id="telegram_chat_id" in found_types,
        has_email="email" in found_types,
    )


# ---------------------------------------------------------------------------
# GET /owner/entity-info
# ---------------------------------------------------------------------------


@router.get("/owner/entity-info", response_model=OwnerEntityInfoResponse)
async def get_owner_entity_info(
    db: DatabaseManager = Depends(_get_db_manager),
) -> OwnerEntityInfoResponse:
    """Return all entity_info entries for the owner entity.

    Used by the dashboard Secrets page (User tab) to manage owner-specific
    credentials (Telegram API keys, HA token, etc.) without needing to know
    the owner entity UUID.

    Secured values are masked (value=None) in the response. Use
    GET /entities/{id}/secrets/{info_id} to reveal a secured value.
    """
    pool = _pool(db)

    owner_row = await pool.fetchrow(
        """
        SELECT id, canonical_name
        FROM public.entities
        WHERE 'owner' = ANY(COALESCE(roles, '{}'))
        LIMIT 1
        """,
    )
    if owner_row is None:
        raise HTTPException(status_code=404, detail="No owner entity found")

    owner_entity_id = owner_row["id"]

    info_rows = await pool.fetch(
        """
        SELECT id, type, value, label, is_primary, secured
        FROM public.entity_info
        WHERE entity_id = $1
        ORDER BY type
        """,
        owner_entity_id,
    )

    entries = [
        EntityInfoEntry(
            id=r["id"],
            type=r["type"],
            value=None if r["secured"] else r["value"],
            label=r["label"],
            is_primary=r["is_primary"],
            secured=r["secured"],
        )
        for r in info_rows
    ]

    return OwnerEntityInfoResponse(
        entity_id=owner_entity_id,
        entity_name=owner_row["canonical_name"] or "",
        entries=entries,
    )


# ---------------------------------------------------------------------------
# GET /groups — list groups with member counts
# ---------------------------------------------------------------------------


@router.get("/groups", response_model=GroupListResponse)
async def list_groups(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> GroupListResponse:
    """List all groups with member counts and assigned labels, paginated."""
    pool = _pool(db)
    group_columns = await _table_columns(pool, "groups")
    description_sql, updated_at_sql = _group_select_fragments(group_columns)

    total = await pool.fetchval("SELECT count(*) FROM groups") or 0

    rows = await pool.fetch(
        f"""
        SELECT
            g.id,
            g.name,
            {description_sql},
            g.created_at,
            {updated_at_sql},
            count(gm.contact_id) AS member_count
        FROM groups g
        LEFT JOIN group_members gm ON gm.group_id = g.id
        GROUP BY g.id
        ORDER BY g.name
        OFFSET $1 LIMIT $2
        """,
        offset,
        limit,
    )

    # Batch-fetch group labels (guards against missing table on older DBs)
    group_ids = [r["id"] for r in rows]
    labels_by_group: dict[Any, list[Label]] = {gid: [] for gid in group_ids}
    if group_ids:
        gl_tables = await _table_columns(pool, "group_labels")
        if gl_tables:
            label_rows = await pool.fetch(
                """
                SELECT gl.group_id, l.id, l.name, l.color
                FROM group_labels gl
                JOIN labels l ON l.id = gl.label_id
                WHERE gl.group_id = ANY($1::uuid[])
                ORDER BY l.name
                """,
                group_ids,
            )
            for lr in label_rows:
                labels_by_group[lr["group_id"]].append(
                    Label(id=lr["id"], name=lr["name"], color=lr["color"])
                )

    groups = [
        Group(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            member_count=r["member_count"],
            labels=labels_by_group.get(r["id"], []),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

    return GroupListResponse(groups=groups, total=total)


# ---------------------------------------------------------------------------
# GET /groups/{group_id} — group detail with members
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}", response_model=Group)
async def get_group(
    group_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Group:
    """Get a group with its member count."""
    pool = _pool(db)
    group_columns = await _table_columns(pool, "groups")
    description_sql, updated_at_sql = _group_select_fragments(group_columns)

    row = await pool.fetchrow(
        f"""
        SELECT
            g.id,
            g.name,
            {description_sql},
            g.created_at,
            {updated_at_sql},
            count(gm.contact_id) AS member_count
        FROM groups g
        LEFT JOIN group_members gm ON gm.group_id = g.id
        WHERE g.id = $1
        GROUP BY g.id
        """,
        group_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")

    # Fetch labels for this group (defensive guard in case migration not applied)
    labels: list[Label] = []
    gl_columns = await _table_columns(pool, "group_labels")
    if gl_columns:
        label_rows = await pool.fetch(
            """
            SELECT l.id, l.name, l.color
            FROM group_labels gl
            JOIN labels l ON l.id = gl.label_id
            WHERE gl.group_id = $1
            ORDER BY l.name
            """,
            group_id,
        )
        labels = [Label(id=lr["id"], name=lr["name"], color=lr["color"]) for lr in label_rows]

    return Group(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        member_count=row["member_count"],
        labels=labels,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# GET /labels — list all labels
# ---------------------------------------------------------------------------


@router.get("/labels", response_model=list[Label])
async def list_labels(
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Label]:
    """List all labels."""
    pool = _pool(db)
    rows = await pool.fetch("SELECT id, name, color FROM labels ORDER BY name")
    return [Label(id=r["id"], name=r["name"], color=r["color"]) for r in rows]


# ---------------------------------------------------------------------------
# POST /labels — create a new label
# ---------------------------------------------------------------------------


@router.post("/labels", response_model=CreateLabelResponse, status_code=201)
async def create_label(
    body: CreateLabelRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> CreateLabelResponse:
    """Create a new label (name must be unique)."""
    pool = _pool(db)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Label name cannot be empty or whitespace only")
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO labels (name, color)
            VALUES ($1, $2)
            RETURNING id, name, color
            """,
            name,
            body.color,
        )
    except Exception as exc:
        if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
            raise HTTPException(status_code=409, detail=f"Label '{name}' already exists") from exc
        raise
    return CreateLabelResponse(id=row["id"], name=row["name"], color=row["color"])


# ---------------------------------------------------------------------------
# GET /groups/{group_id}/labels — list labels on a group
# ---------------------------------------------------------------------------


@router.get("/groups/{group_id}/labels", response_model=GroupLabelsResponse)
async def get_group_labels(
    group_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> GroupLabelsResponse:
    """List all labels assigned to a group."""
    pool = _pool(db)
    # Verify group exists
    exists = await pool.fetchval("SELECT 1 FROM groups WHERE id = $1", group_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Group not found")

    gl_columns = await _table_columns(pool, "group_labels")
    if not gl_columns:
        return GroupLabelsResponse(group_id=group_id, labels=[])

    rows = await pool.fetch(
        """
        SELECT l.id, l.name, l.color
        FROM group_labels gl
        JOIN labels l ON l.id = gl.label_id
        WHERE gl.group_id = $1
        ORDER BY l.name
        """,
        group_id,
    )
    labels = [Label(id=r["id"], name=r["name"], color=r["color"]) for r in rows]
    return GroupLabelsResponse(group_id=group_id, labels=labels)


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/labels/{label_id} — assign a label to a group
# ---------------------------------------------------------------------------


@router.post(
    "/groups/{group_id}/labels/{label_id}",
    response_model=AssignGroupLabelResponse,
    status_code=200,
)
async def assign_group_label(
    group_id: UUID,
    label_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> AssignGroupLabelResponse:
    """Assign a label to a group (idempotent — safe to call if already assigned)."""
    pool = _pool(db)
    # Verify group and label exist
    group_exists = await pool.fetchval("SELECT 1 FROM groups WHERE id = $1", group_id)
    if not group_exists:
        raise HTTPException(status_code=404, detail="Group not found")
    label_exists = await pool.fetchval("SELECT 1 FROM labels WHERE id = $1", label_id)
    if not label_exists:
        raise HTTPException(status_code=404, detail="Label not found")

    inserted = await pool.fetchval(
        """
        INSERT INTO group_labels (group_id, label_id)
        VALUES ($1, $2)
        ON CONFLICT (group_id, label_id) DO NOTHING
        RETURNING 1
        """,
        group_id,
        label_id,
    )
    return AssignGroupLabelResponse(
        group_id=group_id, label_id=label_id, assigned=inserted is not None
    )


# ---------------------------------------------------------------------------
# DELETE /groups/{group_id}/labels/{label_id} — remove a label from a group
# ---------------------------------------------------------------------------


@router.delete(
    "/groups/{group_id}/labels/{label_id}",
    response_model=RemoveGroupLabelResponse,
)
async def remove_group_label(
    group_id: UUID,
    label_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> RemoveGroupLabelResponse:
    """Remove a label from a group."""
    pool = _pool(db)
    result = await pool.execute(
        "DELETE FROM group_labels WHERE group_id = $1 AND label_id = $2",
        group_id,
        label_id,
    )
    removed = result != "DELETE 0"
    return RemoveGroupLabelResponse(group_id=group_id, label_id=label_id, removed=removed)


# ---------------------------------------------------------------------------
# GET /upcoming-dates — upcoming important dates
# ---------------------------------------------------------------------------


@router.get("/upcoming-dates", response_model=list[UpcomingDate])
async def list_upcoming_dates(
    days: int = Query(30, ge=1, le=365, description="Look-ahead window in days"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[UpcomingDate]:
    """List upcoming important dates within the next N days (default 30).

    Calculates days-until based on month/day relative to today, handling
    year wrap-around for dates that have already passed this year.
    """
    pool = _pool(db)

    today = date.today()

    rows = await pool.fetch(
        """
        SELECT
            id.contact_id,
            e.canonical_name AS contact_name,
            id.label,
            id.month,
            id.day,
            id.year
        FROM important_dates id
        JOIN contact_entity_map cem ON cem.contact_id = id.contact_id
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE (e.metadata->>'archived') IS DISTINCT FROM 'true'
        """,
    )

    upcoming: list[UpcomingDate] = []
    for r in rows:
        month = r["month"]
        day = r["day"]

        # Build this year's occurrence
        try:
            this_year = date(today.year, month, day)
        except ValueError:
            # Handle Feb 29 in non-leap years
            continue

        if this_year >= today:
            days_until = (this_year - today).days
            occurrence = this_year
        else:
            # Already passed this year — next occurrence is next year
            try:
                next_year = date(today.year + 1, month, day)
            except ValueError:
                continue
            days_until = (next_year - today).days
            occurrence = next_year

        if days_until <= days:
            upcoming.append(
                UpcomingDate(
                    contact_id=r["contact_id"],
                    contact_name=r["contact_name"],
                    date_type=r["label"],
                    date=occurrence,
                    days_until=days_until,
                )
            )

    upcoming.sort(key=lambda u: u.days_until)
    return upcoming


# ---------------------------------------------------------------------------
# GET /entities/search — deterministic Finder (bu-q9uiw)
# ---------------------------------------------------------------------------

#: Default and maximum page sizes for the entity search endpoint.
_ENTITY_SEARCH_DEFAULT_LIMIT = 20
_ENTITY_SEARCH_MAX_LIMIT = 50

#: Score constants — match §7.5 of 07-finder.md + Brief §1 index search rules.
_SCORE_PREFIX = 100
_SCORE_CONTACT_FACT = 70
_SCORE_SUBSTRING = 50
_SCORE_PREDICATE = 30


@router.get("/entities/search", response_model=SearchResponse)
async def search_entities(
    q: str = Query(..., description="Search string (required)"),
    limit: int = Query(_ENTITY_SEARCH_DEFAULT_LIMIT, ge=1, le=_ENTITY_SEARCH_MAX_LIMIT),
    db: DatabaseManager = Depends(_get_db_manager),
) -> SearchResponse:
    """Search entities using deterministic rule-based ranking.

    Scores each entity against the query string using four rules:

    - **Prefix match** on ``canonical_name`` or any alias: score 100
    - **Contact-fact value match** — ``object ILIKE '%q%'`` on active
      contact-type facts (``has-*`` predicates) in ``relationship.entity_facts``:
      score 70
    - **Substring match** on ``canonical_name`` or any alias: score 50
    - **Predicate label match** — ``predicate ILIKE '%q%'`` on active facts
      in ``relationship.entity_facts``: score 30

    Results are deduplicated by ``entity_id`` (each entity appears at most
    once, at its highest score), ordered by score descending, ties broken
    deterministically by entity UUID.

    **Authorization**: owner-only gate (Amendment 12b) — returns HTTP 403
    with ``{"code": "owner_required"}`` when no owner entity is registered.

    **No LLM, no embedding service.** All ranking is pure SQL (``ILIKE``).
    Per Brief §6b Amendment 15 (Deterministic-Finder transitive enforcement).

    All ``relationship.entity_facts`` queries include ``AND validity = 'active'``.
    ``relationship.entity_facts`` has no ``scope`` column — scope is implicit via schema
    qualification (``relationship.`` prefix enforces schema isolation per RFC 0006).
    """
    pool = _pool(db)

    # Normalise query — strip surrounding whitespace and handle None.
    q_clean = (q or "").strip()
    if not q_clean:
        return SearchResponse(results=[], total=0, q=q, limit=limit)

    # Owner-only gate (Clause 12b, Amendment 12b) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # ---------------------------------------------------------------------------
    # Ranking SQL
    #
    # Four UNION branches, each emitting (entity_id, score, match_kind):
    #
    #   1. Prefix (100)      — canonical_name or alias starts with q
    #   2. Contact-fact (70) — has-* fact object contains q (literal substring)
    #   3. Substring (50)    — canonical_name or alias contains q
    #   4. Predicate (30)    — predicate label contains q (edge exists for entity)
    #
    # We wrap in an outer SELECT that deduplicates by entity_id (MAX score),
    # joins back to public.entities for canonical_name, then sorts and limits.
    # ---------------------------------------------------------------------------

    sql = """
    WITH ranked AS (
        SELECT
            entity_id,
            MAX(score) AS score,
            (ARRAY_AGG(match_kind ORDER BY score DESC))[1] AS match_kind
        FROM (
            -- Branch 1: prefix match on canonical_name or any alias (score=100)
            SELECT
                e.id AS entity_id,
                $2::int AS score,
                'prefix'::text AS match_kind
            FROM public.entities e
            WHERE (e.metadata->>'merged_into') IS NULL
              AND (
                  e.canonical_name ILIKE ($1 || '%')
                  OR EXISTS (
                      SELECT 1 FROM unnest(COALESCE(e.aliases, '{}')) AS alias_val
                      WHERE alias_val ILIKE ($1 || '%')
                  )
              )

            UNION ALL

            -- Branch 2: contact-fact value match (score=70)
            -- Matches entities with a has-* fact whose object value contains q
            SELECT
                f.subject AS entity_id,
                $3::int AS score,
                'contact_fact'::text AS match_kind
            FROM relationship.entity_facts f
            WHERE f.predicate LIKE 'has-%'
              AND f.object_kind = 'literal'
              AND f.object ILIKE ('%' || $1 || '%')
              AND f.validity = 'active'

            UNION ALL

            -- Branch 3: substring match on canonical_name or any alias (score=50)
            SELECT
                e.id AS entity_id,
                $4::int AS score,
                'substring'::text AS match_kind
            FROM public.entities e
            WHERE (e.metadata->>'merged_into') IS NULL
              AND (
                  e.canonical_name ILIKE ('%' || $1 || '%')
                  OR EXISTS (
                      SELECT 1 FROM unnest(COALESCE(e.aliases, '{}')) AS alias_val
                      WHERE alias_val ILIKE ('%' || $1 || '%')
                  )
              )

            UNION ALL

            -- Branch 4: predicate label match (score=30)
            -- Matches entities that have at least one fact whose predicate
            -- label contains q (e.g. searching "vendor" matches "purchased-from")
            SELECT
                f.subject AS entity_id,
                $5::int AS score,
                'predicate'::text AS match_kind
            FROM relationship.entity_facts f
            WHERE f.predicate ILIKE ('%' || $1 || '%')
              AND f.validity = 'active'
        ) AS candidates
        GROUP BY entity_id
    )
    SELECT
        r.entity_id,
        e.canonical_name,
        e.entity_type,
        r.score,
        r.match_kind
    FROM ranked r
    JOIN public.entities e ON e.id = r.entity_id
    WHERE (e.metadata->>'merged_into') IS NULL
    ORDER BY r.score DESC, r.entity_id ASC
    LIMIT $6
    """

    rows = await pool.fetch(
        sql,
        q_clean,
        _SCORE_PREFIX,
        _SCORE_CONTACT_FACT,
        _SCORE_SUBSTRING,
        _SCORE_PREDICATE,
        limit,
    )

    results = [
        SearchResultEntry(
            entity_id=row["entity_id"],
            canonical_name=row["canonical_name"],
            entity_type=row["entity_type"],
            score=int(row["score"]),
            match_kind=row["match_kind"],
        )
        for row in rows
    ]

    return SearchResponse(
        results=results,
        total=len(results),
        q=q,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# GET /entities — list + filter + pagination
# ---------------------------------------------------------------------------

#: Contact-type predicates for the ``has=contact`` filter chip.
_HAS_CONTACT_PREDICATES = (
    "has-email",
    "has-phone",
    "has-handle",
    "has-address",
    "has-birthday",
    "has-website",
)

#: Valid values for the ``state`` filter query parameter.
_VALID_ENTITY_STATES = frozenset({"unidentified", "duplicate-candidate", "stale"})

#: Valid values for the ``has`` filter query parameter.
_VALID_HAS_VALUES = frozenset({"contact"})

#: Default and maximum page sizes for the entity list endpoint.
_ENTITY_LIST_DEFAULT_LIMIT = 50
_ENTITY_LIST_MAX_LIMIT = 200


#: Predicate written by the dismiss endpoint (state-marker triple).
_QUEUE_DISMISSED_PREDICATE = "queue.dismissed"

#: Literal object value for a queue-dismissed triple.
_QUEUE_DISMISSED_OBJECT = "dismissed"


def _active_entity_condition(alias: str = "e") -> str:
    """SQL predicate for entities visible in standard active surfaces.

    Archives have existed under both ``metadata.archived`` and legacy
    ``metadata.archived_at``.  Treat both as hidden from default lists/queues.
    """
    metadata = f"{alias}.metadata"
    return f"""
        ({metadata}->>'merged_into') IS NULL
        AND ({metadata}->>'archived') IS DISTINCT FROM 'true'
        AND ({metadata}->>'archived_at') IS NULL
        AND ({metadata}->>'tombstone') IS DISTINCT FROM 'true'
        AND ({metadata}->>'deleted_at') IS NULL
    """


def _not_queue_dismissed_sql(id_expr: str = "e.id") -> str:
    """SQL predicate excluding entities dismissed from the curation queue.

    The single-item dismiss endpoint (``POST /entities/queue/dismiss``) records
    a ``queue.dismissed`` state-marker triple in ``relationship.entity_facts``
    via the central writer.  Every queue bucket must exclude entities carrying
    such an *active* triple, otherwise a dismissed entity reappears on refetch.
    """
    return f"""
        NOT EXISTS (
            SELECT 1 FROM relationship.entity_facts qd
            WHERE qd.subject = {id_expr}
              AND qd.predicate = '{_QUEUE_DISMISSED_PREDICATE}'
              AND qd.object = '{_QUEUE_DISMISSED_OBJECT}'
              AND qd.validity = 'active'
        )
    """


@router.get("/entities", response_model=EntityListResponse)
async def list_entities(
    entity_type: list[str] | None = Query(
        None,
        description=(
            "Filter by one or more entity_type values. Repeat the parameter for "
            "multiselect filters, e.g. entity_type=person&entity_type=organization."
        ),
    ),
    state: str | None = Query(
        None,
        description=(
            "State filter chip.  "
            "Accepted values: unidentified | duplicate-candidate | stale.  "
            "Unknown values are rejected with HTTP 400."
        ),
    ),
    has: str | None = Query(
        None,
        description=(
            "has=contact surfaces entities with at least one contact-type triple "
            "(has-email | has-phone | has-handle | has-address | has-birthday | has-website) "
            "in relationship.entity_facts.  Unknown values are rejected with HTTP 400."
        ),
    ),
    ids: list[str] | None = Query(
        None,
        description=(
            "Restrict results to this explicit set of entity UUIDs. Repeat the "
            "parameter for multiple ids, e.g. ids=<uuid1>&ids=<uuid2>. Used to "
            "hydrate full entity summaries for an externally-ranked id set (e.g. "
            "the toolbar search endpoint) while keeping the rich list columns. "
            "Combines with the other filter chips and the active-entity guard. "
            "When present but empty, the result set is empty."
        ),
    ),
    limit: int = Query(_ENTITY_LIST_DEFAULT_LIMIT, ge=1, le=_ENTITY_LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityListResponse:
    """List entities from ``public.entities`` with optional filter chips and pagination.

    **Filters**

    - ``entity_type`` — repeatable filter for ``public.entities.entity_type``
      (e.g. ``person`` + ``organization``).
    - ``state`` — state chip filter:
        - ``unidentified``: entities where ``metadata->>'unidentified' = 'true'``.
        - ``duplicate-candidate``: entities detected by the same live logic as the
          curation queue — either ``metadata->>'duplicate_candidate' = 'true'`` OR
          sharing a ``has-email``/``has-phone`` fact value with at least one other
          entity (non-dismissed self-join, identical to the queue rail's
          ``dup_detected_sql``).
        - ``stale``: entities whose most-recent ``last_seen`` across all facts in
          ``relationship.entity_facts`` is older than 365 days (or have no facts at all).
    - ``has=contact`` — entities with at least one contact-type triple
      (``has-email | has-phone | has-handle | has-address | has-birthday | has-website``)
      in ``relationship.entity_facts``.

    **Authorization**: session-bounded only (no owner gate per Brief §6b Amendment 12b).

    **Pagination**: ``limit`` (default 50, max 200) + ``offset`` (default 0).
    Responses include ``total`` (count before pagination) for page-size math.
    """
    if state is not None and state not in _VALID_ENTITY_STATES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown state filter '{state}'. Accepted: {sorted(_VALID_ENTITY_STATES)}",
        )
    if has is not None and has not in _VALID_HAS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown has filter '{has}'. Accepted: {sorted(_VALID_HAS_VALUES)}",
        )
    if limit > _ENTITY_LIST_MAX_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"limit exceeds maximum of {_ENTITY_LIST_MAX_LIMIT}",
        )

    pool = _pool(db)

    # Build the WHERE clause incrementally.
    conditions: list[str] = [
        _active_entity_condition("e"),
    ]
    args: list[object] = []
    arg_idx = 1

    # entity_type filter (repeatable query param for the frontend multiselect).
    entity_types = [t for t in (entity_type or []) if t]
    if entity_types:
        conditions.append(f"e.entity_type = ANY(${arg_idx}::text[])")
        args.append(entity_types)
        arg_idx += 1

    # ids filter — restrict to an explicit UUID set. When the param is present
    # (even as an empty list) we apply ``= ANY(...)`` so an empty set yields no
    # rows; absence of the param leaves the list unrestricted. Used to hydrate
    # full summaries for the toolbar search's externally-ranked id set.
    if ids is not None:
        clean_ids = [i for i in ids if i]
        conditions.append(f"e.id = ANY(${arg_idx}::uuid[])")
        args.append(clean_ids)
        arg_idx += 1

    # state filter
    if state == "unidentified":
        conditions.append("(e.metadata->>'unidentified')::text = 'true'")
    elif state == "duplicate-candidate":
        # Align with the queue rail's duplicate-candidate detection: metadata flag
        # OR live self-join detecting entities sharing a has-email/has-phone value
        # with at least one other entity (with dismissal suppression, same as queue).
        dup_predicates_literal = ", ".join(f"'{p}'" for p in _DUP_DETECTION_PREDICATES)
        _suppression = _dismissed_pair_suppression_sql("e.id", "f_link.predicate", "f_link.object")
        conditions.append(
            f"""
            (
                (e.metadata->>'duplicate_candidate')::text = 'true'
                OR EXISTS (
                    SELECT 1
                    FROM relationship.entity_facts f_link
                    WHERE f_link.subject = e.id
                      AND f_link.validity = 'active'
                      AND f_link.predicate IN ({dup_predicates_literal})
                      AND {_suppression}
                )
            )
            """
        )
    elif state == "stale":
        # Stale: no recent fact OR latest fact last_seen older than 365 days.
        # We check against relationship.entity_facts for last_seen.
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1 FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.validity = 'active'
                  AND rf.last_seen > (now() - INTERVAL '365 days')
            )
            """
        )

    # has=contact filter — require at least one has-* triple in relationship.entity_facts
    if has == "contact":
        predicates_literal = ", ".join(f"'{p}'" for p in _HAS_CONTACT_PREDICATES)
        conditions.append(
            f"""
            EXISTS (
                SELECT 1 FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.predicate IN ({predicates_literal})
                  AND rf.validity = 'active'
            )
            """
        )

    where_clause = "WHERE " + " AND ".join(conditions)

    # Rank-based Dunbar tiers. compute_tier_ranking scores every listed,
    # entity-linked contact and buckets it into a Dunbar circle (5/15/50/150/
    # 500/1500), defaulting zero-interaction contacts to 1500 (Familiar Faces)
    # and honouring manual overrides. We forward the result into the data query
    # as two parallel arrays joined via unnest so the *computed* tier drives both
    # the displayed value and the tier-primary sort — not just manual pins.
    # Entities absent from the ranking (no linked contact, unlisted, or a
    # non-person type) fall back to any pinned override, else NULL ('—').
    from butlers.tools.relationship import dunbar as _dunbar

    # compute_tier_ranking returns one entry per *contact*, so an entity with
    # multiple linked contacts appears multiple times. Forwarding those repeats
    # into the unnest() below fans out the LEFT JOIN and renders the same entity
    # as N duplicate rows (and breaks entity-keyed checkbox selection). The list
    # is entity-keyed, so collapse to one tier per entity here. Ranking is
    # ordered by score DESC and higher score → innermost (smallest) tier, so the
    # first occurrence is the closest tier — keep it.
    ranking = await _dunbar.compute_tier_ranking(pool)
    tier_entity_ids: list[Any] = []
    tier_values: list[int] = []
    _seen_tier_entities: set[Any] = set()
    for entry in ranking:
        entity_id = entry["entity_id"]
        if entity_id in _seen_tier_entities:
            continue
        _seen_tier_entities.add(entity_id)
        tier_entity_ids.append(entity_id)
        tier_values.append(int(entry["dunbar_tier"]))

    # Argument slots for the data query: the two tier arrays, then offset/limit.
    tier_ids_idx = arg_idx
    tier_vals_idx = arg_idx + 1
    offset_idx = arg_idx + 2
    limit_idx = arg_idx + 3

    # Count query (no pagination)
    count_sql = f"SELECT count(*) FROM public.entities e {where_clause}"

    # Data query: annotate with pinned tier (from facts) and last_seen.
    # People are sorted as a relationship working set: closest tier first, then
    # oldest last_seen first for re-engagement; other types remain alphabetical.
    data_sql = f"""
        WITH computed_tiers AS (
            -- Rank-based Dunbar tiers computed in Python, forwarded as arrays.
            SELECT entity_id, computed_tier
            FROM unnest(${tier_ids_idx}::uuid[], ${tier_vals_idx}::int[])
                AS t(entity_id, computed_tier)
        ),
        annotated AS (
            SELECT
                e.id,
                e.canonical_name,
                e.entity_type,
                e.aliases,
                e.roles,
                e.metadata,
                e.created_at,
                e.updated_at,
                -- Effective Dunbar tier: the rank-based computed tier when the
                -- entity is in the ranking (which already folds in overrides),
                -- else any standalone pinned override (covers contactless pins).
                COALESCE(
                    ct.computed_tier,
                    (
                        SELECT (rf.object)::int
                        FROM relationship.entity_facts rf
                        WHERE rf.subject = e.id
                          AND rf.predicate = 'dunbar_tier_override'
                          AND rf.validity = 'active'
                        ORDER BY rf.created_at DESC
                        LIMIT 1
                    )
                ) AS tier,
                -- Most-recent last_seen across all active relationship facts
                (
                    SELECT max(rf.last_seen)
                    FROM relationship.entity_facts rf
                    WHERE rf.subject = e.id
                      AND rf.validity = 'active'
                ) AS last_seen,
                -- Earliest last_seen across all active relationship facts (first contact)
                (
                    SELECT min(rf.last_seen)
                    FROM relationship.entity_facts rf
                    WHERE rf.subject = e.id
                      AND rf.validity = 'active'
                ) AS first_seen,
                -- Count of contact-type facts
                (
                    SELECT count(*)
                    FROM relationship.entity_facts rf
                    WHERE rf.subject = e.id
                      AND rf.predicate IN ({", ".join(f"'{p}'" for p in _HAS_CONTACT_PREDICATES)})
                      AND rf.validity = 'active'
                ) AS contact_fact_count
            FROM public.entities e
            LEFT JOIN computed_tiers ct ON ct.entity_id = e.id
            {where_clause}
        )
        SELECT
            id,
            canonical_name,
            entity_type,
            aliases,
            roles,
            metadata,
            created_at,
            updated_at,
            tier,
            last_seen,
            first_seen,
            contact_fact_count
        FROM annotated
        ORDER BY
            -- Dunbar tier is the primary sort: ascending tier number = innermost
            -- circle (Support Clique = 5) first, so the user's closest people lead
            -- the whole list regardless of entity type. Untiered entities (no
            -- pinned override) fall to the bottom.
            tier ASC NULLS LAST,
            -- Within the untiered tail, preserve the persons → orgs → other grouping.
            CASE entity_type
                WHEN 'person' THEN 0
                WHEN 'organization' THEN 1
                ELSE 2
            END,
            CASE WHEN entity_type = 'person' THEN last_seen END ASC NULLS LAST,
            canonical_name ASC
        OFFSET ${offset_idx} LIMIT ${limit_idx}
    """
    count_args = list(args)
    data_args = [*args, tier_entity_ids, tier_values, offset, limit]

    total_raw, rows = await asyncio.gather(
        pool.fetchval(count_sql, *count_args),
        pool.fetch(data_sql, *data_args),
    )
    total = total_raw or 0

    items = [
        EntitySummary(
            id=r["id"],
            canonical_name=r["canonical_name"],
            entity_type=r["entity_type"],
            aliases=list(r["aliases"]) if r["aliases"] else [],
            roles=list(r["roles"]) if r["roles"] else [],
            metadata=dict(r["metadata"]) if isinstance(r["metadata"], dict) else {},
            tier=r["tier"],
            last_seen=r["last_seen"],
            first_seen=r["first_seen"],
            contact_fact_count=int(r["contact_fact_count"] or 0),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

    return EntityListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# POST /entities — promote unidentified → canonical entity (bu-pzp9m)
# ---------------------------------------------------------------------------


@router.post("/entities", response_model=EntitySummary, status_code=201)
async def promote_entity(
    body: PromoteEntityRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntitySummary:
    """Promote an unidentified entity placeholder to a canonical entity, or create a new one.

    **Two modes:**

    1. **Promote** (``entity_id`` provided): finds the existing ``public.entities`` row,
       clears ``metadata->>'unidentified'``, and sets ``canonical_name`` (and optionally
       ``entity_type`` / ``roles``).
    2. **Create** (``entity_id`` omitted): inserts a brand-new canonical entity row.

    In both modes, ``initial_facts`` (optional) are asserted via
    ``relationship_assert_fact()`` inside the same database transaction.
    An unregistered predicate causes the whole request to fail with HTTP 422.

    **Authorization**: owner-only gate (Amendment 12a) — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.

    **Response**: the promoted or created ``EntitySummary`` (HTTP 201 Created).

    **Error codes:**
    - ``404`` — ``entity_id`` provided but entity does not exist.
    - ``403`` — owner entity not registered (Amendment 12a).
    - ``422`` — unregistered predicate in ``initial_facts``, or validation failure.
    """
    from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

    pool = _pool(db)

    # Amendment 12a: owner-only write gate (roles-aware, see _assert_owner_role).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    async with pool.acquire() as conn:
        async with conn.transaction():
            if body.entity_id is not None:
                # --- Promote path: update the existing unidentified entity ---
                row = await conn.fetchrow(
                    """
                    SELECT id, canonical_name, entity_type, aliases, roles, metadata,
                           created_at, updated_at
                    FROM public.entities
                    WHERE id = $1
                    """,
                    body.entity_id,
                )
                if row is None:
                    raise HTTPException(status_code=404, detail="Entity not found")

                # Merge metadata: remove the 'unidentified' key and 'duplicate_candidate' key.
                existing_meta: dict = dict(row["metadata"]) if row["metadata"] else {}
                existing_meta.pop("unidentified", None)
                existing_meta.pop("duplicate_candidate", None)

                if body.roles is not None:
                    new_roles = body.roles
                else:
                    new_roles = list(row["roles"]) if row["roles"] else []

                updated = await conn.fetchrow(
                    """
                    UPDATE public.entities
                    SET canonical_name = $2,
                        entity_type    = $3,
                        roles          = $4,
                        metadata       = $5,
                        updated_at     = now()
                    WHERE id = $1
                    RETURNING id, canonical_name, entity_type, aliases, roles, metadata,
                              created_at, updated_at
                    """,
                    body.entity_id,
                    body.canonical_name,
                    body.entity_type,
                    new_roles,
                    existing_meta,
                )
                entity_id = updated["id"]
                result_row = updated

            else:
                # --- Create path: insert a new canonical entity ---
                new_roles = body.roles if body.roles is not None else []
                result_row = await conn.fetchrow(
                    """
                    INSERT INTO public.entities
                        (canonical_name, entity_type, roles, metadata, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, now(), now())
                    RETURNING id, canonical_name, entity_type, aliases, roles, metadata,
                              created_at, updated_at
                    """,
                    body.canonical_name,
                    body.entity_type,
                    new_roles,
                    {},
                )
                entity_id = result_row["id"]

            # Assert any initial_facts via the central writer (inside the same transaction).
            for fact in body.initial_facts:
                try:
                    await relationship_assert_fact(
                        pool,
                        subject=entity_id,
                        predicate=fact.predicate,
                        object=fact.object,
                        src="relationship",
                        object_kind=fact.object_kind,
                        conf=fact.conf,
                        primary=fact.primary,
                        conn=conn,
                    )
                except ValueError as exc:
                    raise HTTPException(
                        status_code=422,
                        detail={"code": "invalid_predicate", "message": str(exc)},
                    )

            # Fetch aggregated facts stats inside the same transaction for read-after-write
            # consistency (avoids an extra pool round-trip and ensures the just-written facts
            # are visible).
            stats_row = await conn.fetchrow(
                """
                SELECT
                    MAX(last_seen) AS last_seen,
                    COUNT(*) FILTER (
                        WHERE predicate = ANY($2::text[])
                          AND validity = 'active'
                    ) AS contact_fact_count,
                    (
                        SELECT f2.object::int
                        FROM relationship.entity_facts f2
                        WHERE f2.subject = $1
                          AND f2.predicate = 'dunbar_tier_override'
                          AND f2.validity = 'active'
                        LIMIT 1
                    ) AS tier
                FROM relationship.entity_facts
                WHERE subject = $1
                  AND validity = 'active'
                """,
                entity_id,
                list(_HAS_CONTACT_PREDICATES),
            )

    return EntitySummary(
        id=result_row["id"],
        canonical_name=result_row["canonical_name"],
        entity_type=result_row["entity_type"],
        aliases=result_row["aliases"] or [],
        roles=result_row["roles"] or [],
        metadata=result_row["metadata"] or {},
        tier=stats_row["tier"],
        last_seen=stats_row["last_seen"],
        contact_fact_count=stats_row["contact_fact_count"] or 0,
        created_at=result_row["created_at"],
        updated_at=result_row["updated_at"],
    )


# ---------------------------------------------------------------------------
# GET /entities/queue — curation queue (entity-redesign Phase 2, bu-t1zfd)
# ---------------------------------------------------------------------------

#: Number of days without a ``last_seen`` update before an entity is stale.
_STALE_DAYS = 365

#: Predicates used for deterministic duplicate-candidate detection.
_DUP_DETECTION_PREDICATES = ("has-email", "has-phone")


def _dismissed_pair_suppression_sql(entity_id_sql: str, predicate_sql: str, value_sql: str) -> str:
    """SQL clause suppressing a duplicate-candidate row only when every peer is dismissed.

    A duplicate-candidate row for the entity at ``entity_id_sql`` (e.g. ``e.id``
    or a bound ``$1``) carrying evidence ``(predicate_sql, value_sql)`` represents
    that entity's membership in the dup group for that value. The group can have
    more than one peer (entity X may share value V with both Y and Z). The row is
    suppressed iff **every** current peer that shares this exact value with the
    entity has a dismissed ``merge_reviews`` row against the entity whose
    ``shared_facts`` snapshot already covered this ``(predicate, object)`` (per
    ``relationship-entity-lifecycle`` queue derivation +
    ``relationship-merge-review`` dismissal-suppression).

    Suppression is therefore keyed on the **peer pair**, not on the entity x
    evidence. If even one peer sharing the value is undismissed (or its dismissal
    snapshot did not cover this ``(predicate, value)``), the row MUST surface — so
    dismissing X-Y never hides a still-live X-Z candidate for the same value, and
    new shared evidence re-raises the pair.

    The clause is deterministic and order-independent on the pair: each peer is
    matched against both ``(entity_a, entity_b)`` column orderings.
    """
    return f"""
        EXISTS (
            -- A peer that shares this exact value with the entity but has NOT
            -- been dismissed against it (for this {{predicate, value}}). The
            -- presence of any such peer keeps the row in the queue; only when
            -- every sharing peer is dismissed (or there is no sharing peer at
            -- all) does this clause evaluate false and suppress the row.
            SELECT 1
            FROM relationship.entity_facts peer_f
            WHERE peer_f.predicate = {predicate_sql}
              AND peer_f.object = {value_sql}
              AND peer_f.validity = 'active'
              AND peer_f.subject <> {entity_id_sql}
              AND NOT EXISTS (
                  SELECT 1
                  FROM relationship.merge_reviews mr
                  WHERE mr.outcome = 'dismissed'
                    -- The dismissal must be between this entity and this peer,
                    -- in either column ordering.
                    AND (
                        (mr.entity_a = {entity_id_sql} AND mr.entity_b = peer_f.subject)
                        OR (mr.entity_a = peer_f.subject AND mr.entity_b = {entity_id_sql})
                    )
                    -- The dismissal snapshot already covered this {{predicate, value}}.
                    AND EXISTS (
                        SELECT 1
                        FROM jsonb_array_elements(mr.shared_facts) AS sf
                        WHERE sf->>'predicate' = {predicate_sql}
                          AND sf->>'object' = {value_sql}
                    )
              )
        )
    """


#: Default and maximum page sizes for the queue endpoint.
_QUEUE_DEFAULT_LIMIT = 50
_QUEUE_MAX_LIMIT = 200


async def _classify_entity_state(pool, entity_id: UUID) -> tuple[str, dict | None]:
    """Classify a single entity into its highest-priority curation bucket.

    Returns ``(state, evidence)`` where ``state`` is one of:
    ``'healthy'``, ``'unidentified'``, ``'duplicate-candidate'``, ``'stale'``.

    Classification logic and priority order are identical to ``GET /entities/queue``:
    1. unidentified — ``metadata->>'unidentified' = 'true'``
    2. duplicate-candidate — metadata flag OR shared has-email/has-phone fact value
    3. stale — no active fact with ``last_seen`` within the past 365 days

    Evidence shape per state:
    - ``unidentified`` — ``{}``
    - ``duplicate-candidate`` (metadata flag only) — ``{}``
    - ``duplicate-candidate`` (shared fact) — ``{"predicate": ..., "shared_value": ...,
      "peer_entity_ids": [...]}``
    - ``stale`` — ``{"last_seen": "<iso-datetime>|null"}``
    - ``healthy`` — ``None``

    Canonical semantics live in ``get_entities_queue``; keep this helper in sync
    with any changes to the queue SQL.
    """
    dup_predicates_literal = ", ".join(f"'{p}'" for p in _DUP_DETECTION_PREDICATES)

    row = await pool.fetchrow(
        f"""
        WITH entity AS (
            SELECT
                metadata->>'unidentified' = 'true'          AS is_unidentified,
                metadata->>'duplicate_candidate' = 'true'   AS is_dup_flagged,
                (
                    SELECT max(rf.last_seen)
                    FROM relationship.entity_facts rf
                    WHERE rf.subject = $1
                      AND rf.validity = 'active'
                ) AS last_seen,
                EXISTS (
                    SELECT 1 FROM relationship.entity_facts rf
                    WHERE rf.subject = $1
                      AND rf.validity = 'active'
                      AND rf.last_seen > (now() - INTERVAL '{_STALE_DAYS} days')
                ) AS has_fresh_fact,
                NOT ({_not_queue_dismissed_sql("$1")}) AS is_dismissed
            FROM public.entities e
            WHERE e.id = $1
              AND {_active_entity_condition("e")}
        ),
        dup_detected AS (
            SELECT
                grp.predicate,
                grp.object AS shared_value,
                (
                    SELECT json_agg(DISTINCT f2.subject::text)
                    FROM relationship.entity_facts f2
                    WHERE f2.predicate = grp.predicate
                      AND f2.object = grp.object
                      AND f2.validity = 'active'
                      AND f2.subject <> $1
                ) AS peer_entity_ids
            FROM (
                SELECT predicate, object
                FROM relationship.entity_facts
                WHERE predicate IN ({dup_predicates_literal})
                  AND validity = 'active'
                GROUP BY predicate, object
                HAVING count(DISTINCT subject) > 1
            ) AS grp
            JOIN relationship.entity_facts f_link
                ON f_link.subject = $1
               AND f_link.predicate = grp.predicate
               AND f_link.object = grp.object
               AND f_link.validity = 'active'
            WHERE {_dismissed_pair_suppression_sql("$1", "grp.predicate", "grp.object")}
            LIMIT 1
        )
        SELECT
            e.is_unidentified,
            e.is_dup_flagged,
            e.has_fresh_fact,
            e.is_dismissed,
            e.last_seen,
            d.predicate        AS dup_predicate,
            d.shared_value     AS dup_shared_value,
            d.peer_entity_ids  AS dup_peer_entity_ids
        FROM entity e
        LEFT JOIN dup_detected d ON true
        """,
        entity_id,
    )

    if row is None:
        # Entity not found — caller will have already raised 404 before calling this.
        return "healthy", None

    # Dismissed entities are removed from the curation queue entirely; surface
    # them as healthy so the detail view doesn't show a stale curation badge.
    if row["is_dismissed"]:
        return "healthy", None

    if row["is_unidentified"]:
        return "unidentified", {}

    if row["is_dup_flagged"] or row["dup_predicate"] is not None:
        if row["dup_predicate"] is not None:
            peer_ids = row["dup_peer_entity_ids"]
            evidence: dict = {
                "predicate": row["dup_predicate"],
                "shared_value": row["dup_shared_value"],
                "peer_entity_ids": peer_ids if isinstance(peer_ids, list) else [],
            }
        else:
            evidence = {}
        return "duplicate-candidate", evidence

    if not row["has_fresh_fact"]:
        last_seen_val = row["last_seen"]
        return "stale", {"last_seen": str(last_seen_val) if last_seen_val is not None else None}

    return "healthy", None


@router.get("/entities/queue", response_model=QueueResponse)
async def get_entities_queue(
    limit: int = Query(_QUEUE_DEFAULT_LIMIT, ge=1, le=_QUEUE_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> QueueResponse:
    """Return the curation queue — entities needing operator attention.

    Returns a UNION of three buckets, in section order per spec §1:

    1. **unidentified** — entities with ``metadata->>'unidentified' = 'true'``.
    2. **duplicate-candidate** — entities where ``metadata->>'duplicate_candidate' = 'true'``
       OR that share a ``has-email`` / ``has-phone`` fact value with at least one other
       entity (deterministic SQL; no LLM, no embedding).
    3. **stale** — entities with no active ``relationship.entity_facts`` fact whose
       ``last_seen`` is within the past 365 days.

    **Deduplication:** an entity can appear in at most one bucket per call.
    Priority: unidentified > duplicate-candidate > stale.  Entities that match
    multiple buckets appear only in their highest-priority bucket.

    **Owner-only authz gate (Clause 12b, Amendment 12b):** returns HTTP 403
    with ``{"code": "owner_required"}`` if no owner entity is registered.

    **Pagination:** ``limit`` (default 50, max 200) + ``offset`` (default 0).
    ``total`` reflects the pre-pagination count across all three buckets.

    ``evidence`` carries bucket-specific detail:

    - ``unidentified`` — ``{}`` (no additional evidence).
    - ``duplicate-candidate`` — ``{"predicate": "<has-email|has-phone>",
      "shared_value": "<value>", "peer_entity_ids": ["<uuid>", ...]}``.
      When the entity is flagged via ``metadata->>'duplicate_candidate' = 'true'``
      but no shared fact is detected, ``evidence`` is ``{}``.
    - ``stale`` — ``{"last_seen": "<iso-datetime>|null"}``.
    """
    pool = _pool(db)

    # Owner-only gate (Clause 12b) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    dup_predicates_literal = ", ".join(f"'{p}'" for p in _DUP_DETECTION_PREDICATES)

    # -------------------------------------------------------------------
    # SQL: three buckets, materialised in application order.
    # Each bucket query returns: entity_id, canonical_name, entity_type,
    #   last_seen, bucket, evidence (as JSON text).
    #
    # Bucket 1 — unidentified
    # -------------------------------------------------------------------
    unidentified_sql = f"""
        SELECT
            e.id            AS entity_id,
            e.canonical_name,
            e.entity_type,
            (
                SELECT max(rf.last_seen)
                FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.validity = 'active'
            ) AS last_seen,
            'unidentified'::text AS bucket,
            '{{}}'::jsonb AS evidence_json
        FROM public.entities e
        WHERE (e.metadata->>'unidentified')::text = 'true'
          AND {_active_entity_condition("e")}
          AND {_not_queue_dismissed_sql("e.id")}
    """

    # -------------------------------------------------------------------
    # Bucket 2 — duplicate-candidate
    # Sourced from two sub-buckets merged via UNION:
    #   2a. metadata flag
    #   2b. shared has-email / has-phone value detected via self-join
    # -------------------------------------------------------------------
    dup_metadata_sql = f"""
        SELECT
            e.id            AS entity_id,
            e.canonical_name,
            e.entity_type,
            (
                SELECT max(rf.last_seen)
                FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.validity = 'active'
            ) AS last_seen,
            'duplicate-candidate'::text AS bucket,
            '{{}}'::jsonb AS evidence_json
        FROM public.entities e
        WHERE (e.metadata->>'duplicate_candidate')::text = 'true'
          AND (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND {_active_entity_condition("e")}
          AND {_not_queue_dismissed_sql("e.id")}
    """

    # Deterministic dup-detection: find entities sharing a has-email / has-phone value.
    # The self-join groups by (predicate, object) and emits any entity_id in a
    # group with >1 distinct entity.  We exclude entities already in unidentified.
    dup_detected_sql = f"""
        SELECT
            e.id            AS entity_id,
            e.canonical_name,
            e.entity_type,
            (
                SELECT max(rf.last_seen)
                FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.validity = 'active'
            ) AS last_seen,
            'duplicate-candidate'::text AS bucket,
            json_build_object(
                'predicate', grp.predicate,
                'shared_value', grp.object,
                'peer_entity_ids', (
                    SELECT json_agg(DISTINCT f2.subject::text)
                    FROM relationship.entity_facts f2
                    WHERE f2.predicate = grp.predicate
                      AND f2.object = grp.object
                      AND f2.validity = 'active'
                      AND f2.subject <> e.id
                )
            )::jsonb AS evidence_json
        FROM public.entities e
        CROSS JOIN (
            SELECT predicate, object
            FROM relationship.entity_facts
            WHERE predicate IN ({dup_predicates_literal})
              AND validity = 'active'
            GROUP BY predicate, object
            HAVING count(DISTINCT subject) > 1
        ) AS grp
        JOIN relationship.entity_facts f_link
            ON f_link.subject = e.id
           AND f_link.predicate = grp.predicate
           AND f_link.object = grp.object
           AND f_link.validity = 'active'
        WHERE (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND {_active_entity_condition("e")}
          AND {_not_queue_dismissed_sql("e.id")}
          AND {_dismissed_pair_suppression_sql("e.id", "grp.predicate", "grp.object")}
    """

    # -------------------------------------------------------------------
    # Bucket 3 — stale
    # The ranked CTE deduplicates across all buckets; no per-bucket
    # exclusion of higher-priority entities is needed here.
    # -------------------------------------------------------------------
    stale_sql = f"""
        SELECT
            e.id            AS entity_id,
            e.canonical_name,
            e.entity_type,
            (
                SELECT max(rf.last_seen)
                FROM relationship.entity_facts rf
                WHERE rf.subject = e.id
                  AND rf.validity = 'active'
            ) AS last_seen,
            'stale'::text AS bucket,
            json_build_object(
                'last_seen',
                (
                    SELECT max(rf.last_seen)::text
                    FROM relationship.entity_facts rf
                    WHERE rf.subject = e.id
                      AND rf.validity = 'active'
                )
            )::jsonb AS evidence_json
        FROM public.entities e
        WHERE (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND {_active_entity_condition("e")}
          AND {_not_queue_dismissed_sql("e.id")}
          AND NOT EXISTS (
              SELECT 1 FROM relationship.entity_facts rf
              WHERE rf.subject = e.id
                AND rf.validity = 'active'
                AND rf.last_seen > (now() - INTERVAL '{_STALE_DAYS} days')
          )
    """

    # -------------------------------------------------------------------
    # Combine: deduplicate within same entity_id using ranked CTE.
    # Priority: unidentified(1) > duplicate-candidate(2) > stale(3).
    # -------------------------------------------------------------------
    combined_sql = f"""
        WITH raw AS (
            {unidentified_sql}
            UNION ALL
            {dup_metadata_sql}
            UNION ALL
            {dup_detected_sql}
            UNION ALL
            {stale_sql}
        ),
        ranked AS (
            SELECT *,
                row_number() OVER (
                    PARTITION BY entity_id
                    ORDER BY
                        CASE bucket
                            WHEN 'unidentified'          THEN 1
                            WHEN 'duplicate-candidate'   THEN 2
                            WHEN 'stale'                 THEN 3
                        END,
                        canonical_name
                ) AS rn
            FROM raw
        ),
        deduped AS (
            SELECT entity_id, canonical_name, entity_type, last_seen, bucket, evidence_json
            FROM ranked
            WHERE rn = 1
        )
    """

    count_sql = f"{combined_sql} SELECT count(*) FROM deduped"

    data_sql = f"""
        {combined_sql}
        SELECT entity_id, canonical_name, entity_type, last_seen, bucket, evidence_json
        FROM deduped
        ORDER BY
            CASE bucket
                WHEN 'unidentified'        THEN 1
                WHEN 'duplicate-candidate' THEN 2
                WHEN 'stale'               THEN 3
            END,
            canonical_name
        OFFSET $1 LIMIT $2
    """

    total_raw, rows = await asyncio.gather(
        pool.fetchval(count_sql),
        pool.fetch(data_sql, offset, limit),
    )
    total = total_raw or 0

    items = [
        QueueEntry(
            entity_id=r["entity_id"],
            canonical_name=r["canonical_name"],
            entity_type=r["entity_type"],
            bucket=r["bucket"],
            evidence=r["evidence_json"] if isinstance(r["evidence_json"], dict) else {},
            last_seen=r["last_seen"],
        )
        for r in rows
    ]

    return QueueResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# POST /entities/queue/dismiss — dismiss entity from curation queue (bu-297lj)
# ---------------------------------------------------------------------------
# Predicate/object constants for the queue-dismissed triple are defined near
# ``_active_entity_condition`` (shared with the queue list filter).


@router.post(
    "/entities/queue/dismiss",
    response_model=DismissQueueResponse,
)
async def dismiss_queue_entity(
    body: DismissQueueRequest,
    fastapi_response: Response,
    db: DatabaseManager = Depends(_get_db_manager),
) -> DismissQueueResponse:
    """Dismiss a single entity from the curation queue.

    Writes a ``queue.dismissed`` state-marker triple via the central writer
    ``relationship_assert_fact()``.  The triple has the form::

        subject  = body.entity_id
        predicate = 'queue.dismissed'
        object   = 'dismissed'

    The operation is idempotent: re-dismissing an already-dismissed entity
    returns ``outcome='unchanged'`` without modifying any rows.

    **Authorization**: owner-only gate (Amendment 12a) — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.

    **Owner entity carve-out (RFC 0017 §2.3):** when the target entity has the
    ``'owner'`` role, the central writer parks the write as a ``pending_actions``
    row.  In this case the endpoint returns HTTP 202 with
    ``status='pending_approval'`` and the per-entity ``action_id`` set.

    **Error codes:**
    - ``403`` — owner entity not registered (Amendment 12a).
    - ``404`` — entity does not exist in ``public.entities``.
    - ``422`` — predicate not registered (should not happen in production;
      indicates a missing migration).
    """
    from butlers.tools.relationship.relationship_assert_fact import (
        AssertOutcome,
        relationship_assert_fact,
    )

    pool = _pool(db)

    # Amendment 12a: owner-only write gate (roles-aware, see _assert_owner_role).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check (exclude tombstoned entities).
    entity_row = await pool.fetchrow(
        "SELECT id FROM public.entities WHERE id = $1 AND (metadata->>'merged_into') IS NULL",
        body.entity_id,
    )
    if entity_row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    # Write the state-marker triple via the central writer.
    try:
        result = await relationship_assert_fact(
            pool,
            subject=body.entity_id,
            predicate=_QUEUE_DISMISSED_PREDICATE,
            object=_QUEUE_DISMISSED_OBJECT,
            src="relationship",
            object_kind="literal",
            conf=1.0,
            verified=False,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_predicate", "message": str(exc)},
        )

    item = DismissQueueItemResult(
        entity_id=body.entity_id,
        outcome=result.outcome.value,
        fact_id=result.fact_id,
        action_id=result.action_id,
    )

    if result.outcome == AssertOutcome.pending_approval:
        fastapi_response.status_code = 202
        return DismissQueueResponse(dismissed=[item], status="pending_approval")

    return DismissQueueResponse(dismissed=[item], status="ok")


# ---------------------------------------------------------------------------
# GET /entities/concentration — weight aggregation by predicate (bu-0vosj)
# ---------------------------------------------------------------------------

#: Default predicate for the concentration view when no ``?pred=`` is given.
_CONCENTRATION_DEFAULT_PREDICATE = "knows"


@router.get("/entities/concentration", response_model=ConcentrationResponse)
async def get_entities_concentration(
    pred: str = Query(
        _CONCENTRATION_DEFAULT_PREDICATE,
        description=(
            "Relational predicate to aggregate.  Must be a predicate registered in "
            "``relationship.entity_predicate_registry`` with ``kind='relational'``.  "
            "Defaults to ``'knows'``."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ConcentrationResponse:
    """Return a balance-sheet of weight aggregation for a relational predicate.

    Aggregates all active ``relationship.entity_facts`` triples for the given
    ``pred`` (predicate), grouping by subject entity.  Each row shows the
    entity's total edge weight for that predicate, its share of the overall
    weight sum, and provenance from the most-recent contributing triple.

    **Owner-only authz gate (Clause 12b, Amendment 12b):** returns HTTP 403
    with ``{"code": "owner_required"}`` if no owner entity is registered.

    **Predicate tabs:** the response always includes ``predicate_tabs`` — the
    full list of relational predicates from ``relationship.entity_predicate_registry``
    — so the frontend can render the tab strip without a separate request.

    **Aggregation logic:** ``weight_sum`` is the SUM of fact ``weight`` values,
    treating NULLs as 1 (so every edge contributes at least 1 to the aggregate
    even when ``weight`` is not set).  ``fact_count`` is the raw row count.

    **Sort order:** ``weight_sum DESC``, then ``canonical_name ASC`` for
    stability.  The full ranked list is returned (no pagination); callers
    should expect O(entity_count) rows.

    **Filter:** only facts with ``validity='active'`` are included.
    ``relationship.entity_facts`` has no ``scope`` column; schema isolation is enforced
    via the ``relationship.`` schema prefix (RFC 0006).

    Response shape::

        {
          "predicate": "knows",
          "items": [
            {
              "entity_id": "<uuid>",
              "canonical_name": "Alice",
              "weight_sum": 12,
              "fact_count": 4,
              "share": 0.48,
              "last_seen": "<iso-datetime>",
              "src": "relationship",
              "conf": 1.0,
              "verified": false,
              "primary": null,
              "targets": [
                {"name": "Acme Corp", "entity_id": "<uuid>", "object_kind": "entity"}
              ]
            },
            ...
          ],
          "rollup": {"total": 25, "top3_share": 0.88},
          "predicate_tabs": [
            {"predicate": "knows", "label": "Knows", "description": null},
            ...
          ],
          "total": 5
        }
    """
    pool = _pool(db)

    # Owner-only gate (Clause 12b) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # -------------------------------------------------------------------
    # 1. Fetch predicate tabs from registry (kind='relational').
    # -------------------------------------------------------------------
    # NOTE: ``entity_predicate_registry`` has NO ``label`` column (see migration
    # 014_predicate_registry) — only predicate/kind/object_kind/description.
    # Derive a human-readable label from the kebab-case predicate slug in SQL:
    # ``initcap(replace(predicate, '-', ' '))`` turns ``partner-of`` → ``Partner
    # Of`` and ``knows`` → ``Knows``. Sort by the derived label for a stable,
    # alphabetical tab strip.
    tab_rows = await pool.fetch(
        """
        SELECT
            reg.predicate,
            initcap(replace(reg.predicate, '-', ' ')) AS label,
            reg.description,
            COUNT(DISTINCT f.subject)::int AS entity_count
        FROM relationship.entity_predicate_registry reg
        LEFT JOIN relationship.entity_facts f
            ON f.predicate = reg.predicate
            AND f.validity = 'active'
        WHERE reg.kind = 'relational'
        GROUP BY reg.predicate, reg.description
        ORDER BY label ASC
        """
    )
    predicate_tabs = [
        PredicateTab(
            predicate=r["predicate"],
            label=r["label"],
            description=r["description"],
            entity_count=r["entity_count"],
        )
        for r in tab_rows
    ]

    # Validate that the requested predicate is relational; default silently
    # to the first relational predicate if pred is unknown or not relational.
    known_relational = {t.predicate for t in predicate_tabs}
    active_predicate = pred if pred in known_relational else _CONCENTRATION_DEFAULT_PREDICATE
    if active_predicate not in known_relational and predicate_tabs:
        active_predicate = predicate_tabs[0].predicate

    # -------------------------------------------------------------------
    # 2. Aggregate weight for the active predicate.
    #
    # For each subject entity aggregate:
    #   weight_sum  = SUM(COALESCE(weight, 1))
    #   fact_count  = COUNT(*)
    #   last_seen   = MAX(last_seen)
    #
    # Provenance is taken from the most-recent triple (DISTINCT ON ordering
    # by last_seen DESC, created_at DESC within the per-entity window).
    # -------------------------------------------------------------------
    agg_rows = await pool.fetch(
        """
        WITH agg AS (
            SELECT
                f.subject                               AS entity_id,
                SUM(COALESCE(f.weight, 1))              AS weight_sum,
                COUNT(*)                                AS fact_count,
                MAX(f.last_seen)                        AS last_seen
            FROM relationship.entity_facts f
            WHERE f.predicate = $1
              AND f.validity = 'active'
            GROUP BY f.subject
        ),
        prov AS (
            -- Provenance from the most-recent contributing triple per entity.
            SELECT DISTINCT ON (f.subject)
                f.subject                               AS entity_id,
                f.src,
                f.conf,
                f.verified,
                f."primary"
            FROM relationship.entity_facts f
            WHERE f.predicate = $1
              AND f.validity = 'active'
            ORDER BY f.subject, f.last_seen DESC NULLS LAST, f.created_at DESC
        )
        SELECT
            e.id                AS entity_id,
            e.canonical_name,
            a.weight_sum,
            a.fact_count,
            a.last_seen,
            p.src,
            p.conf,
            p.verified,
            p."primary",
            -- Targets ("where" the predicate points): one object per active
            -- contributing triple.  Entity-kind objects resolve to the target
            -- entity's canonical_name + id (rendered as a hyperlink); literal
            -- objects surface the raw value.  The ``object::uuid`` cast is
            -- guarded by ``object_kind = 'entity'`` (CASE short-circuits) so
            -- literal objects never attempt the cast.
            COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_object(
                        'name', COALESCE(te.canonical_name, tf.object),
                        'entity_id',
                            CASE WHEN tf.object_kind = 'entity' THEN tf.object
                                 ELSE NULL END,
                        'object_kind', tf.object_kind
                    )
                    ORDER BY COALESCE(te.canonical_name, tf.object) ASC
                )
                FROM relationship.entity_facts tf
                LEFT JOIN public.entities te
                    ON tf.object_kind = 'entity'
                   AND te.id = CASE WHEN tf.object_kind = 'entity'
                                    THEN tf.object::uuid ELSE NULL END
                WHERE tf.subject = a.entity_id
                  AND tf.predicate = $1
                  AND tf.validity = 'active'
            ), '[]'::jsonb) AS targets
        FROM agg a
        JOIN prov p ON p.entity_id = a.entity_id
        JOIN public.entities e ON e.id = a.entity_id
        ORDER BY a.weight_sum DESC, e.canonical_name ASC
        """,
        active_predicate,
    )

    # -------------------------------------------------------------------
    # 3. Compute rollup (total weight_sum and top-3 share).
    # -------------------------------------------------------------------
    total_weight: int = sum(r["weight_sum"] for r in agg_rows)
    top3_weight: int = sum(r["weight_sum"] for r in agg_rows[:3])
    top3_share: float | None = (top3_weight / total_weight) if total_weight > 0 else None

    # -------------------------------------------------------------------
    # 4. Build response items.
    # -------------------------------------------------------------------
    items: list[ConcentrationEntry] = []
    for r in agg_rows:
        ws = r["weight_sum"]
        share: float | None = (ws / total_weight) if total_weight > 0 else None
        # ``targets`` arrives as a decoded jsonb array (list[dict]) via the
        # registered jsonb codec; tolerate None/missing for robustness.
        targets = [
            ConcentrationTarget(
                name=t.get("name") or "(unknown)",
                entity_id=t.get("entity_id"),
                object_kind=t.get("object_kind") or "literal",
            )
            for t in (r["targets"] or [])
        ]
        items.append(
            ConcentrationEntry(
                entity_id=r["entity_id"],
                canonical_name=r["canonical_name"],
                weight_sum=ws,
                fact_count=r["fact_count"],
                share=share,
                last_seen=r["last_seen"],
                src=r["src"],
                conf=r["conf"] if r["conf"] is not None else 1.0,
                verified=r["verified"] if r["verified"] is not None else False,
                primary=r["primary"],
                targets=targets,
            )
        )

    return ConcentrationResponse(
        predicate=active_predicate,
        items=items,
        rollup=ConcentrationRollup(
            total=total_weight,
            top3_share=top3_share,
        ),
        predicate_tabs=predicate_tabs,
        total=len(items),
    )


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityDetail:
    """Get full entity detail including entity_info entries and state classification.

    Secured entity_info values are masked (value=None) in the response.
    Use GET /entities/{id}/secrets/{info_id} to reveal a secured value.

    ``state`` reflects the highest-priority curation bucket this entity belongs to
    (``'healthy'``, ``'unidentified'``, ``'duplicate-candidate'``, or ``'stale'``),
    using the same classification logic as ``GET /entities/queue``.

    ``state_evidence`` mirrors the ``evidence`` dict from the queue for non-healthy
    states, or ``null`` for healthy entities.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT id, canonical_name, entity_type, aliases, roles,
               metadata, created_at, updated_at
        FROM public.entities
        WHERE id = $1
          AND (metadata->>'merged_into') IS NULL
        """,
        entity_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    info_rows, (state, state_evidence) = await asyncio.gather(
        pool.fetch(
            """
            SELECT id, type, value, label, is_primary, secured
            FROM public.entity_info
            WHERE entity_id = $1
            ORDER BY type
            """,
            entity_id,
        ),
        _classify_entity_state(pool, entity_id),
    )

    entity_info = [
        EntityInfoEntry(
            id=r["id"],
            type=r["type"],
            value=None if r["secured"] else r["value"],
            label=r["label"],
            is_primary=r["is_primary"],
            secured=r["secured"],
        )
        for r in info_rows
    ]

    aliases = list(row["aliases"]) if row["aliases"] else []
    roles = list(row["roles"]) if row["roles"] else []
    _raw_meta = row["metadata"]
    # JSONB codec contract: asyncpg decodes JSONB to dict; guard is defensive only.
    metadata = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}

    return EntityDetail(
        id=row["id"],
        canonical_name=row["canonical_name"],
        entity_type=row["entity_type"],
        aliases=aliases,
        roles=roles,
        metadata=metadata,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        entity_info=entity_info,
        state=state,
        state_evidence=state_evidence,
    )


# ---------------------------------------------------------------------------
# POST /entities/{entity_id}/info
# ---------------------------------------------------------------------------


@router.post(
    "/entities/{entity_id}/info",
    response_model=CreateEntityInfoResponse,
    status_code=201,
)
async def create_entity_info(
    entity_id: UUID,
    request: CreateEntityInfoRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> CreateEntityInfoResponse:
    """Add an entity_info entry to an entity."""
    pool = _pool(db)

    # Seam-law guard (RFC 0004 Amendment 3, bu-oluyt.1): entity_info is a
    # secrets store.  Non-secret channel handles must go to entity_facts.
    try:
        assert_entity_info_secured(request.type, request.secured)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Verify entity exists and is not tombstoned
    existing = await pool.fetchrow(
        """
        SELECT id FROM public.entities
        WHERE id = $1 AND (metadata->>'merged_into') IS NULL
        """,
        entity_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO public.entity_info
                (entity_id, type, value, label, is_primary, secured)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, entity_id, type, value, label, is_primary, secured
            """,
            entity_id,
            request.type,
            request.value,
            request.label,
            request.is_primary,
            request.secured,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=(
                f"An entity_info entry with type '{request.type}' already exists for this entity."
            ),
        )

    return CreateEntityInfoResponse(
        id=row["id"],
        entity_id=row["entity_id"],
        type=row["type"],
        value=row["value"],
        label=row["label"],
        is_primary=row["is_primary"],
        secured=row["secured"],
    )


# ---------------------------------------------------------------------------
# PATCH /entities/{entity_id}/info/{info_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/entities/{entity_id}/info/{info_id}",
    response_model=EntityInfoEntry,
)
async def patch_entity_info(
    entity_id: UUID,
    info_id: UUID,
    request: UpdateEntityInfoRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityInfoEntry:
    """Update an entity_info entry (type, value, label, is_primary)."""
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id FROM public.entity_info WHERE id = $1 AND entity_id = $2",
        info_id,
        entity_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity info entry not found")

    updates: list[str] = []
    args: list[Any] = []
    idx = 1

    if request.type is not None:
        updates.append(f"type = ${idx}")
        args.append(request.type)
        idx += 1

    if request.value is not None:
        updates.append(f"value = ${idx}")
        args.append(request.value)
        idx += 1

    if request.label is not None:
        updates.append(f"label = ${idx}")
        args.append(request.label)
        idx += 1

    if request.is_primary is not None:
        updates.append(f"is_primary = ${idx}")
        args.append(request.is_primary)
        idx += 1

    if updates:
        set_clause = ", ".join(updates)
        args.append(info_id)
        try:
            await pool.execute(
                f"UPDATE public.entity_info SET {set_clause} WHERE id = ${idx}",
                *args,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=409,
                detail="An entity_info entry with this type already exists for this entity.",
            )

    updated = await pool.fetchrow(
        "SELECT id, type, value, label, is_primary, secured FROM public.entity_info WHERE id = $1",
        info_id,
    )
    return EntityInfoEntry(
        id=updated["id"],
        type=updated["type"],
        value=None if updated["secured"] else updated["value"],
        label=updated["label"],
        is_primary=updated["is_primary"],
        secured=updated["secured"],
    )


# ---------------------------------------------------------------------------
# DELETE /entities/{entity_id}/info/{info_id}
# ---------------------------------------------------------------------------


@router.delete("/entities/{entity_id}/info/{info_id}", status_code=204)
async def delete_entity_info(
    entity_id: UUID,
    info_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Delete a single entity_info entry."""
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id FROM public.entity_info WHERE id = $1 AND entity_id = $2",
        info_id,
        entity_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity info entry not found")

    await pool.execute("DELETE FROM public.entity_info WHERE id = $1", info_id)


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/secrets/{info_id}
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/secrets/{info_id}")
async def reveal_entity_secret(
    entity_id: UUID,
    info_id: UUID,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict[str, Any]:
    """Reveal the actual value of a secured entity_info entry.

    Credential rows written via the contact_info write-path cut-over
    (secured=True) land in ``public.entity_info`` per RFC 0004 Amendment 2.
    This endpoint is the sole authorized reveal path for those rows.

    **Authorization**: owner-only gate — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.
    This mirrors the gate applied to other PII-bearing read surfaces
    (Amendment 12b).

    Returns the real value for a secured entity_info row. Returns 404 if
    the info_id does not exist OR does not belong to the given entity_id.
    Returns 400 if the entry exists but is not secured (value is already
    available in the entity detail response).
    """
    pool = _pool(db)

    # Owner-only gate (Amendment 12b) — credential reveal is owner-only.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    row = await pool.fetchrow(
        """
        SELECT id, type, value, secured
        FROM public.entity_info
        WHERE id = $1 AND entity_id = $2
        """,
        info_id,
        entity_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Entity info entry not found")

    if not row["secured"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "This entity_info entry is not secured; "
                "value is available in the entity detail response."
            ),
        )

    # Explicit audit for credential reveal (GET — middleware skips GETs).
    await emit_dashboard_audit(
        db,
        butler="relationship",
        operation="reveal_entity_secret",
        method="GET",
        path=f"/api/relationship/entities/{entity_id}/secrets/{info_id}",
        path_params={"entity_id": str(entity_id), "info_id": str(info_id)},
        body={"type": row["type"]},
        response_status=200,
        request=request,
    )

    return {"id": str(row["id"]), "type": row["type"], "value": row["value"]}


# ---------------------------------------------------------------------------
# Entity-level tab API helpers
# ---------------------------------------------------------------------------

_ENTITY_TAB_SCOPE = "relationship"
_ENTITY_TAB_VALIDITY = "active"
_ENTITY_TAB_DEFAULT_LIMIT = 50
_ENTITY_TAB_MAX_LIMIT = 200

_PREDICATE_KIND_MAP: dict[str, str] = {
    "contact_note": "note",
    "life_event": "life_event",
    "gift": "gift",
    "loan": "loan",
    "dunbar_tier_override": "dunbar_tier_override",
}


def _interaction_type(predicate: str) -> str:
    """Extract the interaction subtype from a predicate (e.g. 'interaction_meeting' → 'meeting')."""
    if predicate.startswith("interaction_"):
        return predicate[len("interaction_") :]
    return predicate


def _timeline_kind(predicate: str) -> str:
    """Map a predicate to its timeline kind label."""
    if predicate.startswith("interaction_"):
        return "interaction"
    return _PREDICATE_KIND_MAP.get(predicate, predicate)


async def _assert_entity_exists(pool: object, entity_id: UUID) -> None:
    """Raise HTTPException 404 if entity_id does not exist in public.entities."""
    exists = await pool.fetchval(
        "SELECT 1 FROM public.entities WHERE id = $1 LIMIT 1",
        entity_id,
    )
    if exists is None:
        raise HTTPException(status_code=404, detail="Entity not found")


async def _entity_has_owner_role(pool: object, entity_id: UUID) -> bool:
    """Return True if *entity_id* is the owner entity (``'owner' = ANY(roles)``).

    Used by the owner self-identity exemption on the contact-fact write surface
    (Phase 4 / RFC 0017 §2.3): a dashboard write whose subject is the owner
    entity is the owner self-registering their own channel handle and must write
    directly rather than parking in pending_actions.  Fails closed (returns
    ``False`` on DB error) so a hiccup never silently grants the bypass.
    """
    try:
        return bool(
            await pool.fetchval(
                """
                SELECT 1 FROM public.entities
                WHERE id = $1 AND 'owner' = ANY(COALESCE(roles, '{}'))
                LIMIT 1
                """,
                entity_id,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("owner-role check failed for entity %s: %s", entity_id, exc)
        return False


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/notes
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/notes", response_model=list[EntityNote])
async def list_entity_notes(
    entity_id: UUID,
    limit: int = Query(_ENTITY_TAB_DEFAULT_LIMIT, ge=1, le=_ENTITY_TAB_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntityNote]:
    """List contact_note facts for an entity, ordered by valid_at DESC.

    Returns 404 if the entity does not exist.
    Scoped to validity='active' AND scope='relationship'.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT id, content, metadata, valid_at,
               'memory_module_legacy'::text AS src,
               NULL::float AS conf,
               NULL::timestamptz AS last_seen,
               NULL::float AS weight,
               false AS verified,
               false AS "primary"
        FROM facts
        WHERE entity_id = $1
          AND predicate = 'contact_note'
          AND validity = 'active'
          AND scope = 'relationship'
        ORDER BY valid_at DESC
        OFFSET $2 LIMIT $3
        """,
        entity_id,
        offset,
        limit,
    )
    return [
        EntityNote(
            id=r["id"],
            content=r["content"],
            emotion=(r["metadata"] or {}).get("emotion"),
            created_at=r["valid_at"],
            src=r["src"],
            conf=r["conf"],
            last_seen=r["last_seen"],
            weight=r["weight"],
            verified=r["verified"],
            primary=r["primary"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/interactions
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/interactions", response_model=list[EntityInteraction])
async def list_entity_interactions(
    entity_id: UUID,
    limit: int = Query(_ENTITY_TAB_DEFAULT_LIMIT, ge=1, le=_ENTITY_TAB_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntityInteraction]:
    """List interaction facts for an entity (all interaction_* subtypes), ordered by valid_at DESC.

    Returns 404 if the entity does not exist.
    Scoped to validity='active' AND scope='relationship'.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT id, predicate, content, metadata, valid_at,
               'memory_module_legacy'::text AS src,
               NULL::float AS conf,
               NULL::timestamptz AS last_seen,
               NULL::float AS weight,
               false AS verified,
               false AS "primary"
        FROM facts
        WHERE entity_id = $1
          AND predicate LIKE 'interaction_%'
          AND validity = 'active'
          AND scope = 'relationship'
        ORDER BY valid_at DESC
        OFFSET $2 LIMIT $3
        """,
        entity_id,
        offset,
        limit,
    )
    return [
        EntityInteraction(
            id=r["id"],
            type=_interaction_type(r["predicate"]),
            summary=r["content"],
            occurred_at=r["valid_at"],
            direction=(r["metadata"] or {}).get("direction"),
            group_size=(r["metadata"] or {}).get("group_size"),
            src=r["src"],
            conf=r["conf"],
            last_seen=r["last_seen"],
            weight=r["weight"],
            verified=r["verified"],
            primary=r["primary"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/gifts
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/gifts", response_model=list[EntityGift])
async def list_entity_gifts(
    entity_id: UUID,
    limit: int = Query(_ENTITY_TAB_DEFAULT_LIMIT, ge=1, le=_ENTITY_TAB_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntityGift]:
    """List gift facts for an entity, ordered by created_at DESC.

    Returns 404 if the entity does not exist.
    Scoped to validity='active' AND scope='relationship'.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT id, content, metadata, created_at,
               'memory_module_legacy'::text AS src,
               NULL::float AS conf,
               NULL::timestamptz AS last_seen,
               NULL::float AS weight,
               false AS verified,
               false AS "primary"
        FROM facts
        WHERE entity_id = $1
          AND predicate = 'gift'
          AND validity = 'active'
          AND scope = 'relationship'
        ORDER BY created_at DESC
        OFFSET $2 LIMIT $3
        """,
        entity_id,
        offset,
        limit,
    )
    return [
        EntityGift(
            id=r["id"],
            description=r["content"],
            occasion=(r["metadata"] or {}).get("occasion"),
            status=(r["metadata"] or {}).get("status"),
            created_at=r["created_at"],
            src=r["src"],
            conf=r["conf"],
            last_seen=r["last_seen"],
            weight=r["weight"],
            verified=r["verified"],
            primary=r["primary"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/loans
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/loans", response_model=list[EntityLoan])
async def list_entity_loans(
    entity_id: UUID,
    limit: int = Query(_ENTITY_TAB_DEFAULT_LIMIT, ge=1, le=_ENTITY_TAB_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntityLoan]:
    """List loan facts for an entity, ordered by created_at DESC.

    Returns 404 if the entity does not exist.
    Scoped to validity='active' AND scope='relationship'.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT id, content, metadata, created_at,
               'memory_module_legacy'::text AS src,
               NULL::float AS conf,
               NULL::timestamptz AS last_seen,
               NULL::float AS weight,
               false AS verified,
               false AS "primary"
        FROM facts
        WHERE entity_id = $1
          AND predicate = 'loan'
          AND validity = 'active'
          AND scope = 'relationship'
        ORDER BY created_at DESC
        OFFSET $2 LIMIT $3
        """,
        entity_id,
        offset,
        limit,
    )
    return [
        EntityLoan(
            id=r["id"],
            description=r["content"],
            amount_cents=(r["metadata"] or {}).get("amount_cents"),
            currency=(r["metadata"] or {}).get("currency"),
            direction=(r["metadata"] or {}).get("direction"),
            settled=(r["metadata"] or {}).get("settled"),
            settled_at=(r["metadata"] or {}).get("settled_at"),
            created_at=r["created_at"],
            src=r["src"],
            conf=r["conf"],
            last_seen=r["last_seen"],
            weight=r["weight"],
            verified=r["verified"],
            primary=r["primary"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/timeline
# ---------------------------------------------------------------------------

_TIMELINE_PREDICATES = ("contact_note", "life_event", "gift", "loan", "dunbar_tier_override")


@router.get("/entities/{entity_id}/timeline", response_model=list[EntityTimelineItem])
async def list_entity_timeline(
    entity_id: UUID,
    limit: int = Query(_ENTITY_TAB_DEFAULT_LIMIT, ge=1, le=_ENTITY_TAB_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntityTimelineItem]:
    """Unified timeline for an entity across all six predicate families.

    Includes: interaction_*, contact_note, life_event, gift, loan, dunbar_tier_override.
    Excludes: legacy 'activity' facts.
    Ordered by valid_at DESC NULLS LAST, created_at DESC.

    Returns 404 if the entity does not exist.
    Scoped to validity='active' AND scope='relationship'.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT id, predicate, content, metadata, valid_at, created_at,
               'memory_module_legacy'::text AS src,
               NULL::float AS conf,
               NULL::timestamptz AS last_seen,
               NULL::float AS weight,
               false AS verified,
               false AS "primary"
        FROM facts
        WHERE entity_id = $1
          AND (
              predicate = ANY($2::text[])
              OR predicate LIKE 'interaction_%'
          )
          AND validity = 'active'
          AND scope = 'relationship'
        ORDER BY valid_at DESC NULLS LAST, created_at DESC
        OFFSET $3 LIMIT $4
        """,
        entity_id,
        list(_TIMELINE_PREDICATES),
        offset,
        limit,
    )
    return [
        EntityTimelineItem(
            kind=_timeline_kind(r["predicate"]),
            id=r["id"],
            content=r["content"],
            valid_at=r["valid_at"],
            predicate=r["predicate"],
            # JSONB codec contract: asyncpg decodes JSONB to dict; guard is defensive only.
            metadata=dict(r["metadata"]) if isinstance(r["metadata"], dict) else None,
            src=r["src"],
            conf=r["conf"],
            last_seen=r["last_seen"],
            weight=r["weight"],
            verified=r["verified"],
            primary=r["primary"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/linked-contacts
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}/linked-contacts", response_model=list[LinkedContactSummary])
async def list_entity_linked_contacts(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[LinkedContactSummary]:
    """List all contacts whose entity_id matches the given entity UUID.

    Returns 404 if the entity does not exist.
    Returns [] if no contacts are linked to the entity.

    Each entry is enriched with:
    - ``email`` / ``phone`` — primary non-secured values for quick display.
    - ``contact_info`` — all ``relationship.entity_facts`` ``has-*`` triples for
      the entity (non-secured, full value visible) **plus** any ``public.entity_info``
      ``secured=true`` rows (value masked as ``None``).  Entity-facts-sourced entries
      carry ``source="entity_facts"``; secured entity_info entries also carry
      ``source="entity_facts"`` so the frontend routes reveal to the entity-keyed
      endpoint (GET /entities/{id}/secrets/{info_id}).
    - ``labels`` — full label objects assigned to the contact.
    - ``preferred_channel`` — the entity's preferred outreach channel, sourced
      from the entity-keyed ``prefers-channel`` fact (attached to the first
      contact only).

    SECURITY: secured entity_info values are never included in this response.
    Only metadata (id, type, is_primary, secured=true, source) is surfaced so
    the frontend can render a masked chip + reveal affordance.  The real value
    is available only via the owner-only reveal endpoint
    GET /entities/{entity_id}/secrets/{info_id}.

    All entity-level entries (entity_facts + entity_info) are attached to the first
    linked contact (by name order) because they are entity-level, not per-contact.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    # Channel identifiers now come exclusively from relationship.entity_facts (bu-6ioq3).
    # email/phone for quick-display and the full contact_info list are both derived
    # from the entity_facts has-* facts fetched below.
    rows = await pool.fetch(
        """
        SELECT
            cem.contact_id AS id,
            e.canonical_name AS full_name
        FROM contact_entity_map cem
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE cem.entity_id = $1
          AND (e.metadata->>'archived') IS DISTINCT FROM 'true'
        ORDER BY e.canonical_name
        """,
        entity_id,
    )

    if not rows:
        return []

    contact_ids = [r["id"] for r in rows]

    # Batch-fetch supplementary data (labels, entity_facts, entity_info) concurrently.
    # entity_info secured rows (credentials) are fetched separately from entity_facts
    # non-secured channels; both belong to the entity and are attached to the first
    # linked contact.
    label_rows, fact_rows, entity_info_rows, preferred_channel = await asyncio.gather(
        pool.fetch(
            """
            SELECT cl.contact_id, l.id, l.name, l.color
            FROM contact_labels cl
            JOIN labels l ON l.id = cl.label_id
            WHERE cl.contact_id = ANY($1)
            ORDER BY cl.contact_id, l.name
            """,
            contact_ids,
        ),
        pool.fetch(
            """
            SELECT id, predicate, object, "primary"
            FROM relationship.entity_facts
            WHERE subject  = $1
              AND predicate LIKE 'has-%'
              AND validity  = 'active'
              AND object_kind = 'literal'
            ORDER BY predicate ASC, "primary" DESC NULLS LAST, created_at ASC
            """,
            entity_id,
        ),
        pool.fetch(
            """
            SELECT id, type, is_primary, secured
            FROM public.entity_info
            WHERE entity_id = $1
              AND secured = true
            ORDER BY type ASC, is_primary DESC NULLS LAST
            """,
            entity_id,
        ),
        # Active preferred outbound channel — sourced from the entity-keyed
        # ``prefers-channel`` fact (entity-keyed-preferred-channel), NOT the
        # orphaned public.contacts.preferred_channel CRM column.
        pool.fetchval(
            """
            SELECT object
            FROM relationship.entity_facts
            WHERE subject   = $1
              AND predicate = 'prefers-channel'
              AND validity  = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            entity_id,
        ),
    )

    # Deliverable channel set the entity has a contact fact for. Mirrors group 1's
    # reachability mapping (_CHANNEL_REACHABILITY): email proven by has-email,
    # telegram by a telegram-prefixed has-handle. The dashboard channel-preference
    # control offers only these.
    reachable_channels = _deliverable_channels_from_facts(fact_rows)

    labels_by_contact: dict[UUID, list[Label]] = {cid: [] for cid in contact_ids}
    for lr in label_rows:
        labels_by_contact[lr["contact_id"]].append(
            Label(id=lr["id"], name=lr["name"], color=lr["color"])
        )

    # All entity_facts channel entries belong to the entity, not a specific contact.
    # Attach them all to the first linked contact (by name order), consistent with
    # the pre-migration merge behaviour.
    contact_info_entries = [_ef_row_to_ci_entry(fr) for fr in fact_rows]

    # Surface secured entity_info rows as masked ContactInfoEntry objects.
    # SECURITY: value is deliberately excluded from this query; the actual secret is
    # only available via the owner-only reveal endpoint GET /entities/{id}/secrets/{info_id}.
    # source="entity_facts" routes the frontend reveal affordance to useRevealEntityContactSecret
    # (entity-keyed endpoint) rather than the legacy contact-keyed path.
    # predicate and value_hash are None because entity_info rows have no triple predicate —
    # canEdit and canDelete checks in the frontend require both to be non-null, so secured
    # credential rows correctly render as reveal-only (no inline edit/delete affordance).
    secured_entries = [
        ContactInfoEntry(
            id=r["id"],
            type=r["type"],
            value=None,
            is_primary=bool(r["is_primary"]),
            secured=bool(r["secured"]),
            source="entity_facts",
            predicate=None,
            value_hash=None,
        )
        for r in entity_info_rows
    ]

    first_contact_id = contact_ids[0] if contact_ids else None

    ci_by_contact: dict[UUID, list[ContactInfoEntry]] = {cid: [] for cid in contact_ids}
    if first_contact_id is not None:
        ci_by_contact[first_contact_id] = contact_info_entries + secured_entries

    # Derive quick-display email/phone from the same facts (ordered primary-first).
    email_val = _first_ef_value(fact_rows, predicate="has-email")
    phone_val = _first_ef_value(fact_rows, predicate="has-phone")

    return [
        LinkedContactSummary(
            id=r["id"],
            full_name=r["full_name"],
            email=email_val if r["id"] == first_contact_id else None,
            phone=phone_val if r["id"] == first_contact_id else None,
            contact_info=ci_by_contact.get(r["id"], []),
            labels=labels_by_contact.get(r["id"], []),
            # preferred_channel + reachable_channels are entity-level (like
            # contact_info); attach only to the first linked contact.
            preferred_channel=preferred_channel if r["id"] == first_contact_id else None,
            reachable_channels=(reachable_channels if r["id"] == first_contact_id else []),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/message-threads
# ---------------------------------------------------------------------------


@router.get(
    "/entities/{entity_id}/message-threads",
    response_model=list[MessageThreadSummary],
)
async def list_entity_message_threads(
    entity_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[MessageThreadSummary]:
    """Aggregate message activity for an entity, grouped by channel + thread.

    Resolves the entity's channel identifiers from ``relationship.entity_facts``
    has-* triples → matches against ``request_context ->> 'source_sender_identity'``
    in ``switchboard.message_inbox``. Groups by (source_channel, thread_identity)
    ordered by recency.

    Returns ``[]`` when:
    - the entity has no linked contacts with reachable identifiers,
    - the switchboard pool is not registered (cross-pool access unavailable),
    - or no message_inbox rows match.

    Returns 404 if the entity does not exist.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    # Collect candidate sender identifiers from relationship.entity_facts has-* triples
    # plus the entity's own entity_info (some channels store sender identity there for
    # owner-attached accounts).  Replaces the public.contact_info join (bu-6ioq3).
    identifiers = await pool.fetch(
        """
        SELECT DISTINCT ef.object AS value
        FROM relationship.entity_facts ef
        WHERE ef.subject = $1
          AND ef.predicate LIKE 'has-%'
          AND ef.validity = 'active'
          AND ef.object_kind = 'literal'
          AND ef.object IS NOT NULL
        UNION
        SELECT DISTINCT ei.value
        FROM public.entity_info ei
        WHERE ei.entity_id = $1
          AND ei.value IS NOT NULL
          AND ei.secured = false
        """,
        entity_id,
    )
    # For telegram has-handle entries, strip the "telegram:" prefix so the raw
    # numeric user_id is compared against switchboard sender_identity values.
    candidates = [
        _ef_object_to_display_value("has-handle", r["value"])
        if r["value"] and r["value"].startswith(_TELEGRAM_HANDLE_PREFIX)
        else r["value"]
        for r in identifiers
        if r["value"]
    ]
    if not candidates:
        return []

    try:
        sw_pool = db.pool("switchboard")
    except KeyError:
        # Switchboard pool unregistered (e.g. dev without ingestion). Graceful empty.
        return []

    try:
        rows = await sw_pool.fetch(
            """
            WITH matches AS (
                SELECT
                    request_context ->> 'source_channel'        AS source_channel,
                    request_context ->> 'source_thread_identity' AS thread_identity,
                    request_context ->> 'source_sender_identity' AS sender_identity,
                    direction,
                    received_at,
                    normalized_text
                FROM message_inbox
                WHERE request_context ->> 'source_sender_identity' = ANY($1::text[])
            ),
            ranked AS (
                SELECT
                    source_channel,
                    thread_identity,
                    sender_identity,
                    direction,
                    received_at,
                    normalized_text,
                    ROW_NUMBER() OVER (
                        PARTITION BY source_channel, thread_identity
                        ORDER BY received_at DESC
                    ) AS rn,
                    COUNT(*) OVER (
                        PARTITION BY source_channel, thread_identity
                    ) AS message_count
                FROM matches
            )
            SELECT
                source_channel,
                thread_identity,
                sender_identity,
                direction       AS last_direction,
                received_at     AS last_received_at,
                normalized_text AS last_snippet,
                message_count
            FROM ranked
            WHERE rn = 1
            ORDER BY last_received_at DESC NULLS LAST
            LIMIT $2
            """,
            candidates,
            limit,
        )
    except asyncpg.PostgresError as exc:
        logger.warning("message-threads lookup failed for entity %s: %s", entity_id, exc)
        return []

    return [
        MessageThreadSummary(
            source_channel=r["source_channel"],
            thread_identity=r["thread_identity"],
            sender_identity=r["sender_identity"],
            message_count=int(r["message_count"]),
            last_received_at=r["last_received_at"],
            last_direction=r["last_direction"],
            last_snippet=(r["last_snippet"] or None),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/dates — important dates scoped to one entity
# ---------------------------------------------------------------------------


@router.get(
    "/entities/{entity_id}/dates",
    response_model=list[EntityImportantDate],
)
async def list_entity_important_dates(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntityImportantDate]:
    """Return all important_dates for the entity's linked contacts.

    Each row carries the next future occurrence of (month, day) in
    ``upcoming_date``, ordered ascending. Years are preserved when present so
    callers can render birthdays as "Apr 12 (turning 35 in 22 days)".

    Returns 404 if the entity does not exist.
    Returns ``[]`` if the entity has no linked contacts or no dates.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    today = date.today()
    rows = await pool.fetch(
        """
        SELECT
            id.contact_id,
            e.canonical_name AS contact_name,
            id.label,
            id.month,
            id.day,
            id.year
        FROM important_dates id
        JOIN contact_entity_map cem ON cem.contact_id = id.contact_id
        JOIN public.entities e ON e.id = cem.entity_id
        WHERE cem.entity_id = $1
          AND (e.metadata->>'archived') IS DISTINCT FROM 'true'
        """,
        entity_id,
    )

    out: list[EntityImportantDate] = []
    for r in rows:
        month = r["month"]
        day = r["day"]
        try:
            this_year = date(today.year, month, day)
        except ValueError:
            # Feb 29 in non-leap years etc. — skip rather than error.
            continue

        if this_year >= today:
            occurrence = this_year
        else:
            try:
                occurrence = date(today.year + 1, month, day)
            except ValueError:
                continue

        out.append(
            EntityImportantDate(
                contact_id=r["contact_id"],
                contact_name=r["contact_name"],
                label=r["label"],
                month=month,
                day=day,
                year=r["year"],
                upcoming_date=occurrence,
            )
        )

    out.sort(key=lambda d: d.upcoming_date)
    return out


# ---------------------------------------------------------------------------
# PATCH /entities/{entity_id}/dunbar-tier — pin or clear a Dunbar override
# ---------------------------------------------------------------------------


@router.patch(
    "/entities/{entity_id}/dunbar-tier",
    response_model=DunbarTierOverrideResponse,
)
async def patch_entity_dunbar_tier(
    entity_id: UUID,
    body: DunbarTierOverrideRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> DunbarTierOverrideResponse:
    """Pin or clear an entity's Dunbar tier.

    Accepts ``tier`` ∈ {5, 15, 50, 150, 500, 1500} to pin, or ``null`` to clear.

    When the entity has a linked contact, delegates to the canonical
    ``dunbar_tier_set`` engine (which writes to the memory ``facts`` table,
    keyed by entity_id).

    When the entity has NO linked contact (contactless entity), the override is
    written directly to the memory ``facts`` table, keyed by entity_id alone,
    with a synthetic ``subject`` of ``entity:<entity_id>``. This allows
    contactless entities to be pinned to a tier without first requiring a contact
    to be linked.

    Returns 404 if the entity does not exist.
    Returns 422 if ``tier`` is not a valid Dunbar layer value.
    """
    from butlers.tools.relationship import dunbar as _dunbar

    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    contact_row = await pool.fetchrow(
        """
        SELECT contact_id AS id
        FROM contact_entity_map
        WHERE entity_id = $1
        ORDER BY contact_id
        LIMIT 1
        """,
        entity_id,
    )

    if contact_row is not None:
        # Entity has a linked contact — use the canonical engine.
        contact_id = contact_row["id"]
        try:
            result = await _dunbar.dunbar_tier_set(pool, contact_id, body.tier)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return DunbarTierOverrideResponse(
            entity_id=entity_id,
            contact_id=contact_id,
            tier=result.get("tier"),
            action=result["action"],
            message=result["message"],
        )

    # Contactless entity: write the override directly without a contact row.
    if body.tier is not None and body.tier not in _dunbar.VALID_TIERS:
        valid_str = ", ".join(str(t) for t in sorted(_dunbar.VALID_TIERS))
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid tier value {body.tier!r}. "
                f"Valid Dunbar tier values are: {valid_str}. "
                "Pass tier=null to clear the override."
            ),
        )

    entity_id_str = str(entity_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Retract any existing active override for this entity.
            await conn.execute(
                """
                UPDATE facts
                SET validity = 'retracted'
                WHERE predicate = 'dunbar_tier_override'
                  AND scope = 'relationship'
                  AND validity = 'active'
                  AND entity_id = $1::uuid
                """,
                entity_id_str,
            )
            if body.tier is not None:
                await conn.execute(
                    """
                    INSERT INTO facts (
                        subject,
                        predicate,
                        content,
                        scope,
                        entity_id,
                        validity,
                        permanence
                    ) VALUES (
                        $1, 'dunbar_tier_override', $2, 'relationship',
                        $3::uuid, 'active', 'permanent'
                    )
                    """,
                    f"entity:{entity_id_str}",
                    str(body.tier),
                    entity_id_str,
                )

    if body.tier is None:
        return DunbarTierOverrideResponse(
            entity_id=entity_id,
            contact_id=None,
            tier=None,
            action="cleared",
            message="Dunbar tier override cleared. Entity will use rank-based tier assignment.",
        )
    return DunbarTierOverrideResponse(
        entity_id=entity_id,
        contact_id=None,
        tier=body.tier,
        action="set",
        message=(
            f"Dunbar tier override set to {body.tier}. "
            f"Entity is pinned to tier {body.tier} regardless of computed rank."
        ),
    )


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/neighbours — relational neighbours (bu-4wn79)
# ---------------------------------------------------------------------------


@router.get(
    "/entities/{entity_id}/neighbours",
    response_model=NeighboursResponse,
)
async def list_entity_neighbours(
    entity_id: UUID,
    rank: Literal["weight"] | None = Query(
        None,
        description="Ranking key for per-predicate truncation. Only 'weight' in v1.",
    ),
    per_predicate: int = Query(
        6,
        ge=1,
        description="When rank is set, max neighbours returned per predicate group "
        "(top-N by weight). Groups above this carry a remainder count.",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> NeighboursResponse:
    """Return relational triples grouped by predicate for both directions.

    Returns all active relational triples where the given entity is either the
    subject (forward direction) or the object (reverse direction).  Contact
    predicates (``has-*`` family, kind='contact') are excluded; only
    ``kind='relational'`` predicates from ``relationship.entity_predicate_registry``
    are returned.

    Ranked truncation (entity v3, ``dashboard-relationship`` §"Neighbour ranking
    and truncation"): with ``rank=weight`` (and optional ``per_predicate=N``,
    default 6) each predicate group returns its top-N neighbours by
    ``weight DESC`` and ``remainders`` carries the count of unreturned
    neighbours per truncated group (the Hop / Columns "+N more" row).  Without
    ``rank`` the endpoint returns every neighbour and ``remainders`` is empty
    (unchanged behaviour — standing Columns option (a) client-side chaining).

    Owner-only authz gate (Clause 12b, Amendment 12b): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist in ``public.entities``.
    Returns ``{"neighbours": {}, "remainders": {}}`` if the entity has no
    relational triples.

    Response shape::

        {
          "neighbours": {
            "knows": [
              {
                "entity_id": "<uuid>",
                "direction": "forward" | "reverse",
                "src": "relationship",
                "conf": 1.0,
                "last_seen": null | "<iso-datetime>",
                "weight": null | <int>,
                "verified": false,
                "primary": null | <bool>
              },
              ...
            ],
            "family-of": [...]
          }
        }

    Each entry's ``entity_id`` is the OTHER entity (the neighbour), not the
    queried entity.  ``direction='forward'`` means the queried entity is the
    subject of the triple; ``direction='reverse'`` means it is the object.
    """
    pool = _pool(db)

    # Owner-only gate (Clause 12b) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    # Query relationship.entity_facts for both directions, joining predicate_registry
    # to filter only kind='relational' predicates (excludes has-* contact facts).
    # Also JOIN public.entities to resolve canonical_name for each neighbour.
    # The neighbour is f.object when forward (subject=anchor), f.subject otherwise.
    rows = await pool.fetch(
        """
        SELECT
            f.id,
            f.subject,
            f.predicate,
            f.object,
            f.object_kind,
            f.src,
            f.conf,
            f.last_seen,
            f.weight,
            f.verified,
            f."primary",
            CASE
                WHEN f.subject = $1 THEN 'forward'
                ELSE 'reverse'
            END AS direction,
            e.canonical_name
        FROM relationship.entity_facts f
        JOIN relationship.entity_predicate_registry pr ON pr.predicate = f.predicate
        LEFT JOIN public.entities e ON e.id = CASE
            WHEN f.subject = $1 THEN f.object::uuid
            ELSE f.subject
        END
        WHERE pr.kind = 'relational'
          AND f.validity = 'active'
          AND f.object_kind = 'entity'
          AND (
              f.subject = $1
              OR (f.object_kind = 'entity' AND f.object = $1::text)
          )
        ORDER BY f.predicate, f.last_seen DESC NULLS LAST, f.created_at DESC
        """,
        entity_id,
    )

    # Group by predicate.
    grouped: dict[str, list] = {}
    for r in rows:
        predicate = r["predicate"]
        direction = r["direction"]
        # Derive the neighbour entity_id from the direction.
        if direction == "forward":
            neighbour_id_str = r["object"]
        else:
            neighbour_id_str = str(r["subject"])

        try:
            neighbour_uuid = UUID(neighbour_id_str)
        except (ValueError, AttributeError):
            # Skip malformed object values — should not occur with proper writes.
            logger.warning(
                "Skipping neighbour triple with non-UUID object: predicate=%s object=%s",
                predicate,
                neighbour_id_str,
            )
            continue

        entry = NeighbourEntry(
            entity_id=neighbour_uuid,
            canonical_name=r["canonical_name"] or "",
            direction=direction,
            src=r["src"],
            conf=float(r["conf"]) if r["conf"] is not None else 1.0,
            last_seen=r["last_seen"],
            weight=r["weight"],
            verified=bool(r["verified"]) if r["verified"] is not None else False,
            primary=r["primary"],
        )

        if predicate not in grouped:
            grouped[predicate] = []
        grouped[predicate].append(entry)

    # Ranked truncation: top-N by weight per predicate group + remainder count.
    if rank == "weight":
        remainders: dict[str, int] = {}
        for predicate, entries in grouped.items():
            # weight DESC (NULLs last so scored neighbours rank above unscored).
            entries.sort(key=lambda e: (e.weight is None, -(e.weight or 0)))
            if len(entries) > per_predicate:
                remainders[predicate] = len(entries) - per_predicate
                grouped[predicate] = entries[:per_predicate]
        return NeighboursResponse(neighbours=grouped, remainders=remainders)

    return NeighboursResponse(neighbours=grouped)


# ---------------------------------------------------------------------------
# Contact-fact CRUD  (bu-u1w78)
# GET  /entities/{entity_id}/contacts
# POST /entities/{entity_id}/contacts
# DELETE /entities/{entity_id}/contacts/{predicate}/{value_hash}
# ---------------------------------------------------------------------------

_CONTACT_PREDICATE_PREFIX = "has-"


def _contact_value_hash(object_value: str) -> str:
    """Return a deterministic 16-char hex token for *object_value*.

    Used as the stable URL-path segment in DELETE paths.  The token is the
    first 16 hex characters of SHA-256(object_value.encode('utf-8')).
    """
    return hashlib.sha256(object_value.encode("utf-8")).hexdigest()[:16]


def _row_to_contact_fact(r: Any) -> Any:
    """Convert an asyncpg row from ``relationship.entity_facts`` to a ContactFact.

    Raises TypeError if required keys are missing (should not happen with the
    canonical SELECT shape used in the contacts endpoints).
    """
    obj_val: str = r["object"]
    return ContactFact(
        id=r["id"],
        predicate=r["predicate"],
        object=obj_val,
        value_hash=_contact_value_hash(obj_val),
        src=r["src"],
        conf=r["conf"],
        last_seen=r["last_seen"],
        weight=r["weight"],
        verified=r["verified"],
        primary=r["primary"],
    )


@router.get(
    "/entities/{entity_id}/contacts",
    response_model=ContactsResponse,
)
async def list_entity_contacts(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactsResponse:
    """List active contact-fact triples for an entity.

    Returns all ``relationship.entity_facts`` rows where:
    - ``subject = entity_id``
    - ``predicate LIKE 'has-%'`` (contact family)
    - ``validity = 'active'``

    Owner-only authz gate (Clause 12b, Amendment 12b): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist in ``public.entities``.
    Returns ``{"facts": []}`` when the entity has no active contact facts.

    Response shape::

        {
          "facts": [
            {
              "id": "<uuid>",
              "predicate": "has-email",
              "object": "alice@example.com",
              "value_hash": "abcdef0123456789",
              "src": "relationship",
              "conf": 1.0,
              "last_seen": null,
              "weight": null,
              "verified": false,
              "primary": null
            },
            ...
          ]
        }

    Ordered by ``predicate ASC, primary DESC NULLS LAST, created_at DESC``.
    """
    pool = _pool(db)

    # Owner-only gate (Clause 12b) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT
            f.id,
            f.predicate,
            f.object,
            f.src,
            f.conf,
            f.last_seen,
            f.weight,
            f.verified,
            f."primary"
        FROM relationship.entity_facts f
        WHERE f.subject   = $1
          AND f.predicate LIKE 'has-%'
          AND f.validity  = 'active'
        ORDER BY
            f.predicate        ASC,
            f."primary"        DESC NULLS LAST,
            f.created_at       DESC
        """,
        entity_id,
    )

    facts = [_row_to_contact_fact(r) for r in rows]
    return ContactsResponse(facts=facts)


@router.post(
    "/entities/{entity_id}/contacts",
    response_model=AddContactResponse,
    status_code=201,
)
async def add_entity_contact(
    entity_id: UUID,
    body: AddContactRequest,
    fastapi_response: Response,
    db: DatabaseManager = Depends(_get_db_manager),
) -> AddContactResponse:
    """Add (or update) a contact-fact triple for an entity.

    Calls the central writer ``relationship_assert_fact()`` with
    ``object_kind='literal'`` and the supplied provenance fields.

    Owner-only authz gate (Clause 12a, Amendment 12a): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist in ``public.entities``.

    Returns 400 when *predicate* does not begin with ``'has-'`` (rejected
    before hitting the central writer so the error message is clear).

    On success, returns HTTP 201 with the resulting fact row.

    **Owner entity carve-out:** when the entity subject has role ``'owner'``,
    the central writer parks the write as a ``pending_actions`` row.  In this
    case the endpoint returns HTTP 202 with ``outcome='pending_approval'`` and
    ``action_id`` set; ``fact`` is ``null``.
    """
    from butlers.tools.relationship.relationship_assert_fact import (
        AssertOutcome,
        relationship_assert_fact,
    )

    pool = _pool(db)

    # Owner-only authz gate (Clause 12a — write surface) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    # Validate that the predicate is a contact predicate.
    if not body.predicate.startswith(_CONTACT_PREDICATE_PREFIX):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_predicate",
                "message": (
                    f"Predicate {body.predicate!r} is not a contact predicate. "
                    "Contact predicates must start with 'has-'."
                ),
            },
        )

    # Normalise telegram handles to the canonical ``telegram:<bare>`` storage form
    # so storage, resolution, and delivery agree on one format (bu-oluyt.3 /
    # Phase 5).  The ``has-*`` predicate alone cannot distinguish a telegram
    # handle from another handle, so the caller passes the source channel_type.
    stored_value = channel_value_for_storage(body.channel_type or "", body.value)

    # Owner self-identity exemption (RFC 0017 §2.3 / Phase 4 bu-oluyt.4): this is
    # the owner-authz-gated dashboard write surface, so a write whose SUBJECT is
    # the owner entity is the owner self-registering their own channel handle.
    # Apply the trusted ``owner-self`` source SERVER-SIDE (the API request body
    # cannot spoof it — bu-vj46x) so the fact writes directly instead of parking
    # in pending_actions.  Third-party claims about the owner arrive via other
    # code paths (ingestion/MCP, src='relationship') and still park.
    src = body.src
    if await _entity_has_owner_role(pool, entity_id):
        src = "owner-self"

    result = await relationship_assert_fact(
        pool,
        subject=entity_id,
        predicate=body.predicate,
        object=stored_value,
        src=src,
        object_kind="literal",
        conf=body.conf,
        verified=body.verified,
        primary=body.primary,
    )

    if result.outcome == AssertOutcome.pending_approval:
        # Owner carve-out: write parked for human approval → HTTP 202.
        fastapi_response.status_code = 202
        return AddContactResponse(
            outcome=result.outcome.value,
            fact=None,
            action_id=result.action_id,
        )

    # Fetch the resulting active fact to populate the response.
    fact_row = await pool.fetchrow(
        """
        SELECT
            f.id,
            f.predicate,
            f.object,
            f.src,
            f.conf,
            f.last_seen,
            f.weight,
            f.verified,
            f."primary"
        FROM relationship.entity_facts f
        WHERE f.id = $1
        """,
        result.fact_id,
    )
    if fact_row is None:
        # Should not happen — fact_id is fresh from the writer.
        raise HTTPException(status_code=500, detail="Fact row not found after write")

    return AddContactResponse(
        outcome=result.outcome.value,
        fact=_row_to_contact_fact(fact_row),
    )


@router.delete(
    "/entities/{entity_id}/contacts/{predicate}/{value_hash}",
    response_model=DeleteContactResponse,
)
async def delete_entity_contact(
    entity_id: UUID,
    predicate: str,
    value_hash: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> DeleteContactResponse:
    """Retract (soft-delete) an active contact-fact triple.

    Locates the active fact whose ``subject = entity_id``,
    ``predicate = predicate``, and whose ``object`` hashes to ``value_hash``
    (SHA-256[:16]).  Marks the row ``validity = 'retracted'`` directly on the
    ``relationship.entity_facts`` table (the central writer handles upserts; for
    retraction we perform a targeted UPDATE inside the relationship schema role).

    Owner-only authz gate (Clause 12a, Amendment 12a): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist, or if no active fact matching
    ``(entity_id, predicate, value_hash)`` is found.

    On success, returns HTTP 200 with ``{"deleted": true, "fact_id": "<uuid>"}``.
    """
    pool = _pool(db)

    # Validate that the predicate is a contact predicate (consistent with POST).
    if not predicate.startswith(_CONTACT_PREDICATE_PREFIX):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_predicate",
                "message": (
                    f"Predicate {predicate!r} is not a contact predicate. "
                    "Contact predicates must start with 'has-'."
                ),
            },
        )

    # Owner-only authz gate (Clause 12a — write surface) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    # Find the active fact matching (subject, predicate, value_hash).
    # We fetch all active rows for (subject, predicate) and filter by hash
    # in Python to avoid a full-table scan on the object column.
    candidate_rows = await pool.fetch(
        """
        SELECT f.id, f.object
        FROM relationship.entity_facts f
        WHERE f.subject   = $1
          AND f.predicate = $2
          AND f.validity  = 'active'
        """,
        entity_id,
        predicate,
    )

    target_row = None
    for row in candidate_rows:
        if _contact_value_hash(row["object"]) == value_hash:
            target_row = row
            break

    if target_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "contact_fact_not_found",
                "message": (
                    f"No active contact fact found for entity {entity_id}, "
                    f"predicate {predicate!r}, value_hash {value_hash!r}."
                ),
            },
        )

    fact_id: UUID = target_row["id"]

    await pool.execute(
        """
        UPDATE relationship.entity_facts
        SET validity   = 'retracted',
            updated_at = now()
        WHERE id = $1
        """,
        fact_id,
    )

    return DeleteContactResponse(deleted=True, fact_id=fact_id)


@router.post(
    "/entities/{entity_id}/contacts/{predicate}/{value_hash}/verify",
    response_model=MarkContactVerifiedResponse,
)
async def verify_entity_contact(
    entity_id: UUID,
    predicate: str,
    value_hash: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> MarkContactVerifiedResponse:
    """Mark an active contact-fact triple as owner-verified.

    Locates the active fact whose ``subject = entity_id``,
    ``predicate = predicate``, and whose ``object`` hashes to ``value_hash``
    (SHA-256[:16]).  Sets ``verified = true`` on the
    ``relationship.entity_facts`` row.

    Owner-only authz gate: returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist, or if no active fact matching
    ``(entity_id, predicate, value_hash)`` is found.

    On success, returns HTTP 200 with ``{"verified": true, "fact_id": "<uuid>"}``.
    """
    pool = _pool(db)

    # Validate that the predicate is a contact predicate (consistent with DELETE).
    if not predicate.startswith(_CONTACT_PREDICATE_PREFIX):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_predicate",
                "message": (
                    f"Predicate {predicate!r} is not a contact predicate. "
                    "Contact predicates must start with 'has-'."
                ),
            },
        )

    # Owner-only authz gate (Clause 12a — write surface).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    # Find the active fact matching (subject, predicate, value_hash).
    # Fetch all active rows for (subject, predicate) and filter by hash in Python.
    candidate_rows = await pool.fetch(
        """
        SELECT f.id, f.object
        FROM relationship.entity_facts f
        WHERE f.subject   = $1
          AND f.predicate = $2
          AND f.validity  = 'active'
        """,
        entity_id,
        predicate,
    )

    target_row = None
    for row in candidate_rows:
        if _contact_value_hash(row["object"]) == value_hash:
            target_row = row
            break

    if target_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "contact_fact_not_found",
                "message": (
                    f"No active contact fact found for entity {entity_id}, "
                    f"predicate {predicate!r}, value_hash {value_hash!r}."
                ),
            },
        )

    fact_id: UUID = target_row["id"]

    await pool.execute(
        """
        UPDATE relationship.entity_facts
        SET verified   = true,
            updated_at = now()
        WHERE id = $1
        """,
        fact_id,
    )

    return MarkContactVerifiedResponse(verified=True, fact_id=fact_id)


@router.put(
    "/entities/{entity_id}/contacts/{predicate}/{value_hash}",
    response_model=UpdateContactResponse,
)
async def update_entity_contact(
    entity_id: UUID,
    predicate: str,
    value_hash: str,
    body: UpdateContactRequest,
    fastapi_response: Response,
    db: DatabaseManager = Depends(_get_db_manager),
) -> UpdateContactResponse:
    """Edit-in-place a contact-fact triple: retract old value, assert new value.

    Locates the active fact whose ``subject = entity_id``,
    ``predicate = predicate``, and whose ``object`` hashes to ``value_hash``
    (SHA-256[:16]).  Retracts the old row and asserts the new value via the
    central writer inside a single transaction, preserving atomicity.

    Owner-only authz gate (Clause 12a, Amendment 12a): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist, or if no active fact matching
    ``(entity_id, predicate, value_hash)`` is found.

    Returns 400 when *predicate* does not begin with ``'has-'``.

    Returns 400 when ``new_value`` is empty or whitespace-only.

    On success, returns HTTP 200 with the new active fact row and the
    retracted fact UUID.

    **Owner entity carve-out:** when the entity subject has role ``'owner'``,
    the new-value assert is parked as a ``pending_actions`` row.  The old
    fact is NOT retracted until the owner approves.  The endpoint returns
    HTTP 202 with ``outcome='pending_approval'`` and ``action_id`` set;
    ``fact`` and ``retracted_fact_id`` are both ``null``.
    """
    from butlers.tools.relationship.relationship_assert_fact import (
        AssertOutcome,
        relationship_assert_fact,
    )

    pool = _pool(db)

    # Validate that the predicate is a contact predicate.
    if not predicate.startswith(_CONTACT_PREDICATE_PREFIX):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_predicate",
                "message": (
                    f"Predicate {predicate!r} is not a contact predicate. "
                    "Contact predicates must start with 'has-'."
                ),
            },
        )

    # Validate new_value is non-empty.
    new_value = body.new_value.strip()
    if not new_value:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_value",
                "message": "new_value must not be empty or whitespace-only.",
            },
        )

    # Owner-only authz gate (Clause 12a — write surface).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    # Find the active fact matching (subject, predicate, value_hash).
    candidate_rows = await pool.fetch(
        """
        SELECT f.id, f.object
        FROM relationship.entity_facts f
        WHERE f.subject   = $1
          AND f.predicate = $2
          AND f.validity  = 'active'
        """,
        entity_id,
        predicate,
    )

    target_row = None
    for row in candidate_rows:
        if _contact_value_hash(row["object"]) == value_hash:
            target_row = row
            break

    if target_row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "contact_fact_not_found",
                "message": (
                    f"No active contact fact found for entity {entity_id}, "
                    f"predicate {predicate!r}, value_hash {value_hash!r}."
                ),
            },
        )

    old_fact_id: UUID = target_row["id"]
    old_value: str = target_row["object"]

    # If the new value is the same as the old, just update provenance fields
    # via the central writer — retraction is not needed.
    if new_value == old_value:
        result = await relationship_assert_fact(
            pool,
            subject=entity_id,
            predicate=predicate,
            object=new_value,
            src=body.src,
            object_kind="literal",
            conf=body.conf,
            verified=body.verified,
            primary=body.primary,
        )
        if result.outcome == AssertOutcome.pending_approval:
            fastapi_response.status_code = 202
            return UpdateContactResponse(
                outcome=result.outcome.value,
                retracted_fact_id=None,
                fact=None,
                action_id=result.action_id,
            )
        fact_row = await pool.fetchrow(
            """
            SELECT f.id, f.predicate, f.object, f.src, f.conf,
                   f.last_seen, f.weight, f.verified, f."primary"
            FROM relationship.entity_facts f WHERE f.id = $1
            """,
            result.fact_id,
        )
        if fact_row is None:
            raise HTTPException(status_code=500, detail="Fact row not found after write")
        return UpdateContactResponse(
            outcome=result.outcome.value,
            retracted_fact_id=None,
            fact=_row_to_contact_fact(fact_row),
        )

    # New value differs from old: retract old fact + assert new fact atomically.
    # We acquire a single connection and wrap both operations in one transaction
    # so the triple store is never left in a state where both old and new are
    # simultaneously absent or simultaneously active.
    #
    # Owner carve-out: when relationship_assert_fact returns pending_approval,
    # the new value is parked for approval and must NOT be committed.  We raise
    # _PendingApproval inside the transaction to force a rollback — the retract
    # is rolled back too, so the old row stays active while the owner reviews.
    class _PendingApproval(Exception):
        """Sentinel: triggers rollback so retraction is never committed."""

        def __init__(self, inner_result: object) -> None:
            self.result = inner_result

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                # 1. Retract old row.
                await conn.execute(
                    """
                    UPDATE relationship.entity_facts
                    SET validity   = 'retracted',
                        updated_at = now()
                    WHERE id = $1
                    """,
                    old_fact_id,
                )

                # 2. Assert new row via central writer (pass conn= to avoid nested tx).
                result = await relationship_assert_fact(
                    pool,
                    subject=entity_id,
                    predicate=predicate,
                    object=new_value,
                    src=body.src,
                    object_kind="literal",
                    conf=body.conf,
                    verified=body.verified,
                    primary=body.primary,
                    conn=conn,
                )

                if result.outcome == AssertOutcome.pending_approval:
                    # Raise inside the transaction so both the retract and the
                    # pending-approval write are rolled back atomically.
                    raise _PendingApproval(result)
    except _PendingApproval as exc:
        # Owner carve-out: the entire transaction was rolled back.  The old
        # fact row is still active.  Return 202 so the caller knows an approval
        # is needed; fact and retracted_fact_id are both null.
        result = exc.result
        fastapi_response.status_code = 202
        return UpdateContactResponse(
            outcome=result.outcome.value,
            retracted_fact_id=None,
            fact=None,
            action_id=result.action_id,
        )

    # Fetch the new active fact row.
    fact_row = await pool.fetchrow(
        """
        SELECT f.id, f.predicate, f.object, f.src, f.conf,
               f.last_seen, f.weight, f.verified, f."primary"
        FROM relationship.entity_facts f WHERE f.id = $1
        """,
        result.fact_id,
    )
    if fact_row is None:
        raise HTTPException(status_code=500, detail="Fact row not found after write")

    return UpdateContactResponse(
        outcome=result.outcome.value,
        retracted_fact_id=old_fact_id,
        fact=_row_to_contact_fact(fact_row),
    )


# ---------------------------------------------------------------------------
# PUT / DELETE /entities/{entity_id}/preferred-channel
#
# Entity-keyed preferred-outbound-channel control (entity-keyed-preferred-channel,
# group 3). The dashboard ContactChannelCard sets/clears the preference through
# the single-valued ``prefers-channel`` fact (group 1) rather than the orphaned
# ``public.contacts.preferred_channel`` CRM column. ``assert_prefers_channel``
# enforces reachability validation + single-valued supersession;
# ``retract_prefers_channel`` clears it.
# ---------------------------------------------------------------------------


@router.put(
    "/entities/{entity_id}/preferred-channel",
    response_model=SetPreferredChannelResponse,
)
async def set_entity_preferred_channel(
    entity_id: UUID,
    body: SetPreferredChannelRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> SetPreferredChannelResponse:
    """Set the entity's preferred outbound channel via the ``prefers-channel`` fact.

    Single-valued: asserting a preference supersedes any prior active
    ``prefers-channel`` row for the entity so exactly one remains.

    Owner-only authz gate (Clause 12a): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist.

    Returns 400 when *channel* is empty, or when the entity has no contact fact
    proving reachability on that channel (e.g. preferring ``telegram`` when the
    entity has no telegram handle) — the underlying ``assert_prefers_channel``
    raises ``ValueError`` which is mapped to a 400 here.
    """
    from butlers.tools.relationship.relationship_assert_fact import assert_prefers_channel

    pool = _pool(db)

    # Owner-only authz gate (Clause 12a — write surface).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    await _assert_entity_exists(pool, entity_id)

    try:
        result = await assert_prefers_channel(pool, entity_id, body.channel, src="relationship")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_preferred_channel", "message": str(exc)},
        ) from exc

    return SetPreferredChannelResponse(
        outcome=result.outcome.value,
        channel=body.channel.strip(),
    )


@router.delete(
    "/entities/{entity_id}/preferred-channel",
    response_model=ClearPreferredChannelResponse,
)
async def clear_entity_preferred_channel(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ClearPreferredChannelResponse:
    """Clear the entity's preferred channel by retracting any active ``prefers-channel`` fact.

    Idempotent: clearing an already-cleared preference returns ``cleared=0``.

    Owner-only authz gate (Clause 12a): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist.
    """
    from butlers.tools.relationship.relationship_assert_fact import retract_prefers_channel

    pool = _pool(db)

    # Owner-only authz gate (Clause 12a — write surface).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    await _assert_entity_exists(pool, entity_id)

    cleared = await retract_prefers_channel(pool, entity_id)
    return ClearPreferredChannelResponse(cleared=cleared)


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/facts — per-fact provenance grid (bu-mg4dk)
# ---------------------------------------------------------------------------


def _encode_facts_cursor(created_at: datetime, fact_id: UUID | str) -> str:
    """Encode a ``(created_at, id)`` keyset position into an opaque cursor.

    Base64url-encoded JSON so it is safe as a query parameter.  Mirrors the
    repo cursor convention (``src/butlers/core/ingestion_events.py``) for the
    facts drill's ``created_at DESC, id DESC`` keyset.
    """
    payload = {"ca": created_at.isoformat(), "id": str(fact_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _decode_facts_cursor(cursor: str) -> tuple[datetime, str]:
    """Decode an opaque facts cursor back to ``(created_at, id)``.

    Raises ``ValueError`` if the cursor is malformed (caller maps to HTTP 422).
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        return datetime.fromisoformat(payload["ca"]), str(payload["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid cursor: {exc}") from exc


async def _fetch_narrative_drill_facts(
    pool,
    entity_id: UUID,
    *,
    validity: str,
    predicate: str | None,
    limit: int,
) -> list[Any]:
    """Narrative-store facts for the entity facts drill (``store=all`` layer).

    Scope-filtered per the canonical narrative read rule
    (``staleness.narrative_scope_sql`` — ``scope IN ('relationship', 'global')``)
    so the drill, delta banner, compare blocks, and the ``relationship_lookup``
    MCP tool all surface the SAME fact set (bu-3jrq3).
    """
    from butlers.tools.relationship.staleness import (
        narrative_scope_sql,
        narrative_staleness_band_sql,
    )

    narr_args: list[Any] = [entity_id]
    narr_where = ["f.entity_id = $1", narrative_scope_sql("f")]
    # The narrative store records supersession via validity too.
    narr_args.append(validity)
    narr_where.append(f"f.validity = ${len(narr_args)}")
    if predicate is not None:
        narr_args.append(predicate)
        narr_where.append(f"f.predicate = ${len(narr_args)}")

    return await pool.fetch(
        f"""
        SELECT
            f.id,
            f.entity_id   AS subject,
            f.predicate,
            f.content     AS object,
            'literal'::text AS object_kind,
            COALESCE(f.source_butler, 'memory')::text AS src,
            f.confidence  AS conf,
            NULL::int     AS weight,
            f.last_confirmed_at AS last_seen,
            false         AS verified,
            NULL::bool    AS "primary",
            f.validity,
            f.created_at,
            {narrative_staleness_band_sql("f")} AS staleness_band
        FROM facts f
        WHERE {" AND ".join(narr_where)}
        ORDER BY f.created_at DESC, f.id DESC
        LIMIT ${len(narr_args) + 1}
        """,
        *narr_args,
        limit,
    )


@router.get(
    "/entities/{entity_id}/facts",
    response_model=EntityFactsResponse,
)
async def list_entity_facts(
    entity_id: UUID,
    predicate: str | None = Query(None, description="Filter to a single predicate."),
    validity: Literal["active", "superseded"] = Query(
        "active",
        description="Fact validity to return. 'active' (default) or 'superseded' (history).",
    ),
    store: Literal["identity", "all"] = Query(
        "identity",
        description="'identity' (default) returns triple-store rows; "
        "'all' additionally appends labeled narrative-store facts.",
    ),
    limit: int = Query(20, ge=1, le=200, description="Page size (max 200)."),
    cursor: str | None = Query(
        None,
        description="Opaque keyset cursor from a prior response's next_cursor.",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityFactsResponse:
    """Drill endpoint: fact-level read for an entity with full provenance.

    The canonical fact-level read for the Workbench grid and Editorial
    provenance reveals (entity v3, ``dashboard-relationship`` §"Facts drill
    endpoint").

    Filters:

    - ``predicate=`` — restrict to one predicate.
    - ``validity=`` — ``active`` (default) or ``superseded`` (the Workbench
      grid's history view).
    - ``store=`` — ``identity`` (default; ``relationship.entity_facts``) or
      ``all`` (additionally appends labeled narrative-store rows from the
      memory-module ``facts`` table, after the identity rows).

    Every row carries the Provenance contract fields (``src``, ``conf``,
    ``last_observed_at``, ``weight``, ``verified``, ``primary``) plus a
    read-time ``staleness_band`` (``fresh`` / ``aging`` / ``stale``) and its
    ``store`` of origin.

    Pagination is keyset (cursor) per the repo convention — ordered
    ``created_at DESC, id DESC`` with the envelope ``{items, next_cursor,
    has_more}`` (no ``total``).  ``store=all`` paginates the identity store;
    narrative rows are appended to the page (they are the Workbench grid's
    secondary layer and are not independently cursored in v1).

    Owner-only authz gate (Clause 12b): HTTP 403 ``{"code": "owner_required"}``
    if no owner entity is registered.  Returns 404 if the entity does not exist.
    Returns ``{"items": [], "next_cursor": null, "has_more": false}`` when no
    facts match.
    """
    from butlers.tools.relationship.staleness import identity_staleness_band_sql

    pool = _pool(db)

    # Owner-only gate (Clause 12b) — roles-aware via _assert_owner_role.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    keyset: tuple[datetime, str] | None = None
    if cursor is not None:
        try:
            keyset = _decode_facts_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # --- Identity store (relationship.entity_facts) -----------------------
    # Keyset over (created_at, id): page one row past the limit to detect more.
    args: list[Any] = [entity_id, validity]
    where = ["f.subject = $1", "f.validity = $2"]
    if predicate is not None:
        args.append(predicate)
        where.append(f"f.predicate = ${len(args)}")
    if keyset is not None:
        args.append(keyset[0])
        args.append(keyset[1])
        # Strict keyset on the DESC,DESC order.
        where.append(f"(f.created_at, f.id) < (${len(args) - 1}::timestamptz, ${len(args)}::uuid)")
    args.append(limit + 1)
    limit_pos = len(args)

    identity_rows = await pool.fetch(
        f"""
        SELECT
            f.id,
            f.subject,
            f.predicate,
            f.object,
            f.object_kind,
            f.src,
            f.conf,
            f.weight,
            f.last_seen,
            f.verified,
            f."primary",
            f.validity,
            f.created_at,
            {identity_staleness_band_sql("f")} AS staleness_band
        FROM relationship.entity_facts f
        WHERE {" AND ".join(where)}
        ORDER BY f.created_at DESC, f.id DESC
        LIMIT ${limit_pos}
        """,
        *args,
    )

    has_more = len(identity_rows) > limit
    page_rows = list(identity_rows[:limit])

    next_cursor: str | None = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_facts_cursor(last["created_at"], last["id"])

    items = [
        EntityFactEntry(
            id=r["id"],
            subject=r["subject"],
            predicate=r["predicate"],
            object=r["object"],
            object_kind=r["object_kind"],
            src=r["src"],
            conf=float(r["conf"]) if r["conf"] is not None else 1.0,
            weight=r["weight"],
            last_observed_at=r["last_seen"],
            verified=r["verified"],
            primary=r["primary"],
            validity=r["validity"],
            created_at=r["created_at"],
            store="identity",
            staleness_band=r["staleness_band"],
        )
        for r in page_rows
    ]

    # --- Narrative store (memory-module facts table) ----------------------
    # store=all appends labeled narrative rows after the identity page. These
    # are the Workbench grid's secondary layer; they ride the same page (no
    # independent cursor) so the cursor stays a pure identity-store keyset.
    #
    # The narrative layer is appended ONCE, on the first page only (keyset is
    # None). The cursor advances over the identity keyset alone, so without this
    # guard every subsequent page would re-append the full narrative block,
    # duplicating those rows across the paginated result. Gating on the first
    # page keeps the narrative block a single, unduplicated secondary layer.
    if store == "all" and keyset is None:
        narrative_rows = await _fetch_narrative_drill_facts(
            pool, entity_id, validity=validity, predicate=predicate, limit=limit
        )
        items.extend(
            EntityFactEntry(
                id=r["id"],
                subject=r["subject"],
                predicate=r["predicate"],
                object=r["object"],
                object_kind=r["object_kind"],
                src=r["src"],
                conf=float(r["conf"]) if r["conf"] is not None else 1.0,
                weight=r["weight"],
                last_observed_at=r["last_seen"],
                verified=r["verified"],
                primary=r["primary"],
                validity=r["validity"],
                created_at=r["created_at"],
                store="narrative",
                staleness_band=r["staleness_band"],
            )
            for r in narrative_rows
        )

    return EntityFactsResponse(items=items, next_cursor=next_cursor, has_more=has_more)


# ---------------------------------------------------------------------------
# POST /entities/{entity_id}/archive — soft archive (bu-l76uv)
# DELETE /entities/{entity_id} — forget with tombstone (bu-l76uv)
# ---------------------------------------------------------------------------


def _make_owner_required_response() -> JSONResponse:
    """Return a 403 JSONResponse with the owner_required code discriminator.

    Produces ``{"code": "owner_required"}`` at the top level so that both the
    relationship-domain unwrapped convention and the RFC 0007 ``error.code``
    convention are satisfied by standard test assertions on the response body.

    This function is used by ``archive_entity`` and ``forget_entity`` which
    require the roles-aware owner check (Amendment 12a, bu-l76uv).  The
    roles-aware check inspects the ``roles`` column of the returned row so
    that unit-test mocks which return a row unconditionally but set ``roles``
    based on a caller fixture produce the correct 403.
    """
    return JSONResponse(
        status_code=403,
        content={"code": "owner_required", "message": "Owner entity not found"},
    )


async def _get_owner_roles(pool) -> list[str] | None:
    """Query owner entity roles; return None on DB error.

    Fetches the first entity with 'owner' in its roles (production) or the
    first entity returned by the mock (tests).  Callers must inspect the
    returned roles list to decide whether access is granted.

    Returns ``None`` when the query fails (DB error), signalling that the
    owner check should be treated as failed.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT id, roles FROM public.entities
            WHERE 'owner' = ANY(COALESCE(roles, '{}'))
            LIMIT 1
            """
        )
    except Exception as exc:
        logger.warning("Owner role assertion query failed: %s", exc)
        return None

    if row is None:
        return None
    return row["roles"] if row["roles"] else []


async def _assert_owner_role(pool) -> JSONResponse | None:
    """Return a 403 JSONResponse if the caller is not the owner; else None.

    Centralises the Amendment 12a / 12b owner gate so call sites collapse to::

        if (err := await _assert_owner_role(pool)) is not None:
            return err

    Returns ``None`` when the owner role is confirmed.  Returns a
    ``_make_owner_required_response()`` JSONResponse when the owner entity is
    absent or the returned roles list does not contain ``'owner'``.
    """
    owner_roles = await _get_owner_roles(pool)
    if owner_roles is None or "owner" not in owner_roles:
        return _make_owner_required_response()
    return None


@router.post("/entities/{entity_id}/archive", status_code=204)
async def archive_entity(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Response:
    """Soft-archive an entity by setting ``metadata->>'archived' = 'true'``.

    This is a reversible operation.  The entity and all its associated facts
    remain in the database but the entity is excluded from standard list
    queries.  Archive is idempotent: archiving an already-archived entity is
    a no-op (returns 204).

    **Authorization**: owner-only gate (Amendment 12a) — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.

    Returns 404 if the entity does not exist.
    """
    pool = _pool(db)

    # Amendment 12a: owner-only write gate (roles-aware, see _assert_owner_role).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    row = await pool.fetchrow(
        """
        SELECT id FROM public.entities WHERE id = $1
        """,
        entity_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    await pool.execute(
        """
        UPDATE public.entities
        SET metadata   = jsonb_set(COALESCE(metadata, '{}'), '{archived}', 'true'),
            updated_at = now()
        WHERE id = $1
        """,
        entity_id,
    )
    return Response(status_code=204)


@router.delete("/entities/{entity_id}", status_code=204)
async def forget_entity(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> Response:
    """Forget (hard-delete with tombstone) an entity.

    This is a **destructive and irreversible** operation that:

    1. Retracts all active ``relationship.entity_facts`` rows where the entity is the
       subject OR where it appears as an object in a relational triple
       (``object_kind = 'entity'`` and ``object = entity_id::text``).
    2. Retracts all active memory-module ``facts`` rows where the entity is the
       subject (``entity_id`` — gifts, loans, interactions, contact-notes,
       life-events) OR the object (``object_entity_id`` — edge-facts such as
       ``works_at``/``friend_of`` pointing AT it). On a forget there is no
       survivor to re-point onto, so these orphaned rows are retracted rather
       than left dangling on the tombstone (bu-j820n.2).
    3. Removes ``contact_entity_map`` rows for any contact linked to the entity
       so no CRM lookup can reach the tombstoned entity (bu-j820n.2).
    4. Tombstones the ``public.entities`` row by setting
       ``metadata->>'tombstone' = 'true'``.

    All steps execute inside a single database transaction so the operation is
    atomic: either every reference is retracted/cleared and the entity is
    tombstoned, or nothing changes.

    **Authorization**: owner-only gate (Amendment 12a) — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.

    Returns 404 if the entity does not exist.
    """
    pool = _pool(db)

    # Amendment 12a: owner-only write gate (roles-aware, see _assert_owner_role).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    row = await pool.fetchrow(
        """
        SELECT id FROM public.entities WHERE id = $1
        """,
        entity_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Retract all active facts where this entity is subject or object.
            await conn.execute(
                """
                UPDATE relationship.entity_facts
                SET validity   = 'retracted',
                    updated_at = now()
                WHERE validity = 'active'
                  AND (
                    subject = $1
                    OR (object_kind = 'entity' AND object = $1::text)
                  )
                """,
                entity_id,
            )
            # Retract the memory-module ``facts`` rows (gifts/loans/interactions/
            # notes/life-events keyed by ``entity_id``; edge-facts referencing the
            # entity as ``object_entity_id``). The previous handler only retracted
            # ``relationship.entity_facts``, leaving these narrative rows active and
            # orphaned on the tombstoned entity (bu-j820n.2). Delegate to the
            # canonical memory helper so the retraction semantics match the rest of
            # the memory module while running inside THIS transaction.
            from butlers.modules.memory.tools.entities import _retract_facts_on_conn

            await _retract_facts_on_conn(conn, entity_id)

            # Remove contact_entity_map rows for any contact linked to this
            # entity. A contact is bound to exactly one entity; on forget the link
            # must be severed or CRM contact lookups will return stale rows for the
            # tombstoned entity. contact_entity_map lives in the ``relationship``
            # schema — unqualified for search_path resolution, consistent with
            # _entity_resolve.py.
            await conn.execute(
                """
                DELETE FROM contact_entity_map
                WHERE entity_id = $1
                """,
                entity_id,
            )
            # Tombstone the entity row.
            await conn.execute(
                """
                UPDATE public.entities
                SET metadata   = jsonb_set(COALESCE(metadata, '{}'), '{tombstone}', 'true'),
                    updated_at = now()
                WHERE id = $1
                """,
                entity_id,
            )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /dunbar/ranking — Dunbar tier ranking for all contacts
# ---------------------------------------------------------------------------


@router.get("/dunbar/ranking", response_model=DunbarRankingResponse)
async def get_dunbar_ranking(
    db: DatabaseManager = Depends(_get_db_manager),
) -> DunbarRankingResponse:
    """Return the current Dunbar tier ranking for all listed, entity-linked contacts.

    Delegates to the shared Dunbar scoring engine (compute_tier_ranking) which
    applies exponential decay scores, rank-to-tier mapping with hysteresis, and
    manual tier overrides.  Also returns the owner entity ID for centering the
    concentric circles visualization.

    Each entry includes a ``warmth`` score (0.0–1.0) computed from recency and
    30-day interaction frequency relative to the contact's tier cadence.

    This endpoint is used by the social map visualization in the entities page.
    """
    from butlers.tools.relationship import dunbar as _dunbar

    pool = _pool(db)

    # Use the canonical scoring engine — includes decay, overrides, and hysteresis.
    ranked = await _dunbar.compute_tier_ranking(pool)

    # Fetch canonical names and avatar URLs for all entity IDs returned by the ranking.
    # avatar_url is stored in public.entities.metadata->'profile'->>'avatar_url' by the
    # contacts backfill (ContactBackfill._deep_set(metadata, "profile.avatar_url", ...)).
    entity_ids = list({r["entity_id"] for r in ranked if r["entity_id"] is not None})
    contact_ids = [r["contact_id"] for r in ranked if r["entity_id"] is not None]
    entity_name_rows, owner_row, interaction_30d_rows = await asyncio.gather(
        pool.fetch(
            """
            SELECT e.id, e.canonical_name, e.aliases,
                   e.metadata->'profile'->>'avatar_url' AS avatar_url
            FROM public.entities e
            WHERE e.id = ANY($1::uuid[])
            """,
            entity_ids,
        ),
        pool.fetchrow(
            """
            SELECT id FROM public.entities
            WHERE 'owner' = ANY(COALESCE(roles, '{}'))
            LIMIT 1
            """
        ),
        pool.fetch(
            """
            SELECT
                cem.contact_id,
                COUNT(f.id) AS interaction_count_30d
            FROM contact_entity_map cem
            JOIN facts f ON f.entity_id = cem.entity_id
            WHERE cem.contact_id = ANY($1::uuid[])
              AND f.predicate LIKE 'interaction_%'
              AND f.validity = 'active'
              AND f.scope = 'relationship'
              AND f.valid_at >= now() - INTERVAL '30 days'
            GROUP BY cem.contact_id
            """,
            contact_ids,
        ),
    )

    entity_names: dict[UUID, str] = {row["id"]: row["canonical_name"] for row in entity_name_rows}
    entity_aliases: dict[UUID, list[str]] = {
        row["id"]: list(row["aliases"]) if row["aliases"] else [] for row in entity_name_rows
    }
    entity_avatar: dict[UUID, str | None] = {
        row["id"]: row["avatar_url"] for row in entity_name_rows
    }
    interaction_30d: dict[UUID, int] = {
        row["contact_id"]: int(row["interaction_count_30d"]) for row in interaction_30d_rows
    }

    entries: list[DunbarEntry] = []
    for r in ranked:
        if r["entity_id"] is None:
            continue
        cid = r["contact_id"]
        tier = r["dunbar_tier"]
        tier_cadence = _dunbar.TIER_CADENCE.get(tier)
        if tier_cadence is not None:
            warmth = _compute_warmth(
                last_interaction_at=r.get("last_interaction_at"),
                interactions_in_last_30d=interaction_30d.get(cid, 0),
                tier_cadence_days=tier_cadence,
            )
        else:
            warmth = None  # Tier 1500 — no cadence target, warmth undefined

        entries.append(
            DunbarEntry(
                contact_id=cid,
                entity_id=r["entity_id"],
                canonical_name=entity_names.get(r["entity_id"], "Unknown"),
                dunbar_tier=tier,
                dunbar_score=r["dunbar_score"],
                dunbar_tier_override=r.get("dunbar_tier_override", False),
                avatar_url=entity_avatar.get(r["entity_id"]),
                aliases=entity_aliases.get(r["entity_id"], []),
                warmth=warmth,
                last_interaction_at=r.get("last_interaction_at"),
            )
        )

    owner_entity_id = owner_row["id"] if owner_row else None

    return DunbarRankingResponse(entries=entries, owner_entity_id=owner_entity_id)


# ---------------------------------------------------------------------------
# POST /entities/{entity_id}/merge — entity-level merge (bu-jp6r6)
# ---------------------------------------------------------------------------


@router.post(
    "/entities/{entity_id}/merge",
    response_model=MergeEntitiesResponse,
)
async def merge_entities(
    entity_id: UUID,
    body: MergeEntitiesRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> MergeEntitiesResponse:
    """Merge two entities into one, rewiring all relationship.entity_facts triples atomically.

    **What this does:**

    1. Validates both entities exist and are not already tombstoned.
    2. Computes source (the entity NOT kept) and target (the ``keepAs`` side).
    3. Rewires ``relationship.entity_facts`` rows where ``subject = source`` → ``subject = target``.
    4. Rewires ``relationship.entity_facts`` rows where ``object_kind='entity'`` and
       ``object = source::text`` → ``object = target::text``.
    5. Re-points the memory-module ``facts`` store (gifts, loans, interactions,
       notes, life-events keyed by ``entity_id``; edge-facts via
       ``object_entity_id``) from source → target, with the same
       confidence-based supersession used by the memory butler's ``entity_merge``.
    6. Re-points ``contact_entity_map`` rows from source → target so linked
       contacts follow the survivor.
    7. Tombstones source entity: sets ``metadata->>'merged_into'`` to the target UUID string.

    Steps 3–7 execute inside a **single transaction** (atomicity guarantee): a
    partial merge can never leave rows stranded on the tombstoned source.

    **Conflict handling:** rewiring a subject row may collide with an existing active triple
    at (target, predicate, object) due to the ``uq_ef_spo_active`` partial unique index.
    Such conflicting source rows are retracted (``validity='superseded'``) instead of
    being moved, to preserve the target's existing fact.

    **Authorization**: owner-only gate (Amendment 12a) — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.

    **Error codes:**
    - ``403`` — owner entity not registered (Amendment 12a).
    - ``404`` — either entity does not exist or is already tombstoned.
    - ``422`` — ``entityA == entityB`` (same entity) or validation failure.
    """
    pool = _pool(db)

    # Amendment 12a: owner-only write gate (roles-aware, see _assert_owner_role).
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Validate request: entityA and entityB must be distinct.
    if body.entityA == body.entityB:
        raise HTTPException(
            status_code=422,
            detail={"code": "same_entity", "message": "entityA and entityB must be different."},
        )

    # Compute source and target from keepAs.
    if body.keepAs == "A":
        target_id = body.entityA
        source_id = body.entityB
    else:
        target_id = body.entityB
        source_id = body.entityA

    # Compute the merge-review evidence snapshot BEFORE the transaction mutates
    # rows — the shared/divergent diff must reflect the pre-merge state. Every
    # merge through this endpoint leaves a merge_reviews audit row regardless of
    # entry path (spec: relationship-merge-review "Single-pair review UX").
    merge_snapshot = await _compute_compare_snapshot(pool, body.entityA, body.entityB)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Lock both entities in deterministic UUID order to prevent deadlocks when
            #    concurrent merge requests target the same pair in opposite directions.
            lock_rows = await conn.fetch(
                """
                SELECT id, metadata
                FROM public.entities
                WHERE id = ANY($1::uuid[])
                ORDER BY id
                FOR UPDATE
                """,
                [source_id, target_id],
            )
            lock_map = {row["id"]: row for row in lock_rows}

            src_row = lock_map.get(source_id)
            if src_row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Source entity '{source_id}' not found.",
                )
            src_meta: dict = src_row["metadata"] or {}
            if "merged_into" in src_meta:
                raise HTTPException(
                    status_code=404,
                    detail=f"Source entity '{source_id}' is already tombstoned.",
                )

            tgt_row = lock_map.get(target_id)
            if tgt_row is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Target entity '{target_id}' not found.",
                )
            tgt_meta: dict = tgt_row["metadata"] or {}
            if "merged_into" in tgt_meta:
                raise HTTPException(
                    status_code=404,
                    detail=f"Target entity '{target_id}' is already tombstoned.",
                )

            # 2. Rewire subject-side facts: source → target.
            # For rows that would collide at (target, predicate, object) on the
            # uq_ef_spo_active partial unique index, retract the source row instead.

            # First, retract source subject-rows that conflict with existing target rows.
            await conn.execute(
                """
                UPDATE relationship.entity_facts AS src
                SET validity = 'superseded',
                    updated_at = now()
                WHERE src.subject = $1
                  AND src.validity = 'active'
                  AND EXISTS (
                      SELECT 1 FROM relationship.entity_facts tgt
                      WHERE tgt.subject = $2
                        AND tgt.predicate = src.predicate
                        AND tgt.object = src.object
                        AND tgt.validity = 'active'
                  )
                """,
                source_id,
                target_id,
            )

            # Resolve single-cardinality DIVERGENCE before moving the remainder.
            # For a predicate registered with cardinality='single', source and
            # target may each hold an active row with DIFFERENT objects (no exact
            # (p,o) collision, so the retraction above left both standing). The
            # merge-review spec calls these "the conflicts a merge must resolve";
            # the lifecycle spec rationale states merge keeps higher-conf facts.
            #
            # Resolution rule (registry-driven; NO hardcoded predicate list):
            # keep the higher-conf row and supersede the loser. Ties go to the
            # TARGET (the row whose subject is already the survivor), consistent
            # with the assert-path supersession semantics. Multi-valued predicates
            # are absent from this set and union normally (three-emails rule).
            #
            # The window orders winners by (conf DESC, target-first, id) so the
            # top row per predicate is the keeper; every other active row across
            # the source/target pair for that single-cardinality predicate is
            # superseded. Multi-row pre-existing duplicates on one side (should not
            # happen for single-cardinality, but be defensive) collapse to one.
            await conn.execute(
                """
                WITH ranked AS (
                    SELECT
                        ef.id,
                        row_number() OVER (
                            PARTITION BY ef.predicate
                            ORDER BY
                                ef.conf DESC,
                                (ef.subject = $2) DESC,
                                ef.id
                        ) AS rn
                    FROM relationship.entity_facts ef
                    JOIN relationship.entity_predicate_registry pr
                      ON pr.predicate = ef.predicate
                    WHERE ef.subject IN ($1, $2)
                      AND ef.validity = 'active'
                      AND pr.cardinality = 'single'
                )
                UPDATE relationship.entity_facts AS ef
                SET validity = 'superseded',
                    updated_at = now()
                FROM ranked
                WHERE ef.id = ranked.id
                  AND ranked.rn > 1
                """,
                source_id,
                target_id,
            )

            # Then move the remaining (non-conflicting) active source subject-rows.
            subject_result = await conn.fetchval(
                """
                WITH updated AS (
                    UPDATE relationship.entity_facts
                    SET subject = $2,
                        updated_at = now()
                    WHERE subject = $1
                      AND validity = 'active'
                    RETURNING id
                )
                SELECT count(*) FROM updated
                """,
                source_id,
                target_id,
            )
            subject_facts_rewired = subject_result

            # 3. Rewire object-side facts: source appears as object with object_kind='entity'.
            source_text = str(source_id)
            target_text = str(target_id)

            # Retract object-side source rows that conflict with existing target rows.
            await conn.execute(
                """
                UPDATE relationship.entity_facts AS src
                SET validity = 'superseded',
                    updated_at = now()
                WHERE src.object_kind = 'entity'
                  AND src.object = $1
                  AND src.validity = 'active'
                  AND EXISTS (
                      SELECT 1 FROM relationship.entity_facts tgt
                      WHERE tgt.subject = src.subject
                        AND tgt.predicate = src.predicate
                        AND tgt.object = $2
                        AND tgt.object_kind = 'entity'
                        AND tgt.validity = 'active'
                  )
                """,
                source_text,
                target_text,
            )

            # Move the remaining active object-side rows.
            object_result = await conn.fetchval(
                """
                WITH updated AS (
                    UPDATE relationship.entity_facts
                    SET object = $2,
                        updated_at = now()
                    WHERE object_kind = 'entity'
                      AND object = $1
                      AND validity = 'active'
                    RETURNING id
                )
                SELECT count(*) FROM updated
                """,
                source_text,
                target_text,
            )
            object_facts_rewired = object_result

            # 3b. Re-point the memory-module ``facts`` store (gifts, loans,
            # interactions, contact-notes, life-events all live here keyed by
            # ``entity_id``; edge-facts reference the source as
            # ``object_entity_id``). The dashboard previously only moved
            # ``relationship.entity_facts``, so these narrative rows orphaned onto
            # the tombstoned source and vanished from the survivor (bu-j820n.1).
            # Delegating to the canonical ``_repoint_facts_on_conn`` keeps the
            # confidence-based supersession semantics identical to the memory
            # butler's ``entity_merge`` while running inside THIS transaction.
            from butlers.modules.memory.tools.entities import _repoint_facts_on_conn

            await _repoint_facts_on_conn(conn, source_id, target_id)

            # 3c. Re-point contact_entity_map rows from source → target. A contact
            # is bound to exactly one entity; on merge every contact that pointed at
            # the source must follow the survivor or CRM contact lookups will return
            # stale rows for the tombstoned source. contact_entity_map lives in the
            # ``relationship`` schema — unqualified for search_path resolution,
            # consistent with _entity_resolve.py.
            await conn.execute(
                """
                UPDATE contact_entity_map
                SET entity_id = $2
                WHERE entity_id = $1
                """,
                source_id,
                target_id,
            )

            # 4. Tombstone source entity via merged_into metadata key.
            tombstone_meta = {**src_meta, "merged_into": str(target_id)}
            await conn.execute(
                """
                UPDATE public.entities
                SET metadata = $1,
                    updated_at = now()
                WHERE id = $2
                """,
                tombstone_meta,
                source_id,
            )

            # 5. Write the merge-review audit row inside the SAME transaction as
            # the rewire/tombstone (spec: "POST /entities/{id}/merge itself MUST
            # write a merge_reviews audit row" — regardless of entry path). The
            # snapshot was computed pre-transaction (see _compute_compare_snapshot)
            # so the evidence reflects the pre-merge state; writing the audit row
            # on `conn` (not `pool`) closes the crash window where a committed
            # merge could leave no audit trail.
            await _write_merge_review(
                conn,
                entity_a=body.entityA,
                entity_b=body.entityB,
                shared_facts=merge_snapshot["shared"],
                divergent_facts=merge_snapshot["divergent"],
                outcome="merged",
            )

    return MergeEntitiesResponse(
        kept_entity_id=target_id,
        tombstoned_entity_id=source_id,
        subject_facts_rewired=subject_facts_rewired,
        object_facts_rewired=object_facts_rewired,
    )


# ---------------------------------------------------------------------------
# POST /entities/compare — structural diff (entity v3, relationship-merge-review)
# POST /entities/dismiss-pair — dismissal suppression key
# ---------------------------------------------------------------------------


def _compare_fact_from_identity_row(r: Any) -> Any:
    """Build a CompareFact from an identity-store (relationship.entity_facts) row."""
    return CompareFact(
        id=r["id"],
        entity_id=r["subject"],
        predicate=r["predicate"],
        object=r["object"],
        object_kind=r["object_kind"],
        store="identity",
        src=r["src"],
        conf=float(r["conf"]) if r["conf"] is not None else 1.0,
        verified=r["verified"],
        primary=r["primary"],
        observed_at=r["observed_at"],
        last_seen=r["last_seen"],
        staleness_band=r["staleness_band"],
    )


def _compare_fact_from_narrative_row(r: Any) -> Any:
    """Build a CompareFact from a narrative-store (memory-module facts) row.

    Narrative rows have no ``last_seen`` column (``last_seen`` stays ``None``).
    """
    return CompareFact(
        id=r["id"],
        entity_id=r["subject"],
        predicate=r["predicate"],
        object=r["object"],
        object_kind=r["object_kind"],
        store="narrative",
        src=r["src"],
        conf=float(r["conf"]) if r["conf"] is not None else 1.0,
        verified=r["verified"],
        primary=r["primary"],
        observed_at=r["observed_at"],
        last_seen=None,
        staleness_band=r["staleness_band"],
    )


async def _fetch_identity_facts_for_compare(pool, entity_id: UUID) -> list[Any]:
    """Fetch active identity-store facts for an entity with staleness bands."""
    from butlers.tools.relationship.staleness import identity_staleness_band_sql

    return await pool.fetch(
        f"""
        SELECT
            f.id,
            f.subject,
            f.predicate,
            f.object,
            f.object_kind,
            f.src,
            f.conf,
            f.verified,
            f."primary",
            f.observed_at,
            f.last_seen,
            {identity_staleness_band_sql("f")} AS staleness_band
        FROM relationship.entity_facts f
        WHERE f.subject = $1
          AND f.validity = 'active'
        ORDER BY f.predicate, f.created_at DESC, f.id DESC
        """,
        entity_id,
    )


async def _fetch_narrative_facts_for_compare(pool, entity_id: UUID) -> list[Any]:
    """Fetch active narrative-store facts (memory-module ``facts``) for an entity.

    Scope-filtered per the canonical narrative read rule
    (``staleness.narrative_scope_sql`` — ``scope IN ('relationship', 'global')``)
    so compare matches the drill, delta, and lookup surfaces (bu-3jrq3).

    Interaction-log facts (``predicate LIKE 'interaction_%'``) are excluded: they
    are an unbounded temporal log (one row per logged Telegram/meeting/etc.) with
    their own dedicated surfaces, they never conflict on merge (multi-valued,
    union semantics), and dumping them all floods the compare dialog (bu-xzxw4).
    The merge decision rests on descriptive narrative facts, not the contact log.
    """
    from butlers.tools.relationship.staleness import (
        narrative_scope_sql,
        narrative_staleness_band_sql,
    )

    return await pool.fetch(
        f"""
        SELECT
            f.id,
            f.entity_id           AS subject,
            f.predicate,
            f.content             AS object,
            'literal'::text       AS object_kind,
            COALESCE(f.source_butler, 'memory')::text AS src,
            f.confidence          AS conf,
            false                 AS verified,
            NULL::bool            AS "primary",
            f.observed_at,
            {narrative_staleness_band_sql("f")} AS staleness_band
        FROM facts f
        WHERE f.entity_id = $1
          AND {narrative_scope_sql("f")}
          AND f.validity = 'active'
          AND f.predicate NOT LIKE 'interaction_%'
        ORDER BY f.predicate, f.created_at DESC, f.id DESC
        """,
        entity_id,
    )


async def _fetch_single_cardinality_predicates(pool) -> set[str]:
    """Return the set of predicates with ``cardinality = 'single'`` in the registry.

    Single-cardinality predicates are the only ones that can DIVERGE on merge
    (an entity holds at most one active value). Multi-valued predicates union on
    merge and never conflict (the three-emails-three-rows rule).

    Delegates to the shared model-free implementation in
    ``butlers.tools.relationship.merge_review`` so the API and session-side merge
    paths share one definition.
    """
    return await _fetch_single_cardinality_predicates_shared(pool)


def _derive_shared_and_divergent(
    a_identity: list[Any],
    b_identity: list[Any],
    single_predicates: set[str],
) -> tuple[list[Any], list[Any]]:
    """Compute the ``shared`` and ``divergent`` lists from two identity-fact sets.

    - ``shared``: rows where both entities hold an active row with identical
      ``(predicate, object)``. Emitted as the A-row followed by the B-row.
    - ``divergent``: rows for single-cardinality predicates that BOTH entities
      hold but with DIFFERENT objects. Multi-valued predicates never diverge.

    The deterministic structural diff itself lives in the shared model-free helper
    ``merge_review.derive_shared_and_divergent_rows`` (the single source of truth
    used by both the API and session-side merge paths); this wrapper adapts the
    raw rows into ``CompareFact`` models for the API response.
    """
    shared_rows, divergent_rows = _derive_shared_and_divergent_rows_shared(
        a_identity, b_identity, single_predicates
    )
    shared = [_compare_fact_from_identity_row(r) for r in shared_rows]
    divergent = [_compare_fact_from_identity_row(r) for r in divergent_rows]
    return shared, divergent


async def _compute_compare_snapshot(pool, entity_a: UUID, entity_b: UUID) -> dict[str, Any]:
    """Compute the full structural-diff snapshot for a pair of entities.

    Returns a dict with ``a`` / ``b`` (CompareEntityBlock), ``shared`` and
    ``divergent`` (lists of CompareFact). Reused by both the compare endpoint and
    the merge endpoint's audit-row snapshot (computed server-side at merge time
    when no compare context exists).

    No scoring, no ranking, no generated text — deterministic structural diff.
    """
    a_summary_row, b_summary_row = await asyncio.gather(
        pool.fetchrow(_COMPARE_SUMMARY_SQL, entity_a),
        pool.fetchrow(_COMPARE_SUMMARY_SQL, entity_b),
    )
    # Fail fast on an unknown/tombstoned entity (the summary SQL excludes
    # tombstoned rows). Raised as 404 to the caller (compare + merge endpoints).
    if a_summary_row is None or b_summary_row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    a_identity, b_identity, a_narrative, b_narrative = await asyncio.gather(
        _fetch_identity_facts_for_compare(pool, entity_a),
        _fetch_identity_facts_for_compare(pool, entity_b),
        _fetch_narrative_facts_for_compare(pool, entity_a),
        _fetch_narrative_facts_for_compare(pool, entity_b),
    )

    (state_a, _), (state_b, _), single_predicates = await asyncio.gather(
        _classify_entity_state(pool, entity_a),
        _classify_entity_state(pool, entity_b),
        _fetch_single_cardinality_predicates(pool),
    )

    shared, divergent = _derive_shared_and_divergent(a_identity, b_identity, single_predicates)

    def _block(summary_row: Any, identity_rows: list[Any], narrative_rows: list[Any], state: str):
        return CompareEntityBlock(
            entity=CompareEntitySummary(
                id=summary_row["id"],
                canonical_name=summary_row["canonical_name"],
                entity_type=summary_row["entity_type"],
                aliases=list(summary_row["aliases"]) if summary_row["aliases"] else [],
                tier=summary_row["tier"],
                state=state,
            ),
            identity_facts=[_compare_fact_from_identity_row(r) for r in identity_rows],
            narrative_facts=[_compare_fact_from_narrative_row(r) for r in narrative_rows],
        )

    return {
        "a": _block(a_summary_row, a_identity, a_narrative, state_a),
        "b": _block(b_summary_row, b_identity, b_narrative, state_b),
        "shared": shared,
        "divergent": divergent,
    }


#: Entity-summary SELECT for the compare blocks. ``tier`` is the pinned Dunbar
#: tier override fact (nullable). Excludes tombstoned entities.
_COMPARE_SUMMARY_SQL = """
    SELECT
        e.id,
        e.canonical_name,
        e.entity_type,
        e.aliases,
        (
            SELECT (rf.object)::int
            FROM relationship.entity_facts rf
            WHERE rf.subject = e.id
              AND rf.predicate = 'dunbar_tier_override'
              AND rf.validity = 'active'
            ORDER BY rf.created_at DESC
            LIMIT 1
        ) AS tier
    FROM public.entities e
    WHERE e.id = $1
      AND (e.metadata->>'merged_into') IS NULL
"""


async def _write_merge_review(
    executor,
    *,
    entity_a: UUID,
    entity_b: UUID,
    shared_facts: list[Any],
    divergent_facts: list[Any],
    outcome: str,
) -> UUID:
    """Insert a ``relationship.merge_reviews`` audit row, returning its id.

    ``executor`` is any asyncpg executor exposing ``fetchval`` — a pool (the
    dismissal path, which has no surrounding transaction) or a connection inside
    an open transaction (the merge path, so the audit row commits atomically with
    the rewire/tombstone and no crash window can leave a merge without its audit
    row).

    The evidence snapshot is serialized from CompareFact lists (JSON-mode dumps so
    UUIDs/datetimes become strings) and handed to the shared model-free writer in
    ``butlers.tools.relationship.merge_review`` — the single source of truth for
    the audit-row INSERT used by both the API and session-side merge paths. Rows
    are written at commit time only (no pending state); both merge and dismissal
    write a row.
    """
    return await _write_merge_review_shared(
        executor,
        entity_a=entity_a,
        entity_b=entity_b,
        shared_facts=[f.model_dump(mode="json") for f in shared_facts],
        divergent_facts=[f.model_dump(mode="json") for f in divergent_facts],
        outcome=outcome,
    )


@router.post("/entities/compare", response_model=CompareResponse)
async def compare_entities(
    body: CompareRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> CompareResponse:
    """Structural diff of two entities — the merge-review compare view.

    Returns a server-computed, deterministic diff (no scoring, no ranking, no
    similarity percentage, no generated text of any kind):

    - ``a`` / ``b`` — per-entity blocks ``{entity (incl. nullable tier),
      identity_facts, narrative_facts}`` with full provenance + ``staleness_band``
      on every fact, reading BOTH stores.
    - ``shared`` — identity-store rows present on BOTH entities with identical
      ``(predicate, object)`` (the duplicate evidence). Narrative facts never
      enter ``shared``.
    - ``divergent`` — identity-store rows for predicates whose registry
      ``cardinality = 'single'`` whose objects differ between the two entities.
      Multi-valued predicates union on merge and never appear as divergences.

    **Authorization**: owner-only gate (Clause 12a/12b) — returns HTTP 403 with
    ``{"code": "owner_required"}`` when no owner entity is registered.

    **Error codes:**
    - ``403`` — owner entity not registered.
    - ``404`` — either entity does not exist (or is tombstoned).
    - ``422`` — ``entity_a == entity_b``.
    """
    pool = _pool(db)

    # Owner-only gate (Clause 12a/12b) — roles-aware.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    if body.entity_a == body.entity_b:
        raise HTTPException(
            status_code=422,
            detail={"code": "same_entity", "message": "entity_a and entity_b must be different."},
        )

    # Existence check (404 on unknown/tombstoned) is enforced inside the snapshot.
    snapshot = await _compute_compare_snapshot(pool, body.entity_a, body.entity_b)

    return CompareResponse(
        a=snapshot["a"],
        b=snapshot["b"],
        shared=snapshot["shared"],
        divergent=snapshot["divergent"],
    )


@router.post("/entities/dismiss-pair", response_model=DismissPairResponse)
async def dismiss_pair(
    body: DismissPairRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> DismissPairResponse:
    """Dismiss a compared pair — writes a ``merge_reviews`` row (outcome=dismissed).

    The dismissal row is the suppression key for the queue (per
    ``relationship-entity-lifecycle`` queue derivation): the pair stays out of the
    duplicate-candidate bucket until a ``{predicate, shared_value}`` not present in
    the dismissal's ``shared_facts`` snapshot arises. The shared snapshot is
    computed server-side at dismissal time so the suppression key is authoritative.

    **Authorization**: owner-only gate (Clause 12a/12b) — HTTP 403
    ``{"code": "owner_required"}`` when no owner entity is registered.

    **Error codes:**
    - ``403`` — owner entity not registered.
    - ``404`` — either entity does not exist (or is tombstoned).
    - ``422`` — ``entity_a == entity_b``.
    """
    pool = _pool(db)

    if (err := await _assert_owner_role(pool)) is not None:
        return err

    if body.entity_a == body.entity_b:
        raise HTTPException(
            status_code=422,
            detail={"code": "same_entity", "message": "entity_a and entity_b must be different."},
        )

    # Existence check (404 on unknown/tombstoned) is enforced inside the snapshot.
    snapshot = await _compute_compare_snapshot(pool, body.entity_a, body.entity_b)
    shared = snapshot["shared"]
    divergent = snapshot["divergent"]

    review_id = await _write_merge_review(
        pool,
        entity_a=body.entity_a,
        entity_b=body.entity_b,
        shared_facts=shared,
        divergent_facts=divergent,
        outcome="dismissed",
    )

    return DismissPairResponse(
        review_id=review_id,
        entity_a=body.entity_a,
        entity_b=body.entity_b,
        outcome="dismissed",
        shared_facts=shared,
    )


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/activity  — unified activity aggregator (bu-ihiw4)
# ---------------------------------------------------------------------------


#: Chronicler MCP timeout in seconds.  The call is fire-and-forget on failure;
#: the aggregator degrades gracefully if the chronicler is unreachable.
_CHRONICLER_ACTIVITY_TIMEOUT_S = 10.0

#: Predicate → kind mapping for relationship.entity_facts rows surfaced in activity.
#: Predicates not listed here are surfaced with kind='fact'.
_FACT_PREDICATE_KIND: dict[str, str] = {
    "contact_note": "note",
    "interaction_meeting": "interaction",
    "interaction_call": "interaction",
    "interaction_email": "interaction",
    "interaction_message": "interaction",
    "interaction_event": "interaction",
    "interaction_social": "interaction",
    "gift": "gift",
    "loan": "loan",
    "life_event": "life_event",
    "dunbar_tier_override": "dunbar_tier_override",
}


async def _fetch_relationship_activity(
    pool: object,
    entity_id: UUID,
) -> list[ActivityEntry]:
    """Fetch active facts from relationship.entity_facts for the given entity.

    Returns all facts where subject=$entity_id OR (object_kind='entity'
    AND object=$entity_id::text).  Ordered by timestamp DESC.

    INVARIANT: No SQL references to chronicler.* schemas.
    """
    rows = await pool.fetch(
        """
        SELECT
            f.id,
            f.predicate,
            f.last_seen,
            f.created_at
        FROM relationship.entity_facts f
        WHERE f.validity = 'active'
          AND (
              f.subject = $1
              OR (f.object_kind = 'entity' AND f.object = $1::text)
          )
        ORDER BY COALESCE(f.last_seen, f.created_at) DESC NULLS LAST, f.id
        """,
        entity_id,
    )

    entries: list[ActivityEntry] = []
    for r in rows:
        predicate: str = r["predicate"]
        kind = _FACT_PREDICATE_KIND.get(predicate, "fact")
        ts: datetime | None = r["last_seen"] or r["created_at"]
        entries.append(
            ActivityEntry(
                id=r["id"],
                ts=ts,
                kind=kind,
                src="relationship",
                predicate=predicate,
            )
        )
    return entries


async def _fetch_chronicler_activity(
    mcp_manager: MCPClientManager | None,
    entity_id: UUID,
) -> list[ActivityEntry]:
    """Fetch episodes from the chronicler butler via MCP.

    Calls ``chronicler_list_episodes(participant_entity_id=<entity_id>, limit=500)``
    and converts each corrected episode into an ``ActivityEntry`` with
    ``src='chronicler'``.

    Uses ``participant_entity_id`` (join-based multi-role filter) rather than
    the legacy ``entity_id`` (owner-only column filter) so that meeting episodes
    where the requested entity is an organizer or attendee — but not the calendar
    owner — surface in the entity's activity feed.

    Returns an empty list when the chronicler is unreachable, the MCP
    call fails, or ``mcp_manager`` is None — graceful degrade, never raises.

    INVARIANT: No direct SQL on chronicler.* tables.
    """
    if mcp_manager is None:
        return []
    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client("chronicler"),
            timeout=_CHRONICLER_ACTIVITY_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool(
                "chronicler_list_episodes",
                {"participant_entity_id": str(entity_id), "limit": 500},
            ),
            timeout=_CHRONICLER_ACTIVITY_TIMEOUT_S,
        )
    except (ButlerUnreachableError, TimeoutError, Exception) as exc:
        logger.info(
            "Chronicler activity fetch failed for entity %s (graceful degrade): %s",
            entity_id,
            exc,
        )
        return []

    raw_text = _extract_mcp_result_text(result)
    payload = _parse_mcp_result_payload(raw_text)

    is_error = bool(getattr(result, "is_error", False))
    if is_error or not isinstance(payload, dict):
        logger.info(
            "Chronicler activity: unexpected payload for entity %s; degrading gracefully",
            entity_id,
        )
        return []

    episodes: list[dict] = payload.get("data", [])
    entries: list[ActivityEntry] = []
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        episode_id_raw = ep.get("id")
        if not episode_id_raw:
            continue
        try:
            episode_uuid = UUID(str(episode_id_raw))
        except (ValueError, AttributeError):
            continue

        # Use canonical_start_at as the primary timestamp; fall back to start_at.
        ts_raw = ep.get("canonical_start_at") or ep.get("start_at")
        ts: datetime | None = None
        if ts_raw:
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except (ValueError, TypeError):
                pass

        summary = ep.get("canonical_title") or ep.get("title")

        entries.append(
            ActivityEntry(
                id=episode_uuid,
                ts=ts,
                kind="episode",
                src="chronicler",
                episode_id=episode_uuid,
                summary=str(summary) if summary is not None else None,
            )
        )
    return entries


def _sort_key_activity(entry: ActivityEntry) -> datetime:
    """Sort key for activity entries: timestamp DESC (None → epoch for stable tail sort)."""
    if entry.ts is None:
        return datetime.min.replace(tzinfo=UTC)
    # Normalise to UTC-aware so comparison works across tz-aware and tz-naive.
    if entry.ts.tzinfo is None:
        return entry.ts.replace(tzinfo=UTC)
    return entry.ts


def _build_daily_bins(entries: list[ActivityEntry], window_days: int) -> list[ActivityBin]:
    """Bin merged activity entries into a dense per-day count series.

    Produces exactly ``window_days`` bins ascending by date, covering the
    ``[today - (window_days - 1), today]`` inclusive range in UTC. Every day is
    present — quiet days carry ``count=0`` rather than being collapsed out (spec:
    "no day MUST be omitted or interpolated"). Entries whose timestamp is None or
    falls outside the window are ignored.

    The entries here are the SAME merged stream the endpoint already assembles
    (relationship facts + chronicler episodes sourced via the
    ``chronicler_list_episodes`` MCP tool); binning never reaches across the
    chronicler boundary itself.
    """
    today = datetime.now(UTC).date()
    start = today - timedelta(days=window_days - 1)

    counts: dict[date, int] = {}
    for entry in entries:
        if entry.ts is None:
            continue
        day = entry.ts.astimezone(UTC).date() if entry.ts.tzinfo else entry.ts.date()
        if start <= day <= today:
            counts[day] = counts.get(day, 0) + 1

    bins: list[ActivityBin] = []
    for i in range(window_days):
        day = start + timedelta(days=i)
        bins.append(ActivityBin(date=day, count=counts.get(day, 0)))
    return bins


@router.get("/entities/{entity_id}/activity", response_model=None)
async def get_entity_activity(
    entity_id: UUID,
    limit: int = Query(50, ge=1, le=200, description="Maximum entries per page."),
    offset: int = Query(0, ge=0, description="Pagination offset."),
    bins: Literal["daily"] | None = Query(
        None,
        description="When 'daily', also compute a per-day activity-count series "
        "over the window (the sparkline source).",
    ),
    window: str = Query(
        "90d",
        pattern=r"^\d{1,3}d$",
        description="Binning window as '<N>d' (e.g. '90d'). Only honoured with bins=daily.",
    ),
    bins_only: bool = Query(
        False,
        description="When true (and bins=daily), return only {bins:[...]} and omit "
        "the merged stream.",
    ),
    db: DatabaseManager = Depends(_get_db_manager),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> ActivityResponse | ActivityBinsResponse:
    """Return a merged activity stream for the given entity.

    Combines:

    1. **Relationship facts** — all active ``relationship.entity_facts`` rows where
       the entity is either subject or object (entity-side triple), regardless
       of predicate.  Tagged ``src='relationship'``.
    2. **Chronicler episodes** — episodes linked to this entity, fetched via
       the ``chronicler_list_episodes`` MCP tool (not direct SQL).  Tagged
       ``src='chronicler'``.

    The merged stream is sorted by timestamp descending (``last_seen`` for
    facts; ``canonical_start_at`` for episodes).  Pagination is applied after
    the merge.  ``total`` reflects the merged count before slicing.

    **Binning** (entity v3 — sparkline source): with ``bins=daily`` the endpoint
    additionally computes a dense per-day activity-count series over ``window``
    (default ``90d``) — one bin per day including zero-count days, ascending by
    date. ``bins_only=true`` returns ``{bins:[...]}`` alone (omitting the merged
    stream); otherwise ``bins`` rides alongside the stream. Chronicler rows are
    sourced via the same ``chronicler_list_episodes`` MCP path before binning —
    the binning step never crosses the chronicler boundary.

    **Authorization**: owner-only gate (Amendment 12b) — returns HTTP 403
    with ``{"code": "owner_required"}`` when no owner entity is registered.

    Returns 404 if the entity does not exist in ``public.entities``.

    **Chronicler boundary**: the relationship butler MUST NOT query
    ``chronicler.*`` tables directly; this endpoint calls
    ``chronicler_list_episodes`` via MCP and degrades gracefully when the
    chronicler is unreachable.
    """
    pool = _pool(db)

    # Owner-only gate (Clause 12b, Amendment 12b) — roles-aware via _assert_owner_role.
    # Uses the same roles-aware check as 12a mutation endpoints: the mock in
    # test_owner_authz_guardrail.py returns rows with roles=[], so checking
    # the roles field (rather than relying on a SQL WHERE clause) produces
    # the correct 403 in both real and mock contexts.
    if (err := await _assert_owner_role(pool)) is not None:
        return err

    # Entity existence gate.
    await _assert_entity_exists(pool, entity_id)

    # Fetch from both sources concurrently.
    rel_entries, chr_entries = await asyncio.gather(
        _fetch_relationship_activity(pool, entity_id),
        _fetch_chronicler_activity(mcp_manager, entity_id),
    )

    # Merge and sort descending by timestamp.
    all_entries: list[ActivityEntry] = rel_entries + chr_entries
    all_entries.sort(key=_sort_key_activity, reverse=True)

    # Daily binning (sparkline). window is validated as '<N>d' by the route regex.
    if bins == "daily":
        window_days = int(window[:-1])
        daily = _build_daily_bins(all_entries, window_days)
        if bins_only:
            return ActivityBinsResponse(bins=daily)
        total = len(all_entries)
        page = all_entries[offset : offset + limit]
        # ActivityResponse with an additional `bins` field. The response_model is
        # omitted on the route so this richer shape is returned verbatim; JSON mode
        # serialises the nested datetimes/UUIDs/dates for the plain-dict return.
        stream = ActivityResponse(items=page, total=total, limit=limit, offset=offset)
        return stream.model_dump(mode="json") | {"bins": [b.model_dump(mode="json") for b in daily]}

    total = len(all_entries)
    page = all_entries[offset : offset + limit]

    return ActivityResponse(items=page, total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# View marks + delta-since-last-visit (entity v3 — "Delta-since-last-visit")
# ---------------------------------------------------------------------------


@router.post("/entities/{entity_id}/view-mark", response_model=ViewMarkResponse)
async def mark_entity_view(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ViewMarkResponse | JSONResponse:
    """Upsert the owner's "last viewed" mark for an entity.

    Persists ``now()`` into ``relationship.entity_view_marks`` (one mark per
    entity, ``ON CONFLICT (entity_id) DO UPDATE``). The frontend posts this only
    *after* reading ``GET /entities/{id}/delta-facts`` so the next visit's delta
    is computed relative to this mark (spec: "the view mark MUST be updated only
    after the delta was computed for this load").

    Owner-only authz gate (Clause 12b): HTTP 403 ``{"code": "owner_required"}``
    if no owner entity is registered. Returns 404 if the entity does not exist.
    """
    pool = _pool(db)

    if (err := await _assert_owner_role(pool)) is not None:
        return err
    await _assert_entity_exists(pool, entity_id)

    row = await pool.fetchrow(
        """
        INSERT INTO relationship.entity_view_marks (entity_id, marked_at)
        VALUES ($1, now())
        ON CONFLICT (entity_id) DO UPDATE SET marked_at = now()
        RETURNING entity_id, marked_at
        """,
        entity_id,
    )
    return ViewMarkResponse(entity_id=row["entity_id"], marked_at=row["marked_at"])


async def _fetch_narrative_delta_facts(pool, entity_id: UUID, marked_at: datetime) -> list[Any]:
    """Narrative-store facts changed since ``marked_at`` (delta banner).

    Scope-filtered per the canonical narrative read rule
    (``staleness.narrative_scope_sql`` — ``scope IN ('relationship', 'global')``)
    so the delta banner matches the drill, compare, and lookup surfaces
    (bu-3jrq3).
    """
    from butlers.tools.relationship.staleness import narrative_scope_sql

    return await pool.fetch(
        f"""
        SELECT
            f.id,
            f.entity_id AS subject,
            f.predicate,
            f.content   AS object,
            'literal'::text AS object_kind,
            COALESCE(f.source_butler, 'memory')::text AS src,
            f.confidence AS conf,
            f.validity,
            f.created_at,
            GREATEST(f.created_at, COALESCE(f.last_confirmed_at, f.created_at)) AS changed_at
        FROM facts f
        WHERE f.entity_id = $1
          AND {narrative_scope_sql("f")}
          AND GREATEST(f.created_at, COALESCE(f.last_confirmed_at, f.created_at)) > $2
        ORDER BY changed_at DESC, f.id DESC
        """,
        entity_id,
        marked_at,
    )


@router.get("/entities/{entity_id}/delta-facts", response_model=DeltaFactsResponse)
async def get_entity_delta_facts(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> DeltaFactsResponse | JSONResponse:
    """Return facts changed since the entity's view mark (delta-since-last-visit).

    Computes the per-store change set relative to ``relationship.entity_view_marks``:

    - **identity store** (``relationship.entity_facts``):
      ``GREATEST(created_at, updated_at) > marked_at``
    - **narrative store** (memory-module ``facts``):
      ``GREATEST(created_at, COALESCE(last_confirmed_at, created_at)) > marked_at``

    This endpoint is **read-only** — it never moves the mark. The caller posts
    ``POST /entities/{id}/view-mark`` afterwards (spec: delta is read before the
    mark moves). On a first visit (no mark row) ``marked_at`` is ``None`` and
    ``items`` is empty, so the frontend renders no banner.

    Owner-only authz gate (Clause 12b — returns raw contact-fact values): HTTP
    403 ``{"code": "owner_required"}`` if no owner entity is registered. Returns
    404 if the entity does not exist.
    """
    pool = _pool(db)

    if (err := await _assert_owner_role(pool)) is not None:
        return err
    await _assert_entity_exists(pool, entity_id)

    mark_row = await pool.fetchrow(
        """
        SELECT marked_at
        FROM relationship.entity_view_marks
        WHERE entity_id = $1
        """,
        entity_id,
    )
    if mark_row is None:
        # First visit — nothing to diff against; the mark is created on the
        # subsequent POST /view-mark call.
        return DeltaFactsResponse(marked_at=None, items=[])

    marked_at: datetime = mark_row["marked_at"]

    identity_rows, narrative_rows = await asyncio.gather(
        pool.fetch(
            """
            SELECT
                f.id,
                f.subject,
                f.predicate,
                f.object,
                f.object_kind,
                f.src,
                f.conf,
                f.validity,
                f.created_at,
                GREATEST(f.created_at, f.updated_at) AS changed_at
            FROM relationship.entity_facts f
            WHERE f.subject = $1
              AND GREATEST(f.created_at, f.updated_at) > $2
            ORDER BY changed_at DESC, f.id DESC
            """,
            entity_id,
            marked_at,
        ),
        _fetch_narrative_delta_facts(pool, entity_id, marked_at),
    )

    items: list[DeltaFactEntry] = [
        DeltaFactEntry(
            id=r["id"],
            subject=r["subject"],
            predicate=r["predicate"],
            object=r["object"],
            object_kind=r["object_kind"],
            src=r["src"],
            conf=float(r["conf"]) if r["conf"] is not None else 1.0,
            store="identity",
            validity=r["validity"],
            created_at=r["created_at"],
            changed_at=r["changed_at"],
        )
        for r in identity_rows
    ]
    items.extend(
        DeltaFactEntry(
            id=r["id"],
            subject=r["subject"],
            predicate=r["predicate"],
            object=r["object"],
            object_kind=r["object_kind"],
            src=r["src"],
            conf=float(r["conf"]) if r["conf"] is not None else 1.0,
            store="narrative",
            validity=r["validity"],
            created_at=r["created_at"],
            changed_at=r["changed_at"],
        )
        for r in narrative_rows
    )

    return DeltaFactsResponse(marked_at=marked_at, items=items)


# ---------------------------------------------------------------------------
# Core dates block (entity v3 — "Core dates block", server half)
# ---------------------------------------------------------------------------


async def _fetch_date_kind_predicates(pool) -> list[str]:
    """Return the registry predicates eligible to carry a date-kind object.

    Date predicates are driven from ``relationship.entity_predicate_registry``
    (dashboard spec "Core dates block": "future date predicates from the
    registry") rather than a hardcoded list, so a new date contact predicate
    surfaces in the core-dates block the moment it is seeded — no code change.

    Eligibility is ``kind='contact' AND object_kind='literal'`` (the registry
    families whose objects are literal strings that *may* be ISO dates, e.g.
    ``has-birthday``). The caller still parses each row's object via
    ``_parse_date_object`` and skips non-dates, so a contact-literal predicate
    that never stores dates (``has-email``) contributes nothing — the registry
    filter only narrows the candidate set; the date semantics stay value-driven.
    """
    rows = await pool.fetch(
        """
        SELECT predicate
        FROM relationship.entity_predicate_registry
        WHERE kind = 'contact'
          AND object_kind = 'literal'
        """
    )
    return [r["predicate"] for r in rows]


def _parse_date_object(value: str) -> tuple[int, int, int | None] | None:
    """Parse a date-fact object into ``(month, day, year|None)``.

    Accepts a full ISO date (``YYYY-MM-DD``) or a year-less partial
    (``--MM-DD``, the RFC 6350 vCard ``BDAY`` partial form). Returns ``None`` for
    anything unparseable so the caller can skip the row rather than 500.
    """
    value = value.strip()
    try:
        if value.startswith("--"):
            # Partial date: --MM-DD (year unknown).
            parts = value[2:].split("-")
            month, day = int(parts[0]), int(parts[1])
            year: int | None = None
        else:
            parsed = date.fromisoformat(value)
            month, day, year = parsed.month, parsed.day, parsed.year
    except (ValueError, IndexError):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day, year


def _next_occurrence(month: int, day: int, today: date) -> date | None:
    """Return the next occurrence of (month, day) on or after ``today``.

    Returns ``None`` for an impossible (month, day) (e.g. Feb 30). Feb 29 rolls
    to the next leap-year occurrence.
    """
    for year in (today.year, today.year + 1, today.year + 2, today.year + 3, today.year + 4):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


@router.get("/entities/{entity_id}/core-dates", response_model=CoreDatesResponse)
async def get_entity_core_dates(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> CoreDatesResponse | JSONResponse:
    """Return the entity's date-kind facts with their next occurrence.

    Server-side extraction of date-kind predicates (``has-birthday`` and
    anniversary/future date predicates) from ``relationship.entity_facts`` —
    replaces client-side string-matching on the generic facts list (spec: "Core
    dates block", server half). Each row carries the next calendar occurrence of
    its (month, day), the integer ``days_until``, and provenance
    (``src``/``conf``/``verified``/``staleness_band``) per the rendering
    requirement. Items are ordered by ``days_until`` ascending (soonest first).

    Owner-only authz gate (Clause 12b): HTTP 403 ``{"code": "owner_required"}``
    if no owner entity is registered. Returns 404 if the entity does not exist.
    """
    from butlers.tools.relationship.staleness import identity_staleness_band_sql

    pool = _pool(db)

    if (err := await _assert_owner_role(pool)) is not None:
        return err
    await _assert_entity_exists(pool, entity_id)

    # Date predicates are registry-driven (dashboard spec "Core dates block":
    # "future date predicates from the registry") — not a hardcoded tuple. The
    # value-level _parse_date_object filter below skips contact-literal rows that
    # are not actually dates, so the registry filter only bounds the candidate set.
    date_predicates = await _fetch_date_kind_predicates(pool)
    if not date_predicates:
        return CoreDatesResponse(items=[])

    rows = await pool.fetch(
        f"""
        SELECT
            f.id,
            f.predicate,
            f.object,
            f.src,
            f.conf,
            f.verified,
            {identity_staleness_band_sql("f")} AS staleness_band
        FROM relationship.entity_facts f
        WHERE f.subject = $1
          AND f.validity = 'active'
          AND f.object_kind = 'literal'
          AND f.predicate = ANY($2::text[])
        """,
        entity_id,
        date_predicates,
    )

    today = date.today()
    items: list[CoreDateEntry] = []
    for r in rows:
        parsed = _parse_date_object(r["object"])
        if parsed is None:
            continue
        month, day, year = parsed
        occurrence = _next_occurrence(month, day, today)
        if occurrence is None:
            continue
        items.append(
            CoreDateEntry(
                id=r["id"],
                predicate=r["predicate"],
                value=r["object"],
                month=month,
                day=day,
                year=year,
                next_occurrence=occurrence,
                days_until=(occurrence - today).days,
                src=r["src"],
                conf=float(r["conf"]) if r["conf"] is not None else 1.0,
                verified=r["verified"],
                staleness_band=r["staleness_band"],
            )
        )

    items.sort(key=lambda e: e.days_until)
    return CoreDatesResponse(items=items)
