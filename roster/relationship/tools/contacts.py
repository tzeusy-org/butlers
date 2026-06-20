"""Contact CRUD — create, update, get, search, and archive contacts."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import table_columns

logger = logging.getLogger(__name__)


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


def _infer_entity_type(
    first_name: str | None,
    last_name: str | None,
    company: str | None,
) -> str:
    """Infer entity type from contact fields.

    If the contact has no personal name but has a company/org name, treat it as
    an organization.  Otherwise default to person.
    """
    has_personal_name = bool((first_name or "").strip() or (last_name or "").strip())
    has_company = bool((company or "").strip())
    if not has_personal_name and has_company:
        return "organization"
    return "person"


async def _ensure_entity(
    pool: asyncpg.Pool,
    first_name: str | None,
    last_name: str | None,
    nickname: str | None,
    entity_type: str = "person",
) -> str:
    """Resolve or create a memory entity for a contact. Returns entity_id.

    Contacts must always link to an entity.  This function tries to create
    a new entity first (common path for genuinely new people), falls back to
    resolving an existing one if a duplicate-name constraint fires, and raises
    ``RuntimeError`` only if both paths fail.
    """
    from butlers.modules.memory.tools.entities import entity_create, entity_resolve

    canonical_name = _build_canonical_name(first_name, last_name)
    aliases = _build_entity_aliases(first_name, last_name, nickname)

    # Try create first (common case: new entity)
    try:
        result = await entity_create(
            pool,
            canonical_name,
            entity_type,
            aliases=aliases,
        )
        return result["entity_id"]
    except ValueError:
        # Duplicate name — resolve below
        pass
    except Exception:
        logger.exception(
            "_ensure_entity: entity_create failed for %r; falling back to resolve",
            canonical_name,
        )

    # Resolve existing entity
    try:
        candidates = await entity_resolve(pool, canonical_name, entity_type=entity_type)
        if candidates:
            return candidates[0]["entity_id"]
    except Exception:
        logger.exception(
            "_ensure_entity: entity_resolve also failed for %r",
            canonical_name,
        )

    raise RuntimeError(
        f"Cannot resolve or create entity for canonical_name={canonical_name!r}. "
        "Entity creation is mandatory — contacts must always link to an entity."
    )


# Profile fields mirrored from a contact onto its linked entity's
# metadata['profile'] sub-document (Phase 7.4b EXPAND step, bu-0mb6j).  Shape
# mirrors the Google Contacts backfill (src/butlers/modules/contacts/backfill.py)
# and the rel_031 migration so entity-side readers see a consistent profile.
_PROFILE_FIELDS = (
    "first_name",
    "last_name",
    "company",
    "job_title",
    "gender",
    "pronouns",
    "avatar_url",
)


def _profile_from_row(row: Any) -> dict[str, Any]:
    """Extract the CRM profile sub-document from a contact row/dict.

    Only keys whose value is non-NULL are returned so the mirror is additive
    (it never clobbers an existing entity profile key with NULL).
    """
    d = dict(row)
    return {f: d[f] for f in _PROFILE_FIELDS if d.get(f) is not None}


async def _mirror_contact_profile_to_entity(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    *,
    profile: dict[str, Any],
    stay_in_touch_days: int | None = None,
    listed: bool | None = None,
) -> None:
    """Mirror a contact's profile data onto its linked ``public.entities`` row.

    EXPAND step (bu-0mb6j) of the ``public.contacts`` retirement: ``contacts``
    remains the canonical store (still dual-written + read), and this projects
    the same profile data onto ``public.entities`` so the reader re-points
    (separate beads) have an authoritative entity-side mirror to read from.

    Writes:
      - ``entities.metadata['profile'].*`` — additive merge (new non-NULL keys
        win, existing keys preserved).
      - ``entities.stay_in_touch_days`` — when provided AND the column exists.
      - ``entities.listed`` — when provided.

    Best-effort: never raises.  A mirror failure must not block the canonical
    ``public.contacts`` write that already succeeded.  ``name -> canonical_name``
    and ``nickname -> aliases`` are mirrored separately by ``_ensure_entity``
    (create) and ``_sync_entity_update`` (update); they are not repeated here.
    """
    profile_clean = {k: v for k, v in profile.items() if v is not None}
    try:
        await pool.execute(
            """
            UPDATE public.entities
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object(
                                'profile',
                                COALESCE(metadata -> 'profile', '{}'::jsonb) || $2::jsonb
                              ),
                updated_at = now()
            WHERE id = $1
            """,
            entity_id,
            # Pass the dict directly: relationship pools register a jsonb codec
            # (entity_create writes dicts to jsonb), so json.dumps() here would
            # double-encode into a jsonb *string* and break the `||` merge.
            profile_clean,
        )
        if listed is not None:
            await pool.execute(
                "UPDATE public.entities SET listed = $2, updated_at = now() WHERE id = $1",
                entity_id,
                listed,
            )
    except asyncpg.PostgresError:
        logger.warning(
            "mirror profile -> entity %s failed; entity profile may be stale",
            entity_id,
            exc_info=True,
        )
        return

    if stay_in_touch_days is not None:
        try:
            await pool.execute(
                "UPDATE public.entities SET stay_in_touch_days = $2, updated_at = now() "
                "WHERE id = $1",
                entity_id,
                stay_in_touch_days,
            )
        except asyncpg.UndefinedColumnError:
            # entities.stay_in_touch_days predates rel_031 in this schema; the
            # profile mirror above already landed — degrade gracefully.
            pass
        except asyncpg.PostgresError:
            logger.warning(
                "mirror stay_in_touch_days -> entity %s failed",
                entity_id,
                exc_info=True,
            )


async def _sync_entity_update(
    pool: asyncpg.Pool,
    entity_id: str,
    first_name: str | None,
    last_name: str | None,
    nickname: str | None,
) -> None:
    """Update the memory entity canonical name and aliases. Best-effort."""
    from butlers.modules.memory.tools.entities import entity_update

    canonical_name = _build_canonical_name(first_name, last_name)
    aliases = _build_entity_aliases(first_name, last_name, nickname)
    try:
        await entity_update(
            pool,
            entity_id,
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
) -> dict[str, Any]:
    """Create a contact linked to a memory entity.

    Every contact MUST resolve to an entity (the entity may be 'unidentified').
    The entity is resolved-or-created *before* the contact row is inserted so
    that ``entity_id`` is set on the INSERT — never NULL.

    ``memory_pool`` is accepted for backward compatibility with callers that
    supply a separate pool for the memory schema; when omitted, ``pool`` is
    used directly (public.entities is accessible from any pool in the same DB).
    """
    if (first_name is None and last_name is None) and name:
        first_name, last_name = _split_name(name)
    merged_meta = metadata if metadata is not None else (details or {})
    composed_name = name or " ".join(p for p in [first_name, last_name] if p).strip()
    if not composed_name:
        composed_name = nickname or company or "Unknown"

    cols = await table_columns(pool, "contacts")

    # --- Entity creation (mandatory) ---
    entity_pool = memory_pool or pool
    entity_uuid: uuid.UUID | None = None
    if "entity_id" in cols:
        entity_id_str = await _ensure_entity(
            entity_pool,
            first_name=first_name,
            last_name=last_name,
            nickname=nickname,
            entity_type=_infer_entity_type(first_name, last_name, company),
        )
        entity_uuid = uuid.UUID(entity_id_str)

    # --- Build payload (entity_id included in INSERT) ---
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
    if entity_uuid is not None and "entity_id" in cols:
        payload["entity_id"] = entity_uuid

    if not payload:
        raise ValueError("Cannot create contact: no writable columns found (schema mismatch?)")

    json_cols = {"details", "metadata"} & set(payload)
    insert_cols = list(payload.keys())
    placeholders = []
    values: list[Any] = []
    for idx, col in enumerate(insert_cols, start=1):
        if col in json_cols:
            placeholders.append(f"${idx}")
            values.append(payload[col] or {})
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

    # Write-path cut-over (bu-k9ylx): contact_create writes only the contact
    # RECORD (public.contacts), which remains writable.  No channel facts exist
    # at creation time; they are asserted later via channel_add ->
    # relationship_assert_fact().  The former dual-write shim call here was a
    # structural no-op and has been removed.

    # Populate contact_entity_map (rel_029 / bu-ozpyl) so that _entity_resolve
    # can resolve entity_id without querying public.contacts (contacts-schema
    # retirement, bu-oluyt Phase 7).  Best-effort: if the migration has not yet
    # run, catch UndefinedTableError and continue rather than failing the create.
    if entity_uuid is not None:
        try:
            await pool.execute(
                """
                INSERT INTO contact_entity_map (contact_id, entity_id)
                VALUES ($1, $2)
                ON CONFLICT (contact_id) DO NOTHING
                """,
                row["id"],
                entity_uuid,
            )
        except asyncpg.UndefinedTableError:
            # rel_029 migration has not run yet; not fatal.
            pass
        except asyncpg.PostgresError:
            logger.warning(
                "contact_create: failed to populate contact_entity_map for %s",
                result["id"],
                exc_info=True,
            )

    # EXPAND step (bu-0mb6j): mirror the contact's profile + stay_in_touch_days +
    # listed onto the linked entity so entity-side readers have an authoritative
    # copy.  public.contacts above remains the canonical store (dual-write).
    if entity_uuid is not None:
        await _mirror_contact_profile_to_entity(
            entity_pool,
            entity_uuid,
            profile=_profile_from_row(row),
            stay_in_touch_days=dict(row).get("stay_in_touch_days"),
            listed=dict(row).get("listed"),
        )

    return result


async def contact_update(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    memory_pool: asyncpg.Pool | None = None,
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
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )

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
        "stay_in_touch_days",
    ):
        if col in fields and col in cols:
            to_update[col] = fields[col]

    # entity_id — UUID column; requires explicit coercion from str if passed as text.
    if "entity_id" in fields and "entity_id" in cols:
        raw_eid = fields["entity_id"]
        if raw_eid is not None and not isinstance(raw_eid, uuid.UUID):
            raw_eid = uuid.UUID(str(raw_eid))
        to_update["entity_id"] = raw_eid

    if not to_update:
        raise ValueError(
            "At least one field must be provided for update. "
            "Valid fields: first_name, last_name, nickname, company, job_title, "
            "gender, pronouns, avatar_url, metadata, listed, entity_id."
        )

    json_cols = {"details", "metadata"} & set(to_update)
    set_clauses = []
    params: list[Any] = [contact_id]
    idx = 2
    for col, val in to_update.items():
        if col not in cols:
            continue
        if col in json_cols:
            set_clauses.append(f"{col} = ${idx}")
            params.append(val or {})
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

    # Sync entity name fields if any name field changed (best-effort)
    _name_fields = {"name", "first_name", "last_name", "nickname"}
    if _name_fields & set(fields) and "entity_id" in cols:
        existing_dict = dict(existing) if not isinstance(existing, dict) else existing
        entity_id_val = existing_dict.get("entity_id")
        entity_pool = memory_pool or pool
        if entity_id_val is not None:
            await _sync_entity_update(
                entity_pool,
                entity_id=str(entity_id_val),
                first_name=result.get("first_name"),
                last_name=result.get("last_name"),
                nickname=result.get("nickname"),
            )

    # Sync contact_entity_map when entity_id is being changed (rel_029 / bu-0tg4s).
    # contact_create populates this map at creation time; contact_update must keep it
    # in sync when entity_id is explicitly reassigned or cleared.  Best-effort: catch
    # UndefinedTableError so the update degrades gracefully if rel_029 has not run.
    if "entity_id" in to_update:
        try:
            if to_update["entity_id"] is not None:
                await pool.execute(
                    """
                    INSERT INTO contact_entity_map (contact_id, entity_id)
                    VALUES ($1, $2)
                    ON CONFLICT (contact_id) DO UPDATE SET entity_id = EXCLUDED.entity_id
                    """,
                    contact_id,
                    to_update["entity_id"],
                )
            else:
                await pool.execute(
                    "DELETE FROM contact_entity_map WHERE contact_id = $1",
                    contact_id,
                )
        except asyncpg.UndefinedTableError:
            # rel_029 migration has not run yet; not fatal.
            pass
        except asyncpg.PostgresError:
            logger.warning(
                "contact_update: failed to sync contact_entity_map for %s",
                contact_id,
                exc_info=True,
            )

    # EXPAND step (bu-0mb6j): mirror the (post-update) profile + stay_in_touch_days
    # + listed onto the linked entity.  Mirror to the *effective* entity — the
    # newly-assigned one when entity_id was reassigned in this update, else the
    # contact's existing entity.  public.contacts above remains canonical.
    existing_dict_for_mirror = dict(existing) if not isinstance(existing, dict) else existing
    effective_entity_id = to_update.get("entity_id") or existing_dict_for_mirror.get("entity_id")
    if effective_entity_id is not None and "entity_id" in cols:
        mirror_pool = memory_pool or pool
        row_dict = dict(row)
        await _mirror_contact_profile_to_entity(
            mirror_pool,
            effective_entity_id,
            profile=_profile_from_row(row_dict),
            stay_in_touch_days=row_dict.get("stay_in_touch_days"),
            listed=row_dict.get("listed"),
        )

    # Write-path cut-over (bu-k9ylx): contact_update modifies only the contact
    # RECORD (public.contacts) and the linked entity's name — no channel facts
    # change.  The former dual-write shim call here was a no-op and is removed.

    return result


async def contact_get(
    pool: asyncpg.Pool, contact_id: uuid.UUID, *, allow_missing: bool = False
) -> dict[str, Any] | None:
    """Get a contact by ID, enriched with Dunbar tier and decay score.

    For archived contacts, returns last known dunbar_tier and dunbar_score with
    dunbar_stale=True to indicate the data is from before archival.
    """
    row = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id)
    if row is None:
        if allow_missing:
            return None
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    result = _parse_contact(row)
    try:
        from butlers.tools.relationship.dunbar import get_contact_dunbar_with_stale_flag

        dunbar = await get_contact_dunbar_with_stale_flag(pool, contact_id)
        result.update(dunbar)
    except Exception:
        logger.exception("Failed to compute Dunbar fields for contact %s", contact_id)
        result.setdefault("dunbar_tier", 1500)
        result.setdefault("dunbar_score", 0.0)
        result.setdefault("dunbar_tier_override", False)
        result.setdefault("dunbar_stale", True)
    return result


async def contact_search(
    pool: asyncpg.Pool, query: str, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Search contacts by legacy and spec fields, enriched with Dunbar tier/score."""
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

    if not conditions:
        return []

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
    contacts = [_parse_contact(row) for row in rows]

    # Enrich each contact with Dunbar tier, score, and override (batch via compute_tier_ranking)
    try:
        from butlers.tools.relationship.dunbar import compute_tier_ranking

        all_dunbar = await compute_tier_ranking(pool)
        dunbar_by_cid: dict[str, Any] = {str(entry["contact_id"]): entry for entry in all_dunbar}
        for contact in contacts:
            cid = str(contact["id"])
            info = dunbar_by_cid.get(cid, {})
            contact["dunbar_tier"] = info.get("dunbar_tier", 1500)
            contact["dunbar_score"] = info.get("dunbar_score", 0.0)
            contact["dunbar_tier_override"] = info.get("dunbar_tier_override", False)
    except Exception:
        logger.exception("Failed to enrich contacts with Dunbar scores")
        for contact in contacts:
            contact.setdefault("dunbar_tier", 1500)
            contact.setdefault("dunbar_score", 0.0)
            contact.setdefault("dunbar_tier_override", False)

    return contacts


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
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    result = _parse_contact(row)

    # Phase-7 retirement (bu-5nlh6): propagate archive flag to the linked entity
    # so entity-anchored searches (channel_search, contact_search_by_label) exclude
    # archived contacts via entities.listed even after public.contacts is dropped
    # (bu-y6o7q).  entity_id is read from the contact row first; if absent (legacy
    # row without the column), fall back to contact_entity_map via
    # resolve_contact_entity_id.  Best-effort: never let an entity update failure
    # block the contact archive.
    entity_id: uuid.UUID | None = None
    try:
        entity_id = result.get("entity_id")
        if entity_id is None:
            from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id

            entity_id = await resolve_contact_entity_id(pool, contact_id)
        if entity_id is not None:
            await pool.execute(
                "UPDATE public.entities SET listed = false WHERE id = $1",
                entity_id,
            )
    except (ValueError, asyncpg.PostgresError):
        logger.warning(
            "contact_archive: failed to set entities.listed=false for contact_id=%s"
            " (entity_id=%s); archived contact may still appear in entity-anchored searches",
            contact_id,
            entity_id,
            exc_info=True,
        )

    # Write-path cut-over (bu-k9ylx): contact_archive soft-removes the contact
    # RECORD (public.contacts).  Channel-fact retraction now flows through the
    # relationship butler's triple retraction path, not a contact_info shim; the
    # former dual-write retraction call here has been removed.

    return result


async def contact_merge(
    pool: asyncpg.Pool,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    memory_pool: asyncpg.Pool | None = None,
    chronicler_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Merge source contact into target contact.

    The target contact survives; the source is archived. All related records
    (notes, interactions, reminders, etc.) are re-pointed to the target.

    When ``memory_pool`` is provided and both contacts have linked entities,
    the source entity is merged into the target entity via entity_merge so that
    memory facts consolidate under the surviving contact's entity.

    When ``chronicler_pool`` is provided, episode_entities rows in the chronicler
    schema are re-pointed from the source entity to the target entity as part of
    the entity_merge call. Pass the chronicler butler's pool at call sites that
    have access to it; callers without access may pass None (no-op semantics —
    episode_entities repointing is silently skipped).

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
        raise ValueError(
            f"Source contact {source_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    target = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", target_id)
    if target is None:
        raise ValueError(
            f"Target contact {target_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )

    cols = await table_columns(pool, "contacts")

    # Tables that reference contacts — re-point source -> target
    _child_tables = [
        ("notes", "contact_id"),
        ("interactions", "contact_id"),
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

    # Merge memory entities (best-effort)
    if "entity_id" in cols:
        src_entity_id = dict(source).get("entity_id")
        tgt_entity_id = dict(target).get("entity_id")
        entity_pool = memory_pool or pool
        if src_entity_id is not None and tgt_entity_id is not None:
            import uuid as _uuid

            from butlers.modules.memory.tools.entities import entity_merge
            from butlers.tools.relationship.merge_review import (
                compute_merge_evidence,
                write_merge_review,
            )

            # Compute the merge-review audit evidence BEFORE entity_merge + the
            # entity_facts re-pointing below mutate rows, so the snapshot reflects
            # the pre-merge state (spec: relationship-merge-review — every merge
            # leaves a merge_reviews row "regardless of entry path"). Best-effort:
            # never block the (already-committed) contact merge on an audit failure.
            merge_evidence = None
            try:
                merge_evidence = await compute_merge_evidence(
                    pool,
                    _uuid.UUID(str(src_entity_id)),
                    _uuid.UUID(str(tgt_entity_id)),
                )
            except Exception:
                logger.warning(
                    "contact_merge: failed to compute merge-review evidence "
                    "(source=%s target=%s) — audit row will be skipped",
                    src_entity_id,
                    tgt_entity_id,
                    exc_info=True,
                )

            try:
                await entity_merge(
                    entity_pool,
                    str(src_entity_id),
                    str(tgt_entity_id),
                    chronicler_pool=chronicler_pool,
                    # chronicler_pool=None is the no-op path: episode_entities
                    # repointing is silently skipped when caller has no pool.
                )
            except Exception:
                logger.exception(
                    "entity_merge failed for source=%s target=%s; continuing",
                    src_entity_id,
                    tgt_entity_id,
                )

            # Re-point relationship.entity_facts (bu-9z7nd).
            # entity_merge re-points facts in the memory schema (and any extra_pools).
            # relationship.entity_facts uses a different column layout (subject vs
            # entity_id) so generic _repoint_facts_on_pool cannot reach it.  We
            # handle it here with an explicit inline re-pointing against the
            # relationship butler's own pool, which owns the table.
            #
            # Conflict resolution: if the target already has an active triple with the
            # same (subject, predicate, object), the partial-unique index
            # uq_ef_spo_active enforces uniqueness — we must supersede the source
            # row rather than re-pointing it.  Higher conf wins; lower is superseded.
            # This mirrors entity_merge's own conflict-resolution strategy.
            try:
                src_uuid = _uuid.UUID(str(src_entity_id))
                tgt_uuid = _uuid.UUID(str(tgt_entity_id))
                async with pool.acquire() as _conn:
                    async with _conn.transaction():
                        src_ef_rows = await _conn.fetch(
                            "SELECT id, predicate, object, conf FROM relationship.entity_facts "
                            "WHERE subject = $1 AND validity = 'active'",
                            src_uuid,
                        )
                        for ef in src_ef_rows:
                            conflict = await _conn.fetchrow(
                                "SELECT id, conf FROM relationship.entity_facts "
                                "WHERE subject = $1 AND predicate = $2 "
                                "AND object = $3 AND validity = 'active'",
                                tgt_uuid,
                                ef["predicate"],
                                ef["object"],
                            )
                            if conflict is None:
                                # No conflict — re-point subject to target.
                                await _conn.execute(
                                    "UPDATE relationship.entity_facts "
                                    "SET subject = $1, updated_at = now() WHERE id = $2",
                                    tgt_uuid,
                                    ef["id"],
                                )
                            else:
                                # Conflict — higher confidence wins; supersede the loser.
                                if ef["conf"] > conflict["conf"]:
                                    # Source wins: supersede conflict, then re-point source.
                                    await _conn.execute(
                                        "UPDATE relationship.entity_facts "
                                        "SET validity = 'superseded', updated_at = now() "
                                        "WHERE id = $1",
                                        conflict["id"],
                                    )
                                    await _conn.execute(
                                        "UPDATE relationship.entity_facts "
                                        "SET subject = $1, updated_at = now() WHERE id = $2",
                                        tgt_uuid,
                                        ef["id"],
                                    )
                                else:
                                    # Target wins: supersede source row.
                                    await _conn.execute(
                                        "UPDATE relationship.entity_facts "
                                        "SET validity = 'superseded', updated_at = now() "
                                        "WHERE id = $1",
                                        ef["id"],
                                    )
            except asyncpg.PostgresError:  # noqa: BLE001 — best-effort; never block the legacy commit
                logger.warning(
                    "contact_merge: entity_facts subject re-pointing failed "
                    "(source=%s target=%s) — swallowed",
                    src_entity_id,
                    tgt_entity_id,
                    exc_info=True,
                )

            # Object-side re-pointing (bu-igcxb).
            # Re-point rows where object = src_entity_id::text AND object_kind = 'entity'.
            # These represent relational predicates (knows, family-of, etc.) where the
            # source entity appears as the *object* of the triple.  entity_merge cannot
            # reach these because it operates on memory.facts, not relationship.entity_facts.
            #
            # Conflict resolution mirrors the subject-side block: if the target already has
            # an active triple with the same (subject, predicate, object=target::text),
            # higher confidence wins; the loser is superseded.
            try:
                src_obj_str = str(src_entity_id)
                tgt_obj_str = str(tgt_entity_id)
                async with pool.acquire() as _obj_conn:
                    async with _obj_conn.transaction():
                        obj_ef_rows = await _obj_conn.fetch(
                            "SELECT id, subject, predicate, conf FROM relationship.entity_facts "
                            "WHERE object = $1 AND object_kind = 'entity' AND validity = 'active'",
                            src_obj_str,
                        )
                        for obj_ef in obj_ef_rows:
                            obj_conflict = await _obj_conn.fetchrow(
                                "SELECT id, conf FROM relationship.entity_facts "
                                "WHERE subject = $1 AND predicate = $2 "
                                "AND object = $3 "
                                "AND validity = 'active'",
                                obj_ef["subject"],
                                obj_ef["predicate"],
                                tgt_obj_str,
                            )
                            if obj_conflict is None:
                                # No conflict — re-point object to target entity.
                                await _obj_conn.execute(
                                    "UPDATE relationship.entity_facts "
                                    "SET object = $1, updated_at = now() WHERE id = $2",
                                    tgt_obj_str,
                                    obj_ef["id"],
                                )
                            else:
                                # Conflict — higher confidence wins; supersede the loser.
                                if obj_ef["conf"] > obj_conflict["conf"]:
                                    # Source wins: supersede conflict, then re-point source.
                                    await _obj_conn.execute(
                                        "UPDATE relationship.entity_facts "
                                        "SET validity = 'superseded', updated_at = now() "
                                        "WHERE id = $1",
                                        obj_conflict["id"],
                                    )
                                    await _obj_conn.execute(
                                        "UPDATE relationship.entity_facts "
                                        "SET object = $1, updated_at = now() WHERE id = $2",
                                        tgt_obj_str,
                                        obj_ef["id"],
                                    )
                                else:
                                    # Target wins: supersede source row.
                                    await _obj_conn.execute(
                                        "UPDATE relationship.entity_facts "
                                        "SET validity = 'superseded', updated_at = now() "
                                        "WHERE id = $1",
                                        obj_ef["id"],
                                    )
            except asyncpg.PostgresError:  # noqa: BLE001 — best-effort; never block the legacy commit
                logger.warning(
                    "contact_merge: entity_facts object re-pointing failed "
                    "(source=%s target=%s) — swallowed",
                    src_entity_id,
                    tgt_entity_id,
                    exc_info=True,
                )

            # Write the merge_reviews audit row regardless of entry path (spec:
            # relationship-merge-review). Best-effort: the contact + entity merge
            # above are already committed; an audit failure must not surface.
            if merge_evidence is not None:
                try:
                    await write_merge_review(
                        pool,
                        entity_a=_uuid.UUID(str(src_entity_id)),
                        entity_b=_uuid.UUID(str(tgt_entity_id)),
                        shared_facts=merge_evidence["shared"],
                        divergent_facts=merge_evidence["divergent"],
                        outcome="merged",
                    )
                except Exception:
                    logger.warning(
                        "contact_merge: failed to write merge_reviews audit row "
                        "(source=%s target=%s) — merge already committed",
                        src_entity_id,
                        tgt_entity_id,
                        exc_info=True,
                    )

    # Write-path cut-over (bu-k9ylx): the source entity's channel facts are
    # re-pointed to the target entity by the relationship.entity_facts
    # subject/object re-pointing blocks above (entity_merge + the inline
    # re-pointing). The former contact_info-snapshot retraction shim has been
    # removed — channel facts now live only in the triple store.

    # Fetch the updated target
    updated_row = await pool.fetchrow("SELECT * FROM contacts WHERE id = $1", target_id)
    return _parse_contact(updated_row)
