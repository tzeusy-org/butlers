"""Tests for vCard import/export functionality."""

from __future__ import annotations

import shutil
import sys
from unittest.mock import MagicMock, patch

import asyncpg
import pytest
import vobject

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    # Keep tests on the same loop as async fixtures/pools to avoid asyncpg
    # "Future attached to a different loop" failures under xdist.
    pytest.mark.asyncio(loop_scope="session"),
]

# Tables to truncate between tests (in dependency order — children first).
# The relationship chain runs without a schema override, so all unqualified
# tables land in "public" (same as the original hand-rolled DDL approach).
# Cross-butler identity tables are also in "public" (created by core chain).
#
# Note: rel_007 renames "reminders" → "_reminders_backup"; that table is not
# used by vcard tests so it is omitted from the list.
_TRUNCATE_TABLES = [
    # children first (FK → public.contacts)
    # activity_feed, notes, gifts, loans, interactions dropped by rel_009
    # quick_facts dropped by rel_025 (bu-6d5v2)
    "public.important_dates",
    "public.addresses",
    "public.relationships",
    "public.group_members",
    "public.contact_labels",
    # memory / public children
    "public.memory_links",
    # facts (no FK parent, but seeded predicate_registry rows are kept)
    "public.facts",
    # public identity tables (contacts references entities)
    # public.contact_info dropped in migration bead 10 (core_115 / bu-e2ja9)
    "public.contacts",
    "public.entities",
]


@pytest.fixture(autouse=True, scope="module")
def patch_embedding_engine():
    """Patch get_embedding_engine for all tests in this module.

    SPO fact writes (via store_fact) require an EmbeddingEngine. In tests
    there is no real model, so we return a deterministic fake.
    """
    engine = MagicMock()
    engine.embed.return_value = [0.1] * 384
    with patch("butlers.modules.memory.tools.get_embedding_engine", return_value=engine):
        for mod_name in (
            "butlers.tools.relationship.feed",
            "butlers.tools.relationship.interactions",
            "butlers.tools.relationship.notes",
            "butlers.tools.relationship.facts",
            "butlers.tools.relationship.gifts",
            "butlers.tools.relationship.loans",
            "butlers.tools.relationship.tasks",
            "butlers.tools.relationship.reminders",
            "butlers.tools.relationship.life_events",
        ):
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "_embedding_engine"):
                mod._embedding_engine = None
        yield engine


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a database with real Alembic migrations applied (once per module).

    Runs three chains in order:
    - ``core``       — public.entities, public.contacts, … (contact_info dropped by core_115)
    - ``memory``     — facts, predicate_registry, memory_links, … (public schema)
    - ``relationship`` — relationship tables (no schema override; all land in public)

    The ``relationship`` chain intentionally runs **without** a schema override so
    that unqualified table names (contacts, etc.) land in ``public``,
    matching the original hand-rolled DDL behaviour.  The rel_003 migration detects
    that ``relationship.contacts`` does not exist and skips the consolidation step
    (correct for this flat-schema test topology).

    Adding a new migration (column, table, index) requires *no change* here —
    the next test run picks it up automatically.
    """
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory", "relationship"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    """Return an asyncpg pool scoped to the migrated DB with clean tables.

    The database schema is created once per module by ``migrated_db_url``.
    This fixture truncates all data tables before each test so that tests
    are independent without the overhead of re-running migrations.

    All tables live in ``public`` (the relationship chain runs without a schema
    override), so the default search_path is sufficient for unqualified names.
    """
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )

    # Truncate data tables in dependency order (children before parents).
    # CASCADE handles any residual FK children automatically.
    for table in _TRUNCATE_TABLES:
        await p.execute(f"TRUNCATE TABLE {table} CASCADE")  # noqa: S608

    yield p
    await p.close()


async def test_export_single_contact_basic(pool):
    """Test exporting a single contact with basic fields."""
    from butlers.tools.relationship import contact_create, contact_export_vcard

    # Create a basic contact
    contact = await contact_create(pool, "John Doe")

    # Export as vCard
    vcf = await contact_export_vcard(pool, contact["id"])

    # Verify vCard structure
    assert "BEGIN:VCARD" in vcf
    assert "END:VCARD" in vcf
    assert "FN:John Doe" in vcf
    assert "VERSION:" in vcf


async def test_export_all_contacts(pool):
    """Test exporting all contacts."""
    from butlers.tools.relationship import contact_create, contact_export_vcard

    await contact_create(pool, "Alice Smith")
    await contact_create(pool, "Bob Jones")
    await contact_create(pool, "Charlie Brown")

    vcf = await contact_export_vcard(pool)

    # Should contain 3 vCards
    assert vcf.count("BEGIN:VCARD") == 3
    assert vcf.count("END:VCARD") == 3
    assert "Alice Smith" in vcf
    assert "Bob Jones" in vcf
    assert "Charlie Brown" in vcf


async def test_export_excludes_archived(pool):
    """Test that archived contacts are not exported."""
    from butlers.tools.relationship import contact_archive, contact_create, contact_export_vcard

    await contact_create(pool, "Active User")
    c2 = await contact_create(pool, "Archived User")
    await contact_archive(pool, c2["id"])

    vcf = await contact_export_vcard(pool)

    assert vcf.count("BEGIN:VCARD") == 1
    assert "Active User" in vcf
    assert "Archived User" not in vcf


async def test_import_basic_vcard(pool):
    """Test importing a basic vCard."""
    from butlers.tools.relationship import contact_import_vcard, contact_search

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:John Doe
N:Doe;John;;;
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    assert contacts[0]["name"] == "John Doe"

    # Verify it's in the database
    results = await contact_search(pool, "John Doe")
    assert len(results) == 1


async def test_import_vcard_with_address(pool):
    """Test importing a vCard with address."""
    from butlers.tools.relationship import contact_import_vcard

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Bob Johnson
N:Johnson;Bob;;;
ADR;TYPE=HOME:;;456 Oak Ave;Boston;MA;02101;USA
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    details = contacts[0]["details"]
    assert len(details["addresses"]) == 1
    addr = details["addresses"][0]
    assert addr["street"] == "456 Oak Ave"
    assert addr["city"] == "Boston"
    assert addr["state"] == "MA"
    assert addr["postal_code"] == "02101"
    assert addr["country"] == "US"


async def test_import_vcard_with_unrecognized_country_ignores_country(pool):
    """Unrecognized country names should not be truncated into invalid pseudo-codes."""
    from butlers.tools.relationship import contact_import_vcard

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Casey Lane
N:Lane;Casey;;;
ADR;TYPE=HOME:;;123 Market St;San Francisco;CA;94105;United States
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    details = contacts[0]["details"]
    assert len(details["addresses"]) == 1
    assert details["addresses"][0]["country"] is None


async def test_import_vcard_with_birthday_no_year(pool):
    """Test importing a vCard with birthday without year."""
    from butlers.tools.relationship import contact_import_vcard, date_list

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Diana Prince
N:Prince;Diana;;;
BDAY:--12-25
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    dates = await date_list(pool, contacts[0]["id"])
    assert len(dates) == 1
    assert dates[0]["label"] == "birthday"
    assert dates[0]["month"] == 12
    assert dates[0]["day"] == 25
    assert dates[0]["year"] is None


async def test_import_multiple_vcards(pool):
    """Test importing multiple vCards in one string."""
    from butlers.tools.relationship import contact_import_vcard

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Alice Adams
N:Adams;Alice;;;
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Bob Baker
N:Baker;Bob;;;
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Carol Clark
N:Clark;Carol;;;
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 3
    names = [c["name"] for c in contacts]
    assert "Alice Adams" in names
    assert "Bob Baker" in names
    assert "Carol Clark" in names


async def test_import_handles_missing_fields_gracefully(pool):
    """Test that import handles vCards with missing optional fields."""
    from butlers.tools.relationship import contact_import_vcard

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Minimal Contact
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    assert contacts[0]["name"] == "Minimal Contact"
    details = contacts[0]["details"]
    assert details["phones"] == []
    assert details["emails"] == []
    assert details["addresses"] == []


async def test_import_invalid_vcard_raises_error(pool):
    """Test that importing invalid vCard content raises an error."""
    from butlers.tools.relationship import contact_import_vcard

    # vobject.base.ParseError is wrapped by our ValueError
    with pytest.raises((ValueError, vobject.base.ParseError)):
        await contact_import_vcard(pool, "NOT A VCARD")


async def test_round_trip_basic_contact(pool):
    """Test export then import produces equivalent contact data."""
    from butlers.tools.relationship import (
        contact_create,
        contact_export_vcard,
        contact_import_vcard,
    )

    # Create original contact
    details = {
        "phones": [{"number": "+1-555-1111", "type": "CELL"}],
        "emails": [{"address": "test@example.com", "type": "HOME"}],
    }
    original = await contact_create(pool, "Round Trip Test", details)

    # Export
    vcf = await contact_export_vcard(pool, original["id"])

    # Import (creates a new contact)
    imported = await contact_import_vcard(pool, vcf)

    assert len(imported) == 1
    assert imported[0]["name"] == original["name"]

    # Verify contact info
    imported_details = imported[0]["details"]
    assert len(imported_details["phones"]) == 1
    assert imported_details["phones"][0]["number"] == "+1-555-1111"
    assert len(imported_details["emails"]) == 1
    assert imported_details["emails"][0]["address"] == "test@example.com"


async def test_round_trip_full_contact(pool):
    """Test round trip with all supported fields."""
    from butlers.tools.relationship import (
        contact_create,
        contact_export_vcard,
        contact_get,
        contact_import_vcard,
        contact_update,
        date_add,
        date_list,
    )

    # Create comprehensive contact
    details = {
        "phones": [{"number": "+1-555-2222", "type": "WORK"}],
        "emails": [{"address": "full@test.com", "type": "WORK"}],
        "addresses": [
            {
                "street": "789 Pine St",
                "city": "Portland",
                "state": "OR",
                "postal_code": "97201",
                "country": "USA",
                "type": "WORK",
            }
        ],
    }
    original = await contact_create(pool, "Full Test", details)
    await date_add(pool, original["id"], "birthday", 5, 10, 1988)
    await contact_update(pool, original["id"], company="Test Corp", job_title="Test Engineer")

    # Export and import
    vcf = await contact_export_vcard(pool, original["id"])
    imported = await contact_import_vcard(pool, vcf)

    assert len(imported) == 1
    new_contact = imported[0]

    # Verify all fields
    assert new_contact["name"] == "Full Test"

    new_details = new_contact["details"]
    assert len(new_details["phones"]) == 1
    assert new_details["phones"][0]["number"] == "+1-555-2222"
    assert len(new_details["emails"]) == 1
    assert new_details["emails"][0]["address"] == "full@test.com"
    assert len(new_details["addresses"]) == 1
    assert new_details["addresses"][0]["city"] == "Portland"

    new_dates = await date_list(pool, new_contact["id"])
    assert len(new_dates) == 1
    assert new_dates[0]["month"] == 5
    assert new_dates[0]["day"] == 10
    assert new_dates[0]["year"] == 1988

    # ORG/TITLE round-trip via public.contacts
    new_contact_full = await contact_get(pool, new_contact["id"])
    assert new_contact_full["company"] == "Test Corp"
    assert new_contact_full["job_title"] == "Test Engineer"


async def test_export_produces_valid_vcard_30(pool):
    """Test that exported vCard is valid vCard 3.0 format."""
    from butlers.tools.relationship import contact_create, contact_export_vcard

    contact = await contact_create(pool, "Valid Test")
    vcf = await contact_export_vcard(pool, contact["id"])

    # Parse to validate
    vcards = list(vobject.readComponents(vcf))
    assert len(vcards) == 1

    vcard = vcards[0]
    assert vcard.name == "VCARD"
    assert hasattr(vcard, "version")
    assert hasattr(vcard, "fn")
    assert hasattr(vcard, "n")
    assert vcard.fn.value == "Valid Test"


async def test_import_vcard_with_multiple_phones_emails(pool):
    """Test importing a vCard with multiple phones and emails."""
    from butlers.tools.relationship import contact_import_vcard

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Multi Contact
N:Contact;Multi;;;
TEL;TYPE=CELL:+1-555-1111
TEL;TYPE=WORK:+1-555-2222
TEL;TYPE=HOME:+1-555-3333
EMAIL;TYPE=WORK:work@example.com
EMAIL;TYPE=HOME:home@example.com
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    details = contacts[0]["details"]

    assert len(details["phones"]) == 3
    phone_numbers = [p["number"] for p in details["phones"]]
    assert "+1-555-1111" in phone_numbers
    assert "+1-555-2222" in phone_numbers
    assert "+1-555-3333" in phone_numbers

    assert len(details["emails"]) == 2
    SOURCE_EMAILes = [e["address"] for e in details["emails"]]
    assert "work@example.com" in SOURCE_EMAILes
    assert "home@example.com" in SOURCE_EMAILes
