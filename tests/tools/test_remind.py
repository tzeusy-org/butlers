"""Tests for the remind() MCP tool.

remind() writes a one-shot scheduled task, so its scheduling test runs on
``tool_host`` (real clone, no lifecycle) and reads the row back. The validation
test never reaches SQL and uses a DB-free host (bu-rvgzmf.2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.testing.daemon import write_butler_config
from butlers.testing.fake_pool import fake_pool
from butlers.testing.tool_host import host_tools


async def _task_row(host, task_id: str):
    return await host.pool.fetchrow(
        "SELECT cron, until_at, prompt FROM scheduled_tasks WHERE id = $1::uuid", task_id
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_remind_scheduling(tool_host, tmp_path):
    """delay_minutes and remind_at both write a one-shot cron with a matching until_at."""
    host = await tool_host(write_butler_config(tmp_path, "general"))

    result = await host.call(
        "remind", message="Take medication", channel="telegram", delay_minutes=30
    )

    assert result["status"] == "scheduled"
    row = await _task_row(host, result["id"])
    assert len(row["cron"].split()) == 5
    assert "Take medication" in row["prompt"]
    remind_at = datetime.fromisoformat(result["remind_at"])
    assert abs((row["until_at"] - (remind_at + timedelta(minutes=1))).total_seconds()) < 2

    future_time = datetime.now(UTC) + timedelta(hours=2)
    result2 = await host.call("remind", message="Meeting", channel="email", remind_at=future_time)

    assert result2["status"] == "scheduled"
    parts = (await _task_row(host, result2["id"]))["cron"].split()
    assert parts[0] == str(future_time.minute)
    assert parts[1] == str(future_time.hour)


@pytest.mark.unit
async def test_remind_validation_errors(tmp_path):
    """remind returns error status for invalid timing combinations, before any SQL."""
    pool = fake_pool()
    host = await host_tools(write_butler_config(tmp_path, "general"), pool=pool)

    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(hours=1)
    # Each invalid timing combination must be rejected (wording is not the contract).
    cases = [
        {"delay_minutes": 10, "remind_at": future},  # both supplied
        {},  # neither supplied
        {"delay_minutes": 0},  # non-positive delay
        {"remind_at": past},  # past time
    ]
    for kwargs in cases:
        result = await host.call("remind", message="Test", channel="telegram", **kwargs)
        assert result["status"] == "error", f"Expected error status for {kwargs}"
