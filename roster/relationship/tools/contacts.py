"""Contact CRUD — create, update, get, search, and archive contacts."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import table_columns
from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)

# Tenant ID used when writing memory entities on behalf of the relationship butler.
_MEMORY_TENANT_ID = "relationship"


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


def _build_canonical_name(first_name: str | None, last_name: str | None) -> str:
    """Build entity canonical name from first and last name parts."""
    parts = [p.strip() for p in (first_name, last_name) if p and p.strip()]
    return " ".join(parts) or "Unknown"


def _build_entity_aliases(
    first_name: str | None,
    last_name: str | None,
    nickname: str | None,
) -> list[str]:
    """Build deduplicated alias list for an entity."""
    canonical = _build_canonical_name(first_name, last_name)
    candidates = [nickname, first_name]
    aliases = []
    seen = {canonical.lower()}
    for candidate in candidates:
        if candidate and candidate.strip():
            lower = candidate.strip().lower()
            if lower not in seen:
                aliases.append(candidate.strip())
                seen.add(lower)
    return aliases


async def _sync_entity_create(
    memory_pool: asyncpg.Pool,
    first_name: str | None,
    last_name: str | None,
    nickname: str | None,
    tenant_id: str,
) -> str | None:
    """Create a memory entity for a new contact. Returns entity_id or None on failure."""
    from butlers.modules.memory.tools.entities import entity_create

    canonical_name = _build_canonical_name(first_name, last_name)
    aliases = _build_entity_aliases(first_name, last_name, nickname)
    try:
        result = await entity_create(
            memory_pool,
            canonical_name,
            "person",
            tenant_id=tenant_id,
            aliases=aliases,
        )
        return result["entity_id"]
    except Exception:
        logger.exception(
            "entity_create failed for contact (canonical_name=%r); continuing without entity link",
            canonical_name,
        )
        return None


async def _sync_entity_update(
    memory_pool: asyncpg.Pool,
    entity_id: str,
    first_name: str | None,
    last_name: str | None,
    nickname: str | None,
    tenant_id: str,
) -> None:
    """Update the memory entity canonical name and aliases. Best-effort."""
    from butlers.modules.memory.tools.entities import entity_update

    canonical_name = _build_canonical_name(first_name, last_name)
    aliases = _build_entity_aliases(first_name, last_name, nickname)
    try:
        await entity_update(
            memory_pool,
            entity_id,
            tenant_id=tenant_id,
            canonical_name=canonical_name,
            aliases=aliases,
        )
    except Exception:
        logger.exception(
            "entity_update failed for entity_id=%r; continuing without sync",
            entity_id,
        )


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
    memory_pool: asyncpg.Pool | None = None,
    memory_tenant_id: str = _MEMORY_TENANT_ID,
) -> dict[str, Any]:
    """Create a contact with compatibility for both legacy and spec schemas.

    When ``memory_pool`` is provided, a corresponding memory entity is created
    and the returned ``entity_id`` is stored on the contact row. Entity creation
    is fail-open: if it fails the contact is still returned without an entity link.
    """
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

    # Sync memory entity (fail-open)
    if memory_pool is not None and "entity_id" in cols:
        entity_id = await _sync_entity_create(
            memory_pool,
            first_name=result.get("first_name"),
            last_name=result.get("last_name"),
            nickname=result.get("nickname"),
            tenant_id=memory_tenant_id,
        )
        if entity_id is not None:
            updated_row = await pool.fetchrow(
                "UPDATE contacts SET entity_id = $1 WHERE id = $2 RETURNING *",
                uuid.UUID(entity_id),
                result["id"],
            )
            if updated_row is not None:
                result = _parse_contact(updated_row)

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
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    memory_pool: asyncpg.Pool | None = None,
    memory_tenant_id: str = _MEMORY_TENANT_ID,
    **fields: Any,
) -> dict[str, Any]:
    """Update a contact's fields across legacy/spec schemas.

    Security contract: ``roles`` is stripped from ``fields`` before any UPDATE
    is built. Runtime LLM instances must never modify roles; that is a
    privileged operation reserved for the identity layer (owner bootstrap,
    dashboard PATCH endpoint). Any ``roles`` key passed by a caller is silently
    ignored.

    When ``memory_pool`` is provided and the contact has a linked entity,
    name changes (first_name, last_name, nickname) are synced to the entity.
    Contacts without an entity_id are handled gracefully (no crash).
    """
    # Strip roles — runtime instances must never modify roles.
    fields.pop("roles", None)

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

    # Sync entity name fields if any name field changed (fail-open)
    _name_fields = {"name", "first_name", "last_name", "nickname"}
    if memory_pool is not None and _name_fields & set(fields) and "entity_id" in cols:
        existing_dict = dict(existing) if not isinstance(existing, dict) else existing
        entity_id_val = existing_dict.get("entity_id")
        if entity_id_val is not None:
            # Resolve the current name state from the result
            await _sync_entity_update(
                memory_pool,
                entity_id=str(entity_id_val),
                first_name=result.get("first_name"),
                last_name=result.get("last_name"),
                nickname=result.get("nickname"),
                tenant_id=memory_tenant_id,
            )

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


async def contact_merge(
    pool: asyncpg.Pool,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    memory_pool: asyncpg.Pool | None = None,
    memory_tenant_id: str = _MEMORY_TENANT_ID,
) -> dict[str, Any]:
    """Merge source contact into target contact.

    The target contact survives; the source is archived. All related records
    (notes, interactions, reminders, etc.) are re-pointed to the target.

    When ``memory_pool`` is provided and both contacts have linked entities,
    the source entity is merged into the target entity via entity_merge so that
    memory facts consolidate under the surviving contact's entity.

    Legacy contacts with NULL entity_id are handled gracefully (no crash).

    Returns:
        The updated target contact dict.

    Raises:
        ValueError: If source or target contact not found, or IDs are identical.
    """
    if source_id == target_id:
        raise ValueError("source_id and target_id must be different.")

    source = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", source_id)
    if source is None:
        raise ValueError(f"Source contact {source_id} not found")
    target = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", target_id)
    if target is None:
        raise ValueError(f"Target contact {target_id} not found")

    cols = await table_columns(pool, "contacts")

    # Tables that reference contacts — re-point source -> target
    _child_tables = [
        ("notes", "contact_id"),
        ("interactions", "contact_id"),
        ("reminders", "contact_id"),
        ("dates", "contact_id"),
        ("relationships", "contact_a"),
        ("relationships", "contact_b"),
        ("gifts", "contact_id"),
        ("loans", "contact_id"),
        ("group_members", "contact_id"),
        ("contact_labels", "contact_id"),
        ("contact_info", "contact_id"),
        ("addresses", "contact_id"),
        ("facts", "contact_id"),
        ("tasks", "contact_id"),
        ("life_events", "contact_id"),
        ("stay_in_touch", "contact_id"),
        ("activity_feed", "contact_id"),
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            for table, fk_col in _child_tables:
                try:
                    await conn.execute(
                        f"UPDATE {table} SET {fk_col} = $1 WHERE {fk_col} = $2",  # noqa: S608
                        target_id,
                        source_id,
                    )
                except asyncpg.UndefinedTableError:
                    pass  # table not present in this schema variant
                except Exception:
                    logger.exception(
                        "Failed to re-point %s.%s from %s to %s during contact_merge",
                        table,
                        fk_col,
                        source_id,
                        target_id,
                    )

            # Archive the source contact
            archive_clauses: list[str] = []
            if "archived_at" in cols:
                archive_clauses.append("archived_at = now()")
            if "listed" in cols:
                archive_clauses.append("listed = false")
            if "updated_at" in cols:
                archive_clauses.append("updated_at = now()")
            if archive_clauses:
                await conn.execute(
                    f"UPDATE contacts SET {', '.join(archive_clauses)} WHERE id = $1",  # noqa: S608
                    source_id,
                )

    # Merge memory entities (fail-open)
    if memory_pool is not None and "entity_id" in cols:
        src_entity_id = dict(source).get("entity_id")
        tgt_entity_id = dict(target).get("entity_id")
        if src_entity_id is not None and tgt_entity_id is not None:
            from butlers.modules.memory.tools.entities import entity_merge

            try:
                await entity_merge(
                    memory_pool,
                    str(src_entity_id),
                    str(tgt_entity_id),
                    tenant_id=memory_tenant_id,
                )
            except Exception:
                logger.exception(
                    "entity_merge failed for source=%s target=%s; continuing",
                    src_entity_id,
                    tgt_entity_id,
                )

    # Fetch the updated target
    updated_row = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", target_id)
    result = _parse_contact(updated_row)

    await _log_activity(
        pool,
        target_id,
        "contact_merged",
        f"Merged contact {source_id} into '{result['name']}'",
        entity_type="contact",
        entity_id=target_id,
    )
    return result
