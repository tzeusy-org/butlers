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
from datetime import date
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

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
        ActivityFeedItem = _models_module.ActivityFeedItem
        ContactDetail = _models_module.ContactDetail
        ContactListResponse = _models_module.ContactListResponse
        ContactSummary = _models_module.ContactSummary
        ContactsSyncTriggerResponse = _models_module.ContactsSyncTriggerResponse
        Gift = _models_module.Gift
        Group = _models_module.Group
        GroupListResponse = _models_module.GroupListResponse
        Interaction = _models_module.Interaction
        Label = _models_module.Label
        Loan = _models_module.Loan
        Note = _models_module.Note
        UpcomingDate = _models_module.UpcomingDate

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
    """Return column names for a table in public schema."""
    rows = await pool.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
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
# GET /contacts — list with search and label filter
# ---------------------------------------------------------------------------


@router.get("/contacts", response_model=ContactListResponse)
async def list_contacts(
    q: str | None = Query(None, description="Search contacts by name"),
    label: str | None = Query(None, description="Filter by label name"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactListResponse:
    """List contacts with optional search and label filter, paginated."""
    pool = _pool(db)

    conditions: list[str] = ["c.archived_at IS NULL"]
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

    # Count
    count_sql = f"SELECT count(DISTINCT c.id) FROM contacts c{joins}{where}"
    total = await pool.fetchval(count_sql, *args) or 0

    # Data query — fetch contacts with labels, primary email/phone, last interaction
    data_sql = f"""
        SELECT
            c.id,
            c.name AS full_name,
            c.nickname,
            (
                SELECT ci.value FROM contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'email'
                ORDER BY ci.is_primary DESC NULLS LAST, ci.id
                LIMIT 1
            ) AS email,
            (
                SELECT ci.value FROM contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'phone'
                ORDER BY ci.is_primary DESC NULLS LAST, ci.id
                LIMIT 1
            ) AS phone,
            (
                SELECT max(i.occurred_at) FROM interactions i
                WHERE i.contact_id = c.id
            ) AS last_interaction_at
        FROM contacts c{joins}{where}
        GROUP BY c.id
        ORDER BY c.name
        OFFSET ${idx} LIMIT ${idx + 1}
    """
    args.extend([offset, limit])
    rows = await pool.fetch(data_sql, *args)

    # Batch-fetch labels for returned contacts
    contact_ids = [row["id"] for row in rows]
    labels_by_contact: dict[UUID, list[Label]] = {cid: [] for cid in contact_ids}

    if contact_ids:
        label_rows = await pool.fetch(
            """
            SELECT cl.contact_id, l.id, l.name, l.color
            FROM contact_labels cl
            JOIN labels l ON l.id = cl.label_id
            WHERE cl.contact_id = ANY($1)
            ORDER BY l.name
            """,
            contact_ids,
        )
        for lr in label_rows:
            labels_by_contact[lr["contact_id"]].append(
                Label(id=lr["id"], name=lr["name"], color=lr["color"])
            )

    contacts = [
        ContactSummary(
            id=row["id"],
            full_name=row["full_name"],
            nickname=row["nickname"],
            email=row["email"],
            phone=row["phone"],
            labels=labels_by_contact.get(row["id"], []),
            last_interaction_at=row["last_interaction_at"],
        )
        for row in rows
    ]

    return ContactListResponse(contacts=contacts, total=total)


# ---------------------------------------------------------------------------
# POST /contacts/sync — manual contacts sync trigger
# ---------------------------------------------------------------------------


@router.post("/contacts/sync", response_model=ContactsSyncTriggerResponse)
async def trigger_contacts_sync(
    mode: Literal["incremental", "full"] = Query(
        "incremental",
        description="Sync mode: incremental for routine refresh, full for backfill",
    ),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
) -> ContactsSyncTriggerResponse:
    """Trigger contacts sync via the relationship butler's MCP tool."""
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
                {"provider": "google", "mode": mode},
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

    if is_error:
        if _is_credential_error(payload, raw_text):
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
    created = _coerce_count(summary.get("created")) if isinstance(summary, dict) else None
    updated = _coerce_count(summary.get("updated")) if isinstance(summary, dict) else None
    skipped = _coerce_count(summary.get("skipped")) if isinstance(summary, dict) else None
    errors = _coerce_count(summary.get("errors")) if isinstance(summary, dict) else None
    message = summary.get("message") if isinstance(summary, dict) else None
    if not isinstance(message, str):
        message = None

    return ContactsSyncTriggerResponse(
        provider="google",
        mode=mode,
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors,
        summary=summary if isinstance(summary, dict) else {},
        message=message,
    )


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id} — full detail
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}", response_model=ContactDetail)
async def get_contact(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ContactDetail:
    """Get full contact detail with labels, email, phone, birthday."""
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT
            c.id,
            c.name AS full_name,
            c.nickname,
            c.details->>'notes' AS notes,
            c.company,
            c.job_title,
            c.metadata,
            c.created_at,
            c.updated_at,
            (
                SELECT ci.value FROM contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'email'
                ORDER BY ci.is_primary DESC NULLS LAST, ci.id
                LIMIT 1
            ) AS email,
            (
                SELECT ci.value FROM contact_info ci
                WHERE ci.contact_id = c.id AND ci.type = 'phone'
                ORDER BY ci.is_primary DESC NULLS LAST, ci.id
                LIMIT 1
            ) AS phone,
            (
                SELECT max(i.occurred_at) FROM interactions i
                WHERE i.contact_id = c.id
            ) AS last_interaction_at
        FROM contacts c
        WHERE c.id = $1 AND c.archived_at IS NULL
        """,
        contact_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Labels
    label_rows = await pool.fetch(
        """
        SELECT l.id, l.name, l.color
        FROM contact_labels cl
        JOIN labels l ON l.id = cl.label_id
        WHERE cl.contact_id = $1
        ORDER BY l.name
        """,
        contact_id,
    )
    labels = [Label(id=lr["id"], name=lr["name"], color=lr["color"]) for lr in label_rows]

    # Birthday from important_dates
    birthday_row = await pool.fetchrow(
        """
        SELECT month, day, year
        FROM important_dates
        WHERE contact_id = $1 AND label = 'birthday'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        contact_id,
    )
    birthday: date | None = None
    if birthday_row is not None:
        year = birthday_row["year"] or 1900
        birthday = date(year, birthday_row["month"], birthday_row["day"])

    # Address from addresses table (current)
    addr_row = await pool.fetchrow(
        """
        SELECT line_1, line_2, city, province, postal_code, country
        FROM addresses
        WHERE contact_id = $1
        ORDER BY is_current DESC NULLS LAST, id
        LIMIT 1
        """,
        contact_id,
    )
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

    _raw_meta = row["metadata"]
    metadata = dict(_raw_meta) if isinstance(_raw_meta, dict) else {}

    return ContactDetail(
        id=row["id"],
        full_name=row["full_name"],
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
    )


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/notes
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/notes", response_model=list[Note])
async def list_contact_notes(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Note]:
    """List notes for a contact, newest first."""
    pool = _pool(db)
    rows = await pool.fetch(
        """
        SELECT id, contact_id, content, created_at, updated_at
        FROM notes
        WHERE contact_id = $1
        ORDER BY created_at DESC
        """,
        contact_id,
    )
    return [
        Note(
            id=r["id"],
            contact_id=r["contact_id"],
            content=r["content"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/interactions
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/interactions", response_model=list[Interaction])
async def list_contact_interactions(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Interaction]:
    """List interactions for a contact, newest first."""
    pool = _pool(db)
    rows = await pool.fetch(
        """
        SELECT id, contact_id, type, summary, details, occurred_at, created_at
        FROM interactions
        WHERE contact_id = $1
        ORDER BY created_at DESC
        """,
        contact_id,
    )
    return [
        Interaction(
            id=r["id"],
            contact_id=r["contact_id"],
            type=r["type"],
            summary=r["summary"],
            details=r["details"],
            occurred_at=r["occurred_at"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/gifts
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/gifts", response_model=list[Gift])
async def list_contact_gifts(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Gift]:
    """List gifts for a contact, newest first."""
    pool = _pool(db)
    rows = await pool.fetch(
        """
        SELECT id, contact_id, description, direction, occasion, date, value, created_at
        FROM gifts
        WHERE contact_id = $1
        ORDER BY created_at DESC
        """,
        contact_id,
    )
    return [
        Gift(
            id=r["id"],
            contact_id=r["contact_id"],
            description=r["description"],
            direction=r["direction"],
            occasion=r["occasion"],
            date=r["date"],
            value=float(r["value"]) if r["value"] is not None else None,
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/loans
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/loans", response_model=list[Loan])
async def list_contact_loans(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[Loan]:
    """List loans for a contact, newest first."""
    pool = _pool(db)
    rows = await pool.fetch(
        """
        SELECT id, contact_id, description, direction, amount, currency,
               status, date, due_date, created_at
        FROM loans
        WHERE contact_id = $1
        ORDER BY created_at DESC
        """,
        contact_id,
    )
    return [
        Loan(
            id=r["id"],
            contact_id=r["contact_id"],
            description=r["description"],
            direction=r["direction"],
            amount=float(r["amount"]),
            currency=r["currency"],
            status=r["status"],
            date=r["date"],
            due_date=r["due_date"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /contacts/{contact_id}/feed — activity feed
# ---------------------------------------------------------------------------


@router.get("/contacts/{contact_id}/feed", response_model=list[ActivityFeedItem])
async def list_contact_feed(
    contact_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[ActivityFeedItem]:
    """Activity feed for a contact, newest first."""
    pool = _pool(db)
    rows = await pool.fetch(
        """
        SELECT id, contact_id, action, details, created_at
        FROM activity_feed
        WHERE contact_id = $1
        ORDER BY created_at DESC
        """,
        contact_id,
    )
    return [
        ActivityFeedItem(
            id=r["id"],
            contact_id=r["contact_id"],
            action=r["action"],
            details=dict(r["details"]) if isinstance(r["details"], dict) else {},
            created_at=r["created_at"],
        )
        for r in rows
    ]


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
