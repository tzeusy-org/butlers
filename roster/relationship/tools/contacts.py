"""Contact CRUD — create, update, get, search, and archive contacts."""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import table_columns
from butlers.tools.relationship.feed import _log_activity


def _split_name(name: str) -> tuple[str | None, str | None]:
    parts = name.strip().split(None, 1)
    if not parts:
        return None, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _compose_name(data: dict[str, Any]) -> str:
    if data.get("name"):
        return str(data["name"])
    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    combined = " ".join(p for p in (first, last) if p).strip()
    if combined:
        return combined
    return data.get("nickname") or data.get("company") or "Unknown"


def _parse_json_field(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return value
    return {}


def _parse_contact(row: asyncpg.Record) -> dict[str, Any]:
    """Convert a contact row to a dict with backward-compatible fields."""
    d = dict(row)
    if "metadata" in d:
        d["metadata"] = _parse_json_field(d.get("metadata"))
    if "details" in d:
        d["details"] = _parse_json_field(d.get("details"))

    if "metadata" not in d:
        d["metadata"] = d.get("details", {})
    if "details" not in d:
        d["details"] = d.get("metadata", {})

    d["name"] = _compose_name(d)
    return d


async def contact_create(
    pool: asyncpg.Pool,
    name: str | None = None,
    details: dict[str, Any] | None = None,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    nickname: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    gender: str | None = None,
    pronouns: str | None = None,
    avatar_url: str | None = None,
    listed: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a contact with compatibility for both legacy and spec schemas."""
    if (first_name is None and last_name is None) and name:
        first_name, last_name = _split_name(name)
    merged_meta = metadata if metadata is not None else (details or {})
    composed_name = name or " ".join(p for p in [first_name, last_name] if p).strip()
    if not composed_name:
        composed_name = nickname or company or "Unknown"

    cols = await table_columns(pool, "contacts")
    payload: dict[str, Any] = {}

    if "name" in cols:
        payload["name"] = composed_name
    if "details" in cols:
        payload["details"] = details or merged_meta
    if "first_name" in cols:
        payload["first_name"] = first_name
    if "last_name" in cols:
        payload["last_name"] = last_name
    if "nickname" in cols:
        payload["nickname"] = nickname
    if "company" in cols:
        payload["company"] = company
    if "job_title" in cols:
        payload["job_title"] = job_title
    if "gender" in cols:
        payload["gender"] = gender
    if "pronouns" in cols:
        payload["pronouns"] = pronouns
    if "avatar_url" in cols:
        payload["avatar_url"] = avatar_url
    if "listed" in cols and listed is not None:
        payload["listed"] = listed
    if "metadata" in cols:
        payload["metadata"] = merged_meta

    json_cols = {"details", "metadata"} & set(payload)
    insert_cols = list(payload.keys())
    placeholders = []
    values: list[Any] = []
    for idx, col in enumerate(insert_cols, start=1):
        if col in json_cols:
            placeholders.append(f"${idx}::jsonb")
            values.append(json.dumps(payload[col] or {}))
        else:
            placeholders.append(f"${idx}")
            values.append(payload[col])

    row = await pool.fetchrow(
        f"""
        INSERT INTO contacts ({", ".join(insert_cols)})
        VALUES ({", ".join(placeholders)})
        RETURNING *
        """,
        *values,
    )
    result = _parse_contact(row)
    await _log_activity(
        pool,
        result["id"],
        "contact_created",
        f"Created contact '{result['name']}'",
        entity_type="contact",
        entity_id=result["id"],
    )
    return result


async def contact_update(
    pool: asyncpg.Pool, contact_id: uuid.UUID, **fields: Any
) -> dict[str, Any]:
    """Update a contact's fields across legacy/spec schemas."""
    existing = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id)
    if existing is None:
        raise ValueError(f"Contact {contact_id} not found")

    cols = await table_columns(pool, "contacts")
    to_update: dict[str, Any] = {}

    # Backward-compatible inputs
    if "name" in fields:
        to_update["name"] = fields["name"]
        if {"first_name", "last_name"} & cols:
            first, last = _split_name(fields["name"])
            if "first_name" in cols:
                to_update["first_name"] = first
            if "last_name" in cols:
                to_update["last_name"] = last
    if "details" in fields:
        to_update["details"] = fields["details"]
        if "metadata" in cols:
            to_update["metadata"] = fields["details"]

    # Spec inputs
    for col in (
        "first_name",
        "last_name",
        "nickname",
        "company",
        "job_title",
        "gender",
        "pronouns",
        "avatar_url",
        "metadata",
        "listed",
    ):
        if col in fields and col in cols:
            to_update[col] = fields[col]

    if not to_update:
        raise ValueError("At least one field must be provided for update")

    json_cols = {"details", "metadata"} & set(to_update)
    set_clauses = []
    params: list[Any] = [contact_id]
    idx = 2
    for col, val in to_update.items():
        if col not in cols:
            continue
        if col in json_cols:
            set_clauses.append(f"{col} = ${idx}::jsonb")
            params.append(json.dumps(val or {}))
        else:
            set_clauses.append(f"{col} = ${idx}")
            params.append(val)
        idx += 1

    if "updated_at" in cols:
        set_clauses.append("updated_at = now()")

    row = await pool.fetchrow(
        f"UPDATE contacts SET {', '.join(set_clauses)} WHERE id = $1 RETURNING *",  # noqa: S608
        *params,
    )
    result = _parse_contact(row)
    await _log_activity(
        pool,
        contact_id,
        "contact_updated",
        f"Updated contact '{result['name']}'",
        entity_type="contact",
        entity_id=contact_id,
    )
    return result


async def contact_get(
    pool: asyncpg.Pool, contact_id: uuid.UUID, *, allow_missing: bool = False
) -> dict[str, Any] | None:
    """Get a contact by ID."""
    row = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id)
    if row is None:
        if allow_missing:
            return None
        raise ValueError(f"Contact {contact_id} not found")
    return _parse_contact(row)


async def contact_search(
    pool: asyncpg.Pool, query: str, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Search contacts by legacy and spec fields."""
    cols = await table_columns(pool, "contacts")
    conditions: list[str] = []

    if "name" in cols:
        conditions.append("name ILIKE '%' || $1 || '%'")
    if "first_name" in cols:
        conditions.append("first_name ILIKE '%' || $1 || '%'")
    if "last_name" in cols:
        conditions.append("last_name ILIKE '%' || $1 || '%'")
    if "nickname" in cols:
        conditions.append("nickname ILIKE '%' || $1 || '%'")
    if "company" in cols:
        conditions.append("company ILIKE '%' || $1 || '%'")
    if "details" in cols:
        conditions.append("details::text ILIKE '%' || $1 || '%'")
    if "metadata" in cols:
        conditions.append("metadata::text ILIKE '%' || $1 || '%'")

    active_filters: list[str] = []
    if "archived_at" in cols:
        active_filters.append("archived_at IS NULL")
    if "listed" in cols:
        active_filters.append("listed = true")

    where = " AND ".join(active_filters + [f"({' OR '.join(conditions)})"])
    if "name" in cols:
        order = "name"
    else:
        order = "COALESCE(first_name, nickname, company, '')"

    rows = await pool.fetch(
        f"""
        SELECT * FROM contacts
        WHERE {where}
        ORDER BY {order}
        LIMIT $2 OFFSET $3
        """,
        query,
        limit,
        offset,
    )
    return [_parse_contact(row) for row in rows]


async def contact_archive(pool: asyncpg.Pool, contact_id: uuid.UUID) -> dict[str, Any]:
    """Archive a contact across legacy/spec schemas."""
    cols = await table_columns(pool, "contacts")
    updates: list[str] = []
    if "archived_at" in cols:
        updates.append("archived_at = now()")
    if "listed" in cols:
        updates.append("listed = false")
    if "updated_at" in cols:
        updates.append("updated_at = now()")
    if not updates:
        raise ValueError("contacts table has no archive-compatible columns")

    row = await pool.fetchrow(
        f"UPDATE contacts SET {', '.join(updates)} WHERE id = $1 RETURNING *",  # noqa: S608
        contact_id,
    )
    if row is None:
        raise ValueError(f"Contact {contact_id} not found")
    result = _parse_contact(row)
    await _log_activity(
        pool,
        contact_id,
        "contact_archived",
        f"Archived contact '{result['name']}'",
        entity_type="contact",
        entity_id=contact_id,
    )
    return result
