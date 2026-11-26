# Spec: REQ-butler-general-010
"""day_sheet_push dedup on the real attention ledger, with a fake Switchboard client.

Owner recipient resolution, suppression, the date claim, the markers and the attention ledger all
run on a migrated database; only the transport (``deliver`` over MCP) is faked. A second tick, and a
tick after the date marker is lost, must not send the day's sheet again. Synthetic data only.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.core.general_settings import GENERAL_SETTINGS_STATE_KEY
from butlers.jobs import day_sheet_push as job
from butlers.testing.db_manager import db_manager
from tests.integration.test_calendar_day_sheet_pg import _source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

NOW = datetime(2026, 11, 23, 14, 40, tzinfo=UTC)  # 22:40 in Singapore


async def _world(db, monkeypatch):
    monkeypatch.setenv(job.ENABLED_ENV, "1")
    pool = await db("core")
    await pool.execute(
        "INSERT INTO public.state (key, value) VALUES ($1, $2::text::jsonb)",
        GENERAL_SETTINGS_STATE_KEY,
        json.dumps({"timezone": "Asia/Singapore"}),
    )
    owner = await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner']) RETURNING id"
    )
    await pool.execute(
        "INSERT INTO public.entity_info (entity_id, type, value, is_primary) "
        "VALUES ($1, 'telegram_chat_id', 'synthetic-chat', true)",
        owner,
    )
    await _source(pool, "owner", lane="user", role="owner")
    client = MagicMock()
    client.call_tool = AsyncMock(
        return_value=MagicMock(is_error=False, data={"status": "sent", "notification_id": "n-1"})
    )

    async def provider():
        return client

    manager = db_manager({"general": pool}, modules={"general": {"calendar"}})
    return pool, manager, client, provider


async def _ledger(pool) -> list:
    return await pool.fetch(
        "SELECT outcome, dedup_key FROM public.attention_ledger WHERE dedup_key LIKE $1",
        f"{job.JOB_NAME}:%",
    )


async def test_a_second_tick_sends_nothing_and_leaves_one_ledger_row(db, monkeypatch) -> None:
    pool, manager, client, provider = await _world(db, monkeypatch)

    first = await job.run_day_sheet_push(manager, client_provider=provider, now=NOW)
    second = await job.run_day_sheet_push(manager, client_provider=provider, now=NOW)

    assert first["outcome"] == "delivered"
    assert second == {"outcome": "skipped", "reason": "already_sent"}
    client.call_tool.assert_awaited_once()
    rows = await _ledger(pool)
    assert [r["outcome"] for r in rows] == ["delivered"]
    assert await pool.fetchval("SELECT count(*) FROM public.attention_ledger") == 1


async def test_the_ledger_alone_holds_the_date_when_the_marker_is_lost(db, monkeypatch) -> None:
    pool, manager, client, provider = await _world(db, monkeypatch)
    await job.run_day_sheet_push(manager, client_provider=provider, now=NOW)
    await pool.execute("DELETE FROM state WHERE key LIKE $1", f"{job.MARKER_PREFIX}%")

    again = await job.run_day_sheet_push(manager, client_provider=provider, now=NOW)

    assert again == {"outcome": "skipped", "reason": "already_sent"}
    client.call_tool.assert_awaited_once()
    assert await pool.fetchval("SELECT count(*) FROM public.attention_ledger") == 1
