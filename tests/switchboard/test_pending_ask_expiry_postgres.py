"""Refused owner asks, their 24 hour expiry and the purge, over real migrated PostgreSQL."""

from uuid import uuid4

import pytest

from butlers.core.goal_admission import Evidence, SourceRef
from butlers.core.goal_ledger import RETRYABLE_REFUSALS, GoalLedger
from butlers.goal_runner.owner_failure import CAUSE_LINES
from tests.switchboard.goal_admission_fixtures import limits
from tests.switchboard.goal_ledger_fixtures import (  # noqa: F401
    admit,
    configure,
    ledger,
    ledger_url,
)
from tests.switchboard.test_owner_failure_postgres import (  # noqa: F401
    Sender,
    asks,
    owner_goal,
    reporter,
    world,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def refuse_owner_message(owner, runtime, evidence=None):
    """Fill the owner origin's quota, then have a second message refused."""
    await configure(owner, limits(new_goals=1))
    first = await admit(runtime, Evidence("message", "p-1", owner=True))
    assert first.result == "admitted"
    source = SourceRef("message", str(uuid4()))
    refused = await admit(runtime, evidence or Evidence("message", "p-2", owner=True), source)
    return source, refused


async def test_a_quota_refused_owner_message_leaves_one_ask_with_a_source_reference(world):  # noqa: F811
    owner, runtime, _ = world
    source, refused = await refuse_owner_message(owner, runtime)
    assert (refused.result, refused.reason) == ("refused", "quota_exhausted")
    (row,) = [r for r in await asks(owner) if r["cause"] == "admission_refused"]
    assert row["goal_id"] is None and row["state"] == "open" and row["source_key"] == source.key
    assert row["original_ask"] == {
        "v": 1,
        "source": {"kind": "message", "id": source.id},
        "refusal": "quota_exhausted",
    }
    window = row["expires_at"] - row["created_at"]
    assert 23.9 * 3600 < window.total_seconds() <= 24 * 3600 + 1
    # A retry of the same message does not make a second ask.
    await admit(runtime, Evidence("message", "p-2", owner=True), source)
    assert len([r for r in await asks(owner) if r["cause"] == "admission_refused"]) == 1


async def test_only_owner_messages_and_retryable_reasons_leave_an_ask(world):  # noqa: F811
    owner, runtime, _ = world
    await configure(owner, limits(new_goals=1))
    await admit(runtime, Evidence("message", "p-1", owner=True))
    # A third party's refused message must never page the owner.
    refused = await admit(runtime, Evidence("message", "stranger", owner=False))
    assert refused.result == "admitted"  # its own origin's quota is separate
    again = await admit(runtime, Evidence("message", "stranger-2", owner=False))
    again = await admit(runtime, Evidence("message", "stranger-3", owner=False))
    assert again.result == "refused" and again.reason == "quota_exhausted"
    assert [r for r in await asks(owner) if r["cause"] == "admission_refused"] == []
    assert RETRYABLE_REFUSALS == {"quota_exhausted", "limits_unavailable"}
    for reason in ("cause_invalid", "causal_chain_limit", "recorded_without_goal"):
        goals = GoalLedger(runtime)
        await goals.remember_refused_owner_ask(
            SourceRef("message", str(uuid4())),
            Evidence("message", "x", owner=True),
            "owner",
            reason,
        )
    assert [r for r in await asks(owner) if r["cause"] == "admission_refused"] == []


async def test_a_dark_rollout_remembers_nothing(world):  # noqa: F811
    owner, runtime, _ = world
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = false")
    _, refused = await refuse_owner_message(owner, runtime)
    assert refused.reason == "quota_exhausted"
    assert [r for r in await asks(owner) if r["cause"] == "admission_refused"] == []


async def test_a_failure_to_remember_never_changes_the_refusal(world):  # noqa: F811
    owner, runtime, _ = world
    await owner.execute("REVOKE INSERT ON switchboard.pending_asks FROM butler_switchboard_rw")
    try:
        _, refused = await refuse_owner_message(owner, runtime)
        assert (refused.result, refused.reason) == ("refused", "quota_exhausted")
    finally:
        await owner.execute("GRANT INSERT ON switchboard.pending_asks TO butler_switchboard_rw")
    assert await asks(owner) == []


async def test_a_refused_ask_gets_a_subject_and_its_own_closed_cause_line(world):  # noqa: F811
    owner, runtime, _ = world
    await refuse_owner_message(owner, runtime)
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 1
    (row,) = [r for r in await asks(owner) if r["cause"] == "admission_refused"]
    assert row["subject_id"] is not None
    assert sender.lines == [(CAUSE_LINES["admission_refused"], f"pending_ask:{row['id']}")]
    assert await reporter(runtime, Sender()).sweep() == 0


async def test_a_stale_ask_becomes_dropped_stale_clears_its_reference_and_is_never_told(world):  # noqa: F811
    owner, runtime, _ = world
    await refuse_owner_message(owner, runtime)
    await reporter(runtime, Sender()).sweep()
    (row,) = await asks(owner)
    await owner.execute(
        "UPDATE switchboard.pending_asks SET expires_at = clock_timestamp() - interval '1 second'"
    )
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 0 and sender.lines == []
    (row,) = await asks(owner)
    assert (row["state"], row["original_ask"]) == ("dropped_stale", None)
    state = await owner.fetchval(
        "SELECT state FROM public.owner_delivery_subjects WHERE id = $1", row["subject_id"]
    )
    assert state == "expired"


async def test_the_purge_runs_while_delivery_is_dark(world):  # noqa: F811
    owner, runtime, _ = world
    await refuse_owner_message(owner, runtime)
    await owner.execute("UPDATE public.owner_delivery_rollout SET enabled = false")
    await owner.execute(
        "UPDATE switchboard.pending_asks SET expires_at = clock_timestamp() - interval '1 second'"
    )
    await reporter(runtime, Sender()).sweep()
    (row,) = await asks(owner)
    assert (row["state"], row["original_ask"]) == ("dropped_stale", None)


async def test_a_failure_older_than_a_day_is_not_reported_on_first_rollout(world):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await owner_goal(runtime, owner)
    await owner.execute(
        "UPDATE switchboard.goals SET updated_at = clock_timestamp() - interval '25 hours' "
        "WHERE id = $1",
        goal_id,
    )
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 0
    assert await asks(owner) == [] and sender.lines == []


async def test_a_goal_ask_expires_a_day_after_the_failure_not_after_the_sweep(world):  # noqa: F811
    owner, runtime, _ = world
    goal_id = await owner_goal(runtime, owner)
    await owner.execute(
        "UPDATE switchboard.goals SET updated_at = clock_timestamp() - interval '23 hours' "
        "WHERE id = $1",
        goal_id,
    )
    await reporter(runtime, Sender()).sweep()
    (row,) = await asks(owner)
    remaining = (row["expires_at"] - row["created_at"]).total_seconds()
    assert 55 * 60 < remaining < 65 * 60


async def test_the_original_ask_can_exist_only_while_the_ask_is_open(world):  # noqa: F811
    owner, runtime, _ = world
    await refuse_owner_message(owner, runtime)
    (row,) = await asks(owner)
    for state in ("run", "dropped", "dropped_stale", "acked"):
        with pytest.raises(Exception, match="ck_pending_ask_original"):
            await owner.execute(
                "UPDATE switchboard.pending_asks SET state = $2 WHERE id = $1", row["id"], state
            )


async def test_a_line_that_can_never_be_sent_is_given_up_after_a_few_sweeps(world):  # noqa: F811
    from butlers.goal_runner.owner_failure import MAX_LINE_ATTEMPTS

    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    sender = Sender("failed")
    for _ in range(MAX_LINE_ATTEMPTS + 3):
        await reporter(runtime, sender).sweep()
    assert len(sender.lines) == MAX_LINE_ATTEMPTS
    (row,) = await asks(owner)
    assert row["state"] == "open" and row["line_attempts"] == MAX_LINE_ATTEMPTS


async def test_a_forwarded_owner_message_with_outside_content_still_leaves_an_ask(world):  # noqa: F811
    owner, runtime, _ = world
    facts = Evidence("message", "p-2", owner=True, forwarded=True, outside_content=True)
    source, refused = await refuse_owner_message(owner, runtime, facts)
    assert refused.reason == "quota_exhausted"
    (row,) = [r for r in await asks(owner) if r["cause"] == "admission_refused"]
    assert row["source_key"] == source.key


async def test_an_oversized_reference_is_refused_by_the_database(world):  # noqa: F811
    owner, _, _ = world
    with pytest.raises(Exception, match="ck_pending_ask_original"):
        await owner.execute(
            "INSERT INTO switchboard.pending_asks (source_key, cause, original_ask) "
            "VALUES ('big', 'admission_refused', jsonb_build_object('x', repeat('a', 2000)))"
        )


async def test_an_unreadable_limits_refusal_gets_its_own_sentence(world):  # noqa: F811
    from butlers.goal_runner.owner_failure import LIMITS_UNAVAILABLE_LINE

    owner, runtime, _ = world
    await owner.execute(
        "INSERT INTO switchboard.pending_asks (source_key, cause, original_ask) "
        "VALUES ('lim', 'admission_refused', "
        '\'{"v":1,"source":{"kind":"message","id":"m"},"refusal":"limits_unavailable"}\')'
    )
    sender = Sender()
    assert await reporter(runtime, sender).sweep() == 1
    assert sender.lines[0][0] == LIMITS_UNAVAILABLE_LINE
    assert LIMITS_UNAVAILABLE_LINE not in CAUSE_LINES.values()
    assert "busy" not in LIMITS_UNAVAILABLE_LINE


async def test_giving_up_on_a_line_is_logged_once_without_content(world, caplog):  # noqa: F811
    from butlers.goal_runner.owner_failure import MAX_LINE_ATTEMPTS

    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    with caplog.at_level("WARNING"):
        for _ in range(MAX_LINE_ATTEMPTS + 2):
            await reporter(runtime, Sender("failed")).sweep()
    given_up = [r for r in caplog.records if "given up" in r.getMessage()]
    assert len(given_up) == 1


async def test_one_bad_expiry_does_not_stop_the_others(world, monkeypatch):  # noqa: F811
    from butlers.core import owner_delivery as od

    owner, runtime, _ = world
    await refuse_owner_message(owner, runtime)
    await owner_goal(runtime, owner)
    await reporter(runtime, Sender()).sweep()
    rows = await asks(owner)
    assert len(rows) == 2
    bad = rows[0]["id"]
    real = od.close_for_authority

    async def flaky(conn, *, kind, source_ref, status):
        if source_ref == str(bad):
            raise RuntimeError("cannot close")
        return await real(conn, kind=kind, source_ref=source_ref, status=status)

    monkeypatch.setattr(od, "close_for_authority", flaky)
    await owner.execute(
        "UPDATE switchboard.pending_asks SET expires_at = clock_timestamp() - interval '1 second'"
    )
    await reporter(runtime, Sender()).sweep()
    states = {r["id"]: r["state"] for r in await asks(owner)}
    assert states[bad] == "open"
    assert [state for ask, state in states.items() if ask != bad] == ["dropped_stale"]


async def test_a_successful_last_send_does_not_log_giving_up(world, caplog):  # noqa: F811
    from butlers.goal_runner.owner_failure import MAX_LINE_ATTEMPTS

    owner, runtime, _ = world
    await owner_goal(runtime, owner)
    with caplog.at_level("WARNING"):
        for _ in range(MAX_LINE_ATTEMPTS - 1):
            await reporter(runtime, Sender("failed")).sweep()
        await reporter(runtime, Sender()).sweep()
    assert not [r for r in caplog.records if "given up" in r.getMessage()]
