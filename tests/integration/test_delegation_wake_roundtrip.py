"""Real-Postgres regression: the delegated-answer wake protocol (bu-27dxl.5.2).

Exercises core_181 against a fully migrated Postgres instance (testcontainers)
-- not just mocked-pool unit tests (see tests/core/test_delegation_wake.py and
the wake-specific classes in tests/core/test_delegation_ledger.py for those):

- ``public.delegation_ledger``'s new wake columns and
  ``public.delegation_wake_attempts`` round-trip through the real production
  writer/reader.
- First-answer acceptance atomically commits answer_digest/wake_key/
  wake_state=callback_pending (D2).
- Duplicate (same-text) vs. changed-answer resubmission classify correctly
  and a legacy answered row (no v1 provenance) is never treated as either.
- Switchboard's pre-dispatch ledger re-verification (``verify_wake_callback``,
  D3) accepts only the exact authoritative source/target/wake_key.
- ``handle_delegate_wake`` creates exactly one asker-local one-shot task
  against the real ``scheduled_tasks`` table, and duplicate delivery, a
  crash-after-insert replay, and a conflicting deterministic name all
  reconcile per D5 without ever creating a second logical task.
"""

from __future__ import annotations

import shutil

import asyncpg
import pytest

from butlers.core.delegation_ledger import (
    classify_unaccepted_answer,
    get_delegation,
    mark_dispatch_outcome,
    mark_wake_callback_failed,
    record_answer,
    record_ask,
    verify_wake_callback,
)
from butlers.core.delegation_wake import _build_return_task_prompt, handle_delegate_wake
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
    p = await asyncpg.create_pool(migrated_db_url, min_size=1, max_size=5)
    yield p
    await p.close()


async def _make_answered_row(
    pool: asyncpg.Pool, *, asking_butler: str = "finance", target_butler: str = "relationship"
) -> tuple[str, dict]:
    ledger_id = await record_ask(
        pool,
        asking_butler=asking_butler,
        question="Who is Alice's employer?",
        status="pending",
        target_butler=target_butler,
    )
    await mark_dispatch_outcome(pool, ledger_id, status="routed")
    row = await record_answer(pool, ledger_id, answering_butler=target_butler, answer="Acme Corp.")
    assert row is not None
    return ledger_id, row


# ---------------------------------------------------------------------------
# Schema shape
# ---------------------------------------------------------------------------


async def test_delegation_wake_attempts_table_exists(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'delegation_wake_attempts'
        """
    )
    columns = {r["column_name"] for r in rows}
    assert columns == {
        "id",
        "ledger_id",
        "ts",
        "stage",
        "result",
        "retryable",
        "error_class",
        "error_message",
        "actor_butler",
    }


async def test_wake_state_check_constraint_rejects_bogus_value(pool: asyncpg.Pool) -> None:
    ledger_id = await record_ask(
        pool,
        asking_butler="finance",
        question="q",
        status="pending",
        target_butler="relationship",
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "UPDATE public.delegation_ledger SET wake_state = 'bogus' WHERE id = $1", ledger_id
        )


# ---------------------------------------------------------------------------
# First-answer acceptance / duplicate / changed / legacy classification
# ---------------------------------------------------------------------------


async def test_first_answer_atomically_commits_wake_identity(pool: asyncpg.Pool) -> None:
    ledger_id, row = await _make_answered_row(pool)

    assert row["status"] == "answered"
    assert row["answer_digest"] is not None
    assert row["wake_key"] == f"delegation-wake:v1:{ledger_id}:{row['answer_digest']}"
    assert row["wake_state"] == "callback_pending"

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_key"] == row["wake_key"]
    assert reread["wake_state"] == "callback_pending"


async def test_duplicate_same_answer_classifies_as_duplicate(pool: asyncpg.Pool) -> None:
    ledger_id, first = await _make_answered_row(pool)

    second = await record_answer(
        pool, ledger_id, answering_butler="relationship", answer="Acme Corp."
    )
    assert second is None  # guarded UPDATE affects 0 rows on replay

    classification = await classify_unaccepted_answer(
        pool, ledger_id, answering_butler="relationship", answer="Acme Corp."
    )
    assert classification.outcome == "duplicate"
    assert classification.row["wake_key"] == first["wake_key"]

    # The wake identity must be untouched by the replay.
    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_key"] == first["wake_key"]
    assert reread["answer"] == "Acme Corp."


async def test_changed_answer_classifies_as_changed_and_leaves_original_intact(
    pool: asyncpg.Pool,
) -> None:
    ledger_id, first = await _make_answered_row(pool)

    second = await record_answer(
        pool, ledger_id, answering_butler="relationship", answer="Globex Inc."
    )
    assert second is None

    classification = await classify_unaccepted_answer(
        pool, ledger_id, answering_butler="relationship", answer="Globex Inc."
    )
    assert classification.outcome == "changed"

    # The original answer/wake identity must never be overwritten.
    reread = await get_delegation(pool, ledger_id)
    assert reread["answer"] == "Acme Corp."
    assert reread["wake_key"] == first["wake_key"]


async def test_legacy_answered_row_has_no_wake_provenance(pool: asyncpg.Pool) -> None:
    """A row answered before this migration/protocol existed: status=answered,
    but no answer_digest/wake_key. Must classify as 'legacy', never as a
    duplicate/changed candidate, and must be rejected by verify_wake_callback."""
    ledger_id = await pool.fetchval(
        """
        INSERT INTO public.delegation_ledger
            (asking_butler, question, target_butler, status, answer, answered_at, answering_butler)
        VALUES ('finance', 'legacy question', 'relationship', 'answered', 'legacy answer', now(),
                'relationship')
        RETURNING id
        """
    )

    classification = await classify_unaccepted_answer(
        pool, ledger_id, answering_butler="relationship", answer="legacy answer"
    )
    assert classification.outcome == "legacy"

    reason = await verify_wake_callback(
        pool, ledger_id, "any-key", source_butler="relationship", target_butler="finance"
    )
    assert reason is not None
    assert "legacy row" in reason


# ---------------------------------------------------------------------------
# Switchboard pre-dispatch verification (D3)
# ---------------------------------------------------------------------------


async def test_verify_wake_callback_authorizes_exact_match(pool: asyncpg.Pool) -> None:
    ledger_id, row = await _make_answered_row(pool)
    reason = await verify_wake_callback(
        pool, ledger_id, row["wake_key"], source_butler="relationship", target_butler="finance"
    )
    assert reason is None


async def test_verify_wake_callback_rejects_wrong_source(pool: asyncpg.Pool) -> None:
    ledger_id, row = await _make_answered_row(pool)
    reason = await verify_wake_callback(
        pool, ledger_id, row["wake_key"], source_butler="health", target_butler="finance"
    )
    assert reason is not None


async def test_verify_wake_callback_rejects_stale_wake_key(pool: asyncpg.Pool) -> None:
    ledger_id, _row = await _make_answered_row(pool)
    reason = await verify_wake_callback(
        pool,
        ledger_id,
        f"delegation-wake:v1:{ledger_id}:stale-digest",
        source_butler="relationship",
        target_butler="finance",
    )
    assert reason is not None


# ---------------------------------------------------------------------------
# handle_delegate_wake: deterministic asker-local task reconciliation (D5)
# ---------------------------------------------------------------------------


async def test_handle_delegate_wake_creates_exactly_one_task(pool: asyncpg.Pool) -> None:
    ledger_id, row = await _make_answered_row(pool)

    result = await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="finance"
    )

    assert result["status"] == "ok"
    assert result["wake_state"] == "task_created"

    task_rows = await pool.fetch(
        "SELECT id, name, prompt FROM scheduled_tasks WHERE name = $1",
        f"delegate-return-{ledger_id}",
    )
    assert len(task_rows) == 1
    assert str(task_rows[0]["id"]) == result["task_id"]
    assert "Acme Corp." in task_rows[0]["prompt"]

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_state"] == "task_created"
    assert str(reread["wake_task_id"]) == result["task_id"]
    assert reread["wake_task_name"] == f"delegate-return-{ledger_id}"


async def test_handle_delegate_wake_duplicate_delivery_returns_same_task(
    pool: asyncpg.Pool,
) -> None:
    ledger_id, row = await _make_answered_row(pool)

    first = await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="finance"
    )
    second = await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="finance"
    )

    assert second["task_id"] == first["task_id"]
    assert second.get("reconciled") is True

    count = await pool.fetchval(
        "SELECT count(*) FROM scheduled_tasks WHERE name = $1", f"delegate-return-{ledger_id}"
    )
    assert count == 1


async def test_handle_delegate_wake_reconciles_after_crash_before_ledger_update(
    pool: asyncpg.Pool,
) -> None:
    """Simulates a crash between the local task INSERT and the ledger's task
    binding UPDATE: the task exists, but wake_state is still callback_pending
    and wake_task_id is NULL. A replay of the same wake_key must find and bind
    that exact task -- never insert a second one."""
    ledger_id, row = await _make_answered_row(pool)
    task_name = f"delegate-return-{ledger_id}"
    prompt = _build_return_task_prompt(
        ledger_id=ledger_id,
        asking_butler="finance",
        target_butler="relationship",
        question="Who is Alice's employer?",
        answer="Acme Corp.",
        wake_key=row["wake_key"],
        answer_digest=row["answer_digest"],
    )
    orphaned_task_id = await pool.fetchval(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled)
        VALUES ($1, '* * * * *', 'prompt', $2, 'db', true)
        RETURNING id
        """,
        task_name,
        prompt,
    )

    result = await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="finance"
    )

    assert result["status"] == "ok"
    assert result["task_id"] == str(orphaned_task_id)
    assert result.get("reconciled") is True

    count = await pool.fetchval("SELECT count(*) FROM scheduled_tasks WHERE name = $1", task_name)
    assert count == 1  # no second task inserted

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_state"] == "task_created"
    assert str(reread["wake_task_id"]) == str(orphaned_task_id)


async def test_handle_delegate_wake_conflicting_deterministic_name_fails_closed(
    pool: asyncpg.Pool,
) -> None:
    ledger_id, row = await _make_answered_row(pool)
    task_name = f"delegate-return-{ledger_id}"
    unrelated_task_id = await pool.fetchval(
        """
        INSERT INTO scheduled_tasks (name, cron, dispatch_mode, prompt, source, enabled)
        VALUES ($1, '* * * * *', 'prompt', 'an unrelated hand-crafted task', 'db', true)
        RETURNING id
        """,
        task_name,
    )

    result = await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="finance"
    )

    assert result["status"] == "conflict"
    assert result["wake_state"] == "task_conflict"

    # The unrelated task must be untouched, and no second task created.
    task_rows = await pool.fetch("SELECT id FROM scheduled_tasks WHERE name = $1", task_name)
    assert len(task_rows) == 1
    assert task_rows[0]["id"] == unrelated_task_id

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_state"] == "task_conflict"
    assert reread["wake_task_id"] is None


async def test_handle_delegate_wake_rejects_wrong_asking_butler(pool: asyncpg.Pool) -> None:
    ledger_id, row = await _make_answered_row(pool)

    result = await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="health"
    )

    assert result["status"] == "error"
    count = await pool.fetchval(
        "SELECT count(*) FROM scheduled_tasks WHERE name = $1", f"delegate-return-{ledger_id}"
    )
    assert count == 0

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_state"] == "callback_pending"  # unchanged — never advanced


# ---------------------------------------------------------------------------
# Callback failure honesty (D1/D2): never downgrades advanced wake progress.
# ---------------------------------------------------------------------------


async def test_mark_wake_callback_failed_never_downgrades_task_created(
    pool: asyncpg.Pool,
) -> None:
    ledger_id, row = await _make_answered_row(pool)
    await handle_delegate_wake(
        pool, ledger_id=ledger_id, wake_key=row["wake_key"], asking_butler="finance"
    )

    # A stray/late route()-level failure report for this same wake_key must
    # not regress an already-successful task_created state.
    await mark_wake_callback_failed(pool, ledger_id, row["wake_key"])

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_state"] == "task_created"


async def test_mark_wake_callback_failed_advances_pending_row(pool: asyncpg.Pool) -> None:
    ledger_id, row = await _make_answered_row(pool)

    await mark_wake_callback_failed(pool, ledger_id, row["wake_key"])

    reread = await get_delegation(pool, ledger_id)
    assert reread["wake_state"] == "callback_failed"
