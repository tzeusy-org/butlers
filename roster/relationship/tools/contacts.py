"""Contact CRUD — create, update, get, search, and archive contacts.

Cutover (bu-irphu): the CRM contact record is no longer stored in
``public.contacts``.  A "contact" is now reconstructed entirely from two
entity-side stores:

- ``relationship.contact_entity_map`` — the ``(contact_id, entity_id)`` bridge
  (rel_029).  ``contact_id`` is a synthetic CRM id minted at create time.
- ``public.entities`` — the canonical record.  ``canonical_name`` supplies the
  display name; ``metadata['profile']`` holds the CRM profile fields
  (first_name/last_name/company/…); ``metadata['contact_metadata']`` holds the
  free-form CRM metadata; ``listed`` / ``stay_in_touch_days`` are entity columns.

``public.contacts`` is intentionally NEVER read or written here anymore (the
table itself is dropped by the separate guarded bead bu-y6o7q).  The other
relationship readers (resolve, channel, dunbar, vcard, jobs, …) were already
re-pointed onto these same entity-side stores in the preceding retirement steps.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, cast

import asyncpg

logger = logging.getLogger(__name__)


# Canonical projection of a CRM contact from the entity-side stores.  Unqualified
# ``contact_entity_map`` resolves via search_path (relationship in production,
# public in schema-less integration tests) — the same convention used by
# ``_entity_resolve.py``, ``channel.py`` and ``vcard.py``.
_CONTACT_SELECT = """
    SELECT
        m.contact_id          AS id,
        e.id                  AS entity_id,
        e.canonical_name      AS canonical_name,
        e.aliases             AS aliases,
        e.listed              AS listed,
        e.stay_in_touch_days  AS stay_in_touch_days,
        e.metadata            AS entity_metadata
    FROM contact_entity_map m
    JOIN public.entities e ON e.id = m.entity_id
    WHERE m.contact_id = $1
"""

# Leaner projection used by contact_merge (entity_id + profile is all it needs;
# avoids depending on entities.listed / stay_in_touch_days columns existing in
# every merge fixture).
_CONTACT_MERGE_SELECT = """
    SELECT
        m.contact_id      AS id,
        e.id              AS entity_id,
        e.canonical_name  AS canonical_name,
        e.metadata        AS entity_metadata
    FROM contact_entity_map m
    JOIN public.entities e ON e.id = m.entity_id
    WHERE m.contact_id = $1
"""


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


# Profile fields projected onto / read from ``entities.metadata['profile']``.
# Shape mirrors the Google Contacts backfill (src/butlers/modules/contacts/
# backfill.py) and the rel_031 migration so every entity-side reader sees a
# consistent profile.  ``nickname`` is included so it round-trips through the CRUD
# layer (it is also mirrored to ``entities.aliases`` by ``_ensure_entity``).
_PROFILE_FIELDS = (
    "first_name",
    "last_name",
    "company",
    "job_title",
    "gender",
    "pronouns",
    "avatar_url",
)

_PROFILE_WRITE_FIELDS = (*_PROFILE_FIELDS, "nickname")


def _profile_from_row(row: Any) -> dict[str, Any]:
    """Extract the CRM profile sub-document from a contact-shaped row/dict.

    Only keys whose value is non-NULL are returned so the mirror is additive
    (it never clobbers an existing entity profile key with NULL).
    """
    d = dict(row)
    return {f: d[f] for f in _PROFILE_FIELDS if d.get(f) is not None}


def _parse_contact(row: Any) -> dict[str, Any]:
    """Reconstruct a CRM-contact dict from an entity-side row.

    Defensive across every producer:
      - the CRUD canonical SELECT (id, entity_id, canonical_name, aliases,
        listed, stay_in_touch_days, entity_metadata),
      - channel/groups/labels readers (id, entity_id, name=canonical_name,
        metadata),
      - synthesised create/update rows and legacy/mocked contact-shaped rows
        (id, entity_id, first_name, …, metadata).

    Profile fields come from ``entities.metadata['profile']``; the free-form CRM
    payload from ``entities.metadata['contact_metadata']``; the display name from
    ``canonical_name`` (split into first/last when no profile name parts exist).
    """
    d = dict(row)

    raw_meta = d.get("entity_metadata")
    if raw_meta is None and "metadata" in d:
        raw_meta = d.get("metadata")
    meta = _parse_json_field(raw_meta)

    profile = meta.get("profile")
    profile = profile if isinstance(profile, dict) else {}

    contact_meta = meta.get("contact_metadata")
    if isinstance(contact_meta, dict):
        free_meta = contact_meta
    elif "profile" in meta or "contact_metadata" in meta:
        # An entity-metadata document that carries no explicit CRM payload.
        free_meta = {}
    else:
        # Legacy / mocked row whose ``metadata`` IS the free-form CRM payload.
        free_meta = meta

    canonical = str(d.get("canonical_name") or d.get("name") or "").strip()

    first_name = profile.get("first_name") or d.get("first_name")
    last_name = profile.get("last_name") or d.get("last_name")
    if not first_name and not last_name and canonical:
        parts = canonical.split(None, 1)
        first_name = parts[0] if parts else None
        last_name = parts[1] if len(parts) > 1 else None

    nickname = profile.get("nickname") or d.get("nickname")
    listed = d.get("listed")

    result: dict[str, Any] = {
        "id": d.get("id"),
        "entity_id": d.get("entity_id"),
        "canonical_name": canonical or None,
        "first_name": first_name or None,
        "last_name": last_name or None,
        "nickname": nickname or None,
        "company": profile.get("company") or d.get("company") or None,
        "job_title": profile.get("job_title") or d.get("job_title") or None,
        "gender": profile.get("gender") or d.get("gender") or None,
        "pronouns": profile.get("pronouns") or d.get("pronouns") or None,
        "avatar_url": profile.get("avatar_url") or d.get("avatar_url") or None,
        "stay_in_touch_days": d.get("stay_in_touch_days"),
        "listed": True if listed is None else listed,
        "metadata": free_meta,
        "details": free_meta,
    }
    result["name"] = _compose_name(result) or canonical or "Unknown"
    return result


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


def _build_profile_for_write(fields: dict[str, Any]) -> dict[str, Any]:
    """Collect the non-NULL CRM profile fields to project onto an entity."""
    return {f: fields[f] for f in _PROFILE_WRITE_FIELDS if fields.get(f) is not None}


async def _mirror_contact_profile_to_entity(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    *,
    profile: dict[str, Any],
    stay_in_touch_days: int | None = None,
    listed: bool | None = None,
) -> None:
    """Project a contact's profile data onto its linked ``public.entities`` row.

    Cutover (bu-irphu): ``public.entities`` is now the PRIMARY (sole) store for
    the CRM contact record — this is no longer a "mirror" of ``public.contacts``.

    Writes:
      - ``entities.metadata['profile'].*`` — additive merge (new non-NULL keys
        win, existing keys preserved).
      - ``entities.stay_in_touch_days`` — when provided AND the column exists.
      - ``entities.listed`` — when provided.

    Best-effort: never raises.  ``canonical_name`` / ``aliases`` are written
    separately by ``_ensure_entity`` (create) and ``_sync_entity_update`` (update).
    """
    profile_clean = {k: v for k, v in profile.items() if v is not None}
    try:
        if profile_clean:
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
            "write profile -> entity %s failed; entity profile may be stale",
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
            # profile write above already landed — degrade gracefully.
            pass
        except asyncpg.PostgresError:
            logger.warning(
                "write stay_in_touch_days -> entity %s failed",
                entity_id,
                exc_info=True,
            )


async def _write_contact_metadata_to_entity(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    contact_metadata: dict[str, Any] | None,
) -> None:
    """Persist the free-form CRM metadata under ``entities.metadata['contact_metadata']``.

    Kept in a dedicated sub-document so it never collides with the ``profile``
    sub-document or other entity metadata keys (``merged_into``, ``deleted_at``).
    Best-effort: never raises.
    """
    cm = contact_metadata if isinstance(contact_metadata, dict) else {}
    try:
        await pool.execute(
            """
            UPDATE public.entities
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                           || jsonb_build_object('contact_metadata', $2::jsonb),
                updated_at = now()
            WHERE id = $1
            """,
            entity_id,
            cm,
        )
    except asyncpg.PostgresError:
        logger.warning(
            "write contact_metadata -> entity %s failed",
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
    """Create a contact: resolve-or-create its entity, bridge it, write its profile.

    Cutover (bu-irphu): the contact record lives entirely on the entity side.
    This mints a synthetic ``contact_id``, resolves/creates the linked entity,
    writes the bridge row in ``contact_entity_map``, and projects the profile +
    free-form metadata onto ``public.entities``.  ``public.contacts`` is never
    written.

    ``memory_pool`` is accepted for backward compatibility with callers that
    supply a separate pool for the memory schema; when omitted, ``pool`` is
    used directly (public.entities is accessible from any pool in the same DB).
    """
    if (first_name is None and last_name is None) and name:
        first_name, last_name = _split_name(name)
    merged_meta = metadata if metadata is not None else (details or {})

    entity_pool = memory_pool or pool

    # --- Entity resolution (mandatory) ---
    entity_id_str = await _ensure_entity(
        entity_pool,
        first_name=first_name,
        last_name=last_name,
        nickname=nickname,
        entity_type=_infer_entity_type(first_name, last_name, company),
    )
    entity_uuid = uuid.UUID(entity_id_str)

    # --- Mint the synthetic CRM contact id ---
    contact_id = uuid.uuid4()

    # --- Bridge contact_id -> entity_id (rel_029) ---
    try:
        await pool.execute(
            """
            INSERT INTO contact_entity_map (contact_id, entity_id)
            VALUES ($1, $2)
            ON CONFLICT (contact_id) DO NOTHING
            """,
            contact_id,
            entity_uuid,
        )
    except asyncpg.UndefinedTableError:
        # rel_029 migration has not run yet; not fatal — the contact cannot be
        # re-read without the bridge, but the entity write below still lands.
        logger.warning(
            "contact_create: contact_entity_map missing; contact %s not bridged",
            contact_id,
        )
    except asyncpg.PostgresError:
        logger.warning(
            "contact_create: failed to populate contact_entity_map for %s",
            contact_id,
            exc_info=True,
        )

    # --- Project the profile + free-form metadata onto the entity (PRIMARY write) ---
    profile = _build_profile_for_write(
        {
            "first_name": first_name,
            "last_name": last_name,
            "nickname": nickname,
            "company": company,
            "job_title": job_title,
            "gender": gender,
            "pronouns": pronouns,
            "avatar_url": avatar_url,
        }
    )
    await _mirror_contact_profile_to_entity(
        entity_pool,
        entity_uuid,
        profile=profile,
        listed=listed,
    )
    await _write_contact_metadata_to_entity(entity_pool, entity_uuid, merged_meta)

    # Build the return dict directly from known inputs (avoids a re-read and any
    # dependency on entities.listed / stay_in_touch_days columns existing).
    synth = {
        "id": contact_id,
        "entity_id": entity_uuid,
        "canonical_name": _build_canonical_name(first_name, last_name),
        "listed": True if listed is None else listed,
        "stay_in_touch_days": None,
        "entity_metadata": {"profile": profile, "contact_metadata": merged_meta},
    }
    return _parse_contact(synth)


async def contact_update(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    memory_pool: asyncpg.Pool | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Update a contact's fields on the entity-side stores.

    Security contract: ``roles`` is stripped from ``fields`` before any write.
    Runtime LLM instances must never modify roles; that is a privileged operation
    reserved for the identity layer (owner bootstrap, dashboard PATCH endpoint).

    Profile fields, free-form metadata, ``listed`` and ``stay_in_touch_days`` are
    written onto the linked ``public.entities`` row; ``entity_id`` reassignment is
    reflected in ``contact_entity_map``; name changes are synced to the entity
    canonical_name/aliases.  ``public.contacts`` is never written.
    """
    # Strip roles — runtime instances must never modify roles.
    fields.pop("roles", None)

    row = await pool.fetchrow(_CONTACT_SELECT, contact_id)
    if row is None:
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    current = _parse_contact(row)
    current_entity_id = row["entity_id"]

    # Backward-compatible inputs: name -> first/last; details -> metadata.
    if "name" in fields:
        first, last = _split_name(fields["name"])
        fields.setdefault("first_name", first)
        fields.setdefault("last_name", last)
    if "details" in fields and "metadata" not in fields:
        fields["metadata"] = fields["details"]

    # entity_id — UUID column; coerce from text if passed as str.
    reassign_entity = "entity_id" in fields
    if reassign_entity:
        raw_eid = fields["entity_id"]
        if raw_eid is not None and not isinstance(raw_eid, uuid.UUID):
            raw_eid = uuid.UUID(str(raw_eid))
        fields["entity_id"] = raw_eid

    recognized = set(_PROFILE_WRITE_FIELDS) | {
        "name",
        "details",
        "metadata",
        "listed",
        "stay_in_touch_days",
        "entity_id",
    }
    if not (recognized & set(fields)):
        raise ValueError(
            "At least one field must be provided for update. "
            "Valid fields: first_name, last_name, nickname, company, job_title, "
            "gender, pronouns, avatar_url, metadata, listed, stay_in_touch_days, "
            "entity_id."
        )

    # The entity the writes target: the newly-assigned one when reassigned in this
    # update, else the contact's existing entity.
    effective_entity_id = fields["entity_id"] if reassign_entity else current_entity_id

    # --- Sync contact_entity_map on entity_id reassignment (rel_029 / bu-0tg4s) ---
    if reassign_entity:
        try:
            if fields["entity_id"] is not None:
                await pool.execute(
                    """
                    INSERT INTO contact_entity_map (contact_id, entity_id)
                    VALUES ($1, $2)
                    ON CONFLICT (contact_id) DO UPDATE SET entity_id = EXCLUDED.entity_id
                    """,
                    contact_id,
                    fields["entity_id"],
                )
            else:
                await pool.execute(
                    "DELETE FROM contact_entity_map WHERE contact_id = $1",
                    contact_id,
                )
        except asyncpg.UndefinedTableError:
            pass
        except asyncpg.PostgresError:
            logger.warning(
                "contact_update: failed to sync contact_entity_map for %s",
                contact_id,
                exc_info=True,
            )

    # Build the return dict by overlaying the applied updates onto the current one.
    result = dict(current)
    for key in (*_PROFILE_WRITE_FIELDS, "listed", "stay_in_touch_days"):
        if key in fields:
            result[key] = fields[key]
    if "metadata" in fields:
        result["metadata"] = fields["metadata"]
        result["details"] = fields["metadata"]
    if reassign_entity:
        result["entity_id"] = fields["entity_id"]
    result["name"] = _compose_name(result)

    # --- Project the updates onto the effective entity ---
    if effective_entity_id is not None:
        mirror_pool = memory_pool or pool
        profile = _build_profile_for_write(fields)
        await _mirror_contact_profile_to_entity(
            mirror_pool,
            effective_entity_id,
            profile=profile,
            stay_in_touch_days=fields.get("stay_in_touch_days"),
            listed=fields.get("listed"),
        )
        if "metadata" in fields:
            await _write_contact_metadata_to_entity(
                mirror_pool, effective_entity_id, fields["metadata"]
            )
        # Sync canonical_name / aliases when any name field changed.
        if {"name", "first_name", "last_name", "nickname"} & set(fields):
            await _sync_entity_update(
                mirror_pool,
                entity_id=str(effective_entity_id),
                first_name=result.get("first_name"),
                last_name=result.get("last_name"),
                nickname=result.get("nickname"),
            )

    return result


async def contact_get(
    pool: asyncpg.Pool, contact_id: uuid.UUID, *, allow_missing: bool = False
) -> dict[str, Any] | None:
    """Get a contact by ID, enriched with Dunbar tier and decay score.

    For archived contacts, returns last known dunbar_tier and dunbar_score with
    dunbar_stale=True to indicate the data is from before archival.
    """
    row = await pool.fetchrow(_CONTACT_SELECT, contact_id)
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
    """Search listed contacts by name/profile/metadata, enriched with Dunbar tier/score."""
    rows = await pool.fetch(
        """
        SELECT
            m.contact_id          AS id,
            e.id                  AS entity_id,
            e.canonical_name      AS canonical_name,
            e.aliases             AS aliases,
            e.listed              AS listed,
            e.stay_in_touch_days  AS stay_in_touch_days,
            e.metadata            AS entity_metadata
        FROM contact_entity_map m
        JOIN public.entities e ON e.id = m.entity_id
        WHERE e.listed = true
          AND (
            e.canonical_name ILIKE '%' || $1 || '%'
            OR (e.metadata -> 'profile' ->> 'first_name') ILIKE '%' || $1 || '%'
            OR (e.metadata -> 'profile' ->> 'last_name') ILIKE '%' || $1 || '%'
            OR (e.metadata -> 'profile' ->> 'nickname') ILIKE '%' || $1 || '%'
            OR (e.metadata -> 'profile' ->> 'company') ILIKE '%' || $1 || '%'
            OR (e.metadata -> 'contact_metadata')::text ILIKE '%' || $1 || '%'
            OR array_to_string(e.aliases, ' ') ILIKE '%' || $1 || '%'
          )
        ORDER BY e.canonical_name
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
    """Archive a contact by delisting its linked entity (entities.listed = false)."""
    row = await pool.fetchrow(_CONTACT_SELECT, contact_id)
    if row is None:
        raise ValueError(
            f"Contact {contact_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    entity_id = row["entity_id"]

    # Delist the linked entity so entity-anchored searches (channel_search,
    # contact_search, contact_search_by_label — all filter on e.listed = true)
    # exclude the archived contact (bu-5nlh6).
    await pool.execute(
        "UPDATE public.entities SET listed = false, updated_at = now() WHERE id = $1",
        entity_id,
    )

    refreshed = await pool.fetchrow(_CONTACT_SELECT, contact_id)
    return _parse_contact(refreshed if refreshed is not None else row)


# Tables that reference contacts: re-pointed source -> target. Optional per
# schema variant; a missing table or column is skipped inside a savepoint so it
# never aborts the merge transaction.
_CONTACT_CHILD_TABLES = (
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
)


def _removed_entity_refusal(contact_id: uuid.UUID) -> str:
    return (
        f"Contact {contact_id} cannot be merged because its entity has been removed "
        "(deleted, forgotten or merged away). Nothing was changed, and the removed entity "
        "stays removed."
    )


async def _execute_if_present(conn: asyncpg.Connection, query: str, *args: Any) -> None:
    """Run one optional-table statement in a savepoint; skip a missing table/column."""
    try:
        async with conn.transaction():
            await conn.execute(query, *args)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        pass  # not present in this schema variant


async def contact_merge(
    pool: asyncpg.Pool,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> dict[str, Any]:
    """Merge source contact into target contact.

    The target contact survives; the source is collapsed away. All related child
    records (notes, interactions, reminders, etc.) are re-pointed to the target,
    the source ``contact_entity_map`` row is removed, and the surviving entity's
    profile is reconciled with the source profile.

    Every write happens in ONE transaction: the merge either commits whole or
    writes nothing. When the contacts have different linked entities, that
    transaction starts with the merge authority (bu-dhloi1.51). It is the only
    writer of the entities' facts, so its locks, its refusals, its graph edge repair
    and its audit row all cover them. Two contacts of one entity collapse without
    touching it.

    After that commit the merge's rebind receipt settles. That is best-effort: the
    identity is already durable, and the rebind ledger recovers a missed settlement.

    Returns:
        The updated target contact dict.

    Raises:
        ValueError: If source or target contact not found, IDs are identical, or
            either contact's entity has been removed. Nothing is written.
        EntityMergeError: the merge authority refused the entities for another
            reason (for example two occurrences would collide). Nothing is written.
    """
    from .entity_merge import (
        SourceEntityTombstonedError,
        TargetEntityTombstonedError,
        merge_entity_pair_in_transaction,
        settle_entity_merge,
    )

    if source_id == target_id:
        raise ValueError("source_id and target_id must be different.")

    source = await pool.fetchrow(_CONTACT_MERGE_SELECT, source_id)
    if source is None:
        raise ValueError(
            f"Source contact {source_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )
    target = await pool.fetchrow(_CONTACT_MERGE_SELECT, target_id)
    if target is None:
        raise ValueError(
            f"Target contact {target_id} not found. "
            "Use contact_search(query=<name>) to find the correct contact ID."
        )

    src_entity_id = dict(source).get("entity_id")
    tgt_entity_id = dict(target).get("entity_id")
    src_profile = _parse_json_field(dict(source).get("entity_metadata")).get("profile")
    src_profile = src_profile if isinstance(src_profile, dict) else {}
    entities = (
        (uuid.UUID(str(src_entity_id)), uuid.UUID(str(tgt_entity_id)))
        if src_entity_id is not None
        and tgt_entity_id is not None
        and str(src_entity_id) != str(tgt_entity_id)
        else None
    )
    merge = None

    # One transaction for every write. A refused entity merge or any database
    # error rolls the whole merge back.
    async with pool.acquire() as conn:
        async with conn.transaction():
            # First, so its entity, candidate and fact locks and its refusals come
            # before any contact write.
            if entities is not None:
                try:
                    merge = await merge_entity_pair_in_transaction(
                        cast(asyncpg.Connection, conn),
                        source_entity_id=entities[0],
                        target_entity_id=entities[1],
                    )
                except SourceEntityTombstonedError as exc:
                    raise ValueError(_removed_entity_refusal(source_id)) from exc
                except TargetEntityTombstonedError as exc:
                    raise ValueError(_removed_entity_refusal(target_id)) from exc

            for table, fk_col in _CONTACT_CHILD_TABLES:
                await _execute_if_present(
                    conn,
                    f"UPDATE {table} SET {fk_col} = $1 WHERE {fk_col} = $2",  # noqa: S608
                    target_id,
                    source_id,
                )

            # Collapse the source contact onto the surviving entity: remove the
            # now-redundant source bridge row (children already re-pointed above).
            await _execute_if_present(
                conn, "DELETE FROM contact_entity_map WHERE contact_id = $1", source_id
            )

            # Reconcile the surviving entity's profile with the source profile
            # (additive — existing target keys win, new source keys fill gaps).
            if tgt_entity_id is not None and src_profile:
                await conn.execute(
                    """
                    UPDATE public.entities
                    SET metadata = COALESCE(metadata, '{}'::jsonb)
                                   || jsonb_build_object(
                                        'profile',
                                        $2::jsonb
                                        || COALESCE(metadata -> 'profile', '{}'::jsonb)
                                      ),
                        updated_at = now()
                    WHERE id = $1
                    """,
                    tgt_entity_id,
                    src_profile,
                )

    # After commit: settle the rebind receipt (the ledger row committed above
    # recovers a failure).
    if merge is not None:
        try:
            settled = await settle_entity_merge(pool, merge)
        except Exception:
            logger.exception(
                "contact_merge: settling the committed entity merge failed "
                "(source=%s target=%s); the rebind ledger recovers it",
                src_entity_id,
                tgt_entity_id,
            )
        else:
            if settled.failed_schemas:
                logger.warning(
                    "contact_merge: entity rebind failed for schemas %s "
                    "(source=%s target=%s); that daemon retries its failed receipt",
                    ", ".join(settled.failed_schemas),
                    src_entity_id,
                    tgt_entity_id,
                )

    # Fetch the updated target
    updated_row = await pool.fetchrow(_CONTACT_MERGE_SELECT, target_id)
    return _parse_contact(updated_row if updated_row is not None else target)
