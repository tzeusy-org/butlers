"""CRM backfill pipeline for synced canonical contacts.

Implements spec §7: upsert identity resolution, table mapping, and conflict policy
for contacts backfilled from the sync engine.

Three main classes:

- ContactBackfillResolver  – identity matching pipeline (§7.1)
- ContactBackfillWriter    – table mapping and upsert logic (§7.2, §7.3)
- ContactBackfillEngine    – orchestrates resolver → writer (§7.4)

Wire as the apply_contact callback in ContactsSyncEngine construction during on_startup.
Provenance tracked in entities.metadata JSONB under 'sources.contacts.{provider}.{field}'.

Migration note (bu-tzyuh): local_entity_id / entity UUID replaces local_contact_id
throughout. public.contacts is not written or read by resolver/writer; all identity
matching routes through public.entities and relationship.entity_facts.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.modules.contacts.sync import CanonicalContact

logger = logging.getLogger(__name__)

# Metadata namespace for provenance tracking
_PROVENANCE_NS = "sources.contacts"


def _provenance_key(provider: str, field: str) -> str:
    """Build a provenance key for a field under the sources namespace."""
    return f"{_PROVENANCE_NS}.{provider}.{field}"


def _parse_jsonb(value: Any) -> dict[str, Any]:
    """Parse a JSONB column value to a plain dict."""
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _deep_set(d: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set a value at a dotted key path in a nested dict, creating intermediates."""
    parts = dotted_key.split(".")
    current = d
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _deep_get(d: dict[str, Any], dotted_key: str) -> Any:
    """Get a value at a dotted key path in a nested dict. Returns None if missing."""
    parts = dotted_key.split(".")
    current: Any = d
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


class ContactBackfillResolver:
    """Identity matching pipeline for upsert resolution (§7.1).

    Resolution order:
    1. Existing contacts_source_links match (provider + account + external_id)
    2. Primary email exact match via relationship.entity_facts (has-email)
    3. Phone exact/e164 match via relationship.entity_facts (has-phone)
    4. Conservative name match (manual-review flag when ambiguous)

    After bu-tzyuh all matchers return a local_entity_id (entity UUID) directly;
    public.contacts is no longer joined or read.
    """

    def __init__(self, pool: asyncpg.Pool, *, provider: str, account_id: str) -> None:
        self._pool = pool
        self._provider = provider
        self._account_id = account_id

    async def resolve(self, contact: CanonicalContact) -> tuple[uuid.UUID | None, str]:
        """Resolve a canonical contact to a local entity ID.

        Returns
        -------
        (local_entity_id | None, match_strategy)
            match_strategy is one of:
            'source_link', 'email', 'phone', 'name', 'ambiguous_name', 'new'
        """
        # 1. Source link match
        local_id = await self._match_source_link(contact.external_id)
        if local_id is not None:
            return local_id, "source_link"

        # 2. Primary email match
        primary_email = next((e for e in contact.emails if e.primary), None)
        if primary_email is None and contact.emails:
            primary_email = contact.emails[0]
        if primary_email is not None:
            lookup = primary_email.normalized_value or primary_email.value
            local_id = await self._match_email(lookup)
            if local_id is not None:
                return local_id, "email"

        # 3. Phone match (exact or e164)
        for phone in contact.phones:
            lookup_value = phone.e164_normalized or phone.value
            local_id = await self._match_phone(lookup_value)
            if local_id is not None:
                return local_id, "phone"

        # 4. Conservative name match
        display = contact.display_name
        if display:
            matched = await self._match_name(display)
            if len(matched) == 1:
                return matched[0], "name"
            if len(matched) > 1:
                # Ambiguous: multiple candidates — do not auto-merge
                return None, "ambiguous_name"

        return None, "new"

    async def _match_source_link(self, external_id: str) -> uuid.UUID | None:
        row = await self._pool.fetchrow(
            """
            SELECT sl.local_entity_id FROM contacts_source_links sl
            WHERE sl.provider = $1 AND sl.account_id = $2 AND sl.external_contact_id = $3
              AND sl.deleted_at IS NULL AND sl.local_entity_id IS NOT NULL
            """,
            self._provider,
            self._account_id,
            external_id,
        )
        if row is None:
            return None
        local_entity_id = row["local_entity_id"]
        return uuid.UUID(str(local_entity_id)) if local_entity_id is not None else None

    async def _match_email(self, email_value: str) -> uuid.UUID | None:
        # Resolve via the triple store: a ``has-email`` fact's subject is the entity UUID.
        normalized = email_value.strip().lower()
        try:
            row = await self._pool.fetchrow(
                """
                SELECT ef.subject AS entity_id
                FROM relationship.entity_facts ef
                JOIN public.entities e ON e.id = ef.subject
                WHERE ef.predicate = 'has-email' AND ef.object_kind = 'literal'
                  AND ef.validity = 'active' AND lower(ef.object) = $1
                  AND e.entity_type = 'person'
                  AND (e.metadata->>'merged_into') IS NULL
                  AND (e.metadata->>'deleted_at') IS NULL
                ORDER BY ef.created_at ASC NULLS LAST LIMIT 1
                """,
                normalized,
            )
        except Exception:  # noqa: BLE001 — degrade to "no match" if facts unreadable
            return None
        if row is None:
            return None
        return uuid.UUID(str(row["entity_id"]))

    async def _match_phone(self, phone_value: str) -> uuid.UUID | None:
        normalized = phone_value.strip()
        # Fallback: strip non-digits for loose match
        digits = "".join(c for c in normalized if c.isdigit() or c in "+")
        try:
            row = await self._pool.fetchrow(
                """
                SELECT ef.subject AS entity_id
                FROM relationship.entity_facts ef
                JOIN public.entities e ON e.id = ef.subject
                WHERE ef.predicate = 'has-phone'
                  AND ef.object_kind = 'literal'
                  AND ef.validity    = 'active'
                  AND (ef.object = $1 OR ef.object = $2)
                  AND e.entity_type = 'person'
                  AND (e.metadata->>'merged_into') IS NULL
                  AND (e.metadata->>'deleted_at') IS NULL
                ORDER BY ef.created_at ASC NULLS LAST LIMIT 1
                """,
                normalized,
                digits,
            )
        except Exception:  # noqa: BLE001 — degrade to "no match" if facts unreadable
            return None
        if row is None:
            return None
        return uuid.UUID(str(row["entity_id"]))

    async def _match_name(self, display_name: str) -> list[uuid.UUID]:
        name_stripped = display_name.strip()
        if not name_stripped:
            return []
        rows = await self._pool.fetch(
            """
            SELECT id FROM public.entities
            WHERE entity_type = 'person'
              AND (
                canonical_name ILIKE $1
                OR EXISTS (SELECT 1 FROM unnest(aliases) AS a WHERE a ILIKE $1)
              )
              AND (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
            """,
            name_stripped,
        )
        return [uuid.UUID(str(row["id"])) for row in rows]


class ContactBackfillWriter:
    """Table mapping and upsert logic for CRM backfill (§7.2, §7.3).

    After bu-tzyuh, ``local_id`` is an entity UUID (``public.entities.id``).
    Writes or updates:
    - public.entities (canonical_name, metadata with profile fields and provenance)
    - contact_info rows via relationship.entity_facts (emails, phones, urls, usernames)
    - contacts_source_links (provenance link, keyed by local_entity_id)
    - addresses rows (entity-anchored via local_entity_id; requires contacts_004)
    - important_dates rows (entity-anchored via local_entity_id; requires contacts_004)
    - labels + contact_labels (entity-anchored via local_entity_id; requires contacts_004)

    Conflict policy (§7.3):
    - Source wins only for previously source-owned fields (tracked in metadata provenance)
    - Local manual edits win unless explicit refresh
    - Ambiguous merges emit activity feed entries
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        provider: str,
        account_id: str,
    ) -> None:
        self._pool = pool
        self._provider = provider
        self._account_id = account_id
        # Relationship-schema tables that may not exist in other butler schemas
        # (e.g. general, health). Populated lazily by _ensure_table_flags().
        self._table_flags: dict[str, bool] | None = None

    async def _ensure_table_flags(self) -> None:
        """Probe which relationship-only tables exist and are entity-anchored.

        Sets two flag groups per table:
        - ``<table>``: True when the table is visible in the current search_path.
        - ``<table>_entity``: True when the table has the ``local_entity_id``
          column (added by contacts_004 migration).  A missing column means the
          migration has not yet been applied and entity-path writes are skipped.
        """
        if self._table_flags is not None:
            return
        tables = ("addresses", "important_dates", "labels", "contact_labels")
        flags: dict[str, bool] = {}
        for tbl in tables:
            row = await self._pool.fetchrow("SELECT to_regclass($1) IS NOT NULL AS exists", tbl)
            exists = bool(row and row["exists"])
            flags[tbl] = exists
            if exists and tbl != "labels":
                # labels is a reference table; only child tables get local_entity_id.
                col_row = await self._pool.fetchrow(
                    """
                    SELECT 1 FROM pg_attribute
                    WHERE attrelid = to_regclass($1)
                      AND attname  = 'local_entity_id'
                      AND NOT attisdropped
                    """,
                    tbl,
                )
                flags[f"{tbl}_entity"] = col_row is not None
            else:
                flags[f"{tbl}_entity"] = False
        self._table_flags = flags

    def _has_table(self, name: str) -> bool:
        """Return True if the named table was found during init probe."""
        return self._table_flags is not None and self._table_flags.get(name, False)

    def _has_entity_anchor(self, name: str) -> bool:
        """Return True if the table has the local_entity_id column (contacts_004 applied)."""
        return self._table_flags is not None and self._table_flags.get(f"{name}_entity", False)

    async def create_contact(
        self,
        contact: CanonicalContact,
        *,
        executor: Any | None = None,
    ) -> uuid.UUID:
        """Create or update an entity for a backfilled contact, returning entity UUID.

        After bu-tzyuh this method writes to ``public.entities`` only — not
        ``public.contacts``. The entity is resolved/created on ``self._pool``
        (autonomous transaction), then profile metadata is stamped on ``executor``
        (caller's transaction) so the metadata UPDATE and the source-link INSERT
        commit atomically.

        ``executor`` lets the caller run the metadata UPDATE on a specific
        connection/transaction (asyncpg Pool and Connection share the
        ``fetchrow``/``execute`` interface). The engine passes a transactional
        connection so the entity metadata update and its provenance source-link
        commit atomically — otherwise an interrupted sync can leave a link-less
        entity that the next sync cannot find and re-creates as a duplicate.

        Entity resolution and the pre-transaction metadata read both run on the
        pool, NOT on ``executor``: ``_ensure_entity`` recovers from a duplicate
        canonical_name by catching ``UniqueViolationError`` and re-SELECTing, but
        in Postgres a raised error aborts the *enclosing* transaction even when
        Python catches it, so those queries would fail if they shared the caller's
        transaction.  The entity is committed independently first; a stale or
        partial-metadata entity is safe and corrected when the source-link
        transaction rolls back and the next sync retries.
        """
        first = contact.first_name
        last = contact.last_name
        nickname = contact.nickname

        org = contact.organizations[0] if contact.organizations else None
        company = org.company if org else None
        job_title = org.title if org else None

        primary_photo = next((p for p in contact.photos if p.primary), None)
        if primary_photo is None and contact.photos:
            primary_photo = contact.photos[0]
        avatar_url = primary_photo.url if primary_photo else None

        entity_id = await self._ensure_entity(first, last, nickname, company)

        if entity_id is None:
            raise ValueError(
                f"ContactBackfill: could not resolve or create entity for external_id="
                f"{contact.external_id!r}"
            )

        # Read existing metadata from pool before entering caller's transaction so the
        # deep-merge below does not overwrite nested keys already present on the entity
        # (e.g. when _ensure_entity recovers an existing entity via UniqueViolationError
        # instead of inserting a new one with empty metadata).  The fetch runs on
        # self._pool (autonomous) for the same reason _ensure_entity does: a raised error
        # inside a Postgres transaction aborts it even when Python catches it, so any
        # reads needed before the atomic commit must happen outside that transaction.
        existing_row = await self._pool.fetchrow(
            "SELECT metadata FROM public.entities WHERE id = $1", entity_id
        )
        metadata: dict[str, Any] = _parse_jsonb(existing_row["metadata"]) if existing_row else {}

        if first is not None:
            _deep_set(metadata, "profile.first_name", first)
        if last is not None:
            _deep_set(metadata, "profile.last_name", last)
        if nickname is not None:
            _deep_set(metadata, "profile.nickname", nickname)
        if company is not None:
            _deep_set(metadata, "profile.company", company)
        if job_title is not None:
            _deep_set(metadata, "profile.job_title", job_title)
        if avatar_url is not None:
            _deep_set(metadata, "profile.avatar_url", avatar_url)
        self._stamp_provenance(metadata, contact)

        db = executor or self._pool
        # Pass the full merged dict directly: the pool's registered JSONB codec encodes it
        # (json.dumps once). Wrapping in json.dumps here would double-encode.
        await db.execute(
            "UPDATE public.entities SET metadata = $1, updated_at = now() WHERE id = $2",
            metadata,
            entity_id,
        )

        logger.debug(
            "ContactBackfill: upserted entity %s for external_id=%s",
            entity_id,
            contact.external_id,
        )
        return entity_id

    async def _ensure_entity(
        self,
        first_name: str | None,
        last_name: str | None,
        nickname: str | None,
        company: str | None,
    ) -> uuid.UUID | None:
        """Resolve or create an entity for a backfilled contact.

        Always runs on the pool (autonomous statements). The INSERT may collide
        with an existing canonical_name and recover via SELECT in the
        ``UniqueViolationError`` branch; that recovery only works outside any
        caller transaction, since a raised error aborts an enclosing Postgres
        transaction even when caught. Callers needing atomicity (contact +
        source link) resolve the entity via this method *before* opening their
        transaction — see ``create_contact``.
        """
        db = self._pool
        canonical = " ".join(p.strip() for p in (first_name, last_name) if p and p.strip())
        if not canonical:
            canonical = nickname or company or "Unknown"
        entity_type = (
            "organization"
            if (not (first_name or "").strip() and not (last_name or "").strip() and company)
            else "person"
        )
        aliases = [
            c.strip()
            for c in (nickname, first_name)
            if c and c.strip() and c.strip().lower() != canonical.lower()
        ]
        try:
            row = await db.fetchrow(
                "INSERT INTO public.entities (canonical_name, entity_type, "
                "aliases, metadata, roles) VALUES ($1, $2, $3, "
                "'{}'::jsonb, '{}') RETURNING id",
                canonical,
                entity_type,
                aliases,
            )
            return row["id"] if row else None
        except asyncpg.UniqueViolationError:
            row = await db.fetchrow(
                "SELECT id FROM public.entities WHERE LOWER(canonical_name) = LOWER($1) "
                "AND entity_type = $2 "
                "AND (metadata->>'merged_into') IS NULL LIMIT 1",
                canonical,
                entity_type,
            )
            return row["id"] if row else None
        except asyncpg.PostgresError:
            logger.warning(
                "ContactBackfill: entity creation failed for %r", canonical, exc_info=True
            )
            return None

    async def update_contact(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
        *,
        match_strategy: str,
    ) -> dict[str, str]:
        """Update an existing entity, respecting provenance/conflict policy.

        After bu-tzyuh, reads from ``public.entities`` (not ``public.contacts``).
        Profile fields come from ``entity.metadata["profile.*"]`` entries.

        Security contract: ``roles`` is intentionally excluded from all UPDATE SET
        clauses. Role assignment is a privileged operation managed exclusively by
        the identity layer (owner bootstrap, dashboard PATCH endpoint). Google
        Contacts sync must never overwrite roles.

        Returns
        -------
        dict[str, str]
            Mapping of {field: 'updated' | 'skipped_local_edit' | 'conflict'}.
        """
        row = await self._pool.fetchrow(
            "SELECT id, canonical_name, aliases, metadata FROM public.entities WHERE id = $1",
            local_id,
        )
        if row is None:
            raise ValueError(f"Entity {local_id} not found for backfill update")

        existing_meta = _parse_jsonb(row["metadata"])
        field_results: dict[str, str] = {}

        # NOTE: ``roles`` is explicitly excluded from the writable set below.
        # Any field not listed in one of the sections below (name, org, avatar,
        # metadata) will never be written by the sync path.
        updates: dict[str, Any] = {}

        # --- Name fields (stored in metadata["profile.*"]) ---
        for field_name, new_val, canonical_field in [
            ("first_name", contact.first_name, "first_name"),
            ("last_name", contact.last_name, "last_name"),
            ("nickname", contact.nickname, "nickname"),
        ]:
            if new_val is None:
                continue
            prov_key = _provenance_key(self._provider, canonical_field)
            is_source_owned = _deep_get(existing_meta, prov_key) is not None
            current_val = _deep_get(existing_meta, f"profile.{field_name}")
            if current_val is None or is_source_owned:
                updates[field_name] = new_val
                field_results[field_name] = "updated"
            elif current_val != new_val:
                field_results[field_name] = "skipped_local_edit"

        # Rebuild composite canonical_name if name fields changed
        if "first_name" in updates or "last_name" in updates:
            first = updates.get("first_name", _deep_get(existing_meta, "profile.first_name"))
            last = updates.get("last_name", _deep_get(existing_meta, "profile.last_name"))
            nick = updates.get("nickname", _deep_get(existing_meta, "profile.nickname"))
            name_parts = [p for p in [first, last] if p]
            new_canonical = (
                " ".join(name_parts).strip() or nick or row["canonical_name"] or "Unknown"
            )
            updates["canonical_name"] = new_canonical

        # --- Organization fields ---
        org = contact.organizations[0] if contact.organizations else None
        for field_name, new_val, canonical_field in [
            ("company", org.company if org else None, "company"),
            ("job_title", org.title if org else None, "job_title"),
        ]:
            if new_val is None:
                continue
            prov_key = _provenance_key(self._provider, canonical_field)
            is_source_owned = _deep_get(existing_meta, prov_key) is not None
            current_val = _deep_get(existing_meta, f"profile.{field_name}")
            if current_val is None or is_source_owned:
                updates[field_name] = new_val
                field_results[field_name] = "updated"
            elif current_val != new_val:
                field_results[field_name] = "skipped_local_edit"

        # --- Avatar ---
        primary_photo = next((p for p in contact.photos if p.primary), None)
        if primary_photo is None and contact.photos:
            primary_photo = contact.photos[0]
        if primary_photo is not None:
            prov_key = _provenance_key(self._provider, "avatar_url")
            is_source_owned = _deep_get(existing_meta, prov_key) is not None
            current_avatar = _deep_get(existing_meta, "profile.avatar_url")
            if current_avatar is None or is_source_owned:
                updates["avatar_url"] = primary_photo.url
                field_results["avatar_url"] = "updated"
            elif current_avatar != primary_photo.url:
                field_results["avatar_url"] = "skipped_local_edit"

        # --- Update metadata with provenance and profile fields ---
        for field_name in ("first_name", "last_name", "nickname", "company", "job_title"):
            if field_name in updates:
                _deep_set(existing_meta, f"profile.{field_name}", updates[field_name])
        if "avatar_url" in updates:
            _deep_set(existing_meta, "profile.avatar_url", updates["avatar_url"])
        self._stamp_provenance(existing_meta, contact)

        # Pass the dict directly: the registered JSONB codec encodes it once.
        set_clauses: list[str] = ["metadata = metadata || $2::jsonb", "updated_at = now()"]
        params: list[Any] = [local_id, existing_meta]
        idx = 3

        if "canonical_name" in updates:
            set_clauses.append(f"canonical_name = ${idx}")
            params.append(updates["canonical_name"])
            idx += 1

        await self._pool.execute(
            f"UPDATE public.entities SET {', '.join(set_clauses)} WHERE id = $1",  # noqa: S608
            *params,
        )

        return field_results

    async def upsert_contact_info(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert channel-identity facts (email, phone, url, username) for a contact.

        After bu-tzyuh, ``local_id`` IS the entity_id — no join to public.contacts needed.

        Write-path cut-over (bu-k9ylx): channel facts are asserted as triples in
        ``relationship.entity_facts`` via the central writer
        ``relationship_assert_fact()``.  Connector identifier types with no triple
        predicate (e.g. ``telegram_chat_id``) are skipped — they have no home in
        the triple model.
        """
        entity_id = local_id  # local_id IS the entity_id after bu-tzyuh
        if entity_id is None:
            logger.debug(
                "upsert_contact_info: no entity_id; skipping channel facts",
            )
            return

        # (channel_type, value, predicate, is_primary) — predicate maps the
        # connector channel type to a registered contact predicate.
        entries: list[tuple[str, str, str, bool]] = []

        for email in contact.emails:
            entries.append(("email", email.value, "has-email", email.primary))

        for phone in contact.phones:
            entries.append(("phone", phone.value, "has-phone", phone.primary))

        for url in contact.urls:
            entries.append(("website", url.value, "has-website", False))

        for username in contact.usernames:
            service = username.service
            # Telegram usernames use a dedicated type for identity resolution
            if service == "telegram":
                # Strip leading '@' if present (canonical form is without)
                value = username.value.lstrip("@") if username.value else username.value
                entries.append(("telegram_username", value, "has-handle", False))
            else:
                entries.append(("other", username.value, "has-handle", False))

        # For telegram provider, assert telegram_user_id as a has-handle triple
        # so reverse-lookup by telegram_user_id works (identity.py maps
        # telegram_user_id -> has-handle).
        if self._provider == "telegram" and contact.external_id:
            entries.append(("telegram_user_id", contact.external_id, "has-handle", False))

        from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

        for type_, value, predicate, primary in entries:
            try:
                await relationship_assert_fact(
                    self._pool,
                    entity_id,
                    predicate,
                    value,
                    src="contacts-backfill",
                    object_kind="literal",
                    primary=primary,
                )
            except Exception:  # noqa: BLE001 — never block a bulk backfill on one row
                logger.warning(
                    "upsert_contact_info: relationship_assert_fact failed for entity %s "
                    "(ci_type=%r, predicate=%r, value=%r) — channel fact not written",
                    entity_id,
                    type_,
                    predicate,
                    value,
                    exc_info=True,
                )

    async def upsert_addresses(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert addresses from canonical contact into the addresses table.

        Requires the ``local_entity_id`` column (contacts_004 migration).
        Silently no-ops when the table is absent or the migration has not run.

        Each address is inserted only if no existing row with the same
        (local_entity_id, label, line_1) tuple exists — avoiding duplicates on
        repeated sync cycles.  Addresses with no ``street`` value are skipped
        because ``line_1`` is NOT NULL in the schema.
        """
        await self._ensure_table_flags()
        if not self._has_table("addresses"):
            return
        if not self._has_entity_anchor("addresses"):
            logger.debug(
                "upsert_addresses: skipped for entity %s — contacts_004 migration not applied",
                local_id,
            )
            return

        for addr in contact.addresses:
            if not addr.street:
                continue  # line_1 is NOT NULL; skip addresses with no street
            label = addr.label or "Home"
            # Validate country: schema requires VARCHAR(2) so truncate/drop if invalid.
            country = addr.country
            if country is not None:
                country = country.strip()
                if len(country) != 2:  # noqa: PLR2004
                    country = None
            try:
                await self._pool.execute(
                    """
                    INSERT INTO addresses
                        (local_entity_id, label, line_1, line_2,
                         city, province, postal_code, country, is_current)
                    SELECT $1, $2, $3, $4, $5, $6, $7, $8, false
                    WHERE NOT EXISTS (
                        SELECT 1 FROM addresses
                        WHERE local_entity_id = $1
                          AND label            = $2
                          AND line_1           = $3
                    )
                    """,
                    local_id,
                    label,
                    addr.street,
                    None,  # line_2 not provided by Google Contacts sync
                    addr.city,
                    addr.region,
                    addr.postal_code,
                    country,
                )
            except Exception:  # noqa: BLE001 — never block a bulk backfill on one row
                logger.warning(
                    "upsert_addresses: failed for entity %s label=%r street=%r",
                    local_id,
                    label,
                    addr.street,
                    exc_info=True,
                )

    async def upsert_important_dates(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert birthdays and anniversaries into important_dates.

        Requires the ``local_entity_id`` column (contacts_004 migration).
        Silently no-ops when the table is absent or the migration has not run.

        Each date is inserted only if no existing row with the same
        (local_entity_id, label, month, day) tuple exists.  Dates with NULL
        month or day are skipped because those columns are NOT NULL in the schema.
        """
        await self._ensure_table_flags()
        if not self._has_table("important_dates"):
            return
        if not self._has_entity_anchor("important_dates"):
            logger.debug(
                "upsert_important_dates: skipped for entity %s — contacts_004 not applied",
                local_id,
            )
            return

        entries = [(d, d.label or "birthday") for d in contact.birthdays] + [
            (d, d.label or "anniversary") for d in contact.anniversaries
        ]

        for date_obj, label in entries:
            if date_obj.month is None or date_obj.day is None:
                continue  # month and day are NOT NULL in the schema
            try:
                await self._pool.execute(
                    """
                    INSERT INTO important_dates
                        (local_entity_id, label, month, day, year)
                    SELECT $1, $2, $3, $4, $5
                    WHERE NOT EXISTS (
                        SELECT 1 FROM important_dates
                        WHERE local_entity_id = $1
                          AND label            = $2
                          AND month            = $3
                          AND day              = $4
                    )
                    """,
                    local_id,
                    label,
                    date_obj.month,
                    date_obj.day,
                    date_obj.year,
                )
            except Exception:  # noqa: BLE001 — never block a bulk backfill on one row
                logger.warning(
                    "upsert_important_dates: failed for entity %s label=%r month=%s day=%s",
                    local_id,
                    label,
                    date_obj.month,
                    date_obj.day,
                    exc_info=True,
                )

    async def upsert_labels(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert contact group memberships as labels + contact_labels.

        Requires the ``local_entity_id`` column on contact_labels (contacts_004).
        Silently no-ops when the tables are absent or the migration has not run.

        For each group in contact.group_memberships:
        1. Normalize the Google group resource name to a human-readable label.
        2. Ensure a labels row exists (insert if absent, no-op if present).
        3. Insert a contact_labels row keyed by (label_id, local_entity_id)
           if one does not already exist.

        The partial unique index ``idx_contact_labels_label_entity``
        (added by contacts_004) enforces (label_id, local_entity_id) uniqueness
        for entity-anchored rows.
        """
        await self._ensure_table_flags()
        if not self._has_table("labels") or not self._has_table("contact_labels"):
            return
        if not self._has_entity_anchor("contact_labels"):
            logger.debug(
                "upsert_labels: skipped for entity %s — contacts_004 migration not applied",
                local_id,
            )
            return

        for group_resource in contact.group_memberships:
            label_name = _normalize_group_label(group_resource)
            if not label_name:
                continue

            try:
                # Ensure the label exists; fetch its id.
                await self._pool.execute(
                    "INSERT INTO labels (name) VALUES ($1) ON CONFLICT (name) DO NOTHING",
                    label_name,
                )
                label_id = await self._pool.fetchval(
                    "SELECT id FROM labels WHERE name = $1",
                    label_name,
                )
                if label_id is None:
                    continue

                # Insert contact_labels link if not already present.
                await self._pool.execute(
                    """
                    INSERT INTO contact_labels (label_id, local_entity_id)
                    SELECT $1, $2
                    WHERE NOT EXISTS (
                        SELECT 1 FROM contact_labels
                        WHERE label_id        = $1
                          AND local_entity_id = $2
                    )
                    """,
                    label_id,
                    local_id,
                )
            except Exception:  # noqa: BLE001 — never block a bulk backfill on one row
                logger.warning(
                    "upsert_labels: failed for entity %s label=%r",
                    local_id,
                    label_name,
                    exc_info=True,
                )

    async def upsert_source_link(
        self,
        local_id: uuid.UUID | None,
        contact: CanonicalContact,
        *,
        executor: Any | None = None,
    ) -> None:
        """Create or update the contacts_source_links provenance row.

        After bu-tzyuh uses ``local_entity_id`` column (not ``local_contact_id``).

        ``executor`` lets the engine run this on the same transaction as the
        entity metadata UPDATE (see ``create_contact``) so the provenance link is never
        orphaned from its entity.
        """
        db = executor or self._pool
        if contact.deleted:
            # Tombstone: mark existing link as deleted, do not create new
            await db.execute(
                """
                UPDATE contacts_source_links
                SET deleted_at = now(), last_seen_at = now()
                WHERE provider = $1 AND account_id = $2 AND external_contact_id = $3
                """,
                self._provider,
                self._account_id,
                contact.external_id,
            )
            return

        if local_id is None:
            # No local entity to link; skip creating the source link.
            return

        await db.execute(
            """
            INSERT INTO contacts_source_links (
                provider, account_id, external_contact_id,
                local_entity_id, source_etag, last_seen_at, deleted_at
            )
            VALUES ($1, $2, $3, $4, $5, now(), NULL)
            ON CONFLICT (provider, account_id, external_contact_id)
            DO UPDATE SET
                local_entity_id = EXCLUDED.local_entity_id,
                source_etag = EXCLUDED.source_etag,
                last_seen_at = now(),
                deleted_at = NULL
            """,
            self._provider,
            self._account_id,
            contact.external_id,
            local_id,
            contact.etag,
        )

    def _stamp_provenance(
        self,
        metadata: dict[str, Any],
        contact: CanonicalContact,
    ) -> None:
        """Write provenance markers into metadata for source-owned fields."""
        provider = self._provider

        if contact.first_name is not None:
            _deep_set(metadata, _provenance_key(provider, "first_name"), contact.first_name)
        if contact.last_name is not None:
            _deep_set(metadata, _provenance_key(provider, "last_name"), contact.last_name)
        if contact.nickname is not None:
            _deep_set(metadata, _provenance_key(provider, "nickname"), contact.nickname)

        org = contact.organizations[0] if contact.organizations else None
        if org is not None:
            if org.company is not None:
                _deep_set(metadata, _provenance_key(provider, "company"), org.company)
            if org.title is not None:
                _deep_set(metadata, _provenance_key(provider, "job_title"), org.title)

        primary_photo = next((p for p in contact.photos if p.primary), None)
        if primary_photo is None and contact.photos:
            primary_photo = contact.photos[0]
        if primary_photo is not None:
            _deep_set(metadata, _provenance_key(provider, "avatar_url"), primary_photo.url)

        # Record the sync timestamp
        _deep_set(
            metadata,
            f"{_PROVENANCE_NS}.{provider}.last_synced_at",
            datetime.now(UTC).isoformat(),
        )


class ContactBackfillEngine:
    """Orchestrates resolver → writer → activity feed for CRM backfill (§7.4).

    This is the apply_contact callback consumed by ContactsSyncEngine.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        provider: str,
        account_id: str,
    ) -> None:
        self._pool = pool
        self._provider = provider
        self._account_id = account_id
        self._resolver = ContactBackfillResolver(pool, provider=provider, account_id=account_id)
        self._writer = ContactBackfillWriter(pool, provider=provider, account_id=account_id)

    async def __call__(self, contact: CanonicalContact) -> None:
        """Process one canonical contact: resolve, write, feed."""
        try:
            await self._apply(contact)
        except Exception as exc:
            logger.warning(
                "ContactBackfill: failed to apply contact external_id=%s: %s",
                contact.external_id,
                exc,
                exc_info=True,
            )
            raise

    async def _apply(self, contact: CanonicalContact) -> None:
        local_id, strategy = await self._resolver.resolve(contact)

        # Handle tombstone (source deleted) before anything else
        if contact.deleted:
            await self._handle_tombstone(contact, local_id)
            return

        if strategy == "ambiguous_name":
            # Cannot auto-merge; emit a review activity entry
            await self._emit_ambiguous_merge_activity(contact)
            return

        if local_id is not None:
            # Verify resolved entity still exists (stale source links, race conditions)
            exists = await self._pool.fetchval(
                "SELECT 1 FROM public.entities WHERE id = $1", local_id
            )
            if not exists:
                logger.warning(
                    "ContactBackfill: resolved local_id=%s via %s but entity missing; "
                    "treating as new",
                    local_id,
                    strategy,
                )
                local_id = None

        if local_id is None:
            # New contact — create/update entity record. The entity metadata UPDATE
            # and its provenance source-link MUST commit atomically: if the entity
            # lands without a source link, the next sync cannot resolve it by
            # external_id and re-creates it, fanning out duplicate contacts for
            # the same person (the historical Ang-Zhi-Yuan duplication). Run
            # both on one transactional connection so they succeed or fail as a
            # unit. The remaining child-table upserts are best-effort and stay
            # outside the transaction — their failure must not block the
            # idempotency anchor or strand a half-written entity.
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    local_id = await self._writer.create_contact(contact, executor=conn)
                    await self._writer.upsert_source_link(local_id, contact, executor=conn)
            await self._writer.upsert_contact_info(local_id, contact)
            await self._writer.upsert_addresses(local_id, contact)
            await self._writer.upsert_important_dates(local_id, contact)
            await self._writer.upsert_labels(local_id, contact)
            logger.info(
                "ContactBackfill: created/updated entity %s from %s/%s external_id=%s",
                local_id,
                self._provider,
                self._account_id,
                contact.external_id,
            )
        else:
            # Existing entity — update with conflict policy
            field_results = await self._writer.update_contact(
                local_id, contact, match_strategy=strategy
            )
            await self._writer.upsert_source_link(local_id, contact)
            await self._writer.upsert_contact_info(local_id, contact)
            await self._writer.upsert_addresses(local_id, contact)
            await self._writer.upsert_important_dates(local_id, contact)
            await self._writer.upsert_labels(local_id, contact)

            conflicting = {f for f, r in field_results.items() if r == "skipped_local_edit"}
            updated = {f for f, r in field_results.items() if r == "updated"}

            if conflicting:
                logger.info(
                    "ContactBackfill: conflict on entity %s fields=%s (local edits preserved)",
                    local_id,
                    sorted(conflicting),
                )
            elif updated:
                logger.info(
                    "ContactBackfill: updated entity %s fields=%s via strategy=%s",
                    local_id,
                    sorted(updated),
                    strategy,
                )

    async def _handle_tombstone(
        self,
        contact: CanonicalContact,
        local_id: uuid.UUID | None,
    ) -> None:
        """Handle a source-deleted contact (tombstone)."""
        await self._writer.upsert_source_link(local_id, contact)
        if local_id is not None:
            logger.info(
                "ContactBackfill: source tombstone for entity %s external_id=%s",
                local_id,
                contact.external_id,
            )

    async def _emit_ambiguous_merge_activity(
        self,
        contact: CanonicalContact,
    ) -> None:
        """Emit a sync_conflict activity for ambiguous name match."""
        # We cannot link to a specific contact, so we log at module level only.
        logger.warning(
            "ContactBackfill: ambiguous name match for external_id=%s display_name=%r — "
            "skipped auto-merge; manual review required",
            contact.external_id,
            contact.display_name,
        )


# --- Helpers ---


def _build_display_name(contact: CanonicalContact) -> str:
    """Build a display name from a canonical contact."""
    if contact.display_name:
        return contact.display_name
    parts = [p for p in [contact.first_name, contact.last_name] if p]
    combined = " ".join(parts).strip()
    if combined:
        return combined
    if contact.nickname:
        return contact.nickname
    org = contact.organizations[0] if contact.organizations else None
    if org and org.company:
        return org.company
    return contact.external_id


def _normalize_group_label(group_resource: str) -> str:
    """Normalize a Google group resource name to a human-readable label.

    Google group resource names look like 'contactGroups/myContacts' or
    'contactGroups/starred'. We use the last path segment, title-cased.
    """
    if not group_resource:
        return ""
    segment = group_resource.rstrip("/").rsplit("/", 1)[-1]
    # Convert camelCase or underscore to spaced title case
    spaced = re.sub(r"([A-Z])", r" \1", segment).replace("_", " ").strip()
    return spaced.title() if spaced else segment
