"""Tests for the CRM backfill pipeline (butlers-eprz.7).

Integration tests against a provisioned Postgres database covering:
- ContactBackfillResolver: identity matching pipeline (§7.1)
- ContactBackfillWriter: table mapping and upsert logic (§7.2, §7.3)
- ContactBackfillEngine: orchestrated apply_contact callback (§7.4)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from butlers.modules.contacts.backfill import (
    ContactBackfillEngine,
    ContactBackfillResolver,
    ContactBackfillWriter,
    _build_display_name,
    _deep_get,
    _deep_set,
    _normalize_group_label,
    _provenance_key,
)
from butlers.modules.contacts.sync import (
    CanonicalContact,
    ContactAddress,
    ContactDate,
    ContactEmail,
    ContactOrganization,
    ContactPhone,
    ContactPhoto,
    ContactUrl,
    ContactUsername,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared fixture: full CRM + contacts_source_links tables
# ---------------------------------------------------------------------------


@pytest.fixture
async def crm_pool(provisioned_postgres_pool):
    """Provision a fresh Postgres DB with CRM tables for backfill tests."""
    async with provisioned_postgres_pool() as pool:
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                first_name VARCHAR,
                last_name VARCHAR,
                nickname VARCHAR,
                company VARCHAR,
                job_title VARCHAR,
                avatar_url VARCHAR,
                listed BOOLEAN NOT NULL DEFAULT true,
                archived_at TIMESTAMPTZ,
                metadata JSONB,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS contact_info (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                type VARCHAR NOT NULL,
                value TEXT NOT NULL,
                label VARCHAR,
                is_primary BOOLEAN DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE INDEX IF NOT EXISTS idx_contact_info_type_value
                ON contact_info (type, value)
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS addresses (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                label VARCHAR NOT NULL DEFAULT 'Home',
                line_1 TEXT NOT NULL,
                line_2 TEXT,
                city VARCHAR,
                province VARCHAR,
                postal_code VARCHAR,
                country VARCHAR(2),
                is_current BOOLEAN NOT NULL DEFAULT false,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS important_dates (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                label TEXT NOT NULL,
                month INT NOT NULL,
                day INT NOT NULL,
                year INT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS labels (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL UNIQUE,
                color TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS contact_labels (
                label_id UUID NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                PRIMARY KEY (label_id, contact_id)
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS activity_feed (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS contacts_source_accounts (
                provider TEXT NOT NULL,
                account_id TEXT NOT NULL,
                subject_email TEXT,
                connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_success_at TIMESTAMPTZ,
                PRIMARY KEY (provider, account_id)
            )
        """)
        await pool.execute("""
            CREATE TABLE IF NOT EXISTS contacts_source_links (
                provider TEXT NOT NULL,
                account_id TEXT NOT NULL,
                external_contact_id TEXT NOT NULL,
                local_contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
                source_etag TEXT,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                deleted_at TIMESTAMPTZ,
                PRIMARY KEY (provider, account_id, external_contact_id)
            )
        """)
        yield pool


def _make_contact(
    external_id: str = "people/123",
    *,
    display_name: str | None = "Alice Smith",
    first_name: str | None = "Alice",
    last_name: str | None = "Smith",
    emails: list[ContactEmail] | None = None,
    phones: list[ContactPhone] | None = None,
    addresses: list[ContactAddress] | None = None,
    organizations: list[ContactOrganization] | None = None,
    birthdays: list[ContactDate] | None = None,
    anniversaries: list[ContactDate] | None = None,
    urls: list[ContactUrl] | None = None,
    usernames: list[ContactUsername] | None = None,
    photos: list[ContactPhoto] | None = None,
    group_memberships: list[str] | None = None,
    deleted: bool = False,
    etag: str | None = None,
) -> CanonicalContact:
    return CanonicalContact(
        external_id=external_id,
        etag=etag,
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        emails=emails or [],
        phones=phones or [],
        addresses=addresses or [],
        organizations=organizations or [],
        birthdays=birthdays or [],
        anniversaries=anniversaries or [],
        urls=urls or [],
        usernames=usernames or [],
        photos=photos or [],
        group_memberships=group_memberships or [],
        deleted=deleted,
    )


async def _insert_local_contact(
    pool,
    *,
    name: str = "Local Contact",
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    avatar_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    import json

    row = await pool.fetchrow(
        """
        INSERT INTO contacts (name, first_name, last_name, company, job_title, avatar_url, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        RETURNING id
        """,
        name,
        first_name,
        last_name,
        company,
        job_title,
        avatar_url,
        json.dumps(metadata or {}),
    )
    return uuid.UUID(str(row["id"]))


# ---------------------------------------------------------------------------
# Unit tests for pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_provenance_key(self) -> None:
        key = _provenance_key("google", "first_name")
        assert key == "sources.contacts.google.first_name"

    def test_deep_set_creates_nested(self) -> None:
        d: dict[str, Any] = {}
        _deep_set(d, "a.b.c", 42)
        assert d == {"a": {"b": {"c": 42}}}

    def test_deep_get_existing(self) -> None:
        d = {"a": {"b": {"c": 42}}}
        assert _deep_get(d, "a.b.c") == 42

    def test_deep_get_missing(self) -> None:
        d = {"a": {}}
        assert _deep_get(d, "a.b.c") is None

    def test_build_display_name_from_display(self) -> None:
        c = _make_contact(display_name="John Doe", first_name="John", last_name="Doe")
        assert _build_display_name(c) == "John Doe"

    def test_build_display_name_from_parts(self) -> None:
        c = _make_contact(display_name=None, first_name="Jane", last_name="Doe")
        assert _build_display_name(c) == "Jane Doe"

    def test_build_display_name_nickname_fallback(self) -> None:
        c2 = CanonicalContact(external_id="x", nickname="JD")
        assert _build_display_name(c2) == "JD"

    def test_normalize_group_label_camel_case(self) -> None:
        assert _normalize_group_label("contactGroups/myContacts") == "My Contacts"

    def test_normalize_group_label_starred(self) -> None:
        assert _normalize_group_label("contactGroups/starred") == "Starred"

    def test_normalize_group_label_empty(self) -> None:
        assert _normalize_group_label("") == ""


# ---------------------------------------------------------------------------
# ContactBackfillResolver tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestContactBackfillResolver:
    async def test_resolve_returns_new_when_no_match(self, crm_pool) -> None:
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/new1",
            emails=[
                ContactEmail(
                    value="nobody@example.com", primary=True, normalized_value="nobody@example.com"
                )
            ],
        )
        local_id, strategy = await resolver.resolve(contact)
        assert local_id is None
        assert strategy == "new"

    async def test_resolve_via_source_link(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Alice Smith")
        # Register source link
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ($1, $2, $3, $4)
            """,
            "google",
            "acc1",
            "people/abc",
            local_id,
        )
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/abc")
        found_id, strategy = await resolver.resolve(contact)
        assert found_id == local_id
        assert strategy == "source_link"

    async def test_resolve_via_email_match(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Bob Jones")
        # Add email to contact_info
        await crm_pool.execute(
            "INSERT INTO contact_info (contact_id, type, value) VALUES ($1, 'email', $2)",
            local_id,
            "bob@example.com",
        )
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/bob",
            display_name="Bob Jones",
            first_name="Bob",
            last_name="Jones",
            emails=[
                ContactEmail(
                    value="BOB@EXAMPLE.COM", primary=True, normalized_value="bob@example.com"
                )
            ],
        )
        found_id, strategy = await resolver.resolve(contact)
        assert found_id == local_id
        assert strategy == "email"

    async def test_resolve_via_phone_match(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Carol White")
        await crm_pool.execute(
            "INSERT INTO contact_info (contact_id, type, value) VALUES ($1, 'phone', $2)",
            local_id,
            "+15551234567",
        )
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/carol",
            display_name="Carol White",
            emails=[],
            phones=[
                ContactPhone(value="+15551234567", primary=True, e164_normalized="+15551234567")
            ],
        )
        found_id, strategy = await resolver.resolve(contact)
        assert found_id == local_id
        assert strategy == "phone"

    async def test_resolve_via_exact_name(self, crm_pool) -> None:
        local_id = await _insert_local_contact(
            crm_pool, name="Eve Green", first_name="Eve", last_name="Green"
        )
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/eve", display_name="Eve Green", first_name="Eve", last_name="Green"
        )
        found_id, strategy = await resolver.resolve(contact)
        assert found_id == local_id
        assert strategy == "name"

    async def test_resolve_ambiguous_name(self, crm_pool) -> None:
        for i in range(2):
            await _insert_local_contact(
                crm_pool, name="John Doe", first_name="John", last_name="Doe"
            )
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/jd", display_name="John Doe", first_name="John", last_name="Doe"
        )
        found_id, strategy = await resolver.resolve(contact)
        assert found_id is None
        assert strategy == "ambiguous_name"

    async def test_resolve_source_link_deleted_not_matched(self, crm_pool) -> None:
        """A deleted source link should NOT match."""
        local_id = await _insert_local_contact(crm_pool, name="Deleted User")
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links
            (provider, account_id, external_contact_id, local_contact_id, deleted_at)
            VALUES ($1, $2, $3, $4, now())
            """,
            "google",
            "acc1",
            "people/deleted",
            local_id,
        )
        resolver = ContactBackfillResolver(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/deleted")
        found_id, strategy = await resolver.resolve(contact)
        # Should fall through to other matchers, ending as "new"
        assert strategy in ("new", "name", "email")


# ---------------------------------------------------------------------------
# ContactBackfillWriter tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestContactBackfillWriter:
    async def test_create_contact_basic_fields(self, crm_pool) -> None:
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/123",
            display_name="Test Person",
            first_name="Test",
            last_name="Person",
        )
        local_id = await writer.create_contact(contact)
        row = await crm_pool.fetchrow("SELECT * FROM contacts WHERE id = $1", local_id)
        assert row["first_name"] == "Test"
        assert row["last_name"] == "Person"
        assert row["name"] == "Test Person"

    async def test_create_contact_with_org(self, crm_pool) -> None:
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/124",
            organizations=[ContactOrganization(company="Acme Inc", title="Engineer")],
        )
        local_id = await writer.create_contact(contact)
        row = await crm_pool.fetchrow("SELECT * FROM contacts WHERE id = $1", local_id)
        assert row["company"] == "Acme Inc"
        assert row["job_title"] == "Engineer"

    async def test_create_contact_with_avatar(self, crm_pool) -> None:
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/125",
            photos=[ContactPhoto(url="https://example.com/photo.jpg", primary=True)],
        )
        local_id = await writer.create_contact(contact)
        row = await crm_pool.fetchrow("SELECT * FROM contacts WHERE id = $1", local_id)
        assert row["avatar_url"] == "https://example.com/photo.jpg"

    async def test_create_contact_stores_provenance(self, crm_pool) -> None:
        import json

        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/126", first_name="Prov", last_name="Test")
        local_id = await writer.create_contact(contact)
        row = await crm_pool.fetchrow("SELECT metadata FROM contacts WHERE id = $1", local_id)
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        assert meta is not None
        # Check provenance keys are present
        assert _deep_get(meta, "sources.contacts.google.first_name") == "Prov"
        assert _deep_get(meta, "sources.contacts.google.last_name") == "Test"

    async def test_update_contact_source_owned_field_updated(self, crm_pool) -> None:
        """Source-owned fields should be updated on sync."""

        initial_meta = {}
        _deep_set(initial_meta, "sources.contacts.google.first_name", "Old")
        local_id = await _insert_local_contact(
            crm_pool,
            name="Old Name",
            first_name="Old",
            last_name="Name",
            metadata=initial_meta,
        )
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/123", first_name="New", last_name="Name")
        field_results = await writer.update_contact(local_id, contact, match_strategy="source_link")
        assert field_results.get("first_name") == "updated"
        row = await crm_pool.fetchrow("SELECT first_name FROM contacts WHERE id = $1", local_id)
        assert row["first_name"] == "New"

    async def test_update_contact_locally_edited_field_preserved(self, crm_pool) -> None:
        """Locally-edited fields (no provenance) should not be overwritten."""
        # No provenance in metadata — local edit
        local_id = await _insert_local_contact(
            crm_pool,
            name="Local Name",
            first_name="LocalFirst",
            last_name="LocalLast",
            metadata={},
        )
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/456", first_name="SyncFirst", last_name="SyncLast")
        field_results = await writer.update_contact(local_id, contact, match_strategy="email")
        assert field_results.get("first_name") == "skipped_local_edit"
        row = await crm_pool.fetchrow("SELECT first_name FROM contacts WHERE id = $1", local_id)
        assert row["first_name"] == "LocalFirst"  # Preserved

    async def test_upsert_contact_info_emails(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Email Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/789",
            emails=[
                ContactEmail(
                    value="primary@test.com", primary=True, normalized_value="primary@test.com"
                ),
                ContactEmail(
                    value="other@test.com", primary=False, normalized_value="other@test.com"
                ),
            ],
        )
        await writer.upsert_contact_info(local_id, contact)
        rows = await crm_pool.fetch(
            "SELECT * FROM contact_info WHERE contact_id = $1 ORDER BY is_primary DESC",
            local_id,
        )
        assert len(rows) == 2
        assert rows[0]["value"] == "primary@test.com"
        assert rows[0]["is_primary"] is True

    async def test_upsert_contact_info_idempotent(self, crm_pool) -> None:
        """Calling upsert_contact_info twice should not create duplicates."""
        local_id = await _insert_local_contact(crm_pool, name="Idempotent Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/999",
            emails=[
                ContactEmail(
                    value="test@example.com", primary=True, normalized_value="test@example.com"
                )
            ],
        )
        await writer.upsert_contact_info(local_id, contact)
        await writer.upsert_contact_info(local_id, contact)  # Second call
        rows = await crm_pool.fetch(
            "SELECT * FROM contact_info WHERE contact_id = $1 AND type = 'email'",
            local_id,
        )
        assert len(rows) == 1

    async def test_upsert_addresses(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Address Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/addr1",
            addresses=[
                ContactAddress(
                    street="123 Main St",
                    city="Springfield",
                    region="IL",
                    postal_code="62701",
                    country="US",
                    label="Home",
                    primary=True,
                )
            ],
        )
        await writer.upsert_addresses(local_id, contact)
        rows = await crm_pool.fetch("SELECT * FROM addresses WHERE contact_id = $1", local_id)
        assert len(rows) == 1
        assert rows[0]["line_1"] == "123 Main St"
        assert rows[0]["city"] == "Springfield"
        assert rows[0]["country"] == "US"

    async def test_upsert_addresses_idempotent(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Addr Idem Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/addr2",
            addresses=[ContactAddress(street="456 Oak Ave", city="Shelbyville", label="Work")],
        )
        await writer.upsert_addresses(local_id, contact)
        await writer.upsert_addresses(local_id, contact)
        rows = await crm_pool.fetch("SELECT * FROM addresses WHERE contact_id = $1", local_id)
        assert len(rows) == 1

    async def test_upsert_important_dates_birthday(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Birthday Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/bd1",
            birthdays=[ContactDate(year=1990, month=6, day=15, label="birthday")],
        )
        await writer.upsert_important_dates(local_id, contact)
        rows = await crm_pool.fetch("SELECT * FROM important_dates WHERE contact_id = $1", local_id)
        assert len(rows) == 1
        assert rows[0]["label"] == "birthday"
        assert rows[0]["month"] == 6
        assert rows[0]["day"] == 15
        assert rows[0]["year"] == 1990

    async def test_upsert_important_dates_anniversary(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Anniversary Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/ann1",
            anniversaries=[ContactDate(month=9, day=20, label="anniversary")],
        )
        await writer.upsert_important_dates(local_id, contact)
        rows = await crm_pool.fetch("SELECT * FROM important_dates WHERE contact_id = $1", local_id)
        assert len(rows) == 1
        assert rows[0]["label"] == "anniversary"

    async def test_upsert_labels_creates_and_assigns(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Label Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/lbl1",
            group_memberships=["contactGroups/myContacts", "contactGroups/starred"],
        )
        await writer.upsert_labels(local_id, contact)
        rows = await crm_pool.fetch(
            """
            SELECT l.name FROM labels l
            JOIN contact_labels cl ON l.id = cl.label_id
            WHERE cl.contact_id = $1
            ORDER BY l.name
            """,
            local_id,
        )
        label_names = [row["name"] for row in rows]
        assert "My Contacts" in label_names
        assert "Starred" in label_names

    async def test_upsert_labels_idempotent(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Label Idem")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/lbl2", group_memberships=["contactGroups/friends"])
        await writer.upsert_labels(local_id, contact)
        await writer.upsert_labels(local_id, contact)
        rows = await crm_pool.fetch(
            "SELECT count(*) AS cnt FROM contact_labels WHERE contact_id = $1", local_id
        )
        assert rows[0]["cnt"] == 1

    async def test_upsert_source_link_creates(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Source Link Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/sl1", etag='"abc123"')
        await writer.upsert_source_link(local_id, contact)
        row = await crm_pool.fetchrow(
            """
                SELECT * FROM contacts_source_links
                WHERE provider = $1 AND account_id = $2 AND external_contact_id = $3
                """,
            "google",
            "acc1",
            "people/sl1",
        )
        assert row is not None
        assert str(row["local_contact_id"]) == str(local_id)
        assert row["source_etag"] == '"abc123"'
        assert row["deleted_at"] is None

    async def test_upsert_source_link_tombstone(self, crm_pool) -> None:
        local_id = await _insert_local_contact(crm_pool, name="Tombstone Test")
        writer = ContactBackfillWriter(crm_pool, provider="google", account_id="acc1")
        # Create link first
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ($1, $2, $3, $4)
            """,
            "google",
            "acc1",
            "people/tomb",
            local_id,
        )
        contact = _make_contact("people/tomb", deleted=True)
        await writer.upsert_source_link(local_id, contact)
        row = await crm_pool.fetchrow(
            "SELECT deleted_at FROM contacts_source_links WHERE external_contact_id = $1",
            "people/tomb",
        )
        assert row["deleted_at"] is not None


# ---------------------------------------------------------------------------
# ContactBackfillEngine integration tests (orchestration, activity feed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestContactBackfillEngine:
    async def test_new_contact_created_with_all_tables(self, crm_pool) -> None:
        """New sync contact creates CRM records in all relevant tables."""
        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/full1",
            display_name="Full Test",
            first_name="Full",
            last_name="Test",
            emails=[
                ContactEmail(
                    value="full@example.com", primary=True, normalized_value="full@example.com"
                )
            ],
            phones=[ContactPhone(value="+1-555-0100", primary=True)],
            addresses=[
                ContactAddress(street="1 Main", city="Townsville", label="Home", primary=True)
            ],
            organizations=[ContactOrganization(company="Full Corp", title="Manager")],
            birthdays=[ContactDate(month=3, day=25, year=1985, label="birthday")],
            photos=[ContactPhoto(url="https://img.example.com/p.jpg", primary=True)],
            group_memberships=["contactGroups/myContacts"],
        )
        await engine(contact)

        # Verify contact was created
        rows = await crm_pool.fetch("SELECT * FROM contacts WHERE first_name = 'Full'")
        assert len(rows) == 1
        local_id = uuid.UUID(str(rows[0]["id"]))

        # Verify contact_info
        info_rows = await crm_pool.fetch(
            "SELECT type, value FROM contact_info WHERE contact_id = $1 ORDER BY type", local_id
        )
        types_values = {(r["type"], r["value"].lower()) for r in info_rows}
        assert ("email", "full@example.com") in types_values
        assert ("phone", "+1-555-0100") in types_values

        # Verify address
        addr_rows = await crm_pool.fetch(
            "SELECT city FROM addresses WHERE contact_id = $1", local_id
        )
        assert len(addr_rows) == 1
        assert addr_rows[0]["city"] == "Townsville"

        # Verify birthday
        date_rows = await crm_pool.fetch(
            "SELECT label, month, day FROM important_dates WHERE contact_id = $1", local_id
        )
        assert len(date_rows) == 1
        assert date_rows[0]["label"] == "birthday"
        assert date_rows[0]["month"] == 3

        # Verify label
        label_rows = await crm_pool.fetch(
            """
            SELECT l.name FROM labels l
            JOIN contact_labels cl ON l.id = cl.label_id
            WHERE cl.contact_id = $1
            """,
            local_id,
        )
        assert any(r["name"] == "My Contacts" for r in label_rows)

        # Verify source link
        link_row = await crm_pool.fetchrow(
            """
            SELECT local_contact_id FROM contacts_source_links
            WHERE external_contact_id = 'people/full1'
            """,
        )
        assert link_row is not None

        # Verify activity feed
        feed_rows = await crm_pool.fetch(
            "SELECT type FROM activity_feed WHERE contact_id = $1", local_id
        )
        assert any(r["type"] == "contact_synced" for r in feed_rows)

    async def test_existing_contact_matched_by_email_no_duplication(self, crm_pool) -> None:
        """Existing contacts matched by email are updated without duplication."""
        # Create an existing contact with email
        local_id = await _insert_local_contact(crm_pool, name="Bob Existing", first_name="Bob")
        await crm_pool.execute(
            "INSERT INTO contact_info (contact_id, type, value) VALUES ($1, 'email', $2)",
            local_id,
            "bob@existing.com",
        )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/bobx",
            display_name="Bob Existing",
            first_name="Bob",
            last_name="Existing",
            emails=[
                ContactEmail(
                    value="bob@existing.com", primary=True, normalized_value="bob@existing.com"
                )
            ],
        )
        await engine(contact)

        # No duplicate contacts
        contact_rows = await crm_pool.fetch("SELECT id FROM contacts WHERE first_name = 'Bob'")
        assert len(contact_rows) == 1

        # Email not duplicated
        email_rows = await crm_pool.fetch(
            "SELECT id FROM contact_info WHERE contact_id = $1 AND type = 'email'", local_id
        )
        assert len(email_rows) == 1

    async def test_idempotent_double_sync(self, crm_pool) -> None:
        """Calling apply twice for same contact is idempotent."""
        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/idem1",
            emails=[
                ContactEmail(value="idem@test.com", primary=True, normalized_value="idem@test.com")
            ],
            birthdays=[ContactDate(month=1, day=1, year=2000, label="birthday")],
        )
        await engine(contact)
        await engine(contact)

        # Only one contact created
        rows = await crm_pool.fetch("SELECT id FROM contacts WHERE first_name = 'Alice'")
        assert len(rows) == 1
        local_id = uuid.UUID(str(rows[0]["id"]))

        # Only one birthday
        dates = await crm_pool.fetch(
            "SELECT id FROM important_dates WHERE contact_id = $1", local_id
        )
        assert len(dates) == 1

    async def test_source_owned_field_updated_on_sync(self, crm_pool) -> None:
        """Source-owned fields update on sync."""
        # Pre-existing contact with source provenance on first_name

        meta = {}
        _deep_set(meta, "sources.contacts.google.first_name", "OldFirst")
        local_id = await _insert_local_contact(
            crm_pool,
            name="OldFirst Last",
            first_name="OldFirst",
            last_name="Last",
            metadata=meta,
        )
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ('google', 'acc1', 'people/upd1', $1)
            """,
            local_id,
        )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/upd1", first_name="NewFirst", last_name="Last")
        await engine(contact)

        row = await crm_pool.fetchrow("SELECT first_name FROM contacts WHERE id = $1", local_id)
        assert row["first_name"] == "NewFirst"

    async def test_locally_edited_field_preserved(self, crm_pool) -> None:
        """Locally-edited fields (no provenance) are preserved on sync."""
        local_id = await _insert_local_contact(
            crm_pool,
            name="Local Edit",
            first_name="LocallyEdited",
            last_name="Preserved",
            metadata={},  # No provenance
        )
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ('google', 'acc1', 'people/local1', $1)
            """,
            local_id,
        )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/local1", first_name="SyncFirst", last_name="Preserved")
        await engine(contact)

        row = await crm_pool.fetchrow("SELECT first_name FROM contacts WHERE id = $1", local_id)
        assert row["first_name"] == "LocallyEdited"  # Preserved

    async def test_conflict_creates_activity_entry(self, crm_pool) -> None:
        """Ambiguous field changes emit activity feed entries."""
        local_id = await _insert_local_contact(
            crm_pool,
            name="Conflict Test",
            first_name="Conflict",
            metadata={},  # No provenance — local edit
        )
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ('google', 'acc1', 'people/conf1', $1)
            """,
            local_id,
        )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/conf1", first_name="DifferentFirst")
        await engine(contact)

        feed_rows = await crm_pool.fetch(
            "SELECT type FROM activity_feed WHERE contact_id = $1", local_id
        )
        assert any(r["type"] == "contact_sync_conflict" for r in feed_rows)

    async def test_source_tombstone_marks_link_deleted(self, crm_pool) -> None:
        """Source tombstones mark links as deleted without destroying CRM records."""
        local_id = await _insert_local_contact(crm_pool, name="Tombstone Person")
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ('google', 'acc1', 'people/tomb1', $1)
            """,
            local_id,
        )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact("people/tomb1", deleted=True)
        await engine(contact)

        # CRM record still exists
        contact_row = await crm_pool.fetchrow("SELECT id FROM contacts WHERE id = $1", local_id)
        assert contact_row is not None

        # Source link marked deleted
        link_row = await crm_pool.fetchrow(
            """
            SELECT deleted_at FROM contacts_source_links
            WHERE external_contact_id = 'people/tomb1'
            """
        )
        assert link_row["deleted_at"] is not None

        # Activity feed entry
        feed_rows = await crm_pool.fetch(
            "SELECT type FROM activity_feed WHERE contact_id = $1", local_id
        )
        assert any(r["type"] == "contact_sync_deleted_source" for r in feed_rows)

    async def test_ambiguous_name_match_skipped_with_log(self, crm_pool) -> None:
        """Ambiguous name matches skip auto-merge and do not create contacts."""
        for _ in range(2):
            await _insert_local_contact(
                crm_pool, name="Duplicate Name", first_name="Duplicate", last_name="Name"
            )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/ambig",
            display_name="Duplicate Name",
            first_name="Duplicate",
            last_name="Name",
        )
        # Should not raise, should not create new contact
        await engine(contact)

        rows = await crm_pool.fetch("SELECT id FROM contacts WHERE first_name = 'Duplicate'")
        assert len(rows) == 2  # Still 2, no new one created

    async def test_urls_and_usernames_in_contact_info(self, crm_pool) -> None:
        """URLs and usernames are stored as contact_info rows."""
        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/url1",
            urls=[ContactUrl(value="https://example.com/profile", label="homepage")],
            usernames=[ContactUsername(value="alice_handle", service="twitter")],
        )
        await engine(contact)

        rows = await crm_pool.fetch(
            "SELECT type, value FROM contact_info WHERE value IN ($1, $2)",
            "https://example.com/profile",
            "alice_handle",
        )
        types = {r["type"] for r in rows}
        assert "website" in types
        assert "other" in types

    async def test_sync_update_activity_on_field_change(self, crm_pool) -> None:
        """Field updates emit contact_sync_updated activity entry."""

        meta = {}
        _deep_set(meta, "sources.contacts.google.company", "OldCorp")
        local_id = await _insert_local_contact(
            crm_pool,
            name="Update Activity",
            company="OldCorp",
            metadata=meta,
        )
        await crm_pool.execute(
            """
            INSERT INTO contacts_source_links (
            provider, account_id, external_contact_id, local_contact_id)
            VALUES ('google', 'acc1', 'people/upd_act', $1)
            """,
            local_id,
        )

        engine = ContactBackfillEngine(crm_pool, provider="google", account_id="acc1")
        contact = _make_contact(
            "people/upd_act",
            organizations=[ContactOrganization(company="NewCorp")],
        )
        await engine(contact)

        feed_rows = await crm_pool.fetch(
            "SELECT type FROM activity_feed WHERE contact_id = $1", local_id
        )
        assert any(r["type"] == "contact_sync_updated" for r in feed_rows)
