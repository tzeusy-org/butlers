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
