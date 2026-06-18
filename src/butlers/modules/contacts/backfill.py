"""CRM backfill pipeline for synced canonical contacts.

Implements spec §7: upsert identity resolution, table mapping, and conflict policy
for contacts backfilled from the sync engine.

Three main classes:

- ContactBackfillResolver  – identity matching pipeline (§7.1)
- ContactBackfillWriter    – table mapping and upsert logic (§7.2, §7.3)
- ContactBackfillEngine    – orchestrates resolver → writer (§7.4)

Wire as the apply_contact callback in ContactsSyncEngine construction during on_startup.
Provenance tracked in contacts.metadata JSONB under 'sources.contacts.{provider}.{field}'.
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

    Channel matching reads ``relationship.entity_facts`` (subject = entity_id)
    and maps back to a local contact via ``public.contacts.entity_id``.
    """

    def __init__(self, pool: asyncpg.Pool, *, provider: str, account_id: str) -> None:
        self._pool = pool
        self._provider = provider
        self._account_id = account_id

    async def resolve(self, contact: CanonicalContact) -> tuple[uuid.UUID | None, str]:
        """Resolve a canonical contact to a local contact ID.

        Returns
        -------
        (local_contact_id | None, match_strategy)
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
            SELECT sl.local_contact_id FROM contacts_source_links sl
            JOIN public.contacts c ON c.id = sl.local_contact_id
            WHERE sl.provider = $1 AND sl.account_id = $2 AND sl.external_contact_id = $3
              AND sl.deleted_at IS NULL
            """,
            self._provider,
            self._account_id,
            external_id,
        )
        if row is None:
            return None
        local_id = row["local_contact_id"]
        return uuid.UUID(str(local_id)) if local_id is not None else None

    async def _match_email(self, email_value: str) -> uuid.UUID | None:
        # Resolve via the triple store (migration bead 10, bu-e2ja9): a
        # ``has-email`` fact's subject is the entity; map back to a local contact
        # through ``public.contacts.entity_id``.
        normalized = email_value.strip().lower()
        try:
            row = await self._pool.fetchrow(
                """
                SELECT c.id AS contact_id
                FROM relationship.entity_facts ef
                JOIN public.contacts c ON c.entity_id = ef.subject
                WHERE ef.predicate    = 'has-email'
                  AND ef.object_kind  = 'literal'
                  AND ef.validity     = 'active'
                  AND lower(ef.object) = $1
                ORDER BY c.created_at ASC NULLS LAST
                LIMIT 1
                """,
                normalized,
            )
        except Exception:  # noqa: BLE001 — degrade to "no match" if facts unreadable
            return None
        if row is None:
            return None
        return uuid.UUID(str(row["contact_id"]))

    async def _match_phone(self, phone_value: str) -> uuid.UUID | None:
        normalized = phone_value.strip()
        # Fallback: strip non-digits for loose match
        digits = "".join(c for c in normalized if c.isdigit() or c in "+")
        try:
            row = await self._pool.fetchrow(
                """
                SELECT c.id AS contact_id
                FROM relationship.entity_facts ef
                JOIN public.contacts c ON c.entity_id = ef.subject
                WHERE ef.predicate   = 'has-phone'
                  AND ef.object_kind = 'literal'
                  AND ef.validity    = 'active'
                  AND (ef.object = $1 OR ef.object = $2)
                ORDER BY c.created_at ASC NULLS LAST
                LIMIT 1
                """,
                normalized,
                digits,
            )
        except Exception:  # noqa: BLE001 — degrade to "no match" if facts unreadable
            return None
        if row is None:
            return None
        return uuid.UUID(str(row["contact_id"]))

    async def _match_name(self, display_name: str) -> list[uuid.UUID]:
        name_stripped = display_name.strip()
        if not name_stripped:
            return []
        rows = await self._pool.fetch(
            """
            SELECT id FROM contacts
            WHERE (
                name ILIKE $1
                OR CONCAT(COALESCE(first_name, ''), ' ', COALESCE(last_name, '')) ILIKE $1
                OR nickname ILIKE $1
            )
            AND (archived_at IS NULL OR archived_at > now())
            """,
            name_stripped,
        )
        return [uuid.UUID(str(row["id"])) for row in rows]


class ContactBackfillWriter:
    """Table mapping and upsert logic for CRM backfill (§7.2, §7.3).

    Writes or updates:
    - contacts (name, first_name, last_name, nickname, company, job_title, avatar_url, metadata)
    - contact_info rows (emails, phones, urls, usernames)
    - addresses rows
    - important_dates rows (birthdays, anniversaries)
    - labels + contact_labels (group memberships)
    - contacts_source_links (provenance link)

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
        """Probe once which relationship-only tables exist in the current search_path."""
        if self._table_flags is not None:
            return
        tables = ("addresses", "important_dates", "labels", "contact_labels")
        flags: dict[str, bool] = {}
        for tbl in tables:
            row = await self._pool.fetchrow("SELECT to_regclass($1) IS NOT NULL AS exists", tbl)
            flags[tbl] = bool(row and row["exists"])
        self._table_flags = flags

    def _has_table(self, name: str) -> bool:
        """Return True if the named table was found during init probe."""
        return self._table_flags is not None and self._table_flags.get(name, False)

    async def create_contact(
        self,
        contact: CanonicalContact,
        *,
        executor: Any | None = None,
    ) -> uuid.UUID:
        """Create a new CRM contact linked to an entity.

        Resolves or creates an entity before the contact INSERT so that
        ``entity_id`` is always set when the schema supports it.

        ``executor`` lets the caller run the contact INSERT on a specific
        connection/transaction (asyncpg Pool and Connection share the
        ``fetchrow``/``execute`` interface). The engine passes a transactional
        connection so the contact INSERT and its provenance source-link commit
        atomically — otherwise an interrupted sync can leave a link-less
        contact that the next sync cannot find and re-creates as a duplicate.

        Entity resolution deliberately runs on the pool, NOT on ``executor``:
        ``_ensure_entity`` recovers from a duplicate canonical_name by catching
        ``UniqueViolationError`` and re-SELECTing, but in Postgres a raised
        error aborts the *enclosing* transaction even when Python catches it, so
        the recovery query would fail if it shared the caller's transaction. The
        entity is committed independently first; a contactless entity is
        harmless and reused if the contact+source-link transaction rolls back.
        """
        db = executor or self._pool
        display = _build_display_name(contact)
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

        metadata: dict[str, Any] = {}
        self._stamp_provenance(metadata, contact)

        entity_id = await self._ensure_entity(first, last, nickname, company)

        if entity_id is not None:
            try:
                row = await db.fetchrow(
                    """
                    INSERT INTO public.contacts (
                        name, first_name, last_name, nickname,
                        company, job_title, avatar_url, metadata, entity_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    RETURNING id
                    """,
                    display,
                    first,
                    last,
                    nickname,
                    company,
                    job_title,
                    avatar_url,
                    metadata,
                    entity_id,
                )
            except asyncpg.UndefinedColumnError:
                entity_id = None

        if entity_id is None:
            row = await db.fetchrow(
                """
                INSERT INTO public.contacts (
                    name, first_name, last_name, nickname,
                    company, job_title, avatar_url, metadata
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
                """,
                display,
                first,
                last,
                nickname,
                company,
                job_title,
                avatar_url,
                metadata,
            )

        contact_id = uuid.UUID(str(row["id"]))
        logger.debug(
            "ContactBackfill: created contact %s (entity=%s) for external_id=%s",
            contact_id,
            entity_id,
            contact.external_id,
        )
        return contact_id

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
        """Update an existing CRM contact, respecting provenance/conflict policy.

        Security contract: ``roles`` is intentionally excluded from all UPDATE SET
        clauses. Role assignment is a privileged operation managed exclusively by
        the identity layer (owner bootstrap, dashboard PATCH endpoint). Google
        Contacts sync must never overwrite roles.

        Returns
        -------
        dict[str, str]
            Mapping of {field: 'updated' | 'skipped_local_edit' | 'conflict'}.
        """
        row = await self._pool.fetchrow("SELECT * FROM public.contacts WHERE id = $1", local_id)
        if row is None:
            raise ValueError(f"Contact {local_id} not found for backfill update")

        existing_meta = _parse_jsonb(row["metadata"])
        field_results: dict[str, str] = {}

        # NOTE: ``roles`` is explicitly excluded from the writable set below.
        # Any field not listed in one of the sections below (name, org, avatar,
        # metadata) will never be written by the sync path.
        updates: dict[str, Any] = {}

        # --- Name fields ---
        for field_name, new_val, canonical_field in [
            ("first_name", contact.first_name, "first_name"),
            ("last_name", contact.last_name, "last_name"),
            ("nickname", contact.nickname, "nickname"),
        ]:
            if new_val is None:
                continue
            prov_key = _provenance_key(self._provider, canonical_field)
            is_source_owned = _deep_get(existing_meta, prov_key) is not None
            current_val = row[field_name]
            if current_val is None or is_source_owned:
                updates[field_name] = new_val
                field_results[field_name] = "updated"
            elif current_val != new_val:
                field_results[field_name] = "skipped_local_edit"

        # Rebuild composite name
        if "first_name" in updates or "last_name" in updates:
            first = updates.get("first_name", row["first_name"])
            last = updates.get("last_name", row["last_name"])
            nick = updates.get("nickname", row["nickname"])
            name_parts = [p for p in [first, last] if p]
            new_name = " ".join(name_parts).strip() or nick or row.get("name") or "Unknown"
            updates["name"] = new_name

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
            current_val = row[field_name]
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
            current_avatar = row["avatar_url"]
            if current_avatar is None or is_source_owned:
                updates["avatar_url"] = primary_photo.url
                field_results["avatar_url"] = "updated"
            elif current_avatar != primary_photo.url:
                field_results["avatar_url"] = "skipped_local_edit"

        # --- Update metadata with provenance ---
        self._stamp_provenance(existing_meta, contact)
        updates["metadata"] = existing_meta
        updates["updated_at"] = "now()"

        if updates:
            set_clauses: list[str] = []
            params: list[Any] = [local_id]
            idx = 2
            for col, val in updates.items():
                if col == "updated_at":
                    set_clauses.append("updated_at = now()")
                    continue
                if col == "metadata":
                    set_clauses.append(f"{col} = ${idx}")
                    params.append(val)
                else:
                    set_clauses.append(f"{col} = ${idx}")
                    params.append(val)
                idx += 1

            if set_clauses:
                await self._pool.execute(
                    f"UPDATE public.contacts SET {', '.join(set_clauses)} WHERE id = $1",  # noqa: S608
                    *params,
                )

        return field_results

    async def upsert_contact_info(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert channel-identity facts (email, phone, url, username) for a contact.

        Write-path cut-over (bu-k9ylx): channel facts are asserted as triples in
        ``relationship.entity_facts`` via the central writer
        ``relationship_assert_fact()``.  Connector identifier types with no triple
        predicate (e.g. ``telegram_chat_id``) are skipped — they have no home in
        the triple model.
        """
        # Resolve the entity to anchor channel facts on.  Channel facts live on
        # the entity (subject), not the contact, in the triple store.
        entity_row = await self._pool.fetchrow(
            "SELECT entity_id FROM public.contacts WHERE id = $1",
            local_id,
        )
        entity_id = entity_row["entity_id"] if entity_row is not None else None
        if entity_id is None:
            logger.debug(
                "upsert_contact_info: contact %s has no linked entity; skipping channel facts",
                local_id,
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
        """Upsert addresses from canonical contact into the addresses table."""
        await self._ensure_table_flags()
        if not self._has_table("addresses"):
            return
        for addr in contact.addresses:
            # Build a stable line_1 from street or city fallback
            line_1 = addr.street or addr.city or "Unknown"
            label = addr.label or "Home"

            existing = await self._pool.fetchrow(
                """
                SELECT id FROM addresses
                WHERE contact_id = $1 AND label = $2 AND line_1 = $3
                """,
                local_id,
                label,
                line_1,
            )
            if existing is not None:
                # Update mutable fields
                await self._pool.execute(
                    """
                    UPDATE addresses
                    SET city = $2, province = $3, postal_code = $4, country = $5,
                        is_current = $6, updated_at = now()
                    WHERE id = $1
                    """,
                    existing["id"],
                    addr.city,
                    addr.region,
                    addr.postal_code,
                    addr.country[:2] if addr.country and len(addr.country) >= 2 else addr.country,
                    addr.primary,
                )
                continue

            # Validate country code
            country_val = None
            if addr.country:
                country_val = addr.country[:2] if len(addr.country) > 2 else addr.country

            await self._pool.execute(
                """
                INSERT INTO addresses (
                    contact_id, label, line_1, city, province, postal_code, country, is_current
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT DO NOTHING
                """,
                local_id,
                label,
                line_1,
                addr.city,
                addr.region,
                addr.postal_code,
                country_val,
                addr.primary,
            )

    async def upsert_important_dates(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert birthdays and anniversaries into important_dates."""
        await self._ensure_table_flags()
        if not self._has_table("important_dates"):
            return
        date_entries: list[tuple[str, int | None, int | None, int | None]] = []

        for bday in contact.birthdays:
            if bday.month is not None and bday.day is not None:
                date_entries.append(("birthday", bday.month, bday.day, bday.year))

        for ann in contact.anniversaries:
            if ann.month is not None and ann.day is not None:
                date_entries.append(("anniversary", ann.month, ann.day, ann.year))

        for label, month, day, year in date_entries:
            if month is None or day is None:
                continue
            existing = await self._pool.fetchrow(
                """
                SELECT id FROM important_dates
                WHERE contact_id = $1 AND label = $2 AND month = $3 AND day = $4
                """,
                local_id,
                label,
                month,
                day,
            )
            if existing is not None:
                if year is not None:
                    await self._pool.execute(
                        "UPDATE important_dates SET year = $2 WHERE id = $1",
                        existing["id"],
                        year,
                    )
                continue

            await self._pool.execute(
                """
                INSERT INTO important_dates (contact_id, label, month, day, year)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING
                """,
                local_id,
                label,
                month,
                day,
                year,
            )

    async def upsert_labels(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert contact group memberships as labels + contact_labels."""
        await self._ensure_table_flags()
        if not self._has_table("labels"):
            return
        for group_resource in contact.group_memberships:
            # Normalize: use the last segment of the resource name as label
            label_name = _normalize_group_label(group_resource)
            if not label_name:
                continue

            # Upsert the label
            label_row = await self._pool.fetchrow(
                """
                INSERT INTO labels (name)
                VALUES ($1)
                ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                RETURNING id
                """,
                label_name,
            )
            label_id = uuid.UUID(str(label_row["id"]))

            # Assign to contact (idempotent)
            await self._pool.execute(
                """
                INSERT INTO contact_labels (label_id, contact_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                label_id,
                local_id,
            )

    async def upsert_source_link(
        self,
        local_id: uuid.UUID | None,
        contact: CanonicalContact,
        *,
        executor: Any | None = None,
    ) -> None:
        """Create or update the contacts_source_links provenance row.

        ``executor`` lets the engine run this on the same transaction as the
        contact INSERT (see ``create_contact``) so the provenance link is never
        orphaned from its contact.
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
            # No local contact to link; skip creating the source link.
            return

        await db.execute(
            """
            INSERT INTO contacts_source_links (
                provider, account_id, external_contact_id,
                local_contact_id, source_etag, last_seen_at, deleted_at
            )
            VALUES ($1, $2, $3, $4, $5, now(), NULL)
            ON CONFLICT (provider, account_id, external_contact_id)
            DO UPDATE SET
                local_contact_id = EXCLUDED.local_contact_id,
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
            # Verify resolved contact still exists (stale source links, race conditions)
            exists = await self._pool.fetchval(
                "SELECT 1 FROM public.contacts WHERE id = $1", local_id
            )
            if not exists:
                logger.warning(
                    "ContactBackfill: resolved local_id=%s via %s but contact missing; "
                    "treating as new",
                    local_id,
                    strategy,
                )
                local_id = None

        if local_id is None:
            # New contact — create CRM record. The contact INSERT and its
            # provenance source-link MUST commit atomically: if the contact
            # lands without a source link, the next sync cannot resolve it by
            # external_id and re-creates it, fanning out duplicate contacts for
            # the same person (the historical Ang-Zhi-Yuan duplication). Run
            # both on one transactional connection so they succeed or fail as a
            # unit. The remaining child-table upserts are best-effort and stay
            # outside the transaction — their failure must not block the
            # idempotency anchor or strand a half-written contact.
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    local_id = await self._writer.create_contact(contact, executor=conn)
                    await self._writer.upsert_source_link(local_id, contact, executor=conn)
            await self._writer.upsert_contact_info(local_id, contact)
            await self._writer.upsert_addresses(local_id, contact)
            await self._writer.upsert_important_dates(local_id, contact)
            await self._writer.upsert_labels(local_id, contact)
            logger.info(
                "ContactBackfill: created new contact %s from %s/%s external_id=%s",
                local_id,
                self._provider,
                self._account_id,
                contact.external_id,
            )
        else:
            # Existing contact — update with conflict policy
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
                    "ContactBackfill: conflict on contact %s fields=%s (local edits preserved)",
                    local_id,
                    sorted(conflicting),
                )
            elif updated:
                logger.info(
                    "ContactBackfill: updated contact %s fields=%s via strategy=%s",
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
                "ContactBackfill: source tombstone for contact %s external_id=%s",
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
