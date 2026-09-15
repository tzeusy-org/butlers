"""Unit tests for butlers.tools.switchboard.insight.catchup (bu-kqnum.3 slice 3).

Mocked-pool style mirroring tests/core/test_domain_event_wake.py and
tests/core/test_delegation_wake.py — exercises the deterministic-name
catch-up task reconciliation in isolation, with ``schedule_create``/
``schedule_update`` monkeypatched rather than a real Postgres pool.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from butlers.tools.switchboard.insight import catchup
from butlers.tools.switchboard.insight.catchup import TASK_NAME, reconcile_catchup_task

pytestmark = pytest.mark.unit


def _pool_with_existing_task(row: dict | None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


class TestOneShotCronEncoding:
    def test_cron_pins_the_exact_utc_minute_hour_day_month(self):
        deliver_at = datetime(2026, 8, 20, 7, 45, tzinfo=UTC)
        assert catchup._one_shot_cron(deliver_at) == "45 7 20 8 *"

    def test_cron_converts_a_non_utc_instant_to_utc_first(self):
        from zoneinfo import ZoneInfo

        # 23:15 Asia/Singapore (UTC+8) on 2026-08-20 is 15:15 UTC.
        deliver_at = datetime(2026, 8, 20, 23, 15, tzinfo=ZoneInfo("Asia/Singapore"))
        assert catchup._one_shot_cron(deliver_at) == "15 15 20 8 *"


class TestCreatesWhenNoExistingTask:
    async def test_creates_a_one_shot_job_task_for_deliver_at(self, monkeypatch):
        task_id = uuid.uuid4()
        pool = _pool_with_existing_task(None)
        schedule_create = AsyncMock(return_value=task_id)
        monkeypatch.setattr(catchup, "schedule_create", schedule_create)

        deliver_at = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
        result = await reconcile_catchup_task(pool, deliver_at=deliver_at, reason="quiet_hours")

        assert result == {"status": "ok", "state": "task_created", "task_id": str(task_id)}
        schedule_create.assert_awaited_once()
        args, kwargs = schedule_create.await_args
        assert args[1] == TASK_NAME
        assert args[2] == "0 8 20 8 *"
        assert kwargs["dispatch_mode"] == "job"
        assert kwargs["job_name"] == "insight_delivery_cycle"
        assert kwargs["job_args"] == {
            "deliver_at": deliver_at.isoformat(),
            "reason": "quiet_hours",
            "source": "insight_catchup",
        }
        assert kwargs["until_at"] > deliver_at

    async def test_race_on_create_reconciles_against_the_winner(self, monkeypatch):
        """A UniqueViolationError surfaces from schedule_create as ValueError
        (per butlers.core.scheduler's contract) when a concurrent suppressed
        cycle won the insert first; reconciliation re-reads rather than
        erroring the suppressed-skip path it's attached to."""
        winner_id = uuid.uuid4()
        deliver_at = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                None,
                # The winner raced in with the same computed suppression end,
                # so reconciliation finds it already current and never needs
                # a third fetchrow (schedule_update's own existence check).
                {
                    "id": winner_id,
                    "enabled": True,
                    "job_args": {"deliver_at": deliver_at.isoformat()},
                },
            ]
        )
        schedule_create = AsyncMock(side_effect=ValueError("Task name already exists"))
        monkeypatch.setattr(catchup, "schedule_create", schedule_create)

        result = await reconcile_catchup_task(pool, deliver_at=deliver_at, reason="quiet_hours")

        assert result == {"status": "ok", "state": "already_scheduled", "task_id": str(winner_id)}

    async def test_race_with_no_resolvable_winner_reports_error(self, monkeypatch):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[None, None])
        schedule_create = AsyncMock(side_effect=ValueError("Task name already exists"))
        monkeypatch.setattr(catchup, "schedule_create", schedule_create)

        result = await reconcile_catchup_task(
            pool, deliver_at=datetime(2026, 8, 20, 8, 0, tzinfo=UTC), reason="quiet_hours"
        )

        assert result == {"status": "error", "state": "race_unresolved"}


class TestReconciliationAgainstAnExistingTask:
    async def test_already_scheduled_for_the_same_target_is_a_no_op(self, monkeypatch):
        deliver_at = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
        existing_id = uuid.uuid4()
        pool = _pool_with_existing_task(
            {
                "id": existing_id,
                "enabled": True,
                "job_args": {"deliver_at": deliver_at.isoformat()},
            }
        )
        schedule_update = AsyncMock()
        monkeypatch.setattr(catchup, "schedule_update", schedule_update)

        result = await reconcile_catchup_task(pool, deliver_at=deliver_at, reason="quiet_hours")

        assert result == {
            "status": "ok",
            "state": "already_scheduled",
            "task_id": str(existing_id),
        }
        schedule_update.assert_not_awaited()

    async def test_a_materially_different_target_reschedules_in_place(self, monkeypatch):
        existing_id = uuid.uuid4()
        stale_target = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
        new_target = datetime(2026, 8, 20, 12, 30, tzinfo=UTC)
        pool = _pool_with_existing_task(
            {
                "id": existing_id,
                "enabled": True,
                "job_args": {"deliver_at": stale_target.isoformat()},
            }
        )
        schedule_update = AsyncMock()
        monkeypatch.setattr(catchup, "schedule_update", schedule_update)

        result = await reconcile_catchup_task(pool, deliver_at=new_target, reason="context_bus:dnd")

        assert result == {
            "status": "ok",
            "state": "task_rescheduled",
            "task_id": str(existing_id),
        }
        schedule_update.assert_awaited_once()
        args, kwargs = schedule_update.await_args
        assert args[1] == existing_id
        assert kwargs["cron"] == "30 12 20 8 *"
        assert kwargs["enabled"] is True
        assert kwargs["job_args"]["deliver_at"] == new_target.isoformat()
        assert kwargs["job_args"]["reason"] == "context_bus:dnd"

    async def test_a_disabled_already_fired_task_reschedules_and_re_enables(self, monkeypatch):
        """A prior catch-up already fired and auto-disabled (its until_at
        lapsed), but suppression is still in effect for a new reason —
        reconciliation must re-enable it rather than leaving it dormant."""
        existing_id = uuid.uuid4()
        fired_target = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
        new_target = datetime(2026, 8, 20, 14, 0, tzinfo=UTC)
        pool = _pool_with_existing_task(
            {
                "id": existing_id,
                "enabled": False,
                "job_args": {"deliver_at": fired_target.isoformat()},
            }
        )
        schedule_update = AsyncMock()
        monkeypatch.setattr(catchup, "schedule_update", schedule_update)

        result = await reconcile_catchup_task(pool, deliver_at=new_target, reason="quiet_hours")

        assert result["state"] == "task_rescheduled"
        schedule_update.assert_awaited_once()
        assert schedule_update.await_args.kwargs["enabled"] is True

    async def test_within_tolerance_target_drift_does_not_churn_the_row(self, monkeypatch):
        """The windowed cron re-runs delivery_cycle every 30 minutes; a
        recomputed target within the tolerance window of the stored one
        (e.g. sub-second/minute float noise) must not rewrite the row."""
        existing_id = uuid.uuid4()
        stored = datetime(2026, 8, 20, 8, 0, 0, tzinfo=UTC)
        recomputed = datetime(2026, 8, 20, 8, 0, 30, tzinfo=UTC)
        pool = _pool_with_existing_task(
            {
                "id": existing_id,
                "enabled": True,
                "job_args": {"deliver_at": stored.isoformat()},
            }
        )
        schedule_update = AsyncMock()
        monkeypatch.setattr(catchup, "schedule_update", schedule_update)

        result = await reconcile_catchup_task(pool, deliver_at=recomputed, reason="quiet_hours")

        assert result["state"] == "already_scheduled"
        schedule_update.assert_not_awaited()
