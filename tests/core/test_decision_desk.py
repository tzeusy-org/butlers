"""Decision intent store: one live intent per bead, validated against the digest.

bu-ckkpz.3, REQ-owner-decision-desk-001. Real PostgreSQL (core + switchboard
chains through ``sw_042``), so the live-intent invariant is the database's,
not the test's.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid

import asyncpg
import pytest

from butlers.core.decision_desk import (
    DecisionIntentError,
    DecisionPrompt,
    get_prompt,
    latest_intents,
    live_intent,
    record_decision_intent,
    record_prompt_choice,
)
from tests.decision_desk_helpers import (
    NOW,
    make_bead,
    make_digest,
    migrated_switchboard_db,
    switchboard_pool,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def desk_db_url(postgres_container) -> str:
    return migrated_switchboard_db(postgres_container)


@pytest.fixture
async def pool(desk_db_url):
    pool = await switchboard_pool(desk_db_url)
    try:
        yield pool
    finally:
        await pool.close()


async def _record(pool, option="Ship it", *, bead_id="bu-test1", digest=None, source="dashboard"):
    return await record_decision_intent(
        pool,
        bead_id=bead_id,
        option=option,
        source=source,
        actor="owner@dashboard",
        digest=digest if digest is not None else make_digest(make_bead(bead_id)),
    )


async def _insert_prompt(pool, bead_id="bu-test1", options=("Ship it", "Hold")) -> DecisionPrompt:
    row = await pool.fetchrow(
        "INSERT INTO switchboard.decision_prompts (bead_id, options, default_option, created_at) "
        "VALUES ($1, $2::jsonb, 'Hold', $3) RETURNING id",
        bead_id,
        json.dumps(list(options)),
        NOW,
    )
    prompt = await get_prompt(pool, row["id"])
    assert prompt is not None
    return prompt


async def test_records_a_pending_intent(pool) -> None:
    result = await _record(pool)

    assert result.created is True
    assert result.intent.status == "pending"
    assert result.intent.option == "Ship it"
    assert result.intent.attempts == 0
    assert (await live_intent(pool, "bu-test1")) == result.intent


async def test_identical_repeat_returns_the_existing_intent(pool) -> None:
    first = await _record(pool)
    second = await _record(pool)

    assert second.created is False
    assert second.intent.id == first.intent.id
    assert await pool.fetchval("SELECT COUNT(*) FROM switchboard.decision_intents") == 1


async def test_different_option_is_a_conflict(pool) -> None:
    await _record(pool)

    with pytest.raises(DecisionIntentError) as excinfo:
        await _record(pool, "Hold")

    assert excinfo.value.kind == "conflict"
    assert excinfo.value.reason == "intent_conflict:pending"
    assert excinfo.value.existing is not None


async def test_repeat_after_bead_left_the_digest_is_still_already_recorded(pool) -> None:
    first = await _record(pool)
    await pool.execute(
        "UPDATE switchboard.decision_intents SET status = 'applied', finished_at = now()"
    )

    # The applier closed the bead, so it is no longer an open decision.
    second = await _record(pool, digest=make_digest())

    assert second.created is False
    assert second.intent.id == first.intent.id
    assert second.intent.status == "applied"


async def test_a_failed_intent_does_not_block_a_new_choice(pool) -> None:
    await _record(pool)
    await pool.execute(
        "UPDATE switchboard.decision_intents "
        "SET status = 'failed', failure_reason = 'bead_not_open', finished_at = now()"
    )

    result = await _record(pool, "Hold")

    assert result.created is True
    assert result.intent.option == "Hold"
    latest = await latest_intents(pool, ["bu-test1"])
    assert latest["bu-test1"].id == result.intent.id


@pytest.mark.parametrize(
    ("digest", "option", "reason", "kind"),
    [
        (make_digest(available=False), "Ship it", "decisions_unavailable", "unavailable"),
        (make_digest(), "Ship it", "decision_not_open", "invalid"),
        (
            make_digest(make_bead(available=False)),
            "Ship it",
            "structured_details_unavailable",
            "invalid",
        ),
        (make_digest(make_bead()), "Something else", "option_not_offered", "invalid"),
    ],
)
async def test_refusals_are_categorical_and_record_nothing(
    pool, digest, option, reason, kind
) -> None:
    with pytest.raises(DecisionIntentError) as excinfo:
        await _record(pool, option, digest=digest)

    assert (excinfo.value.reason, excinfo.value.kind) == (reason, kind)
    assert await pool.fetchval("SELECT COUNT(*) FROM switchboard.decision_intents") == 0


async def test_concurrent_recorders_admit_exactly_one_live_intent(pool) -> None:
    results = await asyncio.gather(
        *(_record(pool) for _ in range(4)),
        _record(pool, "Hold"),
        return_exceptions=True,
    )

    created = [r for r in results if not isinstance(r, BaseException) and r.created]
    assert len(created) == 1
    assert await pool.fetchval("SELECT COUNT(*) FROM switchboard.decision_intents") == 1
    for result in results:
        if isinstance(result, BaseException):
            assert isinstance(result, DecisionIntentError)
            assert result.kind == "conflict"


async def test_database_rejects_a_second_live_intent(pool) -> None:
    await _record(pool)
    with pytest.raises(asyncpg.UniqueViolationError):
        await pool.execute(
            "INSERT INTO switchboard.decision_intents (bead_id, option, source, actor) "
            "VALUES ('bu-test1', 'Hold', 'dashboard', 'owner@dashboard')"
        )


async def test_database_requires_a_reason_exactly_when_failed(pool) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO switchboard.decision_intents (bead_id, option, source, actor, status) "
            "VALUES ('bu-test1', 'Hold', 'dashboard', 'owner@dashboard', 'failed')"
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO switchboard.decision_intents "
            "(bead_id, option, source, actor, failure_reason) "
            "VALUES ('bu-test1', 'Hold', 'dashboard', 'owner@dashboard', 'x')"
        )


async def test_prompt_choice_records_the_snapshot_option(pool) -> None:
    prompt = await _insert_prompt(pool)

    result = await record_prompt_choice(
        pool,
        prompt=prompt,
        option_index=1,
        actor="owner@telegram",
        digest=make_digest(make_bead()),
    )

    assert result.created is True
    assert result.intent.option == "Hold"
    assert result.intent.source == "telegram"
    assert result.intent.prompt_id == prompt.id


async def test_prompt_choice_is_refused_when_options_changed(pool) -> None:
    prompt = await _insert_prompt(pool)

    with pytest.raises(DecisionIntentError) as excinfo:
        await record_prompt_choice(
            pool,
            prompt=prompt,
            option_index=0,
            actor="owner@telegram",
            digest=make_digest(make_bead(options=("Hold", "Ship it"))),
        )

    assert excinfo.value.reason == "options_changed"
    assert await pool.fetchval("SELECT COUNT(*) FROM switchboard.decision_intents") == 0


async def test_prompt_choice_out_of_range_is_invalid(pool) -> None:
    prompt = await _insert_prompt(pool)

    with pytest.raises(DecisionIntentError) as excinfo:
        await record_prompt_choice(
            pool, prompt=prompt, option_index=2, actor="owner@telegram", digest=make_digest()
        )

    assert excinfo.value.reason == "option_not_offered"


async def test_get_prompt_unknown_id_is_none(pool) -> None:
    assert await get_prompt(pool, uuid.uuid4()) is None


async def test_latest_intents_prefers_live_over_newer_failed(pool) -> None:
    live = await _record(pool)
    await pool.execute(
        "INSERT INTO switchboard.decision_intents "
        "(bead_id, option, source, actor, status, failure_reason, created_at) "
        "VALUES ('bu-test1', 'Hold', 'dashboard', 'owner@dashboard', 'failed', 'x', "
        "now() + interval '1 minute')"
    )

    latest = await latest_intents(pool, ["bu-test1", "bu-none"])

    assert set(latest) == {"bu-test1"}
    assert latest["bu-test1"].id == live.intent.id


# REQ-owner-decision-desk-002: reopening proof uses the actual export parser and DB.
def _reopening_digest(tmp_path, *, finished_at, export_at=None, checked_at=None, updated_at=True):
    import os
    from datetime import timedelta

    from butlers.jobs.decision_review import compute_decision_digest

    export_at = export_at if export_at is not None else finished_at + timedelta(minutes=10)
    checked_at = checked_at if checked_at is not None else export_at
    issue = {
        "id": "bu-test1",
        "title": "Choose again",
        "status": "open",
        "issue_type": "task",
        "labels": ["decision"],
        "created_at": (finished_at - timedelta(days=2)).isoformat(),
        "due_at": (checked_at + timedelta(days=1)).isoformat(),
        "metadata": {"decision": {"options": ["Ship it", "Hold"], "default": "Hold"}},
    }
    if updated_at is True:
        issue["updated_at"] = (finished_at + timedelta(minutes=1)).isoformat()
    elif updated_at is not None:
        issue["updated_at"] = updated_at
    export = tmp_path / "reopened.export.jsonl"
    export.write_text(json.dumps(issue) + "\n")
    os.utime(export, (export_at.timestamp(), export_at.timestamp()))
    return compute_decision_digest(export_path=export, now=checked_at)


async def _applied_reopening_intent(pool, *, finished_at, prompt_id=None, option="Ship it"):
    from datetime import timedelta

    first = await record_decision_intent(
        pool,
        bead_id="bu-test1",
        option=option,
        source="telegram" if prompt_id else "dashboard",
        actor="owner@telegram" if prompt_id else "owner@dashboard",
        prompt_id=prompt_id,
        digest=make_digest(make_bead()),
    )
    return await pool.fetchrow(
        "UPDATE switchboard.decision_intents SET status = 'applied', attempts = 1, "
        "created_at = $2, claimed_at = $3, "
        "finished_at = $4, updated_at = $4 WHERE id = $1 RETURNING *",
        first.intent.id,
        finished_at - timedelta(minutes=2),
        finished_at - timedelta(minutes=1),
        finished_at,
    )


async def test_reopened_applied_intent_allows_a_fresh_explicit_choice_after_grace(
    pool, tmp_path
) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    digest = _reopening_digest(tmp_path, finished_at=finished)
    assert digest.available and digest.open_decisions[0].structured_details_available

    replacement = await _record(pool, "Hold", digest=digest)

    assert replacement.created and replacement.intent.status == "pending"
    assert replacement.intent.id != original["id"]
    stored = await pool.fetchrow(
        "SELECT * FROM switchboard.decision_intents WHERE id = $1", original["id"]
    )
    assert stored["status"] == "superseded"
    assert {k: v for k, v in stored.items() if k not in {"status", "updated_at"}} == {
        k: v for k, v in original.items() if k not in {"status", "updated_at"}
    }
    assert (await live_intent(pool, "bu-test1")).id == replacement.intent.id
    assert (await latest_intents(pool, ["bu-test1"]))["bu-test1"].id == replacement.intent.id

    # A later reopened epoch must create a new id even for identical option text.
    next_finish = NOW + timedelta(minutes=5)
    await pool.execute(
        "UPDATE switchboard.decision_intents SET status = 'applied', finished_at = $2 "
        "WHERE id = $1",
        replacement.intent.id,
        next_finish,
    )
    same_option = await _record(
        pool, "Hold", digest=_reopening_digest(tmp_path, finished_at=next_finish)
    )
    assert same_option.created and same_option.intent.id != replacement.intent.id
    assert same_option.intent.status == "pending"
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM switchboard.decision_intents "
            "WHERE bead_id = 'bu-test1' AND status IN ('pending', 'applying', 'applied')"
        )
        == 1
    )


async def _assert_reopening_stays_applied(pool, original, digest) -> None:
    projected = await latest_intents(pool, ["bu-test1"], digest=digest)
    assert projected["bu-test1"].id == original["id"]
    assert projected["bu-test1"].status == "applied"
    with pytest.raises(DecisionIntentError) as refused:
        await _record(pool, "Hold", digest=digest)
    assert refused.value.reason == "intent_conflict:applied"
    stored = await pool.fetchrow(
        "SELECT * FROM switchboard.decision_intents WHERE id = $1", original["id"]
    )
    assert dict(stored) == dict(original)
    assert await pool.fetchval("SELECT count(*) FROM switchboard.decision_intents") == 1


async def test_reopening_inside_grace_keeps_the_applied_live_slot(pool, tmp_path) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    await _assert_reopening_stays_applied(
        pool,
        original,
        _reopening_digest(
            tmp_path, finished_at=finished, export_at=NOW - timedelta(microseconds=1)
        ),
    )


async def test_fresh_file_mtime_does_not_supersede_old_open_content(pool, tmp_path) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    await _assert_reopening_stays_applied(
        pool,
        original,
        _reopening_digest(tmp_path, finished_at=finished, updated_at=finished.isoformat()),
    )


async def test_aged_post_close_snapshot_does_not_supersede(pool, tmp_path) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=20, microseconds=1)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    await _assert_reopening_stays_applied(
        pool, original, _reopening_digest(tmp_path, finished_at=finished, checked_at=NOW)
    )


async def test_unusable_reopening_timestamps_preserve_applied_history(pool, tmp_path) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    # Distinct source encodings project to the same absent-evidence class.
    # These projection checks are not separate migrated-DB outcomes.
    missing = _reopening_digest(tmp_path, finished_at=finished, updated_at=None)
    malformed = _reopening_digest(tmp_path, finished_at=finished, updated_at="not-a-time")
    naive = _reopening_digest(
        tmp_path, finished_at=finished, updated_at=NOW.replace(tzinfo=None).isoformat()
    )
    assert missing.open_decisions[0].updated_at is None
    assert malformed.open_decisions[0].updated_at is None
    assert naive.open_decisions[0].updated_at is None
    await _assert_reopening_stays_applied(pool, original, missing)
    await _assert_reopening_stays_applied(pool, original, make_digest(available=False))

    original = await pool.fetchrow(
        "UPDATE switchboard.decision_intents SET finished_at = NULL WHERE id = $1 RETURNING *",
        original["id"],
    )
    await _assert_reopening_stays_applied(
        pool, original, _reopening_digest(tmp_path, finished_at=finished)
    )


async def test_future_reopening_evidence_preserves_applied_history(pool, tmp_path) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    await _assert_reopening_stays_applied(
        pool,
        original,
        _reopening_digest(
            tmp_path, finished_at=finished, updated_at=(NOW + timedelta(seconds=1)).isoformat()
        ),
    )
    await _assert_reopening_stays_applied(
        pool,
        original,
        _reopening_digest(
            tmp_path, finished_at=finished, export_at=NOW + timedelta(seconds=1), checked_at=NOW
        ),
    )


async def test_concurrent_reopening_recorders_keep_one_live_intent(pool, tmp_path) -> None:
    from datetime import timedelta

    prompt = await _insert_prompt(pool)
    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished, prompt_id=prompt.id)
    digest = _reopening_digest(tmp_path, finished_at=finished)
    outcomes = await asyncio.gather(
        latest_intents(pool, ["bu-test1"], digest=digest),
        _record(pool, "Hold", digest=digest),
        _record(pool, "Hold", digest=digest),
        record_prompt_choice(
            pool, prompt=prompt, option_index=0, actor="owner@telegram", digest=digest
        ),
        return_exceptions=True,
    )
    assert isinstance(outcomes[0], dict)
    first, second = outcomes[1:3]
    assert not isinstance(first, BaseException) and not isinstance(second, BaseException)
    assert sorted([first.created, second.created]) == [False, True]
    assert first.intent.id == second.intent.id != original["id"]
    assert isinstance(outcomes[3], DecisionIntentError)
    assert outcomes[3].reason == "intent_conflict:superseded"
    assert await pool.fetchval("SELECT count(*) FROM switchboard.decision_intents") == 2
    assert (await live_intent(pool, "bu-test1")).id == first.intent.id
    assert (
        await pool.fetchval(
            "SELECT status FROM switchboard.decision_intents WHERE id = $1", original["id"]
        )
        == "superseded"
    )


async def test_consumed_prompt_cannot_record_after_supersession(pool, tmp_path) -> None:
    from datetime import timedelta

    prompt = await _insert_prompt(pool)
    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished, prompt_id=prompt.id)
    digest = _reopening_digest(tmp_path, finished_at=finished)
    with pytest.raises(DecisionIntentError, match="intent_conflict:superseded"):
        await record_prompt_choice(
            pool, prompt=prompt, option_index=0, actor="owner@telegram", digest=digest
        )
    assert (await latest_intents(pool, ["bu-test1"], digest=digest))[
        "bu-test1"
    ].status == "superseded"
    with pytest.raises(DecisionIntentError, match="intent_conflict:superseded"):
        await record_prompt_choice(
            pool, prompt=prompt, option_index=1, actor="owner@telegram", digest=digest
        )
    assert await pool.fetchval("SELECT count(*) FROM switchboard.decision_intents") == 1
    assert (
        await pool.fetchval(
            "SELECT finished_at FROM switchboard.decision_intents WHERE id = $1", original["id"]
        )
        == finished
    )


async def test_latest_projection_prefers_live_then_newest_terminal_history(pool, tmp_path) -> None:
    from datetime import timedelta

    finished = NOW - timedelta(minutes=10)
    original = await _applied_reopening_intent(pool, finished_at=finished)
    digest = _reopening_digest(tmp_path, finished_at=finished)
    prior = (await latest_intents(pool, ["bu-test1"], digest=digest))["bu-test1"]
    assert prior.id == original["id"] and prior.status == "superseded"
    replacement = await _record(pool, "Hold", digest=digest)
    assert (await latest_intents(pool, ["bu-test1"], digest=digest))[
        "bu-test1"
    ].id == replacement.intent.id
    await pool.execute(
        "UPDATE switchboard.decision_intents SET status = 'failed', "
        "failure_reason = 'bead_closed_elsewhere', finished_at = now() WHERE id = $1",
        replacement.intent.id,
    )
    projected = (await latest_intents(pool, ["bu-test1"], digest=digest))["bu-test1"]
    assert projected.id == replacement.intent.id and projected.status == "failed"
