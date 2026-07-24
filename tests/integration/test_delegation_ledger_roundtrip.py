"""Real-Postgres regression: the cross-butler delegation ledger.

Exercises core_162 (bu-gxmfx) against a fully migrated Postgres instance
(testcontainers) — not just mocked-pool unit tests (see
``tests/core/test_delegation_ledger.py`` for the AsyncMock-pool coverage of
the same module, which mirrors the split used for
``tests/integration/test_attention_ledger_roundtrip.py`` vs
``tests/core/test_attention_ledger.py``):

- ``public.delegation_ledger`` is created with the expected columns and the
  ``status`` CHECK constraint enforces the lifecycle vocabulary.
- ``record_ask`` / ``mark_dispatch_outcome`` / ``record_answer`` /
  ``get_delegation`` / ``list_delegations`` round-trip through the real table
  via the actual production writer/reader in
  ``butlers.core.delegation_ledger``.
- The full lifecycle (``pending`` -> ``routed`` -> ``answered``) and both
  terminal short-circuits (``unroutable``, ``failed``) persist and read back
  honestly — a delegated question is never silently dropped.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.core.delegation_ledger import (
    get_delegation,
    list_delegations,
    mark_dispatch_outcome,
    record_answer,
    record_ask,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])


@pytest.fixture
async def pool(migrated_db_url: str) -> asyncpg.Pool:
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=3)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


async def test_delegation_ledger_table_exists_with_expected_columns(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'delegation_ledger'
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {
        "id",
        "asked_at",
        "asking_butler",
        "question",
        "target_butler",
        "catalog_match_id",
        "catalog_score",
        "status",
        "reason",
        "answer",
        "answered_at",
        "answering_butler",
        "metadata",
        # bu-27dxl.5.2 (core_181) — delegated-answer wake protocol columns.
        "answer_digest",
        "wake_key",
        "wake_state",
        "wake_task_id",
        "wake_task_name",
        "wake_updated_at",
    }


async def test_status_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            """
            INSERT INTO public.delegation_ledger (asking_butler, question, status)
            VALUES ('finance', 'What is on my calendar?', 'bogus')
            """
        )


# ---------------------------------------------------------------------------
# record_ask / mark_dispatch_outcome / record_answer / get_delegation round
# trip via the real production writer+reader.
# ---------------------------------------------------------------------------


async def test_pending_to_routed_to_answered_lifecycle_round_trips(pool: asyncpg.Pool) -> None:
    ledger_id = await record_ask(
        pool,
        asking_butler="finance",
        question="What appointments do I have this week?",
        status="pending",
        target_butler="lifestyle",
        catalog_score=0.87,
    )
    assert ledger_id is not None

    row = await get_delegation(pool, ledger_id)
    assert row is not None
    assert row["status"] == "pending"
    assert row["asking_butler"] == "finance"
    assert row["target_butler"] == "lifestyle"
    assert row["catalog_score"] == pytest.approx(0.87)
    assert row["answer"] is None

    await mark_dispatch_outcome(pool, ledger_id, status="routed")
    row = await get_delegation(pool, ledger_id)
    assert row["status"] == "routed"

    updated = await record_answer(
        pool, ledger_id, answering_butler="lifestyle", answer="Dentist Tuesday 3pm."
    )
    assert updated is not None
    assert updated["status"] == "answered"
    assert updated["answer"] == "Dentist Tuesday 3pm."
    assert updated["answering_butler"] == "lifestyle"
    assert updated["answered_at"] is not None

    row = await get_delegation(pool, ledger_id)
    assert row["status"] == "answered"
    assert row["answer"] == "Dentist Tuesday 3pm."


async def test_record_answer_rejects_wrong_target_butler(pool: asyncpg.Pool) -> None:
    ledger_id = await record_ask(
        pool,
        asking_butler="finance",
        question="What is the weather forecast?",
        status="pending",
        target_butler="lifestyle",
    )
    await mark_dispatch_outcome(pool, ledger_id, status="routed")

    # A different butler than the resolved target must never be able to
    # record the answer.
    updated = await record_answer(
        pool, ledger_id, answering_butler="health", answer="Should not be recorded."
    )
    assert updated is None

    row = await get_delegation(pool, ledger_id)
    assert row["status"] == "routed"
    assert row["answer"] is None


async def test_record_answer_rejects_row_not_yet_routed(pool: asyncpg.Pool) -> None:
    ledger_id = await record_ask(
        pool,
        asking_butler="finance",
        question="Still pending, never dispatched.",
        status="pending",
        target_butler="lifestyle",
    )

    # Row is still 'pending' (never transitioned to 'routed') — an answer
    # must never be recorded against it.
    updated = await record_answer(
        pool, ledger_id, answering_butler="lifestyle", answer="Too early."
    )
    assert updated is None

    row = await get_delegation(pool, ledger_id)
    assert row["status"] == "pending"


async def test_unroutable_and_failed_terminal_outcomes_persist(pool: asyncpg.Pool) -> None:
    unroutable_id = await record_ask(
        pool,
        asking_butler="health",
        question="A question nothing in the catalog covers.",
        status="unroutable",
        reason="no_catalog_match",
    )
    row = await get_delegation(pool, unroutable_id)
    assert row["status"] == "unroutable"
    assert row["reason"] == "no_catalog_match"
    assert row["target_butler"] is None

    failed_id = await record_ask(
        pool,
        asking_butler="health",
        question="A question routed but dispatch failed.",
        status="pending",
        target_butler="messenger",
    )
    await mark_dispatch_outcome(pool, failed_id, status="failed", reason="Switchboard unreachable")
    row = await get_delegation(pool, failed_id)
    assert row["status"] == "failed"
    assert row["reason"] == "Switchboard unreachable"


async def test_mark_dispatch_outcome_is_a_noop_once_already_terminal(pool: asyncpg.Pool) -> None:
    ledger_id = await record_ask(
        pool,
        asking_butler="health",
        question="Already answered before a stray retry lands.",
        status="pending",
        target_butler="messenger",
    )
    await mark_dispatch_outcome(pool, ledger_id, status="routed")
    await record_answer(pool, ledger_id, answering_butler="messenger", answer="Done.")

    # A late/duplicate dispatch-outcome write must not clobber the terminal
    # 'answered' state — the guard is WHERE status = 'pending'.
    await mark_dispatch_outcome(pool, ledger_id, status="failed", reason="stray retry")
    row = await get_delegation(pool, ledger_id)
    assert row["status"] == "answered"
    assert row["answer"] == "Done."


async def test_get_delegation_missing_id_returns_none(pool: asyncpg.Pool) -> None:
    import uuid

    row = await get_delegation(pool, uuid.uuid4())
    assert row is None


async def test_list_delegations_filters_and_paginates(pool: asyncpg.Pool) -> None:
    for i in range(3):
        await record_ask(
            pool,
            asking_butler="chronicler",
            question=f"Filterable question {i}",
            status="unroutable",
            reason="no_catalog_match",
        )

    total, rows = await list_delegations(pool, asking_butler="chronicler", limit=2, offset=0)
    assert total >= 3
    assert len(rows) == 2
    assert all(r["asking_butler"] == "chronicler" for r in rows)

    total_status, rows_status = await list_delegations(pool, status="unroutable")
    assert total_status >= 3
    assert all(r["status"] == "unroutable" for r in rows_status)
