"""Real-Postgres regression coverage for the calendar force-sync queue lease.

The module queue may have multiple workers briefly during daemon handoff.  This
test exercises the production claim and finalize SQL through two CalendarModule
instances so one pending command reaches provider work exactly once.
"""

from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.modules.calendar import CalendarModule

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


_CREATE_ACTION_LOG_SQL = """
CREATE TABLE calendar_action_log (
    id BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_id TEXT,
    action_type TEXT NOT NULL,
    action_status TEXT NOT NULL,
    action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_at TIMESTAMPTZ
)
"""


def _queue_worker(pool) -> CalendarModule:
    module = CalendarModule()
    module._db = SimpleNamespace(pool=pool)
    module._projection_tables_available_cache = True
    return module


async def test_concurrent_queue_workers_execute_one_pending_command_once(
    provisioned_postgres_pool,
) -> None:
    """One real SQL lease admits one provider sync across two module workers."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        await pool.execute(_CREATE_ACTION_LOG_SQL)
        await pool.execute(
            """
            INSERT INTO calendar_action_log (
                idempotency_key, request_id, action_type, action_status, action_payload
            )
            VALUES ($1, $2, $3, $4, $5)
            """,
            "calendar_force_sync:request:once",
            "once",
            "calendar_force_sync",
            "pending",
            {"calendar_ids": ["primary"], "full": False},
        )

        first_worker = _queue_worker(pool)
        second_worker = _queue_worker(pool)
        executions: list[str] = []

        async def _provider_sync(*, calendar_id: str | None, full: bool) -> dict[str, object]:
            executions.append(f"{calendar_id}:{full}")
            await asyncio.sleep(0)
            return {"status": "sync_completed", "errors": None}

        first_worker._run_force_sync = AsyncMock(side_effect=_provider_sync)
        second_worker._run_force_sync = AsyncMock(side_effect=_provider_sync)

        processed = await asyncio.gather(
            first_worker._drain_force_sync_commands(),
            second_worker._drain_force_sync_commands(),
        )

        assert sorted(processed) == [0, 1]
        assert executions == ["primary:False"]
        assert (
            first_worker._run_force_sync.await_count + second_worker._run_force_sync.await_count
            == 1
        )
        row = await pool.fetchrow(
            "SELECT action_status, action_result FROM calendar_action_log WHERE request_id = $1",
            "once",
        )
        assert row is not None
        assert row["action_status"] == "applied"
        assert row["action_result"] == {"status": "sync_completed", "errors": None}


async def test_running_command_blocks_a_pending_successor_from_another_worker(
    provisioned_postgres_pool,
) -> None:
    """A successor waits for the current provider operation instead of overlapping it."""
    async with provisioned_postgres_pool(min_pool_size=2, max_pool_size=4) as pool:
        await pool.execute(_CREATE_ACTION_LOG_SQL)
        await pool.executemany(
            """
            INSERT INTO calendar_action_log (
                idempotency_key, request_id, action_type, action_status, action_payload
            )
            VALUES ($1, $2, $3, $4, $5)
            """,
            [
                (
                    "calendar_force_sync:request:running",
                    "running",
                    "calendar_force_sync",
                    "running",
                    {"calendar_ids": ["primary"], "full": False},
                ),
                (
                    "calendar_force_sync:request:successor",
                    "successor",
                    "calendar_force_sync",
                    "pending",
                    {"calendar_ids": ["primary"], "full": True},
                ),
            ],
        )

        worker = _queue_worker(pool)
        worker._run_force_sync = AsyncMock(
            return_value={"status": "sync_completed", "errors": None}
        )

        assert await worker._drain_force_sync_commands() == 0
        worker._run_force_sync.assert_not_awaited()
        row = await pool.fetchrow(
            "SELECT action_status FROM calendar_action_log WHERE request_id = $1",
            "successor",
        )
        assert row is not None
        assert row["action_status"] == "pending"
