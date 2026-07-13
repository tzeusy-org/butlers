"""Real-Postgres regression: rule-promotion apply/auto-apply (bu-o62bc, bead 4).

Exercises ``rule_promotion_apply`` against a fully migrated switchboard schema
so the mint transaction (INSERT ingestion_rules with provenance + UPDATE the
suggestion, atomically, with the FOR UPDATE status guard), the auto-apply
selection, and the route_to eligibility check are verified against the real SQL
and CHECK constraints — which a mocked-pool unit test cannot catch.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.switchboard.routing.rule_promotion_apply import (
    AUTO_APPLY_ACTOR,
    SuggestionNotApplicable,
    apply_suggestion,
    auto_apply_automated_suggestions,
    mint_rule_from_suggestion,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard"],
        schemas={"switchboard": "switchboard"},
    )


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
        server_settings={"search_path": "switchboard,public"},
    )
    # Clean slate for each test (module-scoped DB, function-scoped isolation).
    # rule_promotion_suggestions.created_rule_id and
    # ingestion_rules.promoted_from_suggestion_id form a two-way FK cycle — break
    # it by nulling the suggestion→rule link before deleting either side.
    async with p.acquire() as conn:
        await conn.execute("UPDATE rule_promotion_suggestions SET created_rule_id = NULL")
        await conn.execute("DELETE FROM ingestion_rules WHERE created_by = 'promotion'")
        await conn.execute("DELETE FROM rule_promotion_suggestions")
    yield p
    await p.close()


async def _insert_suggestion(
    pool: asyncpg.Pool,
    *,
    sender_key: str,
    source_channel: str = "gmail",
    proposed_action: str,
    is_clearly_automated: bool,
) -> str:
    now = datetime.now(UTC)
    return await pool.fetchval(
        """
        INSERT INTO rule_promotion_suggestions
            (suggestion_kind, sender_key, source_channel, proposed_rule_type,
             proposed_condition, proposed_action, evidence_count,
             first_evidence_at, last_evidence_at, is_clearly_automated, status)
        VALUES ('promotion', $1, $2, 'sender_address', $3, $4, 3, $5, $6, $7,
                'pending_review')
        RETURNING id
        """,
        sender_key,
        source_channel,
        {"address": sender_key},
        proposed_action,
        now - timedelta(days=2),
        now,
        is_clearly_automated,
    )


async def test_mint_creates_rule_with_provenance_and_confirms(pool):
    sid = await _insert_suggestion(
        pool,
        sender_key="noreply@acme.com",
        proposed_action="metadata_only",
        is_clearly_automated=True,
    )
    rule = await mint_rule_from_suggestion(pool, sid, decided_by="owner")

    assert rule["created_by"] == "promotion"
    assert str(rule["promoted_from_suggestion_id"]) == str(sid)
    assert rule["action"] == "metadata_only"
    assert rule["scope"] == "global"
    assert rule["enabled"] is True

    sug = await pool.fetchrow(
        "SELECT status, created_rule_id, decided_by, decided_at "
        "FROM rule_promotion_suggestions WHERE id = $1",
        sid,
    )
    assert sug["status"] == "confirmed"
    assert str(sug["created_rule_id"]) == str(rule["id"])
    assert sug["decided_by"] == "owner"
    assert sug["decided_at"] is not None


async def test_mint_is_idempotent_against_double_apply(pool):
    sid = await _insert_suggestion(
        pool,
        sender_key="alerts@acme.com",
        proposed_action="skip",
        is_clearly_automated=True,
    )
    await mint_rule_from_suggestion(pool, sid, decided_by="owner")
    with pytest.raises(SuggestionNotApplicable) as exc:
        await mint_rule_from_suggestion(pool, sid, decided_by="owner")
    assert exc.value.status_code == 409
    # Exactly one rule minted, not two.
    count = await pool.fetchval(
        "SELECT count(*) FROM ingestion_rules WHERE promoted_from_suggestion_id = $1", sid
    )
    assert count == 1


async def test_missing_suggestion_is_404(pool):
    import uuid

    with pytest.raises(SuggestionNotApplicable) as exc:
        await mint_rule_from_suggestion(pool, uuid.uuid4(), decided_by="owner")
    assert exc.value.status_code == 404


async def test_route_to_unregistered_butler_is_422(pool):
    sid = await _insert_suggestion(
        pool,
        sender_key="ceo@acme.com",
        proposed_action="route_to:ghost",
        is_clearly_automated=False,
    )
    with pytest.raises(SuggestionNotApplicable) as exc:
        await apply_suggestion(pool, sid, decided_by="owner")
    assert exc.value.status_code == 422
    # The failed apply left the suggestion pending (transaction rolled back).
    status = await pool.fetchval("SELECT status FROM rule_promotion_suggestions WHERE id = $1", sid)
    assert status == "pending_review"


async def test_route_to_registered_butler_mints(pool):
    await pool.execute(
        "INSERT INTO butler_registry (name, endpoint_url) VALUES ('finance', 'http://localhost:1') "
        "ON CONFLICT (name) DO NOTHING"
    )
    sid = await _insert_suggestion(
        pool,
        sender_key="invoices@acme.com",
        proposed_action="route_to:finance",
        is_clearly_automated=False,
    )
    rule = await apply_suggestion(pool, sid, decided_by="owner")
    assert rule["action"] == "route_to:finance"
    assert rule["created_by"] == "promotion"


async def test_auto_apply_only_mints_automated_skip_metadata(pool):
    auto_skip = await _insert_suggestion(
        pool,
        sender_key="noreply@a.com",
        proposed_action="skip",
        is_clearly_automated=True,
    )
    auto_meta = await _insert_suggestion(
        pool,
        sender_key="alerts@b.com",
        proposed_action="metadata_only",
        is_clearly_automated=True,
    )
    # NOT auto-applied: non-automated skip, and an automated route_to.
    manual_skip = await _insert_suggestion(
        pool,
        sender_key="human@c.com",
        proposed_action="skip",
        is_clearly_automated=False,
    )
    await pool.execute(
        "INSERT INTO butler_registry (name, endpoint_url) VALUES ('finance', 'http://localhost:1') "
        "ON CONFLICT (name) DO NOTHING"
    )
    auto_route = await _insert_suggestion(
        pool,
        sender_key="noreply@d.com",
        proposed_action="route_to:finance",
        is_clearly_automated=True,
    )

    counts = await auto_apply_automated_suggestions(pool)
    assert counts["auto_apply_candidates"] == 2
    assert counts["auto_apply_applied"] == 2

    async def _status(sid):
        return await pool.fetchval(
            "SELECT status FROM rule_promotion_suggestions WHERE id = $1", sid
        )

    assert await _status(auto_skip) == "confirmed"
    assert await _status(auto_meta) == "confirmed"
    # These stay pending — they require an explicit owner confirm.
    assert await _status(manual_skip) == "pending_review"
    assert await _status(auto_route) == "pending_review"

    # Auto-applied rows are stamped with the auto-apply actor for the info surface.
    decided_by = await pool.fetchval(
        "SELECT decided_by FROM rule_promotion_suggestions WHERE id = $1", auto_skip
    )
    assert decided_by == AUTO_APPLY_ACTOR
