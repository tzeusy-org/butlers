"""The real scan forwards symptom identity through the existing candidate seam."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest


async def test_symptom_trend_submission_carries_exact_name(monkeypatch):
    from butlers.jobs._roster import health_jobs
    from butlers.tools.switchboard.insight import broker

    name = "Headache & nausea / left?"
    now = datetime.now(UTC)
    rows = [
        {"name": name, "severity": severity, "occurred_at": now - timedelta(days=day)}
        for day, severity in enumerate((3, 5, 7))
    ]

    async def fetch(query, *args):
        if "SELECT content AS name" in query:
            return rows
        return []

    pool = AsyncMock()
    pool.fetch.side_effect = fetch
    pool.fetchval.return_value = 0
    submit = AsyncMock(return_value={"status": "accepted", "reason": "queued"})
    monkeypatch.setattr(broker, "propose_insight_candidate", submit)
    # This separate best-effort domain event does not carry symptom identities.
    monkeypatch.setattr(health_jobs, "_publish_recovery_state_event", AsyncMock())

    result = await health_jobs.run_insight_scan(pool)

    submit.assert_awaited_once()
    args, candidate = submit.await_args
    assert args == (pool,)
    assert candidate["metadata"] == {"name": name}
    assert candidate["origin_butler"] == "health"
    assert candidate["category"] == "symptom-trend"
    assert candidate["priority"] == 70
    assert candidate["dedup_key"].startswith(f"health:symptom-trend:{name}:")
    assert candidate["message"] == (
        f"{name} has been logged 3 times in the past 7 days (average severity 5.0/10). "
        "Consider tracking patterns or consulting a healthcare provider."
    )
    assert now + timedelta(days=3) <= candidate["expires_at"]
    assert candidate["expires_at"] < datetime.now(UTC) + timedelta(days=3)
    assert candidate["cooldown_days"] is None
    assert result == {
        "candidates_proposed": 1,
        "candidates_accepted": 1,
        "candidates_filtered": 0,
        "candidates_errored": 0,
        "early_exit": False,
    }


pytestmark = pytest.mark.unit
