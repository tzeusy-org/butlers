"""interaction_sync must not count automated mail (out-of-office, bounces) as contact outreach."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import pytest

from roster.relationship.tests.calendar_projection import database_for
from roster.relationship.tests.test_jobs import _insert_contact_anchor, _insert_contact_info

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


async def _insert_inbound_email(pool, *, sender: str, automated_kind: str | None) -> None:
    received_at = datetime.now(UTC) - timedelta(hours=1)
    control: dict[str, str] = {"policy_tier": "default"}
    if automated_kind is not None:
        control["automated_kind"] = automated_kind
    async with database_for(pool).control("switchboard") as conn:
        await conn.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)
        await conn.execute(
            "INSERT INTO switchboard.message_inbox "
            "(received_at, request_context, raw_payload, direction, normalized_text) "
            "VALUES ($1, $2, $3, 'inbound', 'synthetic automated reply')",
            received_at,
            {"source_sender_identity": sender, "source_channel": "email"},
            {"control": control},
        )


async def _interaction_fact_count(pool, contact_id: str) -> int:
    entity_id = await pool.fetchval(
        "SELECT entity_id FROM relationship.contact_entity_map WHERE contact_id = $1::uuid",
        contact_id,
    )
    return await pool.fetchval(
        "SELECT count(*) FROM facts WHERE subject = $1"
        " AND predicate LIKE 'interaction_%' AND scope = 'relationship'",
        f"entity:{entity_id}",
    )


async def test_automated_reply_produces_no_interaction_fact(interaction_sync_pool):
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Auto")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="auto@example.test"
        )
        await _insert_inbound_email(pool, sender="auto@example.test", automated_kind="auto_reply")

        result = await run_interaction_sync(pool)

        assert result["errors"] == 0
        assert result["logged"] == 0
        assert await _interaction_fact_count(pool, contact_id) == 0


async def test_identical_human_email_still_produces_its_fact(interaction_sync_pool):
    from butlers.jobs._roster.relationship_jobs import run_interaction_sync

    async with interaction_sync_pool() as pool:
        contact_id = await _insert_contact_anchor(pool, first_name="Human")
        await _insert_contact_info(
            pool, contact_id=contact_id, ci_type="email", value="human@example.test"
        )
        await _insert_inbound_email(pool, sender="human@example.test", automated_kind=None)

        result = await run_interaction_sync(pool)

        assert result["errors"] == 0
        assert result["logged"] == 1
        assert await _interaction_fact_count(pool, contact_id) == 1
