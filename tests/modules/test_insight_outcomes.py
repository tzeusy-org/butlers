"""Insight outcome ledger, observe-only (bu-s11n0s.15 S1, REQ-proactive-insight-engine-037).

Unit tests pin the exposure taxonomy and the sweep's settle rules over a mocked pool. The
DB-backed class drives the real seams: ``delivery_cycle`` with a budget of one (so one candidate
is delivered and its sibling is a natural budget holdout), the real Finance bill probe, the real
owner-condition ledger and the core_353 tables.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.insight_premise import owner_condition_premise, probe_premise
from butlers.tools.switchboard.insight import broker, outcomes

_docker_available = shutil.which("docker") is not None
_NOW = datetime(2026, 11, 19, 6, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- exposure taxonomy


@pytest.mark.parametrize(
    ("status", "metadata", "delivery_ref", "custody", "expected"),
    [
        ("delivered", {}, {"digest_line_index": None}, False, "delivered"),
        ("delivered", {}, {"digest_line_index": 2}, False, "coalesced"),
        ("delivered", {}, None, True, "delivered"),  # a later attempt reached the owner
        ("withdrawn", {"blocked_by": "budget"}, None, False, "withdrawn"),
        ("expired", {}, None, False, "held_budget"),
        ("expired", {"blocked_by": "budget"}, None, False, "held_budget"),
        ("expired", {"blocked_by": "held_by"}, None, False, "held_context"),
        ("expired", {"blocked_by": "premise_unknown"}, None, False, "held_unverified"),
        ("expired", {"blocked_by": "budget"}, None, True, "held_custody"),
        ("filtered", {"blocked_by": "dedup"}, None, False, "shadowed"),
        ("filtered", {"blocked_by": "cooldown"}, None, False, "shadowed"),
        ("filtered", {"delivery_failure": True}, None, False, "failed"),
        ("filtered", {"delivery_failure": True}, None, True, "held_custody"),
        ("filtered", {"blocked_by": "budget"}, None, False, "held_context"),  # verbosity off
        ("filtered", {}, None, False, "held_context"),
    ],
)
def test_exposure_names_what_reached_the_owner(status, metadata, delivery_ref, custody, expected):
    exposure = outcomes.classify_exposure(status, metadata, delivery_ref, custody_refused=custody)
    assert exposure == expected
    assert exposure in outcomes.EXPOSURES


def test_the_arms_are_disjoint_and_custody_is_never_a_holdout():
    assert not outcomes.TREATED_EXPOSURES & outcomes.HOLDOUT_EXPOSURES
    assert not (outcomes.TREATED_EXPOSURES | outcomes.HOLDOUT_EXPOSURES) & (
        outcomes.EXCLUDED_EXPOSURES
    )
    assert {"held_custody", "shadowed"} <= outcomes.EXCLUDED_EXPOSURES


# --------------------------------------------------------------------------- sweep settle rules


def _sweep_pool(row: dict, reading) -> tuple[MagicMock, AsyncMock]:
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[row])
    conn.execute = AsyncMock(return_value="UPDATE 1")
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool, conn


def _open_row(*, window_end: datetime, unknown_checks: int = 0) -> dict:
    return {
        "candidate_id": uuid.uuid4(),
        "premise": '{"kind": "probe", "butler": "finance", "probe": "bill_still_pending"}',
        "proposed_at": window_end - outcomes.OBSERVATION_WINDOW,
        "window_end": window_end,
        "unknown_checks": unknown_checks,
    }


def _settled(conn: AsyncMock):
    """The (outcome, resolved_at, precision, reason) a settling UPDATE wrote, or None."""
    for call in conn.execute.await_args_list:
        if "SET outcome" in call.args[0]:
            return call.args[2:6]
    return None


@pytest.mark.parametrize(
    ("reading", "window_open", "unknown_checks", "expected"),
    [
        (("holds", None, None, None), True, 0, None),
        (("holds", None, None, None), False, 0, ("persisted", None, None, None)),
        (
            ("resolved", _NOW, "observed_by", "unspecified"),
            True,
            0,
            ("resolved", _NOW, "observed_by", "unspecified"),
        ),
        (("unknown", None, None, None), True, 0, None),
        (("unknown", None, None, None), True, 2, ("unmeasurable", None, None, None)),
        (("unknown", None, None, None), False, 0, ("unmeasurable", None, None, None)),
    ],
)
async def test_sweep_settles_once_and_never_calls_unknown_ineffective(
    monkeypatch, reading, window_open, unknown_checks, expected
):
    window_end = _NOW + timedelta(days=1) if window_open else _NOW - timedelta(seconds=1)
    pool, conn = _sweep_pool(
        _open_row(window_end=window_end, unknown_checks=unknown_checks), reading
    )
    monkeypatch.setattr(outcomes, "read_premise", AsyncMock(return_value=reading))

    counts = await outcomes.sweep_outcomes(pool, now=_NOW)

    assert _settled(conn) == expected
    assert counts["checked"] == 1
    # Every write is guarded so a settled row is never reopened.
    for call in conn.execute.await_args_list:
        if call.args[0].lstrip().startswith("UPDATE"):
            assert "outcome IS NULL" in call.args[0]


async def test_an_exact_resolution_after_the_window_counts_as_persisted(monkeypatch):
    window_end = _NOW - timedelta(days=1)
    pool, conn = _sweep_pool(_open_row(window_end=window_end), None)
    late = ("resolved", _NOW - timedelta(hours=1), "exact", "satisfied")
    monkeypatch.setattr(outcomes, "read_premise", AsyncMock(return_value=late))

    await outcomes.sweep_outcomes(pool, now=_NOW)

    assert _settled(conn) == ("persisted", None, None, None)


async def test_an_overlapping_sweep_does_nothing(monkeypatch):
    pool, conn = _sweep_pool(_open_row(window_end=_NOW), None)
    conn.fetchval = AsyncMock(return_value=False)
    read = AsyncMock()
    monkeypatch.setattr(outcomes, "read_premise", read)

    counts = await outcomes.sweep_outcomes(pool, now=_NOW)

    assert counts["skipped"] == "sweep_in_progress"
    read.assert_not_awaited()
    conn.fetch.assert_not_awaited()


async def test_a_premise_read_failure_is_unknown():
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    premise = owner_condition_premise("finance:bill-overdue", "fp")
    assert await outcomes.read_premise(pool, premise, _NOW, now=_NOW) == (
        "unknown",
        None,
        None,
        None,
    )


async def test_a_probe_that_stopped_holding_is_resolved_by_the_sweep_time(monkeypatch):
    monkeypatch.setattr(outcomes, "check_premise", AsyncMock(return_value=False))
    premise = probe_premise("finance", "bill_still_pending", {"bill_id": "x"})
    assert await outcomes.read_premise(MagicMock(), premise, _NOW, now=_NOW) == (
        "resolved",
        _NOW,
        "observed_by",
        "unspecified",
    )


# --------------------------------------------------------------------------- real seams


@pytest.fixture
async def outcome_pool(db):
    """Real core migrations (core_353 included) plus the real Finance bills table."""
    from butlers.core.owner_conditions import create_owner_conditions_table

    pool = await db("core", "finance", schemas={"finance": "finance"})
    await broker.create_insight_tables(pool)
    await create_owner_conditions_table(pool)
    await pool.execute(
        "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'minimal') "
        "ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'"
    )
    return pool


_INSERT_PENDING_BILL = (
    "INSERT INTO finance.bills (id, payee, amount, currency, due_date, frequency, status) "
    "VALUES ($1, 'Water', 42.00, 'SGD', CURRENT_DATE + 1, 'one_time', 'pending')"
)
_SENT = {
    "notification_id": "n-1",
    "status": "sent",
    "result": {"ok": True, "result": {"message_id": 77, "chat": {"id": 123}}},
}


async def _outcome_rows(pool) -> dict[str, dict]:
    rows = await pool.fetch(
        "SELECT o.*, c.message FROM public.insight_outcomes o "
        "JOIN public.insight_candidates c ON c.id = o.candidate_id"
    )
    return {row["message"]: dict(row) for row in rows}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestInsightOutcomeLedger:
    async def test_a_paid_bill_resolves_in_both_arms_and_a_rerun_changes_nothing(
        self, outcome_pool
    ):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        bills = {}
        for key, priority in (("a", 80), ("b", 70)):
            bill_id = str(uuid.uuid4())
            bills[key] = bill_id
            await outcome_pool.execute(_INSERT_PENDING_BILL, bill_id)
            await broker.propose_insight_candidate(
                outcome_pool,
                origin_butler="finance",
                priority=priority,
                category="bill-due",
                dedup_key=f"finance:bill-due:{key}:2026-11-20",
                message=f"Bill {key} due tomorrow",
                expires_at=now + timedelta(hours=2),
                premise=probe_premise("finance", "bill_still_pending", {"bill_id": bill_id}),
                now=now,
            )
        await outcome_pool.execute("UPDATE insight_settings SET custom_budget = 1 WHERE id = 1")
        await broker.delivery_cycle(outcome_pool, notify_fn=AsyncMock(return_value=_SENT), now=now)
        # The held sibling expires unsent: a natural budget holdout.
        await broker.expire_candidates(outcome_pool, now=now + timedelta(hours=3))

        later = now + timedelta(hours=4)  # live-clock: offsets the database now() created_at
        assert await outcomes.enroll_terminal_candidates(outcome_pool, now=later) == 2
        assert await outcomes.enroll_terminal_candidates(outcome_pool, now=later) == 0
        rows = await _outcome_rows(outcome_pool)
        assert rows["Bill a due tomorrow"]["exposure"] == "delivered"
        assert rows["Bill a due tomorrow"]["exposed_at"] is not None
        assert rows["Bill b due tomorrow"]["exposure"] == "held_budget"
        assert rows["Bill b due tomorrow"]["exposed_at"] is None

        # Still unpaid: nothing settles while the window is open.
        first = await outcomes.sweep_outcomes(outcome_pool, now=later)
        assert first["resolved"] == 0 and first["checked"] == 2

        for bill_id in bills.values():
            await outcome_pool.execute(
                "UPDATE finance.bills SET status = 'paid' WHERE id = $1", bill_id
            )
        paid = later + timedelta(days=1)
        await outcomes.sweep_outcomes(outcome_pool, now=paid)
        settled = await _outcome_rows(outcome_pool)
        for row in settled.values():
            assert row["outcome"] == "resolved"
            assert row["resolved_precision"] == "observed_by"
            assert row["resolved_at"] == paid
            assert row["resolution_reason"] == "unspecified"

        again = await outcomes.sweep_outcomes(outcome_pool, now=paid + timedelta(days=1))
        assert again["checked"] == 0
        assert await _outcome_rows(outcome_pool) == settled

    async def test_a_resolved_condition_copies_its_exact_time_and_reason(self, outcome_pool):
        from butlers.core.condition_ledger import Observation, compute_fingerprint
        from butlers.core.owner_conditions import reconcile_snapshot

        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        source = "health:baseline"
        fingerprint = compute_fingerprint(source, 1, {"metric": "rhr"})
        await reconcile_snapshot(
            outcome_pool,
            source=source,
            observations=[Observation(fingerprint=fingerprint, summary="Resting HR high")],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        await broker.propose_insight_candidate(
            outcome_pool,
            origin_butler="health",
            priority=60,
            category="baseline-check",
            dedup_key="health:baseline:rhr:2026-11-19",
            message="Resting HR above your range",
            expires_at=now + timedelta(hours=2),
            premise=owner_condition_premise(source, fingerprint),
            now=now,
        )
        await broker.delivery_cycle(outcome_pool, notify_fn=AsyncMock(return_value=_SENT), now=now)
        await reconcile_snapshot(
            outcome_pool,
            source=source,
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        await outcome_pool.execute(
            "UPDATE public.owner_conditions SET metadata = "
            "COALESCE(metadata, '{}'::jsonb) || '{\"resolution_reason\": \"satisfied\"}'::jsonb "
            "WHERE source = $1 AND fingerprint = $2",
            source,
            fingerprint,
        )
        resolved_at = await outcome_pool.fetchval(
            "SELECT resolved_at FROM public.owner_conditions WHERE source = $1", source
        )

        later = now + timedelta(hours=1)  # live-clock: offsets the database now() created_at
        await outcomes.run_insight_outcome_sweep(outcome_pool, now=later)

        (row,) = (await _outcome_rows(outcome_pool)).values()
        assert row["outcome"] == "resolved"
        assert row["resolved_precision"] == "exact"
        assert row["resolved_at"] == resolved_at
        assert row["resolution_reason"] == "satisfied"

    async def test_a_custody_refused_delivery_is_excluded_and_an_unreadable_probe_is_unmeasurable(
        self, outcome_pool
    ):
        now = datetime.now(UTC)  # live-clock: candidate created_at is the database now()
        await broker.propose_insight_candidate(
            outcome_pool,
            origin_butler="finance",
            priority=70,
            category="bill-due",
            dedup_key="finance:bill-due:held:2026-11-20",
            message="Held bill",
            expires_at=now + timedelta(hours=2),
            premise=probe_premise("finance", "bill_still_pending", {"bill_id": "not-a-uuid"}),
            now=now,
        )
        refused = {
            "status": "error",
            "error": "Delivery refused before provider start: an endpoint in this send is under "
            "an owner custody hold (endpoint_held).",
        }
        for attempt in range(3):
            await broker.delivery_cycle(
                outcome_pool,
                notify_fn=AsyncMock(return_value=refused),
                now=now + timedelta(minutes=attempt),
            )
        assert await outcome_pool.fetchval("SELECT status FROM insight_candidates") == "filtered"

        later = now + timedelta(hours=1)  # live-clock: offsets the database now() created_at
        await outcomes.enroll_terminal_candidates(outcome_pool, now=later)
        for day in range(outcomes.MAX_UNKNOWN_CHECKS):
            await outcomes.sweep_outcomes(outcome_pool, now=later + timedelta(days=day))

        (row,) = (await _outcome_rows(outcome_pool)).values()
        assert row["exposure"] == "held_custody"
        assert row["outcome"] == "unmeasurable"
        assert row["unknown_checks"] == outcomes.MAX_UNKNOWN_CHECKS

    async def test_only_switchboard_may_touch_the_ledger(self, outcome_pool):
        for table in ("insight_outcomes", "insight_class_efficacy"):
            row = await outcome_pool.fetchrow(
                "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                "has_table_privilege('butler_finance_rw', c.oid, 'SELECT') AS finance_reads, "
                "has_table_privilege('butler_switchboard_rw', c.oid, 'INSERT') AS sb_inserts "
                "FROM pg_class c WHERE c.oid = to_regclass($1)",
                f"public.{table}",
            )
            assert row["relrowsecurity"] is True
            assert row["relforcerowsecurity"] is False
            assert row["finance_reads"] is False
            assert row["sb_inserts"] is True
