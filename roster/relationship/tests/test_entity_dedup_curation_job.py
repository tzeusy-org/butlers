"""Tests for entity dedup curation job (behavior #2: entity dedup/merge detection).

The job scans public.entities for duplicate or near-identical canonical_name
values and surfaces merge candidates as pending_actions rows for owner review.
This file covers:

  - No-op when no entities exist
  - No-op when all entities have unique canonical names
  - Exact duplicate detection (case-insensitive)
  - Near-identical name detection (within Levenshtein threshold)
  - Tombstoned entities are excluded
  - Dedup guard: second run skips already-pending pairs
  - Insight candidate proposed alongside pending_action
  - pending_actions row has correct tool_name and tool_args
  - _levenshtein unit tests (pure logic, no DB)
"""

from __future__ import annotations

import shutil
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

# The roster job module is loaded by conftest.py via _load_roster_jobs and
# registered in sys.modules as butlers.jobs._roster.relationship_jobs.
from butlers.jobs._roster.relationship_jobs import (  # type: ignore[import]
    _ENTITY_DEDUP_CURATION_STATE_KEY,  # noqa: F401 — re-exported for tests below
    _ENTITY_DEDUP_MIN_NAME_LEN_FOR_NEAR_MATCH,
    _levenshtein,
    run_entity_dedup_curation,
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

_CREATE_STATE_SQL = """
CREATE TABLE IF NOT EXISTS state (
    key        TEXT        NOT NULL PRIMARY KEY,
    value      JSONB       NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    version    INTEGER     NOT NULL DEFAULT 1
)
"""


async def _setup_schema(pool: asyncpg.Pool) -> None:
    """Create the minimal schema needed by run_entity_dedup_curation tests."""
    await pool.execute(_CREATE_ENTITIES_SQL)
    await pool.execute(_CREATE_PENDING_ACTIONS_SQL)
    await pool.execute(_CREATE_STATE_SQL)


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Fresh isolated DB with entity dedup curation schema."""
    async with provisioned_postgres_pool() as p:
        await _setup_schema(p)
        yield p


# ---------------------------------------------------------------------------
# Entity helpers
# ---------------------------------------------------------------------------


async def _make_entity(
    pool: asyncpg.Pool,
    *,
    name: str = "Test Person",
    roles: list[str] | None = None,
    tombstone_into: str | None = None,
) -> uuid.UUID:
    """Insert an entity; optionally tombstone it with merged_into metadata."""
    metadata = {}
    if tombstone_into:
        metadata["merged_into"] = tombstone_into
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, name, entity_type, roles, metadata) "
        "VALUES ($1, $1, 'person', $2, $3) RETURNING id",
        name,
        roles or [],
        metadata,
    )


async def _count_pending_for_pair(
    pool: asyncpg.Pool,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> int:
    return await pool.fetchval(
        """
        SELECT COUNT(*) FROM pending_actions
         WHERE tool_name = 'entity_merge'
           AND status = 'pending'
           AND (tool_args ->> 'source_entity_id') = $1
           AND (tool_args ->> 'target_entity_id') = $2
        """,
        str(source_id),
        str(target_id),
    )


# ---------------------------------------------------------------------------
# _levenshtein unit tests (pure logic, no DB)
# ---------------------------------------------------------------------------


def test_levenshtein_identical():
    assert _levenshtein("alice", "alice") == 0


def test_levenshtein_empty_strings():
    assert _levenshtein("", "") == 0
    assert _levenshtein("abc", "") == 3
    assert _levenshtein("", "abc") == 3


def test_levenshtein_one_edit():
    assert _levenshtein("alice", "alic") == 1  # deletion
    assert _levenshtein("alic", "alice") == 1  # insertion
    assert _levenshtein("alice", "alicf") == 1  # substitution


def test_levenshtein_two_edits():
    assert _levenshtein("chloe", "clhoe") == 2  # two transpositions (counted individually)


def test_levenshtein_completely_different():
    assert _levenshtein("alice", "bob") >= 3


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


async def _noop_propose_insight(pool, **kwargs):
    return {"status": "accepted"}


@pytest.fixture
def mock_propose_insight():
    """Patch propose_insight_candidate to a no-op for all dedup tests."""
    mock = AsyncMock(return_value={"status": "accepted"})
    with patch(
        "butlers.tools.switchboard.insight.broker.propose_insight_candidate",
        new=mock,
    ):
        yield mock


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------


async def test_entity_dedup_noop_no_entities(pool: asyncpg.Pool, mock_propose_insight):
    """No entities → no pairs detected, no pending_actions inserted."""
    result = await run_entity_dedup_curation(pool)

    assert result["entities_scanned"] == 0
    assert result["exact_groups_found"] == 0
    assert result["near_identical_pairs_found"] == 0
    assert result["pairs_surfaced"] == 0
    assert result["errors"] == 0

    count = await pool.fetchval("SELECT COUNT(*) FROM pending_actions")
    assert count == 0


async def test_entity_dedup_noop_all_unique(pool: asyncpg.Pool, mock_propose_insight):
    """Unique canonical names → no pairs detected."""
    await _make_entity(pool, name="Alice Smith")
    await _make_entity(pool, name="Bob Jones")
    await _make_entity(pool, name="Carol White")

    result = await run_entity_dedup_curation(pool)

    assert result["entities_scanned"] == 3
    assert result["exact_groups_found"] == 0
    assert result["near_identical_pairs_found"] == 0
    assert result["pairs_surfaced"] == 0
    assert result["errors"] == 0

    count = await pool.fetchval("SELECT COUNT(*) FROM pending_actions")
    assert count == 0


# ---------------------------------------------------------------------------
# Exact duplicate detection
# ---------------------------------------------------------------------------


async def test_entity_dedup_exact_match_same_case(pool: asyncpg.Pool, mock_propose_insight):
    """Two entities with identical canonical_name → one pair surfaced."""
    # Older entity (lower created_at) → target (survives)
    target = await _make_entity(pool, name="Chloe Dupont")
    # Newer entity → source (will be merged away)
    source = await _make_entity(pool, name="Chloe Dupont")

    result = await run_entity_dedup_curation(pool)

    assert result["entities_scanned"] == 2
    assert result["exact_groups_found"] == 1
    assert result["pairs_surfaced"] == 1
    assert result["errors"] == 0

    # Verify pending_actions row
    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1, "expected one pending_action for the duplicate pair"


async def test_entity_dedup_exact_match_case_insensitive(pool: asyncpg.Pool, mock_propose_insight):
    """Case-insensitive exact match → one pair surfaced."""
    target = await _make_entity(pool, name="Papa John")
    source = await _make_entity(pool, name="papa john")

    result = await run_entity_dedup_curation(pool)

    assert result["exact_groups_found"] == 1
    assert result["pairs_surfaced"] == 1

    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1


async def test_entity_dedup_exact_match_leading_trailing_spaces(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Leading/trailing whitespace is trimmed before matching."""
    target = await _make_entity(pool, name="Alice Smith")
    source = await _make_entity(pool, name="  Alice Smith  ")

    result = await run_entity_dedup_curation(pool)

    assert result["exact_groups_found"] == 1
    assert result["pairs_surfaced"] == 1

    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1


async def test_entity_dedup_pending_action_has_correct_tool_name_and_args(
    pool: asyncpg.Pool, mock_propose_insight
):
    """pending_actions row specifies tool_name='entity_merge' with correct tool_args."""
    target = await _make_entity(pool, name="Chloe Dupont")
    source = await _make_entity(pool, name="Chloe Dupont")

    await run_entity_dedup_curation(pool)

    row = await pool.fetchrow(
        "SELECT tool_name, tool_args FROM pending_actions "
        "WHERE tool_name = 'entity_merge' AND status = 'pending' LIMIT 1"
    )
    assert row is not None
    assert row["tool_name"] == "entity_merge"
    args = dict(row["tool_args"])
    assert args["source_entity_id"] == str(source)
    assert args["target_entity_id"] == str(target)


# ---------------------------------------------------------------------------
# Near-identical detection
# ---------------------------------------------------------------------------


async def test_entity_dedup_near_identical_match(pool: asyncpg.Pool, mock_propose_insight):
    """Near-identical names (within Levenshtein threshold) → pair surfaced."""
    # 1 edit: "Martim" vs "Martin"
    await _make_entity(pool, name="Chloe Martin")
    await _make_entity(pool, name="Chloe Martim")  # 1 edit (n→m)

    result = await run_entity_dedup_curation(pool)

    assert result["near_identical_pairs_found"] >= 1
    assert result["pairs_surfaced"] >= 1
    assert result["errors"] == 0


async def test_entity_dedup_near_identical_within_threshold(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Names exactly at threshold (2 edits) are detected."""
    await _make_entity(pool, name="Robert Brown")
    await _make_entity(pool, name="Robirt Broon")  # 2 edits

    result = await run_entity_dedup_curation(pool)

    assert result["near_identical_pairs_found"] >= 1
    assert result["pairs_surfaced"] >= 1


async def test_entity_dedup_beyond_threshold_not_flagged(pool: asyncpg.Pool, mock_propose_insight):
    """Names more than threshold apart are NOT flagged."""
    await _make_entity(pool, name="Alice Smith")
    await _make_entity(pool, name="Alison Jones")  # >> 2 edits

    result = await run_entity_dedup_curation(pool)

    assert result["near_identical_pairs_found"] == 0
    assert result["pairs_surfaced"] == 0


# ---------------------------------------------------------------------------
# Tombstone exclusion
# ---------------------------------------------------------------------------


async def test_entity_dedup_tombstoned_entity_excluded(pool: asyncpg.Pool, mock_propose_insight):
    """Tombstoned entities (merged_into set) are excluded from dedup scan."""
    live_entity = await _make_entity(pool, name="Chloe Dupont")
    # This entity is already merged — should be ignored
    await _make_entity(pool, name="Chloe Dupont", tombstone_into=str(live_entity))

    result = await run_entity_dedup_curation(pool)

    # Only the live entity is scanned; no pairs because tombstoned one is excluded
    assert result["entities_scanned"] == 1
    assert result["exact_groups_found"] == 0
    assert result["pairs_surfaced"] == 0
    assert result["errors"] == 0


async def test_entity_dedup_both_tombstoned_excluded(pool: asyncpg.Pool, mock_propose_insight):
    """When both entities in a potential pair are tombstoned, no pair is surfaced."""
    dummy = uuid.uuid4()
    await _make_entity(pool, name="Chloe Dupont", tombstone_into=str(dummy))
    await _make_entity(pool, name="Chloe Dupont", tombstone_into=str(dummy))

    result = await run_entity_dedup_curation(pool)

    assert result["entities_scanned"] == 0
    assert result["pairs_surfaced"] == 0


# ---------------------------------------------------------------------------
# Dedup guard: idempotency
# ---------------------------------------------------------------------------


async def test_entity_dedup_second_run_skips_existing_pending(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Second run skips pairs that already have a pending pending_actions row."""
    target = await _make_entity(pool, name="Chloe Dupont")
    source = await _make_entity(pool, name="Chloe Dupont")

    # First run: creates the pending_action
    result1 = await run_entity_dedup_curation(pool)
    assert result1["pairs_surfaced"] == 1
    assert result1["pairs_skipped_already_pending"] == 0

    # Second run: skips the already-pending pair
    result2 = await run_entity_dedup_curation(pool)
    assert result2["pairs_surfaced"] == 0
    assert result2["pairs_skipped_already_pending"] == 1

    # Still only one pending_actions row
    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1


async def test_entity_dedup_resurfaced_after_decided(pool: asyncpg.Pool, mock_propose_insight):
    """A pair with a decided (non-pending) pending_action is surfaced again."""
    target = await _make_entity(pool, name="Chloe Dupont")
    source = await _make_entity(pool, name="Chloe Dupont")

    # Insert a 'rejected' pending_action for this pair
    await pool.execute(
        "INSERT INTO pending_actions "
        "(id, tool_name, tool_args, agent_summary, status, requested_at, evidence) "
        "VALUES ($1, 'entity_merge', $2, 'old', 'rejected', now(), '[]')",
        uuid.uuid4(),
        {"source_entity_id": str(source), "target_entity_id": str(target)},
    )

    # Since the existing row has status='rejected' (not 'pending'), should surface again
    result = await run_entity_dedup_curation(pool)
    assert result["pairs_surfaced"] == 1
    assert result["pairs_skipped_already_pending"] == 0


# ---------------------------------------------------------------------------
# Owner-approval routing
# ---------------------------------------------------------------------------


async def test_entity_dedup_owner_entity_goes_through_pending_actions(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Even owner-role entities get surfaced via pending_actions (NEVER autonomous merge)."""
    # Owner entity + a duplicate (both should go to pending_actions for owner review)
    target = await _make_entity(pool, name="Tze (owner)", roles=["owner"])
    source = await _make_entity(pool, name="Tze (owner)")

    result = await run_entity_dedup_curation(pool)

    # Must be surfaced via pending_actions, not autonomously merged
    assert result["pairs_surfaced"] == 1
    assert result["errors"] == 0
    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1


# ---------------------------------------------------------------------------
# Multiple duplicate groups
# ---------------------------------------------------------------------------


async def test_entity_dedup_multiple_groups(pool: asyncpg.Pool, mock_propose_insight):
    """Multiple separate exact-duplicate groups each produce a pending_action."""
    # Group 1: Chloe duplicates
    chloe_target = await _make_entity(pool, name="Chloe Dupont")
    chloe_source = await _make_entity(pool, name="Chloe Dupont")

    # Group 2: Papa duplicates
    papa_target = await _make_entity(pool, name="Papa Jean")
    papa_source = await _make_entity(pool, name="Papa Jean")

    result = await run_entity_dedup_curation(pool)

    assert result["exact_groups_found"] == 2
    assert result["pairs_surfaced"] == 2
    assert result["errors"] == 0

    assert await _count_pending_for_pair(pool, chloe_source, chloe_target) == 1
    assert await _count_pending_for_pair(pool, papa_source, papa_target) == 1


# ---------------------------------------------------------------------------
# Checkpoint is written
# ---------------------------------------------------------------------------


async def test_entity_dedup_checkpoint_written(pool: asyncpg.Pool, mock_propose_insight):
    """Checkpoint state key is written after a successful run."""
    await run_entity_dedup_curation(pool)

    val = await pool.fetchval(
        "SELECT value FROM state WHERE key = $1",
        _ENTITY_DEDUP_CURATION_STATE_KEY,
    )
    assert val is not None, "checkpoint key should be written after run"


# ---------------------------------------------------------------------------
# Minimum-length guard for near-identical matching (bu-q7vfe)
# ---------------------------------------------------------------------------


def test_min_name_len_constant_is_sensible():
    """The minimum-length constant guards against short 3-4 character names."""
    # The guard must be > 4 to exclude 3- and 4-character names
    assert _ENTITY_DEDUP_MIN_NAME_LEN_FOR_NEAR_MATCH >= 5


async def test_entity_dedup_short_names_not_flagged_as_near_identical(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Short distinct names ('Sam'/'Pam', 'Jon'/'Jan', 'Ana'/'Ava') must NOT be flagged.

    These names are clearly different people despite small edit distances.  The
    minimum-length guard must prevent them from surfacing as near-identical merge
    candidates.
    """
    await _make_entity(pool, name="Sam")
    await _make_entity(pool, name="Pam")
    await _make_entity(pool, name="Jon")
    await _make_entity(pool, name="Jan")
    await _make_entity(pool, name="Ana")
    await _make_entity(pool, name="Ava")

    result = await run_entity_dedup_curation(pool)

    # None of these short-name pairs should be flagged near-identical
    assert result["near_identical_pairs_found"] == 0, (
        "Short distinct names (Sam/Pam, Jon/Jan, Ana/Ava) must NOT be flagged as "
        "near-identical merge candidates"
    )
    assert result["pairs_surfaced"] == 0
    assert result["errors"] == 0

    count = await pool.fetchval("SELECT COUNT(*) FROM pending_actions")
    assert count == 0


async def test_entity_dedup_short_name_exact_dup_still_flagged(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Short-name EXACT duplicates must still be flagged regardless of length.

    The minimum-length guard applies only to the Levenshtein near-identical path.
    Exact (case-insensitive) duplicates are caught earlier and must always surface.
    """
    target = await _make_entity(pool, name="Sam")
    source = await _make_entity(pool, name="Sam")  # exact duplicate

    result = await run_entity_dedup_curation(pool)

    assert result["exact_groups_found"] == 1, (
        "Exact short-name duplicates must still be flagged even below min-length guard"
    )
    assert result["pairs_surfaced"] == 1
    assert result["errors"] == 0

    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1


async def test_entity_dedup_short_name_exact_dup_case_insensitive(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Short-name case-insensitive exact duplicates ('Jon'/'jon') are always flagged."""
    target = await _make_entity(pool, name="Jon")
    source = await _make_entity(pool, name="jon")

    result = await run_entity_dedup_curation(pool)

    assert result["exact_groups_found"] == 1
    assert result["pairs_surfaced"] == 1

    count = await _count_pending_for_pair(pool, source, target)
    assert count == 1


async def test_entity_dedup_long_near_identical_names_still_flagged(
    pool: asyncpg.Pool, mock_propose_insight
):
    """Genuine longer near-duplicates (>= min-length) still surface appropriately.

    'Chloe' / 'Chloe ' (with trailing space, trimmed to equal → exact match) and
    'Robert Brown' / 'Robirt Brown' (1 edit on a long name) should still flag.
    """
    # Near-identical: 1 edit on names well above min-length threshold
    await _make_entity(pool, name="Robert Brown")
    await _make_entity(pool, name="Robirt Brown")  # 'e' → 'i' = 1 edit

    result = await run_entity_dedup_curation(pool)

    assert result["near_identical_pairs_found"] >= 1, (
        "Long near-identical names (Robert Brown / Robirt Brown) must still be flagged"
    )
    assert result["pairs_surfaced"] >= 1
    assert result["errors"] == 0


async def test_entity_dedup_five_char_names_still_matched(pool: asyncpg.Pool, mock_propose_insight):
    """Names exactly at the minimum length (e.g. 'Chloe' / 'Chleo') are still checked.

    The guard is a strict less-than (< min_len), so names with len == min_len
    are included in near-identical matching.  'Chloe' / 'Chleo' is 2 edits
    and should be flagged.
    """
    # Verify the names are exactly at the boundary
    assert len("chloe") == _ENTITY_DEDUP_MIN_NAME_LEN_FOR_NEAR_MATCH
    assert len("chleo") == _ENTITY_DEDUP_MIN_NAME_LEN_FOR_NEAR_MATCH

    await _make_entity(pool, name="Chloe")
    await _make_entity(pool, name="Chleo")  # 2 edits (transposition)

    result = await run_entity_dedup_curation(pool)

    assert result["near_identical_pairs_found"] >= 1, (
        "Names at exactly the minimum length boundary must still be near-identical checked"
    )
    assert result["pairs_surfaced"] >= 1
    assert result["errors"] == 0
