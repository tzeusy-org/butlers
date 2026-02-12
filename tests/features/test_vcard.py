"""Tests for vCard import/export functionality."""

from __future__ import annotations

import shutil
import uuid

import pytest
import vobject

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with relationship tables and return a pool."""
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    # Create relationship tables (mirrors Alembic relationship migration)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT,
            details JSONB DEFAULT '{}',
            archived_at TIMESTAMPTZ,
            first_name TEXT,
            last_name TEXT,
            nickname TEXT,
            company TEXT,
            job_title TEXT,
            gender TEXT,
            pronouns TEXT,
            avatar_url TEXT,
            listed BOOLEAN DEFAULT true,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts (first_name, last_name)
    """)
    await p.execute("""
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
    await p.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            title TEXT,
            body TEXT,
            content TEXT,
            emotion TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS contact_info (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            value TEXT NOT NULL,
            label TEXT,
            is_primary BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS addresses (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            label TEXT NOT NULL DEFAULT 'Home',
            line_1 TEXT NOT NULL,
            line_2 TEXT,
            city TEXT,
            province TEXT,
            postal_code TEXT,
            country TEXT,
            is_current BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS quick_facts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (contact_id, key)
        )
    """)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS activity_feed (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            action TEXT,
            summary TEXT,
            type TEXT NOT NULL,
            description TEXT NOT NULL,
            entity_type TEXT,
            entity_id UUID,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_activity_feed_contact_created
            ON activity_feed (contact_id, created_at)
    """)

    yield p
    await db.close()


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


async def test_export_contact_with_phone_email(pool):
    """Test exporting a contact with phone and email."""
    from butlers.tools.relationship import contact_create, contact_export_vcard

    details = {
        "phones": [{"number": "+1-555-1234", "type": "CELL"}],
        "emails": [{"address": "john@example.com", "type": "WORK"}],
    }
    contact = await contact_create(pool, "John Doe", details)

    vcf = await contact_export_vcard(pool, contact["id"])

    assert "TEL" in vcf
    assert "+1-555-1234" in vcf
    assert "EMAIL" in vcf
    assert "john@example.com" in vcf


async def test_export_contact_with_address(pool):
    """Test exporting a contact with address."""
    from butlers.tools.relationship import contact_create, contact_export_vcard

    details = {
        "addresses": [
            {
                "street": "123 Main St",
                "city": "Springfield",
                "state": "IL",
                "postal_code": "62701",
                "country": "USA",
                "type": "HOME",
            }
        ]
    }
    contact = await contact_create(pool, "Jane Smith", details)

    vcf = await contact_export_vcard(pool, contact["id"])

    assert "ADR" in vcf
    assert "123 Main St" in vcf
    assert "Springfield" in vcf
    assert "62701" in vcf


async def test_export_contact_with_birthday(pool):
    """Test exporting a contact with birthday."""
    from butlers.tools.relationship import contact_create, contact_export_vcard, date_add

    contact = await contact_create(pool, "Alice Johnson")
    await date_add(pool, contact["id"], "birthday", 3, 15, 1990)

    vcf = await contact_export_vcard(pool, contact["id"])

    assert "BDAY" in vcf
    assert "1990-03-15" in vcf


async def test_export_contact_with_org_title(pool):
    """Test exporting a contact with organization and title."""
    from butlers.tools.relationship import contact_create, contact_export_vcard, fact_set

    contact = await contact_create(pool, "Bob Brown")
    await fact_set(pool, contact["id"], "company", "Acme Corp")
    await fact_set(pool, contact["id"], "job_title", "Software Engineer")

    vcf = await contact_export_vcard(pool, contact["id"])

    assert "ORG" in vcf
    assert "Acme Corp" in vcf
    assert "TITLE" in vcf
    assert "Software Engineer" in vcf


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


async def test_import_vcard_with_phone_email(pool):
    """Test importing a vCard with phone and email."""
    from butlers.tools.relationship import contact_import_vcard

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Jane Smith
N:Smith;Jane;;;
TEL;TYPE=CELL:+1-555-9876
EMAIL;TYPE=WORK:jane@company.com
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    contact = contacts[0]
    assert contact["name"] == "Jane Smith"

    details = contact["details"]
    assert len(details["phones"]) == 1
    assert details["phones"][0]["number"] == "+1-555-9876"
    assert len(details["emails"]) == 1
    assert details["emails"][0]["address"] == "jane@company.com"


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


async def test_import_vcard_with_birthday(pool):
    """Test importing a vCard with birthday."""
    from butlers.tools.relationship import contact_import_vcard, date_list

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Charlie Davis
N:Davis;Charlie;;;
BDAY:1985-07-20
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    dates = await date_list(pool, contacts[0]["id"])
    assert len(dates) == 1
    assert dates[0]["label"] == "birthday"
    assert dates[0]["month"] == 7
    assert dates[0]["day"] == 20
    assert dates[0]["year"] == 1985


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


async def test_import_vcard_with_org_title(pool):
    """Test importing a vCard with organization and title."""
    from butlers.tools.relationship import contact_import_vcard, fact_list

    vcf = """BEGIN:VCARD
VERSION:3.0
FN:Eve Anderson
N:Anderson;Eve;;;
ORG:Tech Innovations Inc.
TITLE:Product Manager
END:VCARD"""

    contacts = await contact_import_vcard(pool, vcf)

    assert len(contacts) == 1
    facts = await fact_list(pool, contacts[0]["id"])
    facts_dict = {f["key"]: f["value"] for f in facts}

    assert facts_dict["company"] == "Tech Innovations Inc."
    assert facts_dict["job_title"] == "Product Manager"


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
        contact_import_vcard,
        date_add,
        date_list,
        fact_list,
        fact_set,
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
    await fact_set(pool, original["id"], "company", "Test Corp")
    await fact_set(pool, original["id"], "job_title", "Test Engineer")

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

    new_facts = await fact_list(pool, new_contact["id"])
    new_facts_dict = {f["key"]: f["value"] for f in new_facts}
    assert new_facts_dict["company"] == "Test Corp"
    assert new_facts_dict["job_title"] == "Test Engineer"


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
