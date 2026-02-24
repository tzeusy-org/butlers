"""CRM backfill pipeline for synced canonical contacts.

Implements spec §7: upsert identity resolution, table mapping, conflict policy,
and activity feed entries for contacts backfilled from the sync engine.

Three main classes:

- ContactBackfillResolver  – identity matching pipeline (§7.1)
- ContactBackfillWriter    – table mapping and upsert logic (§7.2, §7.3)
- ContactBackfillEngine    – orchestrates resolver → writer → activity feed (§7.4)

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

# Activity feed event types (§7.4)
_ACTIVITY_CONTACT_SYNCED = "contact_synced"
_ACTIVITY_CONTACT_SYNC_UPDATED = "contact_sync_updated"
_ACTIVITY_CONTACT_SYNC_CONFLICT = "contact_sync_conflict"
_ACTIVITY_CONTACT_SYNC_DELETED_SOURCE = "contact_sync_deleted_source"

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
    2. Primary email exact match in contact_info (type='email')
    3. Phone exact/e164 match in contact_info (type='phone')
    4. Conservative name match (manual-review flag when ambiguous)
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
            SELECT local_contact_id FROM contacts_source_links
            WHERE provider = $1 AND account_id = $2 AND external_contact_id = $3
              AND deleted_at IS NULL
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
        normalized = email_value.strip().lower()
        row = await self._pool.fetchrow(
            """
            SELECT ci.contact_id FROM shared.contact_info ci
            WHERE ci.type = 'email'
              AND lower(ci.value) = $1
            LIMIT 1
            """,
            normalized,
        )
        if row is None:
            return None
        return uuid.UUID(str(row["contact_id"]))

    async def _match_phone(self, phone_value: str) -> uuid.UUID | None:
        normalized = phone_value.strip()
        row = await self._pool.fetchrow(
            """
            SELECT ci.contact_id FROM shared.contact_info ci
            WHERE ci.type = 'phone'
              AND (ci.value = $1 OR ci.value = $2)
            LIMIT 1
            """,
            normalized,
            # Fallback: strip non-digits for loose match
            "".join(c for c in normalized if c.isdigit() or c in "+"),
        )
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

    async def create_contact(self, contact: CanonicalContact) -> uuid.UUID:
        """Create a new CRM contact from a canonical contact."""
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

        row = await self._pool.fetchrow(
            """
            INSERT INTO contacts (
                name, first_name, last_name, nickname,
                company, job_title, avatar_url, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            RETURNING id
            """,
            display,
            first,
            last,
            nickname,
            company,
            job_title,
            avatar_url,
            json.dumps(metadata),
        )
        contact_id = uuid.UUID(str(row["id"]))
        logger.debug(
            "ContactBackfill: created contact %s for external_id=%s",
            contact_id,
            contact.external_id,
        )
        return contact_id

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
        row = await self._pool.fetchrow("SELECT * FROM contacts WHERE id = $1", local_id)
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
                    set_clauses.append(f"{col} = ${idx}::jsonb")
                    params.append(json.dumps(val))
                else:
                    set_clauses.append(f"{col} = ${idx}")
                    params.append(val)
                idx += 1

            if set_clauses:
                await self._pool.execute(
                    f"UPDATE contacts SET {', '.join(set_clauses)} WHERE id = $1",  # noqa: S608
                    *params,
                )

        return field_results

    async def upsert_contact_info(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert email, phone, url, and username rows in contact_info.

        Security contract:
        - ``secured`` is intentionally excluded from all UPDATE SET clauses.
          Sync must never flip the secured flag; only privileged identity-layer
          operations may set it.
        - INSERT uses ``ON CONFLICT DO NOTHING`` so that the UNIQUE(type, value)
          constraint on shared.contact_info never raises; when a (type, value)
          pair already exists for another contact we silently skip insertion.
        """
        # Build the full set of contact_info entries to sync
        entries: list[tuple[str, str, str | None, bool]] = []  # (type, value, label, primary)

        for email in contact.emails:
            entries.append(("email", email.value, email.label, email.primary))

        for phone in contact.phones:
            entries.append(("phone", phone.value, phone.label, phone.primary))

        for url in contact.urls:
            entries.append(("website", url.value, url.label, False))

        for username in contact.usernames:
            service = username.service
            entries.append(("other", username.value, service, False))

        for type_, value, label, primary in entries:
            # Check if this value already exists for this contact.
            # NOTE: Only is_primary is updated on existing rows; secured is never modified.
            existing = await self._pool.fetchrow(
                """
                SELECT id, is_primary FROM shared.contact_info
                WHERE contact_id = $1 AND type = $2 AND lower(value) = lower($3)
                """,
                local_id,
                type_,
                value,
            )
            if existing is not None:
                # Update primary status only — never touch secured.
                if primary and not existing["is_primary"]:
                    await self._pool.execute(
                        """
                        UPDATE shared.contact_info SET is_primary = false
                        WHERE contact_id = $1 AND type = $2
                        """,
                        local_id,
                        type_,
                    )
                    await self._pool.execute(
                        "UPDATE shared.contact_info SET is_primary = true WHERE id = $1",
                        existing["id"],
                    )
                continue

            # If setting as primary, clear existing primaries for this type.
            # (Still does not touch secured on any row.)
            if primary:
                await self._pool.execute(
                    """
                    UPDATE shared.contact_info SET is_primary = false
                    WHERE contact_id = $1 AND type = $2
                    """,
                    local_id,
                    type_,
                )

            # ON CONFLICT DO NOTHING handles:
            # 1. Concurrent duplicate inserts for the same (contact_id, type, value).
            # 2. The UNIQUE(type, value) constraint: if this value is already
            #    linked to a *different* contact, we skip insertion silently.
            await self._pool.execute(
                """
                INSERT INTO shared.contact_info (contact_id, type, value, label, is_primary)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT DO NOTHING
                """,
                local_id,
                type_,
                value,
                label,
                primary,
            )

    async def upsert_addresses(
        self,
        local_id: uuid.UUID,
        contact: CanonicalContact,
    ) -> None:
        """Upsert addresses from canonical contact into the addresses table."""
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
    ) -> None:
        """Create or update the contacts_source_links provenance row."""
        if contact.deleted:
            # Tombstone: mark existing link as deleted, do not create new
            await self._pool.execute(
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

        await self._pool.execute(
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

        if local_id is None:
            # New contact — create CRM record
            local_id = await self._writer.create_contact(contact)
            await self._writer.upsert_source_link(local_id, contact)
            await self._writer.upsert_contact_info(local_id, contact)
            await self._writer.upsert_addresses(local_id, contact)
            await self._writer.upsert_important_dates(local_id, contact)
            await self._writer.upsert_labels(local_id, contact)
            await self._log_activity(
                local_id,
                _ACTIVITY_CONTACT_SYNCED,
                self._synced_description(contact, strategy="new"),
            )
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
                await self._log_activity(
                    local_id,
                    _ACTIVITY_CONTACT_SYNC_CONFLICT,
                    self._conflict_description(contact, conflicting),
                )
                logger.info(
                    "ContactBackfill: conflict on contact %s fields=%s (local edits preserved)",
                    local_id,
                    sorted(conflicting),
                )
            elif updated:
                await self._log_activity(
                    local_id,
                    _ACTIVITY_CONTACT_SYNC_UPDATED,
                    self._updated_description(contact, updated, strategy),
                )
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
            await self._log_activity(
                local_id,
                _ACTIVITY_CONTACT_SYNC_DELETED_SOURCE,
                (
                    f"Source {self._provider} marked contact as deleted "
                    f"(external_id={contact.external_id}). "
                    "CRM record preserved; source link marked deleted."
                ),
            )
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

    async def _log_activity(
        self,
        contact_id: uuid.UUID,
        event_type: str,
        description: str,
    ) -> None:
        """Insert an activity_feed entry for a sync event."""
        try:
            await self._pool.execute(
                """
                INSERT INTO activity_feed (contact_id, type, description)
                VALUES ($1, $2, $3)
                """,
                contact_id,
                event_type,
                description,
            )
        except Exception as exc:
            logger.warning(
                "ContactBackfill: failed to log activity contact_id=%s type=%s: %s",
                contact_id,
                event_type,
                exc,
            )

    def _synced_description(self, contact: CanonicalContact, strategy: str) -> str:
        name = contact.display_name or contact.external_id
        return (
            f"Contact synced from {self._provider}/{self._account_id}: {name!r} "
            f"(match_strategy={strategy}, external_id={contact.external_id})"
        )

    def _updated_description(
        self,
        contact: CanonicalContact,
        updated_fields: set[str],
        strategy: str,
    ) -> str:
        name = contact.display_name or contact.external_id
        fields_str = ", ".join(sorted(updated_fields))
        return (
            f"Sync update from {self._provider}/{self._account_id}: {name!r} "
            f"fields=[{fields_str}] (strategy={strategy}, external_id={contact.external_id})"
        )

    def _conflict_description(
        self,
        contact: CanonicalContact,
        conflicting_fields: set[str],
    ) -> str:
        name = contact.display_name or contact.external_id
        fields_str = ", ".join(sorted(conflicting_fields))
        return (
            f"Sync conflict from {self._provider}/{self._account_id}: {name!r} "
            f"— local edits preserved for fields=[{fields_str}] "
            f"(external_id={contact.external_id}). Manual review may be needed."
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
