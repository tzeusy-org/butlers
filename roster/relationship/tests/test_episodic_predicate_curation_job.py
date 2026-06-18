"""Tests for episodic predicate curation job (behavior #5: episodic predicates
leaking into the durable fact store).

The job scans relationship.facts for active rows whose predicate belongs to the
_EPISODIC_PREDICATES taxonomy (interaction_note, current_activity, etc.) AND
whose permanence is 'stable' or 'permanent'. It surfaces these for owner review
via pending_actions with tool_name='memory_reclassify', never auto-mutating.

This file covers:
  - No-op when no facts exist (no pending actions created, checkpoint written)
  - No-op when only non-episodic predicate facts exist at stable permanence
  - Episodic fact at stable permanence → flagged (pending_action created)
  - Episodic fact at permanent permanence → flagged
  - Episodic fact at volatile permanence → NOT flagged (correct permanence)
  - Durable CRM predicates (contact_note, gift, life_event) at stable → NOT flagged
  - interaction_* prefix predicates at stable → NOT flagged (intentional temporal records)
  - Dedup: second run skips already-pending reclassification
  - Owner-entity fact with episodic predicate → still routed through pending_actions
  - Low-confidence episodic fact → flagged (confidence not a criterion for exclusion)
  - Pending_action row has correct tool_name and tool_args fields
  - Insight candidate proposed alongside pending_action (mock verify)
  - Checkpoint state_key written after run
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

# The roster job module is loaded by conftest.py via _load_roster_jobs and
# registered in sys.modules as butlers.jobs._roster.relationship_jobs.
from butlers.jobs._roster.relationship_jobs import (  # type: ignore[import]
    _EPISODIC_CURATION_STATE_KEY,
    _EPISODIC_DURABLE_PERMANENCES,
    _EPISODIC_PREDICATES,
    run_episodic_predicate_curation,
)

# ---------------------------------------------------------------------------
# Skip if Docker unavailable
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

# ---------------------------------------------------------------------------
# Schema creation helpers
# ---------------------------------------------------------------------------

_CREATE_ENTITIES_SQL = """
CREATE TABLE IF NOT EXISTS public.entities (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name TEXT        NOT NULL DEFAULT '',
    name           TEXT        NOT NULL DEFAULT '',
    entity_type    TEXT        NOT NULL DEFAULT 'person',
    aliases        TEXT[]      NOT NULL DEFAULT '{}',
    metadata       JSONB       DEFAULT '{}'::jsonb,
    roles          TEXT[]      NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_CREATE_PENDING_ACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tool_name    TEXT        NOT NULL,
    tool_args    JSONB       NOT NULL,
    agent_summary TEXT,
    session_id   UUID,
    status       VARCHAR     NOT NULL DEFAULT 'pending',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    decided_by   TEXT,
    decided_at   TIMESTAMPTZ,
    execution_result JSONB,
    approval_rule_id UUID,
    why          TEXT,
    evidence     JSONB       NOT NULL DEFAULT '[]'::jsonb
)
"""

_CREATE_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    subject          TEXT        NOT NULL DEFAULT '',
    predicate        TEXT        NOT NULL,
    content          TEXT        NOT NULL DEFAULT '',
    validity         TEXT        NOT NULL DEFAULT 'active',
    scope            TEXT        NOT NULL DEFAULT 'relationship',
    entity_id        UUID,
    object_entity_id UUID,
    confidence       FLOAT       NOT NULL DEFAULT 1.0,
    permanence       TEXT        NOT NULL DEFAULT 'standard',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata         JSONB       DEFAULT '{}'::jsonb
)
"""

_CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key        TEXT        NOT NULL PRIMARY KEY,
    value      JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version    INTEGER     NOT NULL DEFAULT 1
)
"""


async def _setup_schema(pool: asyncpg.Pool) -> None:
    """Create the minimal schema needed by run_episodic_predicate_curation tests."""
    await pool.execute(_CREATE_ENTITIES_SQL)
    await pool.execute(_CREATE_PENDING_ACTIONS_SQL)
    await pool.execute(_CREATE_FACTS_SQL)
    await pool.execute(_CREATE_STATE_SQL)


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh isolated DB with episodic curation schema."""
    async with provisioned_postgres_pool() as p:
        await _setup_schema(p)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_entity(
    pool: asyncpg.Pool,
    *,
    name: str = "Test Person",
    roles: list[str] | None = None,
) -> uuid.UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, name, entity_type, roles) "
        "VALUES ($1, $1, 'person', $2) RETURNING id",
        name,
        roles or [],
    )


async def _insert_fact(
    pool: asyncpg.Pool,
    *,
    predicate: str,
    content: str = "Some content",
    permanence: str = "stable",
    validity: str = "active",
    scope: str = "relationship",
    entity_id: uuid.UUID | None = None,
    confidence: float = 1.0,
) -> uuid.UUID:
    """Insert a row into the facts table; return the fact id."""
    return await pool.fetchval(
        """
        INSERT INTO facts
            (predicate, content, permanence, validity, scope, entity_id, confidence)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id
        """,
        predicate,
        content,
        permanence,
        validity,
        scope,
        entity_id,
        confidence,
    )


async def _count_pending_actions(pool: asyncpg.Pool, tool_name: str = "memory_reclassify") -> int:
    return await pool.fetchval(
        "SELECT COUNT(*) FROM pending_actions WHERE tool_name = $1 AND status = 'pending'",
        tool_name,
    )


async def _get_pending_action(
    pool: asyncpg.Pool, fact_id: uuid.UUID, tool_name: str = "memory_reclassify"
):
    return await pool.fetchrow(
        """
        SELECT id, tool_name, tool_args, agent_summary, why, evidence, expires_at
          FROM pending_actions
         WHERE tool_name = $1
           AND status    = 'pending'
           AND (tool_args ->> 'memory_id') = $2
         LIMIT 1
        """,
        tool_name,
        str(fact_id),
    )


async def _get_checkpoint(pool: asyncpg.Pool) -> str | None:
    row = await pool.fetchrow(
        "SELECT value FROM state WHERE key = $1",
        _EPISODIC_CURATION_STATE_KEY,
    )
    if row is None:
        return None
    v = row["value"]
    if isinstance(v, str):
        return v
    # asyncpg may return a dict with "value" key when column is JSONB
    if isinstance(v, dict):
        return v.get("value")
    return str(v)


# ---------------------------------------------------------------------------
# Taxonomy unit tests (pure logic, no DB)
# ---------------------------------------------------------------------------


def test_episodic_predicates_taxonomy_boundary():
    """Verify the taxonomy contains only known episodic predicates and
    excludes intentional temporal (interaction_*) and durable CRM predicates.

    interaction_note is the one interaction_* predicate that IS included:
    it is an ephemeral free-text annotation that must stay at volatile/ephemeral
    permanence.  interaction_log() rejects type='note' to enforce this invariant,
    so interaction_note at stable permanence always indicates a mis-stored fact.
    """
    # All expected episodic predicates must be present
    for expected in (
        "interaction_note",  # episodic exception: NOT written by interaction_log
        "current_activity",
        "current_mood",
        "today_note",
        "meeting_note",
        "event_note",
        "coordination_note",
    ):
        assert expected in _EPISODIC_PREDICATES, f"{expected!r} missing from _EPISODIC_PREDICATES"

    # Intentional temporal predicates written by interaction_log MUST NOT be in the episodic set
    for excluded in (
        "interaction_call",
        "interaction_meeting",
        "interaction_email",
        "interaction_telegram_user_client",
    ):
        assert excluded not in _EPISODIC_PREDICATES, (
            f"{excluded!r} should NOT be in _EPISODIC_PREDICATES"
        )

    # Durable CRM predicates MUST NOT be in the episodic set
    for excluded_crm in (
        "contact_note",
        "gift",
        "life_event",
        "contact_task",
        "loan",
    ):
        assert excluded_crm not in _EPISODIC_PREDICATES, (
            f"{excluded_crm!r} should NOT be in _EPISODIC_PREDICATES"
        )


def test_episodic_durable_permanences():
    """Only 'stable' and 'permanent' are treated as durable for curation purposes."""
    assert "stable" in _EPISODIC_DURABLE_PERMANENCES
    assert "permanent" in _EPISODIC_DURABLE_PERMANENCES
    # 'volatile' and 'ephemeral' are the correct permanences for episodic facts
    assert "volatile" not in _EPISODIC_DURABLE_PERMANENCES
    assert "ephemeral" not in _EPISODIC_DURABLE_PERMANENCES


# ---------------------------------------------------------------------------
# No-op tests
# ---------------------------------------------------------------------------


async def test_noop_empty_db(pool):
    """No facts → no pending_actions created, checkpoint is written."""
    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ) as mock_propose:
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 0
    assert stats["episodic_found"] == 0
    assert stats["flagged_new"] == 0
    assert stats["errors"] == 0
    mock_propose.assert_not_called()
    assert await _count_pending_actions(pool) == 0
    # Checkpoint should still be written
    ckpt = await _get_checkpoint(pool)
    assert ckpt is not None


async def test_noop_non_episodic_predicates_at_stable(pool):
    """Non-episodic predicates at stable permanence are NOT flagged."""
    entity_id = await _make_entity(pool, name="Alice")
    # CRM predicates that legitimately live at stable
    for pred in ("contact_note", "gift", "life_event", "contact_task", "loan"):
        await _insert_fact(pool, predicate=pred, permanence="stable", entity_id=entity_id)
    # Temporal interaction records that live at stable by design
    for pred in ("interaction_call", "interaction_meeting", "interaction_email"):
        await _insert_fact(pool, predicate=pred, permanence="stable", entity_id=entity_id)

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 0
    assert stats["episodic_found"] == 0
    assert stats["flagged_new"] == 0
    assert await _count_pending_actions(pool) == 0


async def test_noop_episodic_predicate_at_volatile(pool):
    """Episodic predicate at correct permanence ('volatile') is NOT flagged."""
    entity_id = await _make_entity(pool, name="Bob")
    await _insert_fact(
        pool, predicate="interaction_note", permanence="volatile", entity_id=entity_id
    )
    await _insert_fact(
        pool, predicate="current_activity", permanence="ephemeral", entity_id=entity_id
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 0
    assert stats["episodic_found"] == 0
    assert await _count_pending_actions(pool) == 0


async def test_noop_episodic_predicate_at_standard(pool):
    """Episodic predicate at 'standard' permanence is NOT flagged.
    Standard is the default and decays faster than stable — only stable
    and permanent are truly durable and trigger the curation alert."""
    entity_id = await _make_entity(pool, name="Charlie")
    await _insert_fact(pool, predicate="meeting_note", permanence="standard", entity_id=entity_id)

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 0
    assert stats["episodic_found"] == 0
    assert await _count_pending_actions(pool) == 0


async def test_noop_episodic_predicate_retracted(pool):
    """Retracted or superseded episodic facts are NOT flagged."""
    entity_id = await _make_entity(pool, name="Dave")
    await _insert_fact(
        pool,
        predicate="interaction_note",
        permanence="stable",
        validity="retracted",
        entity_id=entity_id,
    )
    await _insert_fact(
        pool,
        predicate="current_mood",
        permanence="stable",
        validity="superseded",
        entity_id=entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 0
    assert await _count_pending_actions(pool) == 0


# ---------------------------------------------------------------------------
# Detection tests
# ---------------------------------------------------------------------------


async def test_episodic_fact_at_stable_flagged(pool):
    """An episodic predicate stored at permanence='stable' is flagged."""
    entity_id = await _make_entity(pool, name="Eve")
    fact_id = await _insert_fact(
        pool,
        predicate="interaction_note",
        content="Had a great chat about their new project.",
        permanence="stable",
        entity_id=entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ) as mock_propose:
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 1
    assert stats["episodic_found"] == 1
    assert stats["flagged_new"] == 1
    assert stats["skipped_already_pending"] == 0
    assert stats["errors"] == 0
    mock_propose.assert_called_once()

    # Verify pending_action row
    pa = await _get_pending_action(pool, fact_id)
    assert pa is not None
    assert pa["tool_name"] == "memory_reclassify"
    tool_args = pa["tool_args"]
    if isinstance(tool_args, str):
        import json

        tool_args = json.loads(tool_args)
    assert tool_args["memory_type"] == "fact"
    assert tool_args["memory_id"] == str(fact_id)
    assert tool_args["permanence_target"] == "volatile"
    assert pa["expires_at"] is not None


async def test_episodic_fact_at_permanent_flagged(pool):
    """An episodic predicate stored at permanence='permanent' is flagged."""
    entity_id = await _make_entity(pool, name="Frank")
    fact_id = await _insert_fact(
        pool,
        predicate="current_activity",
        content="Working on a big presentation this week.",
        permanence="permanent",
        entity_id=entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["flagged_new"] == 1
    pa = await _get_pending_action(pool, fact_id)
    assert pa is not None
    tool_args = pa["tool_args"]
    if isinstance(tool_args, str):
        import json

        tool_args = json.loads(tool_args)
    assert tool_args["permanence_target"] == "volatile"


async def test_multiple_episodic_predicates_all_flagged(pool):
    """Multiple episodic predicates at durable permanence are each flagged."""
    entity_id = await _make_entity(pool, name="Grace")
    fact_ids = []
    for pred in ("interaction_note", "current_mood", "today_note", "meeting_note"):
        fid = await _insert_fact(pool, predicate=pred, permanence="stable", entity_id=entity_id)
        fact_ids.append(fid)

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 4
    assert stats["flagged_new"] == 4
    assert await _count_pending_actions(pool) == 4


async def test_mixed_predicates_only_episodic_flagged(pool):
    """Mix of episodic and durable CRM facts — only episodic ones are flagged."""
    entity_id = await _make_entity(pool, name="Henry")
    episodic_id = await _insert_fact(
        pool, predicate="current_activity", permanence="stable", entity_id=entity_id
    )
    # Durable CRM facts that should not be flagged
    await _insert_fact(pool, predicate="contact_note", permanence="stable", entity_id=entity_id)
    await _insert_fact(pool, predicate="gift", permanence="stable", entity_id=entity_id)
    await _insert_fact(pool, predicate="interaction_call", permanence="stable", entity_id=entity_id)

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["facts_scanned"] == 1
    assert stats["flagged_new"] == 1
    pa = await _get_pending_action(pool, episodic_id)
    assert pa is not None


# ---------------------------------------------------------------------------
# Dedup test
# ---------------------------------------------------------------------------


async def test_dedup_second_run_skips_already_pending(pool):
    """Second run with same fact already pending → skipped_already_pending."""
    entity_id = await _make_entity(pool, name="Iris")
    await _insert_fact(pool, predicate="interaction_note", permanence="stable", entity_id=entity_id)

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        stats1 = await run_episodic_predicate_curation(pool)

    assert stats1["flagged_new"] == 1
    assert stats1["skipped_already_pending"] == 0

    # Second run: same fact still active, pending_action already exists
    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ) as mock_propose2:
        stats2 = await run_episodic_predicate_curation(pool)

    assert stats2["flagged_new"] == 0
    assert stats2["skipped_already_pending"] == 1
    mock_propose2.assert_not_called()
    # Still only one pending_action row
    assert await _count_pending_actions(pool) == 1


# ---------------------------------------------------------------------------
# Owner-entity test
# ---------------------------------------------------------------------------


async def test_owner_entity_fact_routes_through_pending_actions(pool):
    """Owner-entity episodic facts are NOT auto-mutated — they go through
    pending_actions just like any other fact."""
    owner_entity_id = await _make_entity(pool, name="Owner", roles=["owner"])
    fact_id = await _insert_fact(
        pool,
        predicate="current_activity",
        content="Preparing for the board meeting.",
        permanence="stable",
        entity_id=owner_entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        stats = await run_episodic_predicate_curation(pool)

    # Owner entity gets a pending_action — same path as any other entity
    assert stats["flagged_new"] == 1
    pa = await _get_pending_action(pool, fact_id)
    assert pa is not None
    assert pa["tool_name"] == "memory_reclassify"


# ---------------------------------------------------------------------------
# Low-confidence test
# ---------------------------------------------------------------------------


async def test_low_confidence_episodic_fact_is_flagged(pool):
    """Low-confidence episodic facts are still flagged for reclassification.
    The confidence threshold is not a criterion for exclusion here — this job
    is about permanence, not confidence."""
    entity_id = await _make_entity(pool, name="Jack")
    fact_id = await _insert_fact(
        pool,
        predicate="meeting_note",
        content="Possibly discussed project timeline.",
        permanence="stable",
        confidence=0.3,
        entity_id=entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        stats = await run_episodic_predicate_curation(pool)

    assert stats["flagged_new"] == 1
    pa = await _get_pending_action(pool, fact_id)
    assert pa is not None


# ---------------------------------------------------------------------------
# Pending_action content validation
# ---------------------------------------------------------------------------


async def test_pending_action_tool_args_structure(pool):
    """Verify the pending_action row has the correct tool_args structure."""
    entity_id = await _make_entity(pool, name="Karen")
    fact_id = await _insert_fact(
        pool,
        predicate="event_note",
        content="Met at the conference on Tuesday.",
        permanence="stable",
        entity_id=entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        await run_episodic_predicate_curation(pool)

    pa = await _get_pending_action(pool, fact_id)
    assert pa is not None
    assert pa["tool_name"] == "memory_reclassify"

    tool_args = pa["tool_args"]
    if isinstance(tool_args, str):
        import json

        tool_args = json.loads(tool_args)

    assert tool_args["memory_type"] == "fact"
    assert tool_args["memory_id"] == str(fact_id)
    assert tool_args["permanence_target"] == "volatile"

    # expires_at must be set (72-hour window)
    assert pa["expires_at"] is not None
    now_utc = datetime.now(UTC)
    assert pa["expires_at"] > now_utc
    assert pa["expires_at"] < now_utc + timedelta(hours=80)


# ---------------------------------------------------------------------------
# Insight candidate mock verification
# ---------------------------------------------------------------------------


async def test_insight_candidate_proposed_with_correct_params(pool):
    """Verify propose_insight_candidate is called with expected parameters."""
    entity_id = await _make_entity(pool, name="Leo")
    fact_id = await _insert_fact(
        pool,
        predicate="coordination_note",
        content="Need to follow up next week.",
        permanence="stable",
        entity_id=entity_id,
    )

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ) as mock_propose:
        await run_episodic_predicate_curation(pool)

    mock_propose.assert_called_once()
    call_kwargs = mock_propose.call_args.kwargs if mock_propose.call_args.kwargs else {}
    # Positional args: (db_pool, ...)
    call_args = mock_propose.call_args.args if mock_propose.call_args.args else ()

    # Either way, verify key params
    all_args = {**dict(zip(["db_pool", "origin_butler"], call_args)), **call_kwargs}
    assert all_args.get("origin_butler") == "relationship"
    assert all_args.get("category") == "episodic-predicate-in-durable"
    dedup_key = all_args.get("dedup_key", "")
    assert str(fact_id) in dedup_key


# ---------------------------------------------------------------------------
# Checkpoint state test
# ---------------------------------------------------------------------------


async def test_checkpoint_written_after_run(pool):
    """Checkpoint is written to state after each run, regardless of findings."""
    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ):
        await run_episodic_predicate_curation(pool)

    ckpt = await _get_checkpoint(pool)
    assert ckpt is not None
    # Checkpoint should be a recent ISO timestamp
    parsed = datetime.fromisoformat(str(ckpt).strip('"'))
    assert parsed > datetime.now(UTC) - timedelta(minutes=5)


async def test_checkpoint_updated_on_second_run(pool):
    """Checkpoint is updated on each run."""
    entity_id = await _make_entity(pool, name="Mia")
    await _insert_fact(pool, predicate="today_note", permanence="stable", entity_id=entity_id)

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
        return_value={"status": "accepted"},
    ):
        await run_episodic_predicate_curation(pool)
    ckpt1 = await _get_checkpoint(pool)

    # Mark the fact as retracted so second run finds nothing
    await pool.execute("UPDATE facts SET validity='retracted' WHERE predicate='today_note'")

    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new_callable=AsyncMock,
    ):
        await run_episodic_predicate_curation(pool)
    ckpt2 = await _get_checkpoint(pool)

    # Both checkpoints should be valid timestamps
    assert ckpt1 is not None
    assert ckpt2 is not None
