"""Tests for the rule-promotion trigger scheduled-job wrapper (bu-wuwy9).

Covers: butler.toml schedule registration, deterministic job registry
wiring, and job_args validation/overrides. The scan/gate/classifier logic
itself is unit-tested in
``roster/switchboard/tests/test_rule_promotion_trigger.py``; the end-to-end
DB path is covered in
``tests/integration/test_switchboard_rule_promotion_trigger_job.py``.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_butler_toml_has_rule_promotion_trigger_schedule():
    import tomllib
    from pathlib import Path

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    entry = next((s for s in schedules if s["name"] == "rule-promotion-trigger"), None)
    assert entry is not None
    assert entry["dispatch_mode"] == "job"
    assert entry["job_name"] == "rule_promotion_trigger"


def test_rule_promotion_trigger_registered_in_switchboard_job_registry():
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    registry = get_deterministic_schedule_job_registry()
    assert "rule_promotion_trigger" in registry.get("switchboard", {})


class TestJobArgsValidation:
    async def test_no_job_args_uses_defaults(self):
        from butlers.tools.switchboard.routing.rule_promotion import (
            DEFAULT_LOOKBACK_WINDOW,
            DEFAULT_MIN_DISTINCT_DAYS,
            DEFAULT_MIN_ELAPSED_FLOOR,
            DEFAULT_PROMOTION_THRESHOLD,
        )
        from roster.switchboard.jobs.rule_promotion_trigger import (
            run_rule_promotion_trigger_job,
        )

        with patch(
            "roster.switchboard.jobs.rule_promotion_trigger.run_rule_promotion_trigger",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = {"candidates_scanned": 0}
            await run_rule_promotion_trigger_job(db_pool=object(), job_args=None)

        _, kwargs = mock_run.call_args
        assert kwargs["threshold"] == DEFAULT_PROMOTION_THRESHOLD
        assert kwargs["min_distinct_days"] == DEFAULT_MIN_DISTINCT_DAYS
        assert kwargs["min_elapsed"] == DEFAULT_MIN_ELAPSED_FLOOR
        assert kwargs["lookback"] == DEFAULT_LOOKBACK_WINDOW

    async def test_job_args_override_defaults(self):
        from roster.switchboard.jobs.rule_promotion_trigger import (
            run_rule_promotion_trigger_job,
        )

        with patch(
            "roster.switchboard.jobs.rule_promotion_trigger.run_rule_promotion_trigger",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = {"candidates_scanned": 0}
            await run_rule_promotion_trigger_job(
                db_pool=object(),
                job_args={
                    "threshold": 5,
                    "min_distinct_days": 3,
                    "min_elapsed_hours": 12,
                    "lookback_days": 7,
                },
            )

        _, kwargs = mock_run.call_args
        assert kwargs["threshold"] == 5
        assert kwargs["min_distinct_days"] == 3
        assert kwargs["min_elapsed"] == timedelta(hours=12)
        assert kwargs["lookback"] == timedelta(days=7)

    async def test_unknown_job_arg_raises(self):
        from roster.switchboard.jobs.rule_promotion_trigger import (
            run_rule_promotion_trigger_job,
        )

        with pytest.raises(ValueError, match="unknown key"):
            await run_rule_promotion_trigger_job(db_pool=object(), job_args={"bogus": 1})

    async def test_threshold_below_two_raises(self):
        from roster.switchboard.jobs.rule_promotion_trigger import (
            run_rule_promotion_trigger_job,
        )

        with pytest.raises(ValueError, match="threshold"):
            await run_rule_promotion_trigger_job(db_pool=object(), job_args={"threshold": 1})

    async def test_negative_min_elapsed_hours_raises(self):
        from roster.switchboard.jobs.rule_promotion_trigger import (
            run_rule_promotion_trigger_job,
        )

        with pytest.raises(ValueError, match="min_elapsed_hours"):
            await run_rule_promotion_trigger_job(
                db_pool=object(), job_args={"min_elapsed_hours": -1}
            )

    async def test_non_positive_lookback_days_raises(self):
        from roster.switchboard.jobs.rule_promotion_trigger import (
            run_rule_promotion_trigger_job,
        )

        with pytest.raises(ValueError, match="lookback_days"):
            await run_rule_promotion_trigger_job(db_pool=object(), job_args={"lookback_days": 0})
