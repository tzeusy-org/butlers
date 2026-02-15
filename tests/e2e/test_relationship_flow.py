"""E2E tests for relationship butler spawner and tool integration.

Tests the relationship butler's contact and note management through spawner triggers.
Validates that spawner.trigger() correctly creates contacts and logs notes in the
relationship butler database.

Scenarios:
1. Contact creation via spawner: Trigger with contact creation request, verify contact row
2. Note logging: Trigger with a note about a contact, verify notes table
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from asyncpg.pool import Pool

    from tests.e2e.conftest import ButlerEcosystem


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Scenario 1: Contact creation via spawner
# ---------------------------------------------------------------------------


async def test_contact_creation_via_spawner(
    butler_ecosystem: ButlerEcosystem,
    relationship_pool: Pool,
) -> None:
    """Trigger relationship butler spawner to create a contact, verify DB row.

    Uses the relationship spawner to create a contact via real Claude Code call.
    Validates that the contact is created in the database with the correct name field.
    """
    relationship_daemon = butler_ecosystem.butlers["relationship"]
    assert relationship_daemon.spawner is not None, "Relationship spawner must be initialized"

    # Trigger spawner with contact creation request
    prompt = "Add a new contact: Sarah Chen, my colleague at Anthropic"
    result = await relationship_daemon.spawner.trigger(prompt)

    # Verify spawner returned success
    assert result is not None, "Spawner should return result"
    assert isinstance(result, dict), "Spawner result should be dict"

    # Query contacts table for Sarah Chen
    contact_row = await relationship_pool.fetchrow(
        """
        SELECT * FROM contacts
        WHERE first_name ILIKE '%sarah%'
           OR last_name ILIKE '%chen%'
           OR name ILIKE '%sarah%'
        ORDER BY created_at DESC
        LIMIT 1
        """
    )

    # Validate contact was created
    assert contact_row is not None, "Contact should be created in database"

    # Check that name contains expected values (flexible for either first_name/last_name or name)
    contact_dict = dict(contact_row)
    name_check = (
        ("first_name" in contact_dict and contact_dict.get("first_name", "").lower() == "sarah")
        or ("last_name" in contact_dict and contact_dict.get("last_name", "").lower() == "chen")
        or ("name" in contact_dict and "sarah" in contact_dict.get("name", "").lower())
    )
    assert name_check, (
        f"Contact should contain 'Sarah' or 'Chen' in name fields. Got: {contact_dict}"
    )

    # Verify session was logged
    session_row = await relationship_pool.fetchrow(
        """
        SELECT * FROM sessions
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    assert session_row is not None, "Session should be logged"
    assert session_row["success"] is True, "Session should be marked as successful"


# ---------------------------------------------------------------------------
# Scenario 2: Note logging via spawner
# ---------------------------------------------------------------------------


async def test_note_logging_via_spawner(
    butler_ecosystem: ButlerEcosystem,
    relationship_pool: Pool,
) -> None:
    """Trigger relationship butler spawner to log a note, verify notes table.

    First creates a contact, then logs a note about that contact.
    Validates that the note is stored in the notes table with correct content.
    """
    relationship_daemon = butler_ecosystem.butlers["relationship"]
    assert relationship_daemon.spawner is not None, "Relationship spawner must be initialized"

    # Step 1: Create a contact first
    contact_creation_prompt = "Add a new contact: John Smith, my friend"
    await relationship_daemon.spawner.trigger(contact_creation_prompt)

    # Query for the created contact to get its ID
    contact_row = await relationship_pool.fetchrow(
        """
        SELECT id, name, first_name FROM contacts
        WHERE first_name ILIKE '%john%'
           OR name ILIKE '%john%'
        ORDER BY created_at DESC
        LIMIT 1
        """
    )

    assert contact_row is not None, "Contact should be created before note logging"
    contact_id = contact_row["id"]

    # Step 2: Log a note about the contact
    note_prompt = "Note about John Smith: He loves hiking and specialty coffee"
    result = await relationship_daemon.spawner.trigger(note_prompt)

    # Verify spawner returned success
    assert result is not None, "Spawner should return result"
    assert isinstance(result, dict), "Spawner result should be dict"

    # Query notes table for the logged note
    note_row = await relationship_pool.fetchrow(
        """
        SELECT * FROM notes
        WHERE contact_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        contact_id,
    )

    # Validate note was created
    assert note_row is not None, "Note should be logged in database"

    # Check note content (handle both 'content' and 'body' column names for compatibility)
    note_dict = dict(note_row)
    note_text = note_dict.get("body") or note_dict.get("content") or ""
    assert len(note_text) > 0, "Note should have non-empty content/body"
    assert "hiking" in note_text.lower() or "coffee" in note_text.lower(), (
        f"Note should contain expected content. Got: {note_text}"
    )

    # Verify contact_id matches
    assert note_dict["contact_id"] == contact_id, "Note should be linked to correct contact"


# ---------------------------------------------------------------------------
# Scenario 3: Multiple contacts and session tracking
# ---------------------------------------------------------------------------


async def test_multiple_contacts_session_tracking(
    butler_ecosystem: ButlerEcosystem,
    relationship_pool: Pool,
) -> None:
    """Create multiple contacts via spawner and verify session tracking.

    Tests that multiple spawner invocations create separate sessions
    and that contacts are correctly isolated.
    """
    relationship_daemon = butler_ecosystem.butlers["relationship"]
    assert relationship_daemon.spawner is not None, "Relationship spawner must be initialized"

    # Count initial contacts and sessions
    initial_contacts_count = await relationship_pool.fetchval("SELECT COUNT(*) FROM contacts")
    initial_sessions_count = await relationship_pool.fetchval("SELECT COUNT(*) FROM sessions")

    # Create first contact
    prompt1 = "Add contact: Alice Johnson, alice@example.com"
    result1 = await relationship_daemon.spawner.trigger(prompt1)
    assert result1 is not None, "First spawner call should return result"

    # Create second contact
    prompt2 = "Add contact: Bob Williams, my neighbor"
    result2 = await relationship_daemon.spawner.trigger(prompt2)
    assert result2 is not None, "Second spawner call should return result"

    # Verify contact count increased by 2
    final_contacts_count = await relationship_pool.fetchval("SELECT COUNT(*) FROM contacts")
    assert final_contacts_count >= initial_contacts_count + 2, (
        "Should have created at least 2 new contacts"
    )

    # Verify session count increased by 2
    final_sessions_count = await relationship_pool.fetchval("SELECT COUNT(*) FROM sessions")
    assert final_sessions_count >= initial_sessions_count + 2, (
        "Should have created at least 2 new sessions"
    )

    # Verify both contacts exist with distinct names
    alice_row = await relationship_pool.fetchrow(
        """
        SELECT * FROM contacts
        WHERE first_name ILIKE '%alice%' OR name ILIKE '%alice%'
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    bob_row = await relationship_pool.fetchrow(
        """
        SELECT * FROM contacts
        WHERE first_name ILIKE '%bob%' OR name ILIKE '%bob%'
        ORDER BY created_at DESC
        LIMIT 1
        """
    )

    assert alice_row is not None, "Alice contact should exist"
    assert bob_row is not None, "Bob contact should exist"
    assert alice_row["id"] != bob_row["id"], "Contacts should have different IDs"

    # Verify all recent sessions succeeded
    recent_sessions = await relationship_pool.fetch(
        """
        SELECT * FROM sessions
        ORDER BY started_at DESC
        LIMIT 2
        """
    )

    assert len(recent_sessions) >= 2, "Should have at least 2 recent sessions"
    for session in recent_sessions:
        assert session["success"] is True, f"Session {session['id']} should be successful"
