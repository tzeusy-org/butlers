"""The eligibility sweep also reconciles calendar Vouch/Block rules (bu-5ii4gj.18)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from butlers.roster_imports import roster_module

_job = roster_module("switchboard", "jobs/eligibility_sweep")
_RESULT = {"evaluated": 0, "skipped": 0, "transitioned": 0, "transitions": []}
_TARGET = "butlers.modules.calendar.organizer_decisions.reconcile_endpoint_rules"


async def test_the_sweep_runs_the_reconcile_on_the_same_pool() -> None:
    pool = object()
    with (
        patch.object(_job, "run_eligibility_sweep", AsyncMock(return_value=_RESULT)),
        patch(_TARGET, AsyncMock(return_value=0)) as reconcile,
    ):
        assert await _job.run_eligibility_sweep_job(pool) == _RESULT  # type: ignore[arg-type]
    reconcile.assert_awaited_once_with(pool)


async def test_a_failing_reconcile_never_fails_the_liveness_sweep() -> None:
    with (
        patch.object(_job, "run_eligibility_sweep", AsyncMock(return_value=_RESULT)),
        patch(_TARGET, AsyncMock(side_effect=RuntimeError("down"))),
    ):
        assert await _job.run_eligibility_sweep_job(object()) == _RESULT  # type: ignore[arg-type]
