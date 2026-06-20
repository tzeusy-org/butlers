"""Unit coverage for scheduler behavior against legacy table shapes."""

from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest


class _LegacySchedulerPool:
    def __init__(self) -> None:
        self.fetch_queries: list[str] = []
        self.execute_queries: list[str] = []
        self.fetchrow_queries: list[str] = []
        self.column_probe_args: list[tuple[Any, ...]] = []

    async def fetchval(self, query: str, *args: Any) -> bool:
        if "information_schema.columns" in query:
            column = args[1]
            return column == "task_type"
        if "information_schema.tables" in query:
            return False
        return False

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_queries.append(query)
        if "information_schema.columns" in query:
            self.column_probe_args.append(args)
            requested = set(args[1])
            return [{"column_name": column} for column in requested if column == "task_type"]
        if "FROM scheduled_tasks" in query and "next_run_at <= $1" in query:
            from datetime import datetime

            return [
                {
                    "id": "task-id",
                    "name": "legacy-task",
                    "cron": "*/5 * * * *",
                    "dispatch_mode": "prompt",
                    "prompt": "run legacy task",
                    "job_name": None,
                    "job_args": None,
                    "complexity": "medium",
                    "until_at": None,
                    "next_run_at": datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
                }
            ]
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        """Claim UPDATE — always succeeds in this test pool."""
        self.fetchrow_queries.append(query)
        if "UPDATE scheduled_tasks" in query and "IS NOT DISTINCT FROM" in query:
            # Simulate a successful claim: return a non-None row with the task id.
            return {"id": args[0]}
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_queries.append(query)
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_tick_uses_null_until_at_projection_for_legacy_schema(caplog) -> None:
    from butlers.core.model_routing import Complexity
    from butlers.core.scheduler import tick

    pool = _LegacySchedulerPool()
    dispatch_calls: list[dict[str, Any]] = []

    async def dispatch(**kwargs: Any) -> dict[str, str]:
        dispatch_calls.append(kwargs)
        return {"status": "ok"}

    with caplog.at_level("WARNING", logger="butlers.core.scheduler"):
        count = await tick(pool, dispatch)

    column_probe_queries = [
        query for query in pool.fetch_queries if "information_schema.columns" in query
    ]
    assert column_probe_queries
    assert set(pool.column_probe_args[0][1]) == {
        "max_token_budget",
        "task_type",
        "until_at",
    }
    due_task_queries = [
        query
        for query in pool.fetch_queries
        if "FROM scheduled_tasks" in query and "next_run_at <= $1" in query
    ]
    assert due_task_queries
    assert "NULL::timestamptz AS until_at" in due_task_queries[0]
    assert count == 1
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["prompt"] == "run legacy task"
    assert dispatch_calls[0]["trigger_source"] == "schedule:legacy-task"
    assert dispatch_calls[0]["complexity"] is Complexity.WORKHORSE
    assert pool.execute_queries
    assert "scheduled_tasks.until_at column missing" in caplog.text
