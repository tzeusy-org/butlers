"""Relationship/CRM endpoints.

Provides endpoints for contacts, groups, labels, notes, interactions,
gifts, loans, upcoming dates, and activity feeds. All data is queried
directly from the relationship butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
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
        ContactDetail = _models_module.ContactDetail
        ContactListResponse = _models_module.ContactListResponse
        ContactSummary = _models_module.ContactSummary
        ContactsSyncTriggerResponse = _models_module.ContactsSyncTriggerResponse
        Group = _models_module.Group
        GroupListResponse = _models_module.GroupListResponse
        Label = _models_module.Label
        UpcomingDate = _models_module.UpcomingDate
        ContactInfoEntry = _models_module.ContactInfoEntry
        ContactMergeRequest = _models_module.ContactMergeRequest
        ContactMergeResponse = _models_module.ContactMergeResponse
        ContactPatchRequest = _models_module.ContactPatchRequest
        CreateContactInfoRequest = _models_module.CreateContactInfoRequest
        CreateContactInfoResponse = _models_module.CreateContactInfoResponse
        PatchContactInfoRequest = _models_module.PatchContactInfoRequest
        OwnerSetupStatus = _models_module.OwnerSetupStatus
        OwnerEntityInfoResponse = _models_module.OwnerEntityInfoResponse
        EntitySuggestion = _models_module.EntitySuggestion
        UnlinkedContactSummary = _models_module.UnlinkedContactSummary
        UnlinkedContactsResponse = _models_module.UnlinkedContactsResponse
        LinkEntityRequest = _models_module.LinkEntityRequest
        LinkEntityResponse = _models_module.LinkEntityResponse
        CreateAndLinkEntityRequest = _models_module.CreateAndLinkEntityRequest
        CreateAndLinkEntityResponse = _models_module.CreateAndLinkEntityResponse
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
        ContactInteractionItem = _models_module.ContactInteractionItem
        ContactInteractionThreadResponse = _models_module.ContactInteractionThreadResponse
        OverdueContactItem = _models_module.OverdueContactItem
        OverdueContactsResponse = _models_module.OverdueContactsResponse
        EntitySummary = _models_module.EntitySummary
        EntityListResponse = _models_module.EntityListResponse
        NeighbourEntry = _models_module.NeighbourEntry
        NeighboursResponse = _models_module.NeighboursResponse
        SearchResultEntry = _models_module.SearchResultEntry
        SearchResponse = _models_module.SearchResponse
        QueueEntry = _models_module.QueueEntry
        QueueResponse = _models_module.QueueResponse

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
# GET /contacts — list with search and label filter
# ---------------------------------------------------------------------------


@router.get("/contacts", response_model=ContactListResponse)
async def list_contacts(
    q: str | None = Query(None, description="Search contacts by name"),
    label: str | None = Query(None, description="Filter by label name"),
    archived: bool = Query(False, description="Include only archived contacts"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactListResponse:
    """List contacts with optional search and label filter, paginated."""
    pool = _pool(db)

    conditions: list[str] = ["c.archived_at IS NOT NULL" if archived else "c.archived_at IS NULL"]
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(f"c.name ILIKE '%' || ${idx} || '%'")
        args.append(q)
        idx += 1

    joins = ""
    if label is not None:
        joins = (
            " JOIN contact_labels cl_f ON cl_f.contact_id = c.id"
            " JOIN labels lf ON lf.id = cl_f.label_id"
        )
        conditions.append(f"lf.name = ${idx}")
        args.append(label)
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

    # Count and data queries run in parallel — they share the same WHERE but
    # are otherwise independent.
    count_sql = f"SELECT count(DISTINCT c.id) FROM contacts c{joins}{where}"

    data_sql = f"""
        SELECT c.id, c.name AS full_name, c.first_name, c.last_name, c.nickname
        FROM contacts c{joins}{where}
        GROUP BY c.id
        ORDER BY c.name
        OFFSET ${idx} LIMIT ${idx + 1}
    """
    data_args = [*args, offset, limit]

    total_raw, rows = await asyncio.gather(
        pool.fetchval(count_sql, *args),
        pool.fetch(data_sql, *data_args),
    )
    total = total_raw or 0

    # Batch-fetch all supplementary data for the returned page in parallel,
    # replacing the old per-row correlated subqueries.
    contact_ids = [row["id"] for row in rows]
    labels_by_contact: dict[UUID, list[Label]] = {cid: [] for cid in contact_ids}
    email_by_contact: dict[UUID, str | None] = {cid: None for cid in contact_ids}
    phone_by_contact: dict[UUID, str | None] = {cid: None for cid in contact_ids}
    last_interaction_by_contact: dict[UUID, Any] = {cid: None for cid in contact_ids}

    if contact_ids:
        label_rows, ci_rows, interaction_rows = await asyncio.gather(
            pool.fetch(
                """
                SELECT cl.contact_id, l.id, l.name, l.color
                FROM contact_labels cl
                JOIN labels l ON l.id = cl.label_id
                WHERE cl.contact_id = ANY($1)
                ORDER BY l.name
                """,
                contact_ids,
            ),
            pool.fetch(
                """
                SELECT DISTINCT ON (ci.contact_id, ci.type)
                    ci.contact_id, ci.type, ci.value
                FROM public.contact_info ci
                WHERE ci.contact_id = ANY($1)
                  AND ci.type IN ('email', 'phone')
                ORDER BY ci.contact_id, ci.type,
                         ci.is_primary DESC NULLS LAST, ci.id
                """,
                contact_ids,
            ),
            pool.fetch(
                """
                SELECT c.id AS contact_id, MAX(f.valid_at) AS last_at
                FROM contacts c
                JOIN facts f ON f.entity_id = c.entity_id
                WHERE c.id = ANY($1)
                  AND f.predicate LIKE 'interaction_%'
                  AND f.validity = 'active'
                  AND f.scope = 'relationship'
                GROUP BY c.id
                """,
                contact_ids,
            ),
        )
        for lr in label_rows:
            labels_by_contact[lr["contact_id"]].append(
                Label(id=lr["id"], name=lr["name"], color=lr["color"])
            )
        for ci in ci_rows:
            if ci["type"] == "email":
                email_by_contact[ci["contact_id"]] = ci["value"]
            else:
                phone_by_contact[ci["contact_id"]] = ci["value"]
        for ir in interaction_rows:
            last_interaction_by_contact[ir["contact_id"]] = ir["last_at"]

    contacts = [
        ContactSummary(
            id=row["id"],
            full_name=row["full_name"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            nickname=row["nickname"],
            email=email_by_contact.get(row["id"]),
            phone=phone_by_contact.get(row["id"]),
            labels=labels_by_contact.get(row["id"], []),
            last_interaction_at=last_interaction_by_contact.get(row["id"]),
        )
        for row in rows
    ]

    return ContactListResponse(contacts=contacts, total=total)


# ---------------------------------------------------------------------------
# POST /contacts/sync — manual contacts sync trigger
# ---------------------------------------------------------------------------


_SUPPORTED_SYNC_PROVIDERS = {"google", "telegram"}


@router.post("/contacts/sync", response_model=ContactsSyncTriggerResponse)
async def trigger_contacts_sync(
    provider: str = Query(
        "google",
        description="Provider to sync: 'google' or 'telegram'",
    ),
    mode: Literal["incremental", "full"] = Query(
        "incremental",
        description="Sync mode: incremental for routine refresh, full for backfill",
    ),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
) -> ContactsSyncTriggerResponse:
    """Trigger contacts sync via the relationship butler's MCP tool."""
    if provider not in _SUPPORTED_SYNC_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported provider '{provider}'. Must be one of: {sorted(_SUPPORTED_SYNC_PROVIDERS)}",  # noqa: E501
        )

    if not any(cfg.name == BUTLER_DB for cfg in configs):
        raise HTTPException(status_code=404, detail="Relationship butler is not configured")

    try:
        client = await asyncio.wait_for(
            mcp_manager.get_client(BUTLER_DB),
            timeout=_CONTACTS_SYNC_TIMEOUT_S,
        )
        result = await asyncio.wait_for(
            client.call_tool(
                "contacts_sync_now",
                {"provider": provider, "mode": mode},
            ),
            timeout=_CONTACTS_SYNC_TIMEOUT_S,
        )
    except ButlerUnreachableError:
        raise HTTPException(
            status_code=503,
            detail="Relationship butler is unreachable; contacts sync cannot start",
        )
    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="Contacts sync request timed out before completion",
        )
    except Exception as exc:
        logger.warning("Unexpected contacts sync trigger failure", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to start contacts sync: {exc}")

    raw_text = _extract_mcp_result_text(result)
    payload = _parse_mcp_result_payload(raw_text)
    is_error = bool(getattr(result, "is_error", False))

    # The MCP tool may return a normal (non-error) result that contains an
    # "error" key when the sync engine catches an exception internally.
    # Detect this so it isn't silently swallowed as a success.
    if not is_error and isinstance(payload, dict) and "error" in payload:
        is_error = True

    if is_error:
        if provider == "google" and _is_credential_error(payload, raw_text):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Google credentials are missing or invalid. "
                    "Complete OAuth setup at /api/oauth/google/start "
                    "or update credentials at /api/oauth/google/credentials."
                ),
            )

        detail = raw_text or "Contacts sync failed"
        if isinstance(payload, dict):
            for key in ("detail", "error", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    detail = value
                    break
        raise HTTPException(status_code=502, detail=f"Contacts sync failed: {detail}")

    summary = _extract_sync_summary(payload)
    fetched = _coerce_count(summary.get("fetched")) if isinstance(summary, dict) else None
    applied = _coerce_count(summary.get("applied")) if isinstance(summary, dict) else None
    skipped = _coerce_count(summary.get("skipped")) if isinstance(summary, dict) else None
    deleted = _coerce_count(summary.get("deleted")) if isinstance(summary, dict) else None
    provider_total = (
        _coerce_count(summary.get("provider_total")) if isinstance(summary, dict) else None
    )
    message = summary.get("message") if isinstance(summary, dict) else None
    if not isinstance(message, str):
        message = None

    return ContactsSyncTriggerResponse(
        provider=provider,
        mode=mode,
        fetched=fetched,
        applied=applied,
        skipped=skipped,
        deleted=deleted,
        provider_total=provider_total,
        summary=summary if isinstance(summary, dict) else {},
        message=message,
    )


# ---------------------------------------------------------------------------
# GET /contacts/pending
# ---------------------------------------------------------------------------


@router.get("/contacts/pending", response_model=list[ContactDetail])
async def list_pending_contacts(
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[ContactDetail]:
    """List contacts with metadata.needs_disambiguation=true.

    Returns temp contacts created during identity resolution that require
    the owner's attention to either confirm as known contacts or merge into
    an existing contact.
    """
    pool = _pool(db)

    rows = await pool.fetch(
        """
        SELECT
            c.id,
            c.name AS full_name,
            c.first_name,
            c.last_name,
            c.nickname,
            c.details->>'notes' AS notes,
            c.company,
            c.job_title,
            c.metadata,
            c.created_at,
            c.updated_at,
            COALESCE(e.roles, '{}') AS roles,
            c.entity_id
        FROM contacts c
        LEFT JOIN public.entities e ON e.id = c.entity_id
        WHERE c.archived_at IS NULL
          AND (c.metadata->>'needs_disambiguation')::boolean = true
        ORDER BY c.created_at DESC
        """,
    )

    result: list[ContactDetail] = []
    for row in rows:
        cid = row["id"]

        ci_rows = await pool.fetch(
            """
            SELECT id, type, value, is_primary, secured, parent_id, context
            FROM public.contact_info
            WHERE contact_id = $1
            ORDER BY is_primary DESC NULLS LAST, type, id
            """,
            cid,
        )
        contact_info_entries = [
            ContactInfoEntry(
                id=ci["id"],
                type=ci["type"],
                value=None if ci["secured"] else ci["value"],
                is_primary=bool(ci["is_primary"]),
                secured=bool(ci["secured"]),
                parent_id=ci["parent_id"],
                context=ci["context"],
            )
            for ci in ci_rows
        ]

        _raw_meta = row["metadata"]
        # JSONB codec contract: asyncpg decodes JSONB to dict; guard is defensive only.
        metadata = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}
        raw_roles = row["roles"]
        roles = list(raw_roles) if raw_roles else []

        result.append(
            ContactDetail(
                id=cid,
                full_name=row["full_name"],
                first_name=row["first_name"],
                last_name=row["last_name"],
                nickname=row["nickname"],
                email=next((ci.value for ci in contact_info_entries if ci.type == "email"), None),
                phone=next((ci.value for ci in contact_info_entries if ci.type == "phone"), None),
                labels=[],
                last_interaction_at=None,
                notes=row["notes"],
                birthday=None,
                company=row["company"],
                job_title=row["job_title"],
                address=None,
                metadata=metadata,
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                roles=roles,
                entity_id=row["entity_id"],
                contact_info=contact_info_entries,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Helpers: memory pool discovery & entity suggestion scoring
# ---------------------------------------------------------------------------


async def _get_memory_pool(db: DatabaseManager):
    """Find the first butler pool that has an ``entities`` table.

    Returns the asyncpg Pool or None if no memory-capable butler is available.
    """
    for butler_name in db.butler_names:
        try:
            candidate_pool = db.pool(butler_name)
            has_entities = await candidate_pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'entities')"
            )
            if has_entities:
                return candidate_pool
        except KeyError:
            continue
    return None


async def _suggest_entities(
    rel_pool,
    memory_pool,
    contact_row: dict,
    *,
    search_override: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Score and rank entity suggestions for an unlinked contact.

    Three scoring layers:
    1. Name match via entity_resolve (exact=100|80 (fact-count promoted), prefix=50, fuzzy=20)
    2. Contact info email/phone match against entity aliases/metadata (email=70, phone=50)
    3. Company/org match via entity_resolve with 0.3x multiplier

    Returns a list of dicts ready for EntitySuggestion construction.
    """
    from butlers.modules.memory.tools.entities import entity_resolve

    candidates: dict[str, dict] = {}  # entity_id -> best candidate dict

    def _merge(entity_id: str, candidate: dict):
        if entity_id in candidates:
            if candidate["score"] > candidates[entity_id]["score"]:
                candidates[entity_id] = candidate
        else:
            candidates[entity_id] = candidate

    name = search_override or contact_row.get("full_name") or ""
    if name.strip():
        results = await entity_resolve(
            memory_pool,
            name,
            entity_type="person",
            enable_fuzzy=True,
        )
        for r in results:
            _merge(r["entity_id"], r)

    # Layer 2: contact info matching
    contact_id = contact_row.get("id")
    if contact_id is not None:
        info_rows = await rel_pool.fetch(
            "SELECT type, value FROM public.contact_info WHERE contact_id = $1",
            contact_id,
        )
        for info in info_rows:
            ci_type = info["type"]
            ci_value = info["value"]
            if not ci_value:
                continue
            if ci_type in ("email", "phone"):
                try:
                    matches = await entity_resolve(
                        memory_pool,
                        ci_value,
                        enable_fuzzy=False,
                    )
                except Exception:  # noqa: BLE001
                    matches = []
                score = 70.0 if ci_type == "email" else 50.0
                for m in matches:
                    _merge(
                        m["entity_id"],
                        {**m, "score": score},
                    )

    # Layer 3: company/org match
    company = contact_row.get("company")
    if company and company.strip():
        try:
            org_results = await entity_resolve(
                memory_pool,
                company,
                entity_type="organization",
                enable_fuzzy=False,
            )
        except Exception:  # noqa: BLE001
            org_results = []
        for r in org_results:
            _merge(
                r["entity_id"],
                {**r, "score": r["score"] * 0.3},
            )

    sorted_candidates = sorted(candidates.values(), key=lambda c: -c["score"])
    return sorted_candidates[:limit]


# ---------------------------------------------------------------------------
# GET /contacts/unlinked — paginated unlinked contacts with suggestions
# ---------------------------------------------------------------------------


@router.get("/contacts/unlinked", response_model=UnlinkedContactsResponse)
async def list_unlinked_contacts(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, min_length=1, max_length=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> UnlinkedContactsResponse:
    """Return contacts that have no entity_id, excluding pending/archived ones."""
    pool = _pool(db)

    name_filter = ""
    params_count: list[Any] = []
    if q:
        name_filter = " AND c.name ILIKE $1"
        params_count = [f"%{q}%"]

    total = (
        await pool.fetchval(
            f"""
        SELECT count(*)
        FROM contacts c
        WHERE c.entity_id IS NULL
          AND c.archived_at IS NULL
          AND (c.metadata->>'needs_disambiguation')::boolean IS NOT TRUE
          {name_filter}
        """,
            *params_count,
        )
        or 0
    )

    # Build positional params: optional $1 for q, then offset/limit
    params_rows: list[Any] = []
    if q:
        params_rows.append(f"%{q}%")
    offset_idx = len(params_rows) + 1
    limit_idx = offset_idx + 1
    params_rows.extend([offset, limit])

    rows = await pool.fetch(
        f"""
        SELECT c.id, c.name AS full_name, c.first_name, c.last_name, c.company,
               (SELECT ci.value FROM public.contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'email'
                  AND ci.is_primary = true LIMIT 1) AS email,
               (SELECT ci.value FROM public.contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'phone'
                  AND ci.is_primary = true LIMIT 1) AS phone
        FROM contacts c
        WHERE c.entity_id IS NULL
          AND c.archived_at IS NULL
          AND (c.metadata->>'needs_disambiguation')::boolean IS NOT TRUE
          {name_filter}
        ORDER BY c.name
        OFFSET ${offset_idx} LIMIT ${limit_idx}
        """,
        *params_rows,
    )

    memory_pool = await _get_memory_pool(db)

    contacts = []
    for r in rows:
        suggestions = []
        if memory_pool is not None:
            try:
                raw = await _suggest_entities(pool, memory_pool, dict(r))
                suggestions = [
                    EntitySuggestion(
                        entity_id=s["entity_id"],
                        canonical_name=s["canonical_name"],
                        entity_type=s["entity_type"],
                        score=s["score"],
                        name_match=s.get("name_match", "unknown"),
                        aliases=s.get("aliases", []),
                    )
                    for s in raw
                ]
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to compute suggestions for contact %s", r["id"], exc_info=True
                )

        contacts.append(
            UnlinkedContactSummary(
                id=r["id"],
                full_name=r["full_name"],
                first_name=r["first_name"],
                last_name=r["last_name"],
                email=r["email"],
                phone=r["phone"],
                company=r["company"],
                suggestions=suggestions,
            )
        )

    return UnlinkedContactsResponse(contacts=contacts, total=total)


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/entity-suggestions
# ---------------------------------------------------------------------------


@router.get(
    "/contacts/{contact_id}/entity-suggestions",
    response_model=list[EntitySuggestion],
)
async def get_entity_suggestions(
    contact_id: UUID,
    q: str | None = Query(None, description="Override search term"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[EntitySuggestion]:
    """On-demand entity suggestions for a specific contact, optionally with search override."""
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id, name AS full_name, first_name, last_name, company FROM contacts WHERE id = $1",
        contact_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    memory_pool = await _get_memory_pool(db)
    if memory_pool is None:
        return []

    raw = await _suggest_entities(pool, memory_pool, dict(row), search_override=q)
    return [
        EntitySuggestion(
            entity_id=s["entity_id"],
            canonical_name=s["canonical_name"],
            entity_type=s["entity_type"],
            score=s["score"],
            name_match=s.get("name_match", "unknown"),
            aliases=s.get("aliases", []),
        )
        for s in raw
    ]


# ---------------------------------------------------------------------------
# POST /contacts/{contact_id}/link-entity
# ---------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/link-entity", response_model=LinkEntityResponse)
async def link_entity(
    contact_id: UUID,
    request: LinkEntityRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> LinkEntityResponse:
    """Set entity_id on a contact, linking it to a memory entity."""
    pool = _pool(db)

    contact = await pool.fetchrow(
        "SELECT id FROM contacts WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Validate entity exists
    memory_pool = await _get_memory_pool(db)
    if memory_pool is None:
        raise HTTPException(status_code=503, detail="Memory module not available")

    from butlers.modules.memory.tools.entities import entity_get

    entity = await entity_get(memory_pool, str(request.entity_id))
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    await pool.execute(
        "UPDATE contacts SET entity_id = $1, updated_at = now() WHERE id = $2",
        request.entity_id,
        contact_id,
    )

    return LinkEntityResponse(contact_id=contact_id, entity_id=request.entity_id)


# ---------------------------------------------------------------------------
# POST /contacts/{contact_id}/create-entity
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/create-entity",
    response_model=CreateAndLinkEntityResponse,
    status_code=201,
)
async def create_and_link_entity(
    contact_id: UUID,
    request: CreateAndLinkEntityRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> CreateAndLinkEntityResponse:
    """Create a new memory entity from contact data and link it."""
    pool = _pool(db)

    contact = await pool.fetchrow(
        "SELECT id, name AS full_name, first_name, nickname, company FROM contacts "
        "WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    memory_pool = await _get_memory_pool(db)
    if memory_pool is None:
        raise HTTPException(status_code=503, detail="Memory module not available")

    from butlers.modules.memory.tools.entities import entity_create

    canonical_name = request.canonical_name or contact["full_name"]

    # Auto-generate aliases from first_name and nickname
    aliases = list(request.aliases) if request.aliases else []
    if not aliases:
        if contact["first_name"] and contact["first_name"] != canonical_name:
            aliases.append(contact["first_name"])
        if contact["nickname"] and contact["nickname"] not in aliases:
            aliases.append(contact["nickname"])

    meta = dict(request.metadata) if request.metadata else {}
    if contact["company"] and "company" not in meta:
        meta["company"] = contact["company"]

    try:
        result = await entity_create(
            memory_pool,
            canonical_name,
            request.entity_type,
            aliases=aliases or None,
            metadata=meta or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    import uuid as _uuid

    entity_id = _uuid.UUID(result["entity_id"])

    await pool.execute(
        "UPDATE contacts SET entity_id = $1, updated_at = now() WHERE id = $2",
        entity_id,
        contact_id,
    )

    return CreateAndLinkEntityResponse(
        contact_id=contact_id,
        entity_id=entity_id,
        canonical_name=canonical_name,
    )


# ---------------------------------------------------------------------------
# GET /contacts/overdue — contacts overdue by days threshold or tier cadence
# ---------------------------------------------------------------------------


@router.get("/contacts/overdue", response_model=OverdueContactsResponse)
async def list_overdue_contacts(
    days: int = Query(14, ge=1, le=3650, description="Fallback threshold in days"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> OverdueContactsResponse:
    """Return contacts whose last contact is overdue.

    A contact is overdue when ``days_since_last_contact`` exceeds either the
    ``days`` query parameter **or** their Dunbar tier's target cadence —
    whichever threshold is *shorter*.  This means a tier-5 contact with a
    14-day cadence is flagged even if ``days=30``.

    Response fields:
    - ``tier`` — human-readable tier label (e.g. ``'tier-5'``).
    - ``owed_days`` — how many days past the effective threshold.
    - ``last_contact_date`` — date of most recent interaction, or null.
    - ``target_cadence_days`` — the effective cadence used (min of ``days``
      and the tier cadence).

    Tier 1500 contacts (outermost acquaintances) with no explicit
    ``stay_in_touch_days`` override are excluded — they have no cadence target.
    """
    from butlers.tools.relationship import dunbar as _dunbar

    pool = _pool(db)

    now = datetime.now(UTC)

    # Fetch all listed contacts (metadata only — no facts join needed; last_interaction_at
    # comes from the Dunbar ranking result below for entity-linked contacts).
    contact_rows = await pool.fetch(
        """
        SELECT
            c.id,
            c.name AS full_name,
            c.entity_id,
            c.stay_in_touch_days
        FROM contacts c
        WHERE c.listed = true
          AND c.archived_at IS NULL
        """
    )

    if not contact_rows:
        return OverdueContactsResponse(contacts=[])

    # Compute tier ranking for all contacts in one pass via the public wrapper.
    ranked = await _dunbar.compute_tier_ranking(pool)
    dunbar_by_cid: dict[UUID, dict[str, Any]] = {entry["contact_id"]: entry for entry in ranked}

    results: list[OverdueContactItem] = []
    for row in contact_rows:
        cid = row["id"]
        full_name = row["full_name"] or ""
        stay_in_touch = row["stay_in_touch_days"]

        dunbar_info = dunbar_by_cid.get(cid, {"dunbar_tier": 1500})
        dunbar_tier = dunbar_info["dunbar_tier"]

        # Determine effective cadence from explicit override or tier default.
        if stay_in_touch is not None:
            tier_cadence = int(stay_in_touch)
        else:
            tier_cadence = _dunbar.TIER_CADENCE.get(dunbar_tier)

        if tier_cadence is None:
            # Tier 1500 with no stay_in_touch_days — no cadence target, skip.
            continue

        # Effective threshold is the shorter of the two (tier cadence vs. query param).
        effective_threshold = min(days, tier_cadence)

        # last_interaction_at comes from the Dunbar engine for entity-linked contacts;
        # contacts with no entity link have no canonical interaction facts.
        last_at = dunbar_info.get("last_interaction_at")

        if last_at is None:
            days_since: float = float("inf")
            last_contact_date = None
        else:
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=UTC)
            days_since = max((now - last_at).total_seconds() / 86400.0, 0.0)
            last_contact_date = last_at.date()

        if days_since < effective_threshold:
            continue

        if days_since == float("inf"):
            # Never contacted — sort above all contacts with a measured overdue period.
            owed_days = 365 * 100
        else:
            owed_days = max(1, int(days_since - effective_threshold))

        results.append(
            OverdueContactItem(
                contact_id=cid,
                name=full_name,
                tier=f"tier-{dunbar_tier}",
                owed_days=owed_days,
                last_contact_date=last_contact_date,
                target_cadence_days=effective_threshold,
            )
        )

    # Sort by owed_days descending (most overdue first)
    results.sort(key=lambda r: r.owed_days, reverse=True)
    return OverdueContactsResponse(contacts=results)


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/interactions — chronological interaction thread
# ---------------------------------------------------------------------------


@router.get(
    "/contacts/{contact_id}/interactions",
    response_model=ContactInteractionThreadResponse,
)
async def list_contact_interactions(
    contact_id: UUID,
    limit: int = Query(20, ge=1, le=100, description="Max interactions to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactInteractionThreadResponse:
    """Return a chronological list of interactions for a contact.

    Source: ``facts`` table with ``predicate LIKE 'interaction_%'`` joined via
    the contact's ``entity_id``.  Results are ordered chronologically
    (oldest first) so the caller sees the conversation thread in natural order.

    Direction values:
    - ``'in'``      — contact-to-owner (incoming)
    - ``'out'``     — owner-to-contact (outgoing)
    - ``'drafted'`` — drafted but unsent

    Returns 404 if the contact does not exist.
    Returns an empty interactions list if the contact has no recorded interactions.
    """
    pool = _pool(db)

    # Resolve contact → entity_id
    contact_row = await pool.fetchrow(
        "SELECT id, entity_id FROM contacts WHERE id = $1",
        contact_id,
    )
    if contact_row is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    entity_id = contact_row["entity_id"]

    if entity_id is None:
        # Contact exists but has no entity link — no facts to return
        return ContactInteractionThreadResponse(interactions=[])

    # Fetch interaction facts ordered chronologically (oldest first)
    rows = await pool.fetch(
        """
        SELECT id, content, metadata, valid_at
        FROM facts
        WHERE entity_id = $1
          AND predicate LIKE 'interaction_%'
          AND validity = 'active'
          AND scope = 'relationship'
        ORDER BY valid_at ASC NULLS LAST, created_at ASC
        LIMIT $2
        """,
        entity_id,
        limit,
    )

    _VALID_DIRECTIONS = frozenset({"in", "out", "drafted"})
    # Map legacy interaction_log() direction values to the API discriminator.
    _DIRECTION_MAP: dict[str, str] = {
        "incoming": "in",
        "outgoing": "out",
        # "mutual" has no clean in/out mapping; render as null.
    }

    def _direction(meta: dict | None) -> str | None:
        if not meta:
            return None
        raw = meta.get("direction")
        if raw in _VALID_DIRECTIONS:
            return raw
        return _DIRECTION_MAP.get(raw)

    items = [
        ContactInteractionItem(
            ts=row["valid_at"],
            direction=_direction(row["metadata"]),
            text=row["content"] or "",
        )
        for row in rows
    ]
    return ContactInteractionThreadResponse(interactions=items)


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id} — full detail
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}", response_model=ContactDetail)
async def get_contact(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactDetail:
    """Get full contact detail with labels, email, phone, birthday, roles, entity_id.

    Secured contact_info values are masked (value=None) in the response.
    Use GET /contacts/{id}/secrets/{info_id} to reveal a secured value.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT
            c.id,
            c.name AS full_name,
            c.first_name,
            c.last_name,
            c.nickname,
            c.details->>'notes' AS notes,
            c.company,
            c.job_title,
            c.metadata,
            c.created_at,
            c.updated_at,
            COALESCE(e.roles, '{}') AS roles,
            c.entity_id,
            c.preferred_channel,
            (
                SELECT ci.value FROM public.contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'email'
                ORDER BY ci.is_primary DESC NULLS LAST, ci.id
                LIMIT 1
            ) AS email,
            (
                SELECT ci.value FROM public.contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'phone'
                ORDER BY ci.is_primary DESC NULLS LAST, ci.id
                LIMIT 1
            ) AS phone,
            (
                SELECT MAX(f.valid_at) FROM facts f
                WHERE f.entity_id = c.entity_id
                  AND f.predicate LIKE 'interaction_%'
                  AND f.validity = 'active'
                  AND f.scope = 'relationship'
            ) AS last_interaction_at
        FROM contacts c
        LEFT JOIN public.entities e ON e.id = c.entity_id
        WHERE c.id = $1 AND c.archived_at IS NULL
        """,
        contact_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Run independent detail queries concurrently
    label_rows, birthday_row, addr_row, ci_rows = await asyncio.gather(
        pool.fetch(
            """
            SELECT l.id, l.name, l.color
            FROM contact_labels cl
            JOIN labels l ON l.id = cl.label_id
            WHERE cl.contact_id = $1
            ORDER BY l.name
            """,
            contact_id,
        ),
        pool.fetchrow(
            """
            SELECT month, day, year
            FROM important_dates
            WHERE contact_id = $1 AND label = 'birthday'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            contact_id,
        ),
        pool.fetchrow(
            """
            SELECT line_1, line_2, city, province, postal_code, country
            FROM addresses
            WHERE contact_id = $1
            ORDER BY is_current DESC NULLS LAST, id
            LIMIT 1
            """,
            contact_id,
        ),
        pool.fetch(
            """
            SELECT id, type, value, is_primary, secured, parent_id, context
            FROM public.contact_info
            WHERE contact_id = $1
            ORDER BY is_primary DESC NULLS LAST, type, id
            """,
            contact_id,
        ),
    )

    labels = [Label(id=lr["id"], name=lr["name"], color=lr["color"]) for lr in label_rows]

    birthday: date | None = None
    if birthday_row is not None:
        year = birthday_row["year"] or 1900
        birthday = date(year, birthday_row["month"], birthday_row["day"])

    address: str | None = None
    if addr_row is not None:
        parts = [
            addr_row["line_1"],
            addr_row["line_2"],
            addr_row["city"],
            addr_row["province"],
            addr_row["postal_code"],
            addr_row["country"],
        ]
        address = ", ".join(p for p in parts if p)
    contact_info_entries = [
        ContactInfoEntry(
            id=ci["id"],
            type=ci["type"],
            value=None if ci["secured"] else ci["value"],
            is_primary=bool(ci["is_primary"]),
            secured=bool(ci["secured"]),
            parent_id=ci["parent_id"],
            context=ci["context"],
        )
        for ci in ci_rows
    ]

    _raw_meta = row["metadata"]
    # JSONB codec contract: asyncpg decodes JSONB to dict; guard is defensive only.
    metadata = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}

    raw_roles = row["roles"]
    roles = list(raw_roles) if raw_roles else []

    return ContactDetail(
        id=row["id"],
        full_name=row["full_name"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        nickname=row["nickname"],
        email=row["email"],
        phone=row["phone"],
        labels=labels,
        last_interaction_at=row["last_interaction_at"],
        notes=row["notes"],
        birthday=birthday,
        company=row["company"],
        job_title=row["job_title"],
        address=address,
        metadata=metadata,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        roles=roles,
        entity_id=row["entity_id"],
        contact_info=contact_info_entries,
        preferred_channel=row["preferred_channel"],
    )


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/secrets/{info_id}
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/secrets/{info_id}")
async def reveal_contact_secret(
    contact_id: UUID,
    info_id: UUID,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> dict[str, Any]:
    """Reveal the actual value of a secured contact_info entry.

    Returns the real value for a secured contact_info row.  Returns 404 if
    the info_id does not exist OR does not belong to the given contact_id —
    preventing enumeration of secured values across contacts.

    The response is intentionally minimal: ``{"id": ..., "type": ..., "value": ...}``.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT id, type, value, secured
        FROM public.contact_info
        WHERE id = $1 AND contact_id = $2
        """,
        info_id,
        contact_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Contact info entry not found")

    if not row["secured"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "This contact_info entry is not secured; "
                "value is available in the contact detail response."
            ),
        )

    # Explicit audit for credential reveal (GET — middleware skips GETs).
    await emit_dashboard_audit(
        db,
        butler="relationship",
        operation="contact_secret_reveal",
        method="GET",
        path=f"/api/relationship/contacts/{contact_id}/secrets/{info_id}",
        path_params={"contact_id": str(contact_id), "info_id": str(info_id)},
        body={"type": row["type"]},
        response_status=200,
        request=request,
    )

    return {"id": str(row["id"]), "type": row["type"], "value": row["value"]}


# ---------------------------------------------------------------------------
# PATCH /contacts/{contact_id}
# ---------------------------------------------------------------------------


@router.patch("/contacts/{contact_id}", response_model=ContactDetail)
async def patch_contact(
    contact_id: UUID,
    request: ContactPatchRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactDetail:
    """Partially update a contact.

    Supported fields: full_name, nickname, company, job_title, roles.
    This is the sole write path for role assignment.  Only provided
    (non-None) fields are updated.
    """
    pool = _pool(db)

    # Verify contact exists
    existing = await pool.fetchrow(
        "SELECT id FROM contacts WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Build UPDATE from provided fields
    updates: list[str] = []
    args: list[Any] = []
    idx = 1

    if request.first_name is not None:
        updates.append(f"first_name = ${idx}")
        args.append(request.first_name)
        idx += 1

    if request.last_name is not None:
        updates.append(f"last_name = ${idx}")
        args.append(request.last_name)
        idx += 1

    # Recompose the denormalized `name` column from first/last if either changed,
    # or use full_name directly if provided (backward compat).
    if request.full_name is not None:
        updates.append(f"name = ${idx}")
        args.append(request.full_name)
        idx += 1
    elif request.first_name is not None or request.last_name is not None:
        # Fetch current values to compose the full name
        cur = await pool.fetchrow(
            "SELECT first_name, last_name FROM contacts WHERE id = $1",
            contact_id,
        )
        first = request.first_name if request.first_name is not None else (cur["first_name"] or "")
        last = request.last_name if request.last_name is not None else (cur["last_name"] or "")
        composed = " ".join(p for p in [first, last] if p).strip() or "Unknown"
        updates.append(f"name = ${idx}")
        args.append(composed)
        idx += 1

    if request.nickname is not None:
        updates.append(f"nickname = ${idx}")
        args.append(request.nickname)
        idx += 1

    if request.company is not None:
        updates.append(f"company = ${idx}")
        args.append(request.company)
        idx += 1

    if request.job_title is not None:
        updates.append(f"job_title = ${idx}")
        args.append(request.job_title)
        idx += 1

    # Roles are updated on the entity, not the contact.
    # Handled separately below after the contact UPDATE.

    if request.preferred_channel is not None:
        # Empty string clears the preference
        val = request.preferred_channel if request.preferred_channel else None
        updates.append(f"preferred_channel = ${idx}")
        args.append(val)
        idx += 1

    if updates:
        updates.append("updated_at = now()")
        set_clause = ", ".join(updates)
        args.append(contact_id)
        await pool.execute(
            f"UPDATE contacts SET {set_clause} WHERE id = ${idx}",
            *args,
        )

    # Update roles on the linked entity (if roles provided and entity linked)
    if request.roles is not None:
        entity_row = await pool.fetchrow(
            "SELECT entity_id FROM contacts WHERE id = $1",
            contact_id,
        )
        if entity_row and entity_row["entity_id"] is not None:
            await pool.execute(
                "UPDATE public.entities SET roles = $1, updated_at = now() WHERE id = $2",
                request.roles,
                entity_row["entity_id"],
            )

    # Return updated contact detail
    return await get_contact(contact_id=contact_id, db=db)


# ---------------------------------------------------------------------------
# DELETE /contacts/{contact_id}
# ---------------------------------------------------------------------------


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Hard-delete a contact and all its associated contact_info.

    CASCADE on public.contact_info FK handles info cleanup.
    Source links in contacts_source_links are also removed so a
    future sync can re-create the contact from scratch if needed.
    """
    pool = _pool(db)

    existing = await pool.fetchrow(
        "SELECT id FROM contacts WHERE id = $1",
        contact_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Remove source links so re-sync can recreate cleanly
    has_source_links = await pool.fetchval(
        "SELECT to_regclass('contacts_source_links') IS NOT NULL"
    )
    if has_source_links:
        await pool.execute(
            "DELETE FROM contacts_source_links WHERE local_contact_id = $1",
            contact_id,
        )

    await pool.execute("DELETE FROM contacts WHERE id = $1", contact_id)


# ---------------------------------------------------------------------------
# POST /contacts/{contact_id}/archive
# ---------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/archive", status_code=204)
async def archive_contact(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Soft-archive a contact.

    Sets archived_at to now(). Source links are preserved so that future
    syncs recognise the contact and skip re-creation.
    """
    pool = _pool(db)

    existing = await pool.fetchrow(
        "SELECT id FROM contacts WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    await pool.execute(
        "UPDATE contacts SET archived_at = now(), updated_at = now() WHERE id = $1",
        contact_id,
    )


# ---------------------------------------------------------------------------
# POST /contacts/{contact_id}/unarchive
# ---------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/unarchive", status_code=204)
async def unarchive_contact(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Restore an archived contact."""
    pool = _pool(db)

    existing = await pool.fetchrow(
        "SELECT id FROM contacts WHERE id = $1 AND archived_at IS NOT NULL",
        contact_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Archived contact not found")

    await pool.execute(
        "UPDATE contacts SET archived_at = NULL, updated_at = now() WHERE id = $1",
        contact_id,
    )


# ---------------------------------------------------------------------------
# POST /contacts/{contact_id}/confirm
# ---------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/confirm", response_model=ContactDetail)
async def confirm_contact(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactDetail:
    """Confirm a pending disambiguation contact.

    Removes ``needs_disambiguation`` from the contact's metadata, marking
    the contact as confirmed by the owner.  Returns the updated contact.
    """
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id, metadata FROM contacts WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    _raw_meta = row["metadata"]
    # JSONB codec contract: asyncpg decodes JSONB to dict; guard is defensive only.
    metadata = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}
    metadata.pop("needs_disambiguation", None)

    await pool.execute(
        "UPDATE contacts SET metadata = $1, updated_at = now() WHERE id = $2",
        metadata,
        contact_id,
    )

    return await get_contact(contact_id=contact_id, db=db)


# ---------------------------------------------------------------------------
# POST /contacts/{contact_id}/merge
# ---------------------------------------------------------------------------


@router.post("/contacts/{contact_id}/merge", response_model=ContactMergeResponse)
async def merge_contact(
    contact_id: UUID,
    request: ContactMergeRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactMergeResponse:
    """Merge a temp contact into a target contact.

    Moves all contact_info from the source (temp) contact to the target,
    attempts entity_merge if both contacts have entity_ids, then deletes
    the source contact.

    ``contact_id`` in the URL is the **target** (survives).
    ``source_contact_id`` in the request body is the **temp** (deleted).
    """
    pool = _pool(db)

    source_id = request.source_contact_id

    # Validate target contact exists
    target_row = await pool.fetchrow(
        "SELECT id, entity_id FROM contacts WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if target_row is None:
        raise HTTPException(status_code=404, detail="Target contact not found")

    # Validate source contact exists
    source_row = await pool.fetchrow(
        "SELECT id, entity_id FROM contacts WHERE id = $1",
        source_id,
    )
    if source_row is None:
        raise HTTPException(status_code=404, detail="Source contact not found")

    if contact_id == source_id:
        raise HTTPException(status_code=400, detail="Source and target contacts must be different")

    # Move contact_info from source to target.
    # First delete source rows whose (type, value) already exist on the target to
    # avoid producing duplicates (public.contact_info has no unique constraint on
    # (contact_id, type, value), so a plain UPDATE would silently create them).
    await pool.execute(
        """
        DELETE FROM public.contact_info
        WHERE contact_id = $1
          AND (type, value) IN (
              SELECT type, value
              FROM public.contact_info
              WHERE contact_id = $2
          )
        """,
        source_id,
        contact_id,
    )
    moved_result = await pool.fetch(
        """
        UPDATE public.contact_info
        SET contact_id = $1
        WHERE contact_id = $2
        RETURNING id
        """,
        contact_id,
        source_id,
    )
    contact_info_moved = len(moved_result)

    # Attempt entity_merge if both have entity_ids
    entity_merged = False
    src_entity_id = source_row["entity_id"]
    tgt_entity_id = target_row["entity_id"]

    if src_entity_id is not None and tgt_entity_id is not None:
        try:
            from butlers.modules.memory.tools.entities import entity_merge

            memory_pool = await _get_memory_pool(db)

            if memory_pool is not None:
                await entity_merge(
                    memory_pool,
                    str(src_entity_id),
                    str(tgt_entity_id),
                )
                entity_merged = True
        except Exception:  # noqa: BLE001
            logger.warning(
                "merge_contact: entity_merge failed for %s -> %s, continuing without it",
                src_entity_id,
                tgt_entity_id,
                exc_info=True,
            )

    # Delete the source contact (cascades to remaining references if any)
    await pool.execute(
        "DELETE FROM contacts WHERE id = $1",
        source_id,
    )

    return ContactMergeResponse(
        target_contact_id=contact_id,
        source_contact_id=source_id,
        contact_info_moved=contact_info_moved,
        entity_merged=entity_merged,
    )


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
# POST /contacts/{contact_id}/contact-info
# ---------------------------------------------------------------------------


@router.post(
    "/contacts/{contact_id}/contact-info",
    response_model=CreateContactInfoResponse,
    status_code=201,
)
async def create_contact_info(
    contact_id: UUID,
    http_request: Request,
    request: CreateContactInfoRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> CreateContactInfoResponse:
    """Add a contact_info entry (email, telegram, phone, etc.) to a contact."""
    pool = _pool(db)

    # Verify contact exists
    existing = await pool.fetchrow(
        "SELECT id FROM contacts WHERE id = $1 AND archived_at IS NULL",
        contact_id,
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO public.contact_info
                (contact_id, type, value, is_primary, secured, parent_id, context)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id, contact_id, type, value, is_primary, secured, parent_id, context
            """,
            contact_id,
            request.type,
            request.value,
            request.is_primary,
            request.secured,
            request.parent_id,
            request.context,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"A {request.type} entry with this value already exists.",
        )

    result = CreateContactInfoResponse(
        id=row["id"],
        contact_id=row["contact_id"],
        type=row["type"],
        value=row["value"],
        is_primary=row["is_primary"],
        secured=row["secured"],
        parent_id=row["parent_id"],
        context=row["context"],
    )

    # Explicit audit — middleware also fires but this carries a richer operation label.
    await emit_dashboard_audit(
        db,
        butler="relationship",
        operation="contact_info_create",
        method="POST",
        path=f"/api/relationship/contacts/{contact_id}/contact-info",
        path_params={"contact_id": str(contact_id)},
        body={"type": request.type, "is_primary": request.is_primary, "secured": request.secured},
        response_status=201,
        request=http_request,
    )

    return result


# ---------------------------------------------------------------------------
# DELETE /contacts/{contact_id}/contact-info/{info_id}
# ---------------------------------------------------------------------------


@router.delete("/contacts/{contact_id}/contact-info/{info_id}", status_code=204)
async def delete_contact_info(
    contact_id: UUID,
    info_id: UUID,
    request: Request,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Delete a single contact_info entry."""
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id FROM public.contact_info WHERE id = $1 AND contact_id = $2",
        info_id,
        contact_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contact info entry not found")

    await pool.execute("DELETE FROM public.contact_info WHERE id = $1", info_id)

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="relationship",
        operation="contact_info_delete",
        method="DELETE",
        path=f"/api/relationship/contacts/{contact_id}/contact-info/{info_id}",
        path_params={"contact_id": str(contact_id), "info_id": str(info_id)},
        response_status=204,
        request=request,
    )


# ---------------------------------------------------------------------------
# PATCH /contacts/{contact_id}/contact-info/{info_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/contacts/{contact_id}/contact-info/{info_id}",
    response_model=ContactInfoEntry,
)
async def patch_contact_info(
    contact_id: UUID,
    info_id: UUID,
    http_request: Request,
    request: PatchContactInfoRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactInfoEntry:
    """Update a contact_info entry (type, value, is_primary)."""
    pool = _pool(db)

    row = await pool.fetchrow(
        "SELECT id FROM public.contact_info WHERE id = $1 AND contact_id = $2",
        info_id,
        contact_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Contact info entry not found")

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

    if request.is_primary is not None:
        updates.append(f"is_primary = ${idx}")
        args.append(request.is_primary)
        idx += 1

    # context uses sentinel to distinguish "not provided" from "set to None"
    if "context" in (request.model_fields_set or set()):
        updates.append(f"context = ${idx}")
        args.append(request.context)
        idx += 1

    if updates:
        set_clause = ", ".join(updates)
        args.append(info_id)
        await pool.execute(
            f"UPDATE public.contact_info SET {set_clause} WHERE id = ${idx}",
            *args,
        )

    # When toggling is_primary=true, clear siblings of same type (top-level only)
    if request.is_primary is True:
        entry = await pool.fetchrow(
            "SELECT contact_id, type FROM public.contact_info WHERE id = $1",
            info_id,
        )
        if entry is not None:
            await pool.execute(
                """
                UPDATE public.contact_info SET is_primary = false
                WHERE contact_id = $1 AND type = $2 AND parent_id IS NULL AND id != $3
                """,
                entry["contact_id"],
                entry["type"],
                info_id,
            )

    updated = await pool.fetchrow(
        "SELECT id, type, value, is_primary, secured, parent_id, context"
        " FROM public.contact_info WHERE id = $1",
        info_id,
    )
    entry_result = ContactInfoEntry(
        id=updated["id"],
        type=updated["type"],
        value=updated["value"],
        is_primary=updated["is_primary"],
        secured=updated["secured"],
        parent_id=updated["parent_id"],
        context=updated["context"],
    )

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    audit_body: dict = {}
    if request.type is not None:
        audit_body["type"] = request.type
    if request.is_primary is not None:
        audit_body["is_primary"] = request.is_primary
    # Note: request.value is intentionally excluded (may contain credential values)
    await emit_dashboard_audit(
        db,
        butler="relationship",
        operation="contact_info_patch",
        method="PATCH",
        path=f"/api/relationship/contacts/{contact_id}/contact-info/{info_id}",
        path_params={"contact_id": str(contact_id), "info_id": str(info_id)},
        body=audit_body or None,
        response_status=200,
        request=http_request,
    )

    return entry_result


# ---------------------------------------------------------------------------
# GET /groups — list groups with member counts
# ---------------------------------------------------------------------------


@router.get("/groups", response_model=GroupListResponse)
async def list_groups(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> GroupListResponse:
    """List all groups with member counts, paginated."""
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

    groups = [
        Group(
            id=r["id"],
            name=r["name"],
            description=r["description"],
            member_count=r["member_count"],
            labels=[],
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

    return Group(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        member_count=row["member_count"],
        labels=[],
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
            c.name AS contact_name,
            id.label,
            id.month,
            id.day,
            id.year
        FROM important_dates id
        JOIN contacts c ON c.id = id.contact_id
        WHERE c.archived_at IS NULL
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
      contact-type facts (``has-*`` predicates) in ``relationship.facts``:
      score 70
    - **Substring match** on ``canonical_name`` or any alias: score 50
    - **Predicate label match** — ``predicate ILIKE '%q%'`` on active facts
      in ``relationship.facts``: score 30

    Results are deduplicated by ``entity_id`` (each entity appears at most
    once, at its highest score), ordered by score descending, ties broken
    deterministically by entity UUID.

    **Authorization**: owner-only gate (Amendment 12b) — returns HTTP 403
    with ``{"code": "owner_required"}`` when no owner entity is registered.

    **No LLM, no embedding service.** All ranking is pure SQL (``ILIKE``).
    Per Brief §6b Amendment 15 (Deterministic-Finder transitive enforcement).

    All ``relationship.facts`` queries include ``AND validity = 'active'``
    and ``AND scope = 'relationship'`` (correctness filter per PR #1772/#1773
    review requirement).
    """
    pool = _pool(db)

    # Normalise query — strip surrounding whitespace and handle None.
    q_clean = (q or "").strip()
    if not q_clean:
        return SearchResponse(results=[], total=0, q=q, limit=limit)

    # Owner-only gate (Clause 12b, Amendment 12b).
    await _assert_owner_entity_exists(pool)

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
            FROM relationship.facts f
            WHERE f.predicate LIKE 'has-%'
              AND f.object_kind = 'literal'
              AND f.object ILIKE ('%' || $1 || '%')
              AND f.validity = 'active'
              AND f.scope = 'relationship'

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
            FROM relationship.facts f
            WHERE f.predicate ILIKE ('%' || $1 || '%')
              AND f.validity = 'active'
              AND f.scope = 'relationship'
        ) AS candidates
        GROUP BY entity_id
    )
    SELECT
        r.entity_id,
        e.canonical_name,
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


@router.get("/entities", response_model=EntityListResponse)
async def list_entities(
    entity_type: str | None = Query(
        None, description="Filter by entity_type (e.g. person, organization)"
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
            "in relationship.facts.  Unknown values are rejected with HTTP 400."
        ),
    ),
    limit: int = Query(_ENTITY_LIST_DEFAULT_LIMIT, ge=1, le=_ENTITY_LIST_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityListResponse:
    """List entities from ``public.entities`` with optional filter chips and pagination.

    **Filters**

    - ``entity_type`` — filters ``public.entities.entity_type`` (e.g. ``person``,
      ``organization``, ``location``).
    - ``state`` — state chip filter:
        - ``unidentified``: entities where ``metadata->>'unidentified' = 'true'``.
        - ``duplicate-candidate``: entities where ``metadata->>'duplicate_candidate' = 'true'``.
        - ``stale``: entities whose most-recent ``last_seen`` across all facts in
          ``relationship.facts`` is older than 365 days (or have no facts at all).
    - ``has=contact`` — entities with at least one contact-type triple
      (``has-email | has-phone | has-handle | has-address | has-birthday | has-website``)
      in ``relationship.facts``.

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
        "(e.metadata->>'merged_into') IS NULL",
    ]
    args: list[object] = []
    arg_idx = 1

    # entity_type filter
    if entity_type is not None:
        conditions.append(f"e.entity_type = ${arg_idx}")
        args.append(entity_type)
        arg_idx += 1

    # state filter
    if state == "unidentified":
        conditions.append("(e.metadata->>'unidentified')::text = 'true'")
    elif state == "duplicate-candidate":
        conditions.append("(e.metadata->>'duplicate_candidate')::text = 'true'")
    elif state == "stale":
        # Stale: no recent fact OR latest fact last_seen older than 365 days.
        # We check against relationship.facts for last_seen.
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1 FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
                  AND rf.last_seen > (now() - INTERVAL '365 days')
            )
            """
        )

    # has=contact filter — require at least one has-* triple in relationship.facts
    if has == "contact":
        predicates_literal = ", ".join(f"'{p}'" for p in _HAS_CONTACT_PREDICATES)
        conditions.append(
            f"""
            EXISTS (
                SELECT 1 FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.predicate IN ({predicates_literal})
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            )
            """
        )

    where_clause = "WHERE " + " AND ".join(conditions)

    # Count query (no pagination)
    count_sql = f"SELECT count(*) FROM public.entities e {where_clause}"

    # Data query: annotate with pinned tier (from facts) and last_seen
    data_sql = f"""
        SELECT
            e.id,
            e.canonical_name,
            e.entity_type,
            e.aliases,
            e.roles,
            e.metadata,
            e.created_at,
            e.updated_at,
            -- Pinned Dunbar tier override from relationship.facts
            (
                SELECT (rf.content)::int
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.predicate = 'dunbar_tier_override'
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
                ORDER BY rf.created_at DESC
                LIMIT 1
            ) AS tier,
            -- Most-recent last_seen across all active relationship facts
            (
                SELECT max(rf.last_seen)
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            ) AS last_seen,
            -- Count of contact-type facts
            (
                SELECT count(*)
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.predicate IN ({", ".join(f"'{p}'" for p in _HAS_CONTACT_PREDICATES)})
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            ) AS contact_fact_count
        FROM public.entities e
        {where_clause}
        ORDER BY e.canonical_name ASC
        OFFSET ${arg_idx} LIMIT ${arg_idx + 1}
    """
    count_args = list(args)
    data_args = [*args, offset, limit]

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
# GET /entities/queue — curation queue (entity-redesign Phase 2, bu-t1zfd)
# ---------------------------------------------------------------------------

#: Number of days without a ``last_seen`` update before an entity is stale.
_STALE_DAYS = 365

#: Predicates used for deterministic duplicate-candidate detection.
_DUP_DETECTION_PREDICATES = ("has-email", "has-phone")

#: Default and maximum page sizes for the queue endpoint.
_QUEUE_DEFAULT_LIMIT = 50
_QUEUE_MAX_LIMIT = 200


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
    3. **stale** — entities with no active ``relationship.facts`` fact whose
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

    # Owner-only gate (Clause 12b).
    await _assert_owner_entity_exists(pool)

    dup_predicates_literal = ", ".join(f"'{p}'" for p in _DUP_DETECTION_PREDICATES)

    # -------------------------------------------------------------------
    # SQL: three buckets, materialised in application order.
    # Each bucket query returns: entity_id, canonical_name, entity_type,
    #   last_seen, bucket, evidence (as JSON text).
    #
    # Bucket 1 — unidentified
    # -------------------------------------------------------------------
    unidentified_sql = """
        SELECT
            e.id            AS entity_id,
            e.canonical_name,
            e.entity_type,
            (
                SELECT max(rf.last_seen)
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            ) AS last_seen,
            'unidentified'::text AS bucket,
            '{}'::jsonb AS evidence_json
        FROM public.entities e
        WHERE (e.metadata->>'unidentified')::text = 'true'
          AND (e.metadata->>'merged_into') IS NULL
    """

    # -------------------------------------------------------------------
    # Bucket 2 — duplicate-candidate
    # Sourced from two sub-buckets merged via UNION:
    #   2a. metadata flag
    #   2b. shared has-email / has-phone value detected via self-join
    # -------------------------------------------------------------------
    dup_metadata_sql = """
        SELECT
            e.id            AS entity_id,
            e.canonical_name,
            e.entity_type,
            (
                SELECT max(rf.last_seen)
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            ) AS last_seen,
            'duplicate-candidate'::text AS bucket,
            '{}'::jsonb AS evidence_json
        FROM public.entities e
        WHERE (e.metadata->>'duplicate_candidate')::text = 'true'
          AND (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND (e.metadata->>'merged_into') IS NULL
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
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            ) AS last_seen,
            'duplicate-candidate'::text AS bucket,
            json_build_object(
                'predicate', grp.predicate,
                'shared_value', grp.object,
                'peer_entity_ids', (
                    SELECT json_agg(DISTINCT f2.entity_id::text)
                    FROM relationship.facts f2
                    WHERE f2.predicate = grp.predicate
                      AND f2.object = grp.object
                      AND f2.validity = 'active'
                      AND f2.scope = 'relationship'
                      AND f2.entity_id <> e.id
                )
            )::jsonb AS evidence_json
        FROM public.entities e
        JOIN (
            SELECT predicate, object
            FROM relationship.facts
            WHERE predicate IN ({dup_predicates_literal})
              AND validity = 'active'
              AND scope = 'relationship'
            GROUP BY predicate, object
            HAVING count(DISTINCT entity_id) > 1
        ) AS grp
            ON grp.predicate IN ({dup_predicates_literal})
        JOIN relationship.facts f_link
            ON f_link.entity_id = e.id
           AND f_link.predicate = grp.predicate
           AND f_link.object = grp.object
           AND f_link.validity = 'active'
           AND f_link.scope = 'relationship'
        WHERE (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND (e.metadata->>'merged_into') IS NULL
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
                FROM relationship.facts rf
                WHERE rf.entity_id = e.id
                  AND rf.validity = 'active'
                  AND rf.scope = 'relationship'
            ) AS last_seen,
            'stale'::text AS bucket,
            json_build_object(
                'last_seen',
                (
                    SELECT max(rf.last_seen)::text
                    FROM relationship.facts rf
                    WHERE rf.entity_id = e.id
                      AND rf.validity = 'active'
                      AND rf.scope = 'relationship'
                )
            )::jsonb AS evidence_json
        FROM public.entities e
        WHERE (e.metadata->>'unidentified') IS DISTINCT FROM 'true'
          AND (e.metadata->>'merged_into') IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM relationship.facts rf
              WHERE rf.entity_id = e.id
                AND rf.validity = 'active'
                AND rf.scope = 'relationship'
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
# GET /entities/{entity_id}
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> EntityDetail:
    """Get full entity detail including entity_info entries.

    Secured entity_info values are masked (value=None) in the response.
    Use GET /entities/{id}/secrets/{info_id} to reveal a secured value.
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

    info_rows = await pool.fetch(
        """
        SELECT id, type, value, label, is_primary, secured
        FROM public.entity_info
        WHERE entity_id = $1
        ORDER BY type
        """,
        entity_id,
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

    Returns the real value for a secured entity_info row. Returns 404 if
    the info_id does not exist OR does not belong to the given entity_id.
    """
    pool = _pool(db)

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
        SELECT id, content, metadata, valid_at
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
        SELECT id, predicate, content, metadata, valid_at
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
        SELECT id, content, metadata, created_at
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
        SELECT id, content, metadata, created_at
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
        SELECT id, predicate, content, metadata, valid_at, created_at
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
    Each entry includes a primary email and phone for quick display.
    """
    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    rows = await pool.fetch(
        """
        SELECT
            c.id,
            c.full_name,
            (
                SELECT ci.value
                FROM public.contact_info ci
                WHERE ci.contact_id = c.id
                  AND ci.type = 'email'
                  AND ci.secured = false
                ORDER BY ci.is_primary DESC, ci.id
                LIMIT 1
            ) AS email,
            (
                SELECT ci.value
                FROM public.contact_info ci
                WHERE ci.contact_id = c.id
                  AND ci.type = 'phone'
                  AND ci.secured = false
                ORDER BY ci.is_primary DESC, ci.id
                LIMIT 1
            ) AS phone
        FROM public.contacts c
        WHERE c.entity_id = $1
          AND c.archived_at IS NULL
        ORDER BY c.full_name
        """,
        entity_id,
    )
    return [
        LinkedContactSummary(
            id=r["id"],
            full_name=r["full_name"],
            email=r["email"],
            phone=r["phone"],
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

    Resolves the entity's linked contacts → their ``public.contact_info``
    identifiers → matches against ``request_context ->> 'source_sender_identity'``
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

    # Collect candidate sender identifiers from linked contacts' contact_info
    # plus the entity's own entity_info (some channels store sender identity
    # there for owner-attached accounts).
    identifiers = await pool.fetch(
        """
        SELECT DISTINCT ci.value
        FROM public.contact_info ci
        JOIN public.contacts c ON c.id = ci.contact_id
        WHERE c.entity_id = $1
          AND c.archived_at IS NULL
          AND ci.value IS NOT NULL
          AND ci.secured = false
        UNION
        SELECT DISTINCT ei.value
        FROM public.entity_info ei
        WHERE ei.entity_id = $1
          AND ei.value IS NOT NULL
          AND ei.secured = false
        """,
        entity_id,
    )
    candidates = [r["value"] for r in identifiers if r["value"]]
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
            c.name AS contact_name,
            id.label,
            id.month,
            id.day,
            id.year
        FROM important_dates id
        JOIN contacts c ON c.id = id.contact_id
        WHERE c.entity_id = $1
          AND c.archived_at IS NULL
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
    Resolves the entity to any one of its linked contacts, then delegates to
    the canonical ``dunbar_tier_set`` engine. The override is stored as a fact
    keyed by entity_id, so the choice of contact is irrelevant beyond
    satisfying the engine's contract.

    Returns 404 if the entity has no linked contact (override storage requires
    one for the engine's bookkeeping).
    """
    from butlers.tools.relationship import dunbar as _dunbar

    pool = _pool(db)
    await _assert_entity_exists(pool, entity_id)

    contact_row = await pool.fetchrow(
        """
        SELECT id FROM contacts
        WHERE entity_id = $1 AND archived_at IS NULL
        ORDER BY id
        LIMIT 1
        """,
        entity_id,
    )
    if contact_row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Entity '{entity_id}' has no linked contact. "
                "Link a contact before pinning a Dunbar tier."
            ),
        )
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


# ---------------------------------------------------------------------------
# GET /entities/{entity_id}/neighbours — relational neighbours (bu-4wn79)
# ---------------------------------------------------------------------------


async def _assert_owner_entity_exists(pool) -> None:
    """Raise HTTP 403 (owner_required) unless an owner entity is registered.

    Checks that at least one entity with ``'owner' = ANY(roles)`` exists in
    ``public.entities``.  This is the Clause 12b owner-only gate for
    PII-bearing read surfaces (Amendment 12b, entity-redesign Phase 2).

    In v1, the dashboard is single-tenant and there is no per-request caller
    identity attached to API calls.  The gate therefore checks system-level
    bootstrapping: if the owner entity is present, access is granted; if not,
    the system is considered misconfigured and access is denied to prevent
    data leakage.

    Returns HTTP 403 with ``{"code": "owner_required"}`` on failure, matching
    the envelope contract in ``rfcs/0007:75-87``.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT id FROM public.entities
            WHERE 'owner' = ANY(COALESCE(roles, '{}'))
            LIMIT 1
            """
        )
    except Exception as exc:
        logger.warning("Owner entity assertion query failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail={"code": "owner_required", "message": "Owner entity assertion failed"},
        )

    if row is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "owner_required", "message": "Owner entity not found"},
        )


@router.get(
    "/entities/{entity_id}/neighbours",
    response_model=NeighboursResponse,
)
async def list_entity_neighbours(
    entity_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> NeighboursResponse:
    """Return relational triples grouped by predicate for both directions.

    Returns all active relational triples where the given entity is either the
    subject (forward direction) or the object (reverse direction).  Contact
    predicates (``has-*`` family, kind='contact') are excluded; only
    ``kind='relational'`` predicates from ``relationship.predicate_registry``
    are returned.

    Owner-only authz gate (Clause 12b, Amendment 12b): returns HTTP 403 with
    ``{"code": "owner_required"}`` if no owner entity is registered.

    Returns 404 if the entity does not exist in ``public.entities``.
    Returns ``{"neighbours": {}}`` if the entity has no relational triples.

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

    # Owner-only gate (Clause 12b).
    await _assert_owner_entity_exists(pool)

    # Entity existence check.
    await _assert_entity_exists(pool, entity_id)

    # Query relationship.facts for both directions, joining predicate_registry
    # to filter only kind='relational' predicates (excludes has-* contact facts).
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
            END AS direction
        FROM relationship.facts f
        JOIN relationship.predicate_registry pr ON pr.predicate = f.predicate
        WHERE pr.kind = 'relational'
          AND f.validity = 'active'
          AND f.scope = 'relationship'
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

    return NeighboursResponse(neighbours=grouped)


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

    # Fetch canonical names for all entity IDs returned by the ranking.
    entity_ids = [r["entity_id"] for r in ranked if r["entity_id"] is not None]
    contact_ids = [r["contact_id"] for r in ranked if r["entity_id"] is not None]
    entity_name_rows, avatar_rows, owner_row, interaction_30d_rows = await asyncio.gather(
        pool.fetch(
            """
            SELECT e.id, e.canonical_name, e.aliases
            FROM public.entities e
            WHERE e.id = ANY($1::uuid[])
            """,
            entity_ids,
        ),
        pool.fetch(
            """
            SELECT id, avatar_url
            FROM public.contacts
            WHERE id = ANY($1::uuid[])
            """,
            contact_ids,
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
                c.id AS contact_id,
                COUNT(f.id) AS interaction_count_30d
            FROM contacts c
            JOIN facts f ON f.entity_id = c.entity_id
            WHERE c.id = ANY($1::uuid[])
              AND f.predicate LIKE 'interaction_%'
              AND f.validity = 'active'
              AND f.scope = 'relationship'
              AND f.valid_at >= now() - INTERVAL '30 days'
            GROUP BY c.id
            """,
            contact_ids,
        ),
    )

    entity_names: dict[UUID, str] = {row["id"]: row["canonical_name"] for row in entity_name_rows}
    entity_aliases: dict[UUID, list[str]] = {
        row["id"]: list(row["aliases"]) if row["aliases"] else [] for row in entity_name_rows
    }
    contact_avatars: dict[UUID, str | None] = {row["id"]: row["avatar_url"] for row in avatar_rows}
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
                avatar_url=contact_avatars.get(cid),
                aliases=entity_aliases.get(r["entity_id"], []),
                warmth=warmth,
                last_interaction_at=r.get("last_interaction_at"),
            )
        )

    owner_entity_id = owner_row["id"] if owner_row else None

    return DunbarRankingResponse(entries=entries, owner_entity_id=owner_entity_id)
