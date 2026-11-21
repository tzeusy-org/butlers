"""Outcome shaping under the owner's opt-in (bu-s11n0s.15 S3-S5, REQ-proactive-insight-engine-037).

Unit tests pin the decision rules over mocked pools: what a verdict does, the urgent band and
break-through category staying unshaped, a stale verdict lapsing, a ``keep`` reply restoring a
class, and the once-per-30-days notice. The DB-backed class drives the real ``delivery_cycle``
and engagement proxy against the real core_353 and core_354 tables.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.insight_premise import probe_premise
from butlers.tools.switchboard.insight import broker, shaping

_docker_available = shutil.which("docker") is not None
_NOW = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)


def _verdict(verdict: str = "self_resolving", band: str = "normal", **over) -> shaping.ClassVerdict:
    fields = {
        "origin_butler": "finance",
        "category": "bill-due",
        "band": band,
        "verdict": verdict,
        "n_treated": 6,
        "n_holdout": 5,
        "treated_resolved": 5,
        "holdout_resolved": 5,
        "computed_at": _NOW - timedelta(hours=6),
        **over,
    }
    return shaping.ClassVerdict(**fields)


def _candidate(priority: int = 70, category: str = "bill-due") -> dict:
    return {
        "id": uuid.uuid4(),
        "origin_butler": "finance",
        "category": category,
        "priority": priority,
    }


def _row(v: shaping.ClassVerdict) -> dict:
    return {
        "origin_butler": v.origin_butler,
        "category": v.category,
        "priority_band": v.band,
        "verdict": v.verdict,
        "n_treated": v.n_treated,
        "n_holdout": v.n_holdout,
        "treated_resolved": v.treated_resolved,
        "holdout_resolved": v.holdout_resolved,
        "computed_at": v.computed_at,
    }


def _pool(verdict_rows: list[dict], feedback_rows: list[dict] | None = None) -> MagicMock:
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=[verdict_rows, feedback_rows or []])
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


# --------------------------------------------------------------------------- the setting


async def test_the_setting_is_observe_unless_the_owner_set_shape():
    pool = MagicMock()
    for stored, expected in (("shape", "shape"), ("observe", "observe"), (None, "observe")):
        pool.fetchval = AsyncMock(return_value=stored)
        assert await shaping.shaping_mode(pool) == expected
    pool.fetchval = AsyncMock(side_effect=RuntimeError("column does not exist"))
    assert await shaping.shaping_mode(pool) == "observe"


def test_no_repository_code_writes_the_owner_setting():
    from pathlib import Path

    root = Path(shaping.__file__).parent
    for source in root.glob("*.py"):
        text = source.read_text()
        assert "SET outcome_shaping" not in text, source.name
        assert "outcome_shaping =" not in text.replace("outcome_shaping = 'shape'", ""), source.name


# --------------------------------------------------------------------------- what a verdict does


def test_a_self_resolving_class_is_digest_only_at_half_weight_citing_counts():
    shaped = shaping.decide(_verdict("self_resolving"), {"since": _NOW.isoformat()}, None)
    assert shaped is not None
    assert (shaped.weight, shaped.digest_only) == (0.5, True)
    assert "5 of 5 resolved without my telling you" in shaped.reason
    assert "5 of 6 when I did" in shaped.reason


def test_an_effective_class_overrides_the_click_proxy_with_full_weight():
    shaped = shaping.decide(_verdict("effective"), {}, None)
    assert shaped is not None
    assert (shaped.weight, shaped.digest_only) == (1.0, False)


@pytest.mark.parametrize("verdict", ["ignored", "unmeasured"])
def test_ignored_and_unmeasured_classes_are_left_to_the_click_proxy(verdict):
    assert shaping.decide(_verdict(verdict), {}, None) is None


def test_the_urgent_band_and_break_through_are_never_shaped():
    assert shaping.decide(_verdict("self_resolving", band="urgent"), {}, None) is None
    assert shaping.decide(_verdict("self_resolving", category="break_through"), {}, None) is None


def test_a_keep_reply_after_the_demotion_began_restores_the_class():
    began = _NOW - timedelta(days=2)
    state = {"since": began.isoformat()}
    before = began - timedelta(hours=1)
    assert shaping.decide(_verdict(), state, before) is not None
    assert shaping.decide(_verdict(), state, began + timedelta(hours=1)) is None


def test_a_changed_verdict_restarts_since_and_an_unchanged_one_keeps_it():
    first = shaping.next_state(None, "self_resolving", _NOW)
    assert first["since"] == _NOW.isoformat()
    later = _NOW + timedelta(days=3)
    assert shaping.next_state(first, "self_resolving", later) == first
    flipped = shaping.next_state(first, "effective", later)
    assert (flipped["verdict"], flipped["since"]) == ("effective", later.isoformat())


# --------------------------------------------------------------------------- the plan


async def test_the_plan_demotes_only_non_urgent_candidates_of_a_fresh_verdict():
    normal, urgent = _candidate(70), _candidate(95)
    pool = _pool([_row(_verdict())])
    plan = await shaping.plan_shaping(pool, [normal, urgent], {}, now=_NOW)
    assert list(plan.by_candidate) == [str(normal["id"])]
    # The urgent candidate's class is not even looked up.
    pool.fetch.assert_awaited()
    assert "<> 'urgent'" in pool.fetch.await_args_list[0].args[0]
    # The verdict change is persisted as state, the only write.
    assert pool.execute.await_count == 1


async def test_a_stale_verdict_shapes_nothing():
    stale = _verdict(computed_at=_NOW - shaping.VERDICT_FRESH_FOR - timedelta(seconds=1))
    plan = await shaping.plan_shaping(_pool([_row(stale)]), [_candidate()], {}, now=_NOW)
    assert plan.by_candidate == {}


async def test_an_unreadable_efficacy_table_leaves_delivery_alone():
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("relation does not exist"))
    plan = await shaping.plan_shaping(pool, [_candidate()], {}, now=_NOW)
    assert plan.by_candidate == {} and plan.states == {}


async def test_a_keep_reply_in_the_feedback_ledger_restores_the_class():
    v = _verdict()
    state = {"finance|bill-due|normal": {"verdict": "self_resolving", "since": _NOW.isoformat()}}
    feedback = [
        {
            "origin_butler": "finance",
            "category": "bill-due",
            "last_useful": _NOW + timedelta(hours=1),
            "last_any": _NOW + timedelta(hours=1),
        }
    ]
    plan = await shaping.plan_shaping(
        _pool([_row(v)], feedback), [_candidate()], {"shaping_state": state}, now=_NOW
    )
    assert plan.by_candidate == {}


async def test_class_state_is_written_as_text_so_it_survives_any_jsonb_codec():
    # The Switchboard pool may carry a json codec; a pre-encoded string bound to a bare ::jsonb
    # would then be stored as a JSON string and read back as no state at all.
    pool = MagicMock()
    pool.execute = AsyncMock()
    await shaping._store_state(pool, "k", {"verdict": "ignored"})
    sql = pool.execute.await_args.args[0]
    assert "$2::text::jsonb" in sql
    assert isinstance(pool.execute.await_args.args[2], str)


# --------------------------------------------------------------------------- the notice


async def _plan_for(verdict: shaping.ClassVerdict, settings: dict, feedback=None):
    candidate = _candidate()
    plan = await shaping.plan_shaping(
        _pool([_row(verdict)], feedback), [candidate], settings, now=_NOW
    )
    return plan, candidate


async def test_a_demotion_is_spoken_once_with_counts_and_a_door_candidate():
    plan, candidate = await _plan_for(_verdict(), {})
    (notice,) = shaping.build_notices(plan, [candidate], now=_NOW)
    assert notice.insight_id == str(candidate["id"])
    assert "5 of 5 resolved without my telling you" in notice.text
    assert "quiet" in notice.text and "keep" in notice.text.lower()


async def test_a_class_already_told_about_is_not_told_again():
    told = {
        "verdict": "self_resolving",
        "since": _NOW.isoformat(),
        "noticed_verdict": "self_resolving",
        "noticed_at": _NOW.isoformat(),
    }
    plan, candidate = await _plan_for(
        _verdict(), {"shaping_state": {"finance|bill-due|normal": told}}
    )
    assert shaping.build_notices(plan, [candidate], now=_NOW + timedelta(days=1)) == []


async def test_a_flapping_class_is_spoken_at_most_once_in_30_days():
    state = {
        "verdict": "ignored",
        "since": (_NOW - timedelta(days=1)).isoformat(),
        "noticed_verdict": "self_resolving",
        "noticed_at": (_NOW - timedelta(days=29)).isoformat(),
    }
    plan, candidate = await _plan_for(
        _verdict("ignored"), {"shaping_state": {"finance|bill-due|normal": state}}
    )
    assert shaping.build_notices(plan, [candidate], now=_NOW) == []
    later = _NOW + timedelta(days=2)
    plan.verdicts = {k: _verdict("ignored", computed_at=later) for k in plan.verdicts}
    assert len(shaping.build_notices(plan, [candidate], now=later)) == 1


async def test_an_ignored_class_is_spoken_only_when_the_owner_gave_no_feedback():
    plan, candidate = await _plan_for(_verdict("ignored"), {})
    (notice,) = shaping.build_notices(plan, [candidate], now=_NOW)
    assert "have not moved anything" in notice.text
    heard = [
        {
            "origin_butler": "finance",
            "category": "bill-due",
            "last_useful": None,
            "last_any": _NOW - timedelta(days=3),
        }
    ]
    plan, candidate = await _plan_for(_verdict("ignored"), {}, heard)
    assert shaping.build_notices(plan, [candidate], now=_NOW) == []


async def test_a_restored_class_is_not_announced_as_demoted():
    began = _NOW - timedelta(days=1)
    state = {"finance|bill-due|normal": {"verdict": "self_resolving", "since": began.isoformat()}}
    feedback = [
        {"origin_butler": "finance", "category": "bill-due", "last_useful": _NOW, "last_any": _NOW}
    ]
    plan, candidate = await _plan_for(_verdict(), {"shaping_state": state}, feedback)
    assert shaping.build_notices(plan, [candidate], now=_NOW) == []


async def test_a_class_that_is_not_delivered_is_not_announced():
    plan, _candidate_in_plan = await _plan_for(_verdict(), {})
    assert shaping.build_notices(plan, [_candidate()], now=_NOW) == []


# --------------------------------------------------------------------------- the proxy (S5)


def _planned(verdicts: list[shaping.ClassVerdict]) -> shaping.ShapingPlan:
    plan = shaping.ShapingPlan()
    plan.verdicts = {v.key: v for v in verdicts}
    return plan


def test_a_delivery_is_proxy_tracked_unless_its_own_band_is_measured():
    plan = _planned([_verdict("ignored", band="normal"), _verdict("unmeasured", band="low")])
    assert plan.proxy_retired(_candidate(70))  # measured, same band: retired
    assert not plan.proxy_retired(_candidate(95))  # urgent band: never shaped, proxy stays
    assert not plan.proxy_retired(_candidate(20))  # low band is unmeasured: proxy stays
    assert not plan.proxy_retired(_candidate(70, category="other"))
    assert not shaping.ShapingPlan().proxy_retired(_candidate(70))  # observe: empty plan


# --------------------------------------------------------------------------- real seams

_INSERT_PENDING_BILL = (
    "INSERT INTO finance.bills (id, payee, amount, currency, due_date, frequency, status) "
    "VALUES ($1, 'Water', 42.00, 'SGD', CURRENT_DATE + 1, 'one_time', 'pending')"
)
_SENT = {
    "notification_id": "n-1",
    "status": "sent",
    "result": {"ok": True, "result": {"message_id": 77, "chat": {"id": 123}}},
}


@pytest.fixture
async def shaped_pool(db):
    from butlers.core.owner_conditions import create_owner_conditions_table

    pool = await db("core", "finance", schemas={"finance": "finance"})
    await broker.create_insight_tables(pool)
    await create_owner_conditions_table(pool)
    await pool.execute(
        "INSERT INTO insight_settings (id, verbosity, custom_budget) VALUES (1, 'minimal', 1) "
        "ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal', custom_budget = 1, "
        "outcome_shaping = 'observe', shaping_state = '{}'::jsonb"
    )
    # Quiet hours are not under test (bu-gfi2kd): clear them.
    await pool.execute(
        "UPDATE public.approvals_policy "
        "SET quiet_start_hour = NULL, quiet_end_hour = NULL WHERE id = 1"
    )
    return pool


async def _seed_verdict(pool, verdict: str, *, band: str = "normal", now: datetime) -> None:
    await pool.execute(
        """
        INSERT INTO public.insight_class_efficacy (
            origin_butler, category, priority_band, bucket_date, n_treated, n_holdout,
            treated_resolved, holdout_resolved, verdict, computed_at
        ) VALUES ('finance', 'bill-due', $1, $2, 6, 5, 5, 5, $3, $4)
        """,
        band,
        now.date(),
        verdict,
        now,
    )


async def _propose_bill(pool, *, key: str, priority: int, now: datetime) -> str:
    bill_id = str(uuid.uuid4())
    await pool.execute(_INSERT_PENDING_BILL, bill_id)
    dedup_key = f"finance:bill-due:{key}:2026-11-22"
    result = await broker.propose_insight_candidate(
        pool,
        origin_butler="finance",
        priority=priority,
        category="bill-due",
        dedup_key=dedup_key,
        message=f"Bill {key} due tomorrow",
        expires_at=now + timedelta(days=2),
        premise=probe_premise("finance", "bill_still_pending", {"bill_id": bill_id}),
        now=now,
    )
    assert result["status"] == "accepted"
    return await _id_of(pool, dedup_key)


async def _id_of(pool, dedup_key: str) -> str:
    return str(
        await pool.fetchval("SELECT id FROM insight_candidates WHERE dedup_key = $1", dedup_key)
    )


async def _propose_sleep(pool, *, day: str, now: datetime) -> str:
    dedup_key = f"health:sleep:{day}"
    result = await broker.propose_insight_candidate(
        pool,
        origin_butler="health",
        priority=60,
        category="sleep",
        dedup_key=dedup_key,
        message=f"Sleep dipped {day}",
        expires_at=now + timedelta(days=2),
        now=now,
    )
    assert result["status"] == "accepted"
    return await _id_of(pool, dedup_key)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def _status(pool, candidate_id: str) -> str:
    return await pool.fetchval(
        "SELECT status FROM insight_candidates WHERE id = $1::uuid", candidate_id
    )


@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
class TestShapingOnRealTables:
    async def test_observe_leaves_a_self_resolving_class_standalone(self, shaped_pool):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await _seed_verdict(shaped_pool, "self_resolving", now=now)
        bill = await _propose_bill(shaped_pool, key="a", priority=70, now=now)
        notify = AsyncMock(return_value=_SENT)

        result = await broker.delivery_cycle(shaped_pool, notify_fn=notify, now=now)

        assert result["delivered"] == [bill]
        assert "5 of 5" not in result["category_budget_reasons"]["bill-due"]
        state = await shaped_pool.fetchval("SELECT shaping_state FROM insight_settings")
        assert _json(state) == {}

    async def test_shape_keeps_a_self_resolving_class_out_of_standalone_delivery(self, shaped_pool):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await shaped_pool.execute("UPDATE insight_settings SET outcome_shaping = 'shape'")
        await _seed_verdict(shaped_pool, "self_resolving", now=now)
        bill = await _propose_bill(shaped_pool, key="a", priority=70, now=now)
        notify = AsyncMock(return_value=_SENT)

        result = await broker.delivery_cycle(shaped_pool, notify_fn=notify, now=now)

        assert result["delivered"] == []
        notify.assert_not_awaited()
        assert await _status(shaped_pool, bill) == "pending"
        assert (
            "5 of 5 resolved without my telling you"
            in result["category_budget_reasons"]["bill-due"]
        )

    async def test_shape_folds_the_class_into_a_delivery_already_going_out(self, shaped_pool):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await shaped_pool.execute("UPDATE insight_settings SET outcome_shaping = 'shape'")
        await _seed_verdict(shaped_pool, "self_resolving", now=now)
        bill = await _propose_bill(shaped_pool, key="a", priority=70, now=now)
        other = await _propose_sleep(shaped_pool, day="2026-11-22", now=now)
        notify = AsyncMock(return_value=_SENT)

        result = await broker.delivery_cycle(shaped_pool, notify_fn=notify, now=now)

        assert sorted(result["delivered"]) == sorted([bill, other])
        ref = _json(
            await shaped_pool.fetchval(
                "SELECT delivery_ref FROM insight_candidates WHERE id = $1::uuid", bill
            )
        )
        assert ref["digest_line_index"] is not None  # a digest line, never standalone
        sent_text, metadata = notify.await_args.args
        assert "I stopped pushing bill-due reminders" in sent_text
        (door,) = metadata["shaping_notices"]
        assert door["insight_id"] == bill
        assert door["keep"].endswith("/useful") and door["quiet"].endswith("/mute")
        # Told once: the next cycle, with a fresh bill riding along, says nothing more.
        await _propose_bill(shaped_pool, key="b", priority=70, now=now)
        await _propose_sleep(shaped_pool, day="2026-11-23", now=now)
        notify.reset_mock()
        await shaped_pool.execute("DELETE FROM insight_cooldowns")
        await broker.delivery_cycle(shaped_pool, notify_fn=notify, now=now + timedelta(days=1))
        notify.assert_awaited_once()
        assert "I stopped pushing" not in notify.await_args.args[0]

    async def test_a_keep_reply_restores_the_class_standalone(self, shaped_pool):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await shaped_pool.execute("UPDATE insight_settings SET outcome_shaping = 'shape'")
        await _seed_verdict(shaped_pool, "self_resolving", now=now)
        first = await _propose_bill(shaped_pool, key="a", priority=70, now=now)
        await broker.delivery_cycle(shaped_pool, notify_fn=AsyncMock(return_value=_SENT), now=now)
        await broker.record_insight_feedback(
            shaped_pool,
            insight_id=first,
            verdict="useful",
            actor="owner",
            now=now + timedelta(minutes=1),
        )
        second = await _propose_bill(shaped_pool, key="b", priority=70, now=now)

        result = await broker.delivery_cycle(
            shaped_pool, notify_fn=AsyncMock(return_value=_SENT), now=now + timedelta(minutes=2)
        )

        # Standalone again: one slot, the earlier of two equal-weight bills.
        assert result["delivered"] == [first]
        assert await _status(shaped_pool, second) == "pending"

    async def test_the_urgent_band_is_never_shaped(self, shaped_pool):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await shaped_pool.execute("UPDATE insight_settings SET outcome_shaping = 'shape'")
        await _seed_verdict(shaped_pool, "self_resolving", band="urgent", now=now)
        bill = await _propose_bill(shaped_pool, key="a", priority=95, now=now)

        result = await broker.delivery_cycle(
            shaped_pool, notify_fn=AsyncMock(return_value=_SENT), now=now
        )

        assert result["delivered"] == [bill]

    async def test_riders_and_measured_classes_leave_no_proxy_row_but_urgent_ones_do(
        self, shaped_pool
    ):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await shaped_pool.execute("UPDATE insight_settings SET outcome_shaping = 'shape'")
        await _seed_verdict(shaped_pool, "self_resolving", now=now)
        bill = await _propose_bill(shaped_pool, key="a", priority=70, now=now)
        sleep = await _propose_sleep(shaped_pool, day="2026-11-22", now=now)
        await broker.delivery_cycle(shaped_pool, notify_fn=AsyncMock(return_value=_SENT), now=now)
        tracked = {
            str(row["insight_id"])
            for row in await shaped_pool.fetch("SELECT insight_id FROM insight_engagement")
        }
        # The rider (a measured class) has no proxy row, so the any-message mark, the category
        # weight and the 14-day disengagement rate never see it; the unmeasured class keeps one.
        assert tracked == {sleep}
        assert bill not in tracked

        urgent = await _propose_bill(shaped_pool, key="u", priority=95, now=now)
        await shaped_pool.execute("DELETE FROM insight_cooldowns")
        await broker.delivery_cycle(
            shaped_pool, notify_fn=AsyncMock(return_value=_SENT), now=now + timedelta(minutes=1)
        )
        row = await shaped_pool.fetchval(
            "SELECT count(*) FROM insight_engagement WHERE insight_id = $1::uuid", urgent
        )
        assert row == 1  # the measured class's urgent band is never shaped: the proxy stays

    async def test_riders_do_not_spend_the_daily_send_budget(self, shaped_pool):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await shaped_pool.execute("UPDATE insight_settings SET outcome_shaping = 'shape'")
        await _seed_verdict(shaped_pool, "self_resolving", now=now)
        for key in ("a", "b", "c"):
            await _propose_bill(shaped_pool, key=key, priority=70, now=now)
        await _propose_sleep(shaped_pool, day="2026-11-22", now=now)
        result = await broker.delivery_cycle(
            shaped_pool, notify_fn=AsyncMock(return_value=_SENT), now=now
        )
        assert len(result["delivered"]) == 4  # one standalone slot plus three riders
        assert await broker.sends_today(shaped_pool, now=now) == 1
