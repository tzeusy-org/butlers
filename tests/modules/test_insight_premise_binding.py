"""Premise-bound proactive speech (bu-q7vx1q.5).

An insight names the fact it asserts. The broker re-checks that fact before
selection (withdrawing the candidate, unsent, when it is false), records where
the message landed, and quietly amends it in place when the fact later stops
being true.

Unit tests (no Docker) cover the premise contract, probe verdicts, the
amendment state machine and delivery-reference extraction. The DB-backed class
drives the real seams: ``delivery_cycle`` against a migrated-shape schema and
``reconcile_snapshot``'s ``post_write`` hook.
"""

from __future__ import annotations

import importlib.util
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.condition_ledger import ConditionTransition
from butlers.core.insight_premise import (
    enqueue_premise_amendments,
    normalize_premise,
    owner_condition_premise,
    probe_premise,
)
from butlers.tools.switchboard.insight import broker, premises

_docker_available = shutil.which("docker") is not None
_PINNED_NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _future(days: int = 7) -> datetime:
    return _PINNED_NOW + timedelta(days=days)


class TestPremiseContract:
    def test_null_premise_stays_null(self):
        assert normalize_premise(None) is None

    def test_probe_premise_round_trips_and_drops_unknown_keys(self):
        premise = normalize_premise(
            {"kind": "probe", "butler": "finance", "probe": "p", "args": {"a": 1}, "junk": 1}
        )
        assert premise == probe_premise("finance", "p", {"a": 1})
        assert premise == {"kind": "probe", "butler": "finance", "probe": "p", "args": {"a": 1}}

    @pytest.mark.parametrize(
        "bad",
        [
            {"kind": "nope"},
            {"kind": "owner_condition", "source": "s"},
            {"kind": "owner_condition", "source": "", "fingerprint": "f"},
            {"kind": "probe", "butler": "finance", "probe": "p", "args": []},
            "not-a-dict",
        ],
    )
    def test_malformed_premise_is_rejected(self, bad):
        with pytest.raises(ValueError):
            normalize_premise(bad)


@pytest.mark.asyncio(loop_scope="session")
class TestProposeValidation:
    async def test_propose_rejects_a_malformed_premise_before_any_write(self):
        pool = AsyncMock()
        result = await broker.propose_insight_candidate(
            pool,
            origin_butler="finance",
            priority=80,
            category="bill-due",
            dedup_key="finance:bill-due:b1:2026-08-21",
            message="Bill due tomorrow",
            expires_at=_future(),
            premise={"kind": "probe"},
            now=_PINNED_NOW,
        )
        assert result["status"] == "error"
        pool.execute.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="session")
class TestPremiseVerdicts:
    async def test_null_premise_holds(self):
        assert await premises.check_premise(AsyncMock(), None) is True

    @pytest.mark.parametrize(
        ("db_status", "expected"),
        [("pending", True), ("overdue", True), ("paid", False), (None, None)],
    )
    async def test_finance_bill_probe(self, db_status, expected):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None if db_status is None else {"status": db_status})
        premise = probe_premise("finance", "bill_still_pending", {"bill_id": "b1"})
        assert await premises.check_premise(pool, premise) is expected

    async def test_unregistered_probe_and_probe_errors_are_unknown_not_false(self):
        pool = AsyncMock()
        assert await premises.check_premise(pool, probe_premise("nobody", "nothing")) is None
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))
        premise = probe_premise("finance", "bill_still_pending", {"bill_id": "b1"})
        assert await premises.check_premise(pool, premise) is None

    @pytest.mark.parametrize(
        ("rows", "expected"),
        [([{"state": "open"}], True), ([{"state": "resolved"}], False), ([], None)],
    )
    async def test_owner_condition_premise(self, rows, expected):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        assert await premises.check_premise(pool, owner_condition_premise("s", "f")) is expected


class TestDeliveryRef:
    _TELEGRAM = {"ok": True, "result": {"message_id": 77, "chat": {"id": 123}}}

    @pytest.mark.parametrize(
        "result",
        [
            _TELEGRAM,
            {"structuredContent": _TELEGRAM},
            {
                "content": [
                    {
                        "type": "text",
                        "text": '{"ok": true, "result": {"message_id": 77, "chat": {"id": 123}}}',
                    }
                ]
            },
        ],
        ids=["bare", "wrapped", "mcp-text"],
    )
    def test_provider_message_found_however_the_route_wrapped_it(self, result):
        ref = broker._build_delivery_ref(
            {"notification_id": "n1", "status": "sent", "result": result},
            channel=None,
            standalone=True,
            digest_line_index=None,
            delivered_at=_PINNED_NOW,
        )
        assert (ref["channel"], ref["chat_id"], ref["provider_message_id"]) == (
            "telegram",
            "123",
            77,
        )
        assert ref["notification_id"] == "n1"

    def test_a_result_without_a_chat_leaves_no_editable_reference(self):
        ref = broker._build_delivery_ref(
            {"status": "sent", "result": {"message_id": 5}},
            channel="email",
            standalone=True,
            digest_line_index=None,
            delivered_at=_PINNED_NOW,
        )
        assert "provider_message_id" not in ref
        assert ref["channel"] == "email"

    def test_unrechecked_candidate_is_stamped_with_its_proposal_time(self):
        stamped = broker._stamp_unverified(
            {"message": "Bill due tomorrow", "created_at": datetime(2026, 8, 19, 7, 5, tzinfo=UTC)}
        )
        assert stamped["message"] == "Bill due tomorrow (as of 2026-08-19 07:05 UTC)"


@pytest.mark.asyncio(loop_scope="session")
class TestEnqueueHook:
    @staticmethod
    def _transition(kind, resolved_at=None):
        return ConditionTransition(
            condition_id=MagicMock(),
            source="finance:bill-overdue",
            fingerprint="fp",
            episode=1,
            state="resolved" if kind == "resolved" else "open",
            transition=kind,
            escalation_level="L0",
            next_reescalate_at=None,
            resolved_at=resolved_at,
        )

    @staticmethod
    def _conn(fetchval):
        conn = MagicMock()
        conn.fetchval = fetchval
        conn.transaction = MagicMock(
            return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
        )
        return conn

    async def test_only_resolved_transitions_enqueue(self):
        conn = self._conn(AsyncMock(return_value=1))
        queued = await enqueue_premise_amendments(
            conn,
            [self._transition("opened"), self._transition("resolved", _PINNED_NOW)],
        )
        assert queued == 1
        conn.fetchval.assert_awaited_once()
        assert conn.fetchval.await_args.args[1:4] == ("finance:bill-overdue", "fp", _PINNED_NOW)

    async def test_a_failed_enqueue_never_fails_the_reconcile_it_rides(self):
        conn = self._conn(AsyncMock(side_effect=RuntimeError("definer missing")))
        assert (
            await enqueue_premise_amendments(conn, [self._transition("resolved", _PINNED_NOW)]) == 0
        )


@pytest.mark.asyncio(loop_scope="session")
class TestApplyPendingAmendments:
    @staticmethod
    def _row(**overrides):
        ref = {
            "standalone": True,
            "channel": "telegram",
            "chat_id": "123",
            "provider_message_id": 77,
        }
        row = {
            "id": "a1",
            "attempts": 0,
            "candidate_id": "c1",
            "message": "Bill due tomorrow",
            "origin_butler": "finance",
            "priority": 90,
            "dedup_key": "finance:bill-due:b1:2026-08-21",
            "premise": None,
            "delivery_ref": ref,
        }
        row.update(overrides)
        return row

    @staticmethod
    def _pool(rows):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=rows)
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock(return_value="UPDATE 1")
        return pool

    async def _apply(self, rows, amend_fn):
        pool = self._pool(rows)
        with patch.object(broker, "record_attention_event", AsyncMock()) as ledger:
            counts = await broker.apply_pending_amendments(pool, amend_fn, now=_PINNED_NOW)
        return counts, pool, ledger

    @staticmethod
    def _final_state(pool):
        return pool.execute.await_args.args[2]

    async def test_a_standalone_telegram_message_is_struck_through_in_place(self):
        amend = AsyncMock(return_value={"status": "edited"})
        counts, pool, ledger = await self._apply([self._row()], amend)
        assert counts == {"applied": 1, "folded": 0, "retry": 0}
        ref, text = amend.await_args.args
        assert ref["provider_message_id"] == 77
        assert text == "~~Bill due tomorrow~~\nResolved"
        assert self._final_state(pool) == "applied"
        assert ledger.await_args.kwargs["outcome"] == "amended"

    async def test_a_rejected_edit_folds_instead_of_pinging(self):
        counts, pool, ledger = await self._apply(
            [self._row()], AsyncMock(return_value={"status": "rejected"})
        )
        assert counts["folded"] == 1
        assert self._final_state(pool) == "fold"
        ledger.assert_not_awaited()

    @pytest.mark.parametrize(
        "ref",
        [
            {"standalone": False, "channel": "telegram", "chat_id": "1", "provider_message_id": 2},
            {"standalone": True, "channel": "email"},
        ],
        ids=["digest-line", "email"],
    )
    async def test_an_uneditable_delivery_folds_without_touching_the_transport(self, ref):
        amend = AsyncMock()
        counts, pool, _ = await self._apply([self._row(delivery_ref=ref)], amend)
        assert counts["folded"] == 1
        amend.assert_not_awaited()

    async def test_transport_errors_retry_then_fold_after_three_attempts(self):
        amend = AsyncMock(side_effect=RuntimeError("network"))
        counts, pool, _ = await self._apply([self._row(attempts=1)], amend)
        assert counts == {"applied": 0, "folded": 0, "retry": 1}
        counts, pool, _ = await self._apply([self._row(attempts=2)], amend)
        assert counts["folded"] == 1

    async def test_a_replayed_amendment_already_settled_is_not_double_counted(self):
        pool = self._pool([self._row()])
        pool.execute = AsyncMock(return_value="UPDATE 0")
        with patch.object(broker, "record_attention_event", AsyncMock()) as ledger:
            counts = await broker.apply_pending_amendments(
                pool, AsyncMock(return_value={"status": "edited"}), now=_PINNED_NOW
            )
        assert counts["applied"] == 0
        ledger.assert_not_awaited()


def _migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_255_insight_premise_binding.py"
    )
    spec = importlib.util.spec_from_file_location("core_255_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def premise_pool(provisioned_postgres_pool):
    """Insight tables + owner_conditions + the finance bill lookup, as migrated."""
    from butlers.core.owner_conditions import create_owner_conditions_table

    migration = _migration()
    async with provisioned_postgres_pool() as pool:
        await broker.create_insight_tables(pool)
        await create_owner_conditions_table(pool)
        await pool.execute("CREATE SCHEMA IF NOT EXISTS finance")
        await pool.execute("CREATE TABLE finance.bills (id uuid PRIMARY KEY, status text NOT NULL)")
        await pool.execute(migration.CREATE_BILL_STATUS_FN)
        await pool.execute(migration.CREATE_ENQUEUE_FN)
        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'minimal') "
            "ON CONFLICT (id) DO UPDATE SET verbosity = 'minimal'"
        )
        yield pool


_TELEGRAM_SENT = {
    "notification_id": "n-1",
    "status": "sent",
    "result": {"ok": True, "result": {"message_id": 77, "chat": {"id": 123}}},
}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not _docker_available, reason="Docker not available")
@pytest.mark.integration
class TestPremiseBoundDelivery:
    async def test_a_bill_paid_before_the_cycle_is_withdrawn_and_never_sent(self, premise_pool):
        bill_id = "8c1b3a52-6a6e-4a7e-9a52-0d5f6f1d9c01"
        await premise_pool.execute(
            "INSERT INTO finance.bills (id, status) VALUES ($1, 'pending')", bill_id
        )
        proposed = await broker.propose_insight_candidate(
            premise_pool,
            origin_butler="finance",
            priority=92,
            category="bill-due",
            dedup_key=f"finance:bill-due:{bill_id}:2026-08-21",
            message="Bill due tomorrow: Water",
            expires_at=_future(),
            premise=probe_premise("finance", "bill_still_pending", {"bill_id": bill_id}),
            now=_PINNED_NOW,
        )
        assert proposed["status"] == "accepted"
        await premise_pool.execute(
            "UPDATE finance.bills SET status = 'paid' WHERE id = $1", bill_id
        )

        notify = AsyncMock(return_value=_TELEGRAM_SENT)
        result = await broker.delivery_cycle(premise_pool, notify_fn=notify, now=_PINNED_NOW)

        notify.assert_not_awaited()
        assert result["withdrawn"] == 1
        assert result["delivered"] == []
        row = await premise_pool.fetchrow("SELECT status, metadata FROM insight_candidates")
        assert row["status"] == "withdrawn"

    async def test_a_still_unpaid_bill_is_delivered_and_an_unknown_one_is_stamped(
        self, premise_pool
    ):
        bill_id = "8c1b3a52-6a6e-4a7e-9a52-0d5f6f1d9c02"
        await premise_pool.execute(
            "INSERT INTO finance.bills (id, status) VALUES ($1, 'pending')", bill_id
        )
        for key, args in (("a", {"bill_id": bill_id}), ("b", {"bill_id": "not-a-uuid"})):
            await broker.propose_insight_candidate(
                premise_pool,
                origin_butler="finance",
                priority=80 if key == "a" else 70,
                category="bill-due",
                dedup_key=f"finance:bill-due:{key}:2026-08-21",
                message=f"Bill {key} due tomorrow",
                expires_at=_future(),
                premise=probe_premise("finance", "bill_still_pending", args),
                now=_PINNED_NOW,
            )
        await premise_pool.execute("UPDATE insight_settings SET custom_budget = 1 WHERE id = 1")
        notify = AsyncMock(return_value=_TELEGRAM_SENT)
        await broker.delivery_cycle(premise_pool, notify_fn=notify, now=_PINNED_NOW)
        assert "Bill a due tomorrow" in notify.await_args.args[0]
        assert "(as of" not in notify.await_args.args[0]

        await premise_pool.execute("UPDATE insight_settings SET custom_budget = 3 WHERE id = 1")
        await premise_pool.execute("TRUNCATE insight_cooldowns")
        notify.reset_mock()
        await broker.delivery_cycle(
            premise_pool, notify_fn=notify, now=_PINNED_NOW + timedelta(minutes=1)
        )
        sent = notify.await_args.args[0]
        assert "Bill b due tomorrow (as of" in sent

    async def test_a_resolved_condition_amends_the_delivered_message_exactly_once(
        self, premise_pool
    ):
        from butlers.core.condition_ledger import Observation, compute_fingerprint
        from butlers.core.owner_conditions import reconcile_snapshot

        source = "finance:bill-overdue"
        fingerprint = compute_fingerprint(source, 1, {"bill_id": "b1"})
        obs = Observation(fingerprint=fingerprint, summary="Water overdue")
        await reconcile_snapshot(
            premise_pool,
            source=source,
            observations=[obs],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        await broker.propose_insight_candidate(
            premise_pool,
            origin_butler="finance",
            priority=92,
            category="bill-overdue",
            dedup_key="finance:bill-overdue:b1:2026-08-21",
            message="Water bill is overdue",
            expires_at=_future(),
            premise=owner_condition_premise(source, fingerprint),
            now=_PINNED_NOW,
        )
        notify = AsyncMock(return_value=_TELEGRAM_SENT)
        await broker.delivery_cycle(premise_pool, notify_fn=notify, now=_PINNED_NOW)
        ref = broker._json_object(
            await premise_pool.fetchval("SELECT delivery_ref FROM insight_candidates")
        )
        assert (ref["chat_id"], ref["provider_message_id"], ref["standalone"]) == ("123", 77, True)

        # The condition resolves; a second reconcile of the same state is a no-op.
        for _ in range(2):
            await reconcile_snapshot(
                premise_pool,
                source=source,
                observations=[],
                snapshot_complete=True,
                initial_grace_seconds=3600,
            )
        assert await premise_pool.fetchval("SELECT count(*) FROM insight_amendments") == 1

        amend = AsyncMock(return_value={"status": "edited"})
        later = _PINNED_NOW + timedelta(minutes=5)
        first = await broker.delivery_cycle(
            premise_pool, notify_fn=notify, amend_fn=amend, now=later
        )
        second = await broker.delivery_cycle(
            premise_pool, notify_fn=notify, amend_fn=amend, now=later + timedelta(minutes=5)
        )

        assert (first["amended"], second["amended"]) == (1, 0)
        amend.assert_awaited_once()
        assert amend.await_args.args[0]["provider_message_id"] == 77
        assert amend.await_args.args[1].startswith("~~Water bill is overdue~~\nResolved")
        assert await premise_pool.fetchval("SELECT state FROM insight_amendments") == "applied"

    async def test_an_uneditable_amendment_rides_the_next_delivery_as_a_digest_line(
        self, premise_pool
    ):
        from butlers.core.condition_ledger import Observation, compute_fingerprint
        from butlers.core.owner_conditions import reconcile_snapshot

        source = "finance:bill-overdue"
        fingerprint = compute_fingerprint(source, 1, {"bill_id": "b2"})
        await reconcile_snapshot(
            premise_pool,
            source=source,
            observations=[Observation(fingerprint=fingerprint, summary="Rent overdue")],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
        await broker.propose_insight_candidate(
            premise_pool,
            origin_butler="finance",
            priority=92,
            category="bill-overdue",
            dedup_key="finance:bill-overdue:b2:2026-08-21",
            message="Rent is overdue",
            expires_at=_future(),
            premise=owner_condition_premise(source, fingerprint),
            now=_PINNED_NOW,
        )
        notify = AsyncMock(return_value=_TELEGRAM_SENT)
        await broker.delivery_cycle(premise_pool, notify_fn=notify, now=_PINNED_NOW)
        await reconcile_snapshot(
            premise_pool,
            source=source,
            observations=[],
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )

        rejected = AsyncMock(return_value={"status": "rejected"})
        notify.reset_mock()
        await broker.delivery_cycle(
            premise_pool, notify_fn=notify, amend_fn=rejected, now=_PINNED_NOW + timedelta(hours=1)
        )
        notify.assert_not_awaited()  # a correction is never its own ping
        assert await premise_pool.fetchval("SELECT state FROM insight_amendments") == "fold"

        await broker.propose_insight_candidate(
            premise_pool,
            origin_butler="health",
            priority=80,
            category="refill",
            dedup_key="health:refill:r1:2026-08-22",
            message="Refill due",
            expires_at=_future(),
            now=_PINNED_NOW,
        )
        await broker.delivery_cycle(
            premise_pool, notify_fn=notify, now=_PINNED_NOW + timedelta(hours=2)
        )
        sent = notify.await_args.args[0]
        assert "Refill due" in sent
        assert "Since last digest: Resolved" in sent
        assert await premise_pool.fetchval("SELECT state FROM insight_amendments") == "folded"
