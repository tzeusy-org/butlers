"""Decision routing job: attention gates, budget, and at-most-once prompts.

bu-ckkpz.3, REQ-owner-decision-desk-003 / -004. Real PostgreSQL for the prompt
reservation and the attention ledger; the notify boundary, owner recipient and
callback secret are stubbed.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from butlers.jobs import decision_routing
from butlers.jobs.decision_routing import DAILY_PROMPT_BUDGET, run_decision_routing
from tests.decision_desk_helpers import (
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

# The ledger stamps rows with the database clock, and the deferral dedup reads
# them back relative to the job's clock, so the job clock must be real time.
NOW = datetime.now(UTC).replace(microsecond=0)
_SENT = {"status": "sent", "notification_id": "n-1", "transport": {"outcome": "confirmed"}}


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


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("BUTLERS_DECISION_ROUTING_ENABLED", "1")


@pytest.fixture
def seams():
    """Stub the owner-facing boundaries; yields the deliver mock."""
    deliver = AsyncMock(return_value=_SENT)
    with (
        patch.object(decision_routing, "_check_suppression", AsyncMock(return_value=None)),
        patch.object(
            decision_routing,
            "resolve_owner_telegram_recipient",
            AsyncMock(return_value="100200300"),
        ),
        patch.object(decision_routing, "_callback_secret", AsyncMock(return_value="s3cret")),
        patch.object(
            decision_routing, "_dashboard_base_url", return_value="https://dash.example.test"
        ),
        patch("butlers.tools.switchboard.notification.deliver.deliver", deliver),
    ):
        yield deliver


async def _prompts(pool) -> dict[str, str | None]:
    rows = await pool.fetch("SELECT bead_id, delivery_outcome FROM switchboard.decision_prompts")
    return {row["bead_id"]: row["delivery_outcome"] for row in rows}


async def _ledger(pool) -> list[tuple[str, str, str | None]]:
    rows = await pool.fetch(
        "SELECT dedup_key, outcome, reason FROM public.attention_ledger ORDER BY occurred_at, id"
    )
    return [(row["dedup_key"], row["outcome"], row["reason"]) for row in rows]


async def test_disabled_by_default_touches_nothing(pool, monkeypatch, seams) -> None:
    monkeypatch.delenv("BUTLERS_DECISION_ROUTING_ENABLED", raising=False)

    result = await run_decision_routing(pool, _now=NOW, _digest=make_digest(make_bead()))

    assert result == {"enabled": False}
    seams.assert_not_awaited()
    assert await _prompts(pool) == {}


async def test_unavailable_digest_sends_nothing(pool, enabled, seams) -> None:
    result = await run_decision_routing(pool, _now=NOW, _digest=make_digest(available=False))

    assert result["available"] is False
    seams.assert_not_awaited()


async def test_delivers_one_prompt_per_bead_once(pool, enabled, seams) -> None:
    digest = make_digest(make_bead())

    first = await run_decision_routing(pool, _now=NOW, _digest=digest)
    second = await run_decision_routing(pool, _now=NOW + timedelta(minutes=15), _digest=digest)

    assert first["outcomes"] == {"delivered": 1}
    assert second["candidates"] == 0
    assert seams.await_count == 1
    envelope = seams.await_args.kwargs["notify_request"]
    assert envelope["delivery"]["intent"] == "decision_request"
    assert envelope["delivery"]["recipient"] == "100200300"
    assert await _prompts(pool) == {"bu-test1": "delivered"}
    assert await _ledger(pool) == [("decision_prompt:bu-test1", "delivered", None)]


async def test_escalated_beads_go_first_then_oldest(pool, enabled, seams) -> None:
    beads = [
        make_bead(f"bu-{n}", age=timedelta(days=10 - n)) for n in range(DAILY_PROMPT_BUDGET + 1)
    ]
    digest = make_digest(*beads, escalated=("bu-2",))

    result = await run_decision_routing(pool, _now=NOW, _digest=digest)

    sent = [call.kwargs["notify_request"]["delivery"]["message"] for call in seams.await_args_list]
    assert [line.split("\n")[1].split(",")[0] for line in sent] == ["bu-2", "bu-0", "bu-1"]
    assert result["outcomes"] == {"delivered": 3, "budget_exhausted": 1}
    ledger = await _ledger(pool)
    assert ("decision_prompt:bu-3", "deferred", "budget_exhausted") in ledger


async def test_budget_is_a_rolling_24_hours(pool, enabled, seams) -> None:
    beads = [make_bead(f"bu-{n}") for n in range(DAILY_PROMPT_BUDGET + 1)]
    digest = make_digest(*beads)
    await run_decision_routing(pool, _now=NOW, _digest=digest)

    held = await run_decision_routing(pool, _now=NOW + timedelta(hours=23), _digest=digest)
    released = await run_decision_routing(pool, _now=NOW + timedelta(hours=25), _digest=digest)

    assert held["outcomes"] == {"budget_exhausted": 1}
    assert released["outcomes"] == {"delivered": 1}
    assert seams.await_count == DAILY_PROMPT_BUDGET + 1


async def test_quiet_hours_defer_with_one_ledger_row_per_window(pool, enabled, seams) -> None:
    digest = make_digest(make_bead())
    with patch.object(
        decision_routing, "_check_suppression", AsyncMock(return_value="quiet_hours")
    ):
        first = await run_decision_routing(pool, _now=NOW, _digest=digest)
        await run_decision_routing(pool, _now=NOW + timedelta(hours=1), _digest=digest)

    assert first["deferred"] == "quiet_hours"
    seams.assert_not_awaited()
    assert await _prompts(pool) == {}
    assert await _ledger(pool) == [("decision_prompt:bu-test1", "deferred", "quiet_hours")]

    # Still eligible once the hold lifts.
    after = await run_decision_routing(pool, _now=NOW + timedelta(hours=2), _digest=digest)
    assert after["outcomes"] == {"delivered": 1}


async def test_not_attempted_is_retried_but_uncertain_never_is(pool, enabled, seams) -> None:
    digest = make_digest(make_bead("bu-a"), make_bead("bu-b"))
    seams.side_effect = [
        {"status": "failed", "notification_id": "n", "transport": {"outcome": "not_attempted"}},
        {"status": "failed", "notification_id": "n", "transport": {"outcome": "uncertain"}},
    ]

    await run_decision_routing(pool, _now=NOW, _digest=digest)
    assert await _prompts(pool) == {"bu-a": "not_attempted", "bu-b": "uncertain"}

    seams.side_effect = None
    later = await run_decision_routing(pool, _now=NOW + timedelta(minutes=15), _digest=digest)

    assert later["outcomes"] == {"delivered": 1}
    assert seams.await_args.kwargs["notify_request"]["delivery"]["message"].startswith(
        "Decision needed"
    )
    assert await _prompts(pool) == {"bu-a": "delivered", "bu-b": "uncertain"}


async def test_a_resent_prompt_gets_fresh_token_binding(pool, enabled, seams) -> None:
    digest = make_digest(make_bead())
    seams.return_value = {
        "status": "failed",
        "notification_id": "n",
        "transport": {"outcome": "not_attempted"},
    }
    await run_decision_routing(pool, _now=NOW, _digest=digest)
    first_token = seams.await_args.kwargs["notify_request"]["actions"][0]["callback_token"]

    seams.return_value = _SENT
    await run_decision_routing(pool, _now=NOW + timedelta(minutes=15), _digest=digest)
    second_token = seams.await_args.kwargs["notify_request"]["actions"][0]["callback_token"]

    created_at = await pool.fetchval("SELECT created_at FROM switchboard.decision_prompts")
    assert created_at == NOW + timedelta(minutes=15)
    assert first_token.split(":")[1] == second_token.split(":")[1]
    assert first_token != second_token


async def test_exception_after_reservation_is_uncertain(pool, enabled, seams) -> None:
    seams.side_effect = RuntimeError("boom")

    result = await run_decision_routing(pool, _now=NOW, _digest=make_digest(make_bead()))

    assert result["outcomes"] == {"uncertain": 1}
    assert await _prompts(pool) == {"bu-test1": "uncertain"}
    assert await _ledger(pool) == [("decision_prompt:bu-test1", "failed", "delivery_uncertain")]


async def test_stale_in_flight_reservation_becomes_uncertain(pool, enabled, seams) -> None:
    await pool.execute(
        "INSERT INTO switchboard.decision_prompts "
        "(bead_id, options, default_option, created_at, updated_at) "
        "VALUES ('bu-test1', '[\"Ship it\", \"Hold\"]', 'Hold', $1, $1)",
        NOW - timedelta(minutes=11),
    )

    result = await run_decision_routing(pool, _now=NOW, _digest=make_digest(make_bead()))

    assert result["candidates"] == 0
    seams.assert_not_awaited()
    assert await _prompts(pool) == {"bu-test1": "uncertain"}


async def test_missing_recipient_or_secret_is_not_attempted(pool, enabled, seams) -> None:
    with patch.object(decision_routing, "_callback_secret", AsyncMock(return_value=None)):
        await run_decision_routing(pool, _now=NOW, _digest=make_digest(make_bead()))

    seams.assert_not_awaited()
    assert await _prompts(pool) == {"bu-test1": "not_attempted"}
    assert await _ledger(pool) == [
        ("decision_prompt:bu-test1", "failed", "callback_secret_unavailable")
    ]


async def test_envelope_refused_before_routing_is_terminal(pool, enabled, seams) -> None:
    seams.return_value = {"status": "failed", "error": "Invalid notify.v1 envelope: x"}
    digest = make_digest(make_bead())

    await run_decision_routing(pool, _now=NOW, _digest=digest)
    later = await run_decision_routing(pool, _now=NOW + timedelta(minutes=15), _digest=digest)

    assert later["candidates"] == 0
    assert await _prompts(pool) == {"bu-test1": "rejected"}


async def test_decided_or_unstructured_beads_are_not_offered(pool, enabled, seams) -> None:
    await pool.execute(
        "INSERT INTO switchboard.decision_intents (bead_id, option, source, actor) "
        "VALUES ('bu-decided', 'Hold', 'dashboard', 'owner@dashboard')"
    )
    digest = make_digest(
        make_bead("bu-decided"),
        make_bead("bu-bare", available=False),
        make_bead("bu-wide", options=tuple(f"o{n}" for n in range(17))),
    )

    result = await run_decision_routing(pool, _now=NOW, _digest=digest)

    assert result["candidates"] == 0
    seams.assert_not_awaited()
