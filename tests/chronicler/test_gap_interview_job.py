"""The ``chronicler_gap_interview`` job-mode schedule (bu-idc5mx.4).

The job asks for yesterday in the owner's general timezone through the same
``ask_gap_interview`` service the MCP tool calls, and passes its status through unchanged.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.chronicler import gap_interview_job
from butlers.chronicler.gap_interview_job import run_gap_interview_job

pytestmark = pytest.mark.unit


@pytest.fixture
def ask(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    stub = AsyncMock(return_value={"status": "no_gap", "date": "sentinel"})
    monkeypatch.setattr(gap_interview_job, "ask_gap_interview", stub)
    return stub


def _general_timezone(monkeypatch: pytest.MonkeyPatch, zone: str) -> None:
    monkeypatch.setattr(
        "butlers.core.general_settings.resolve_general_timezone", AsyncMock(return_value=zone)
    )


@pytest.mark.parametrize(
    ("zone", "now", "yesterday"),
    [
        # 09:00 NZDT on 22 Oct is still 21 Oct in UTC: yesterday is local, not UTC.
        ("Pacific/Auckland", datetime(2026, 10, 21, 20, 0, tzinfo=UTC), "2026-10-21"),
        # 09:00 EST on the US fall-back (25-hour) day.
        ("America/New_York", datetime(2026, 11, 1, 14, 0, tzinfo=UTC), "2026-10-31"),
        # 00:30 EDT just after the 23-hour spring-forward day: "24 hours ago" lands on 7 Mar.
        ("America/New_York", datetime(2026, 3, 9, 4, 30, tzinfo=UTC), "2026-03-08"),
    ],
    ids=["ahead-of-utc", "dst-fall-back", "after-dst-spring-forward"],
)
async def test_job_asks_for_yesterday_in_the_general_timezone(
    monkeypatch, ask, zone, now, yesterday
):
    _general_timezone(monkeypatch, zone)

    await run_gap_interview_job(MagicMock(), None, now=now)

    assert ask.await_args.kwargs == {"date_label": yesterday, "timezone": zone}


async def test_job_rejects_unknown_job_args_before_asking(monkeypatch, ask):
    _general_timezone(monkeypatch, "UTC")

    with pytest.raises(RuntimeError, match="unsupported keys: \\['date_label'\\]"):
        await run_gap_interview_job(MagicMock(), {"date_label": "2026-10-21"})

    ask.assert_not_awaited()


def _register_tools(pool) -> dict:
    from butlers.modules.registry import default_registry

    default_registry()  # imports roster modules as synthetic modules
    mod = importlib.import_module("butlers.modules._roster_chronicler")
    registered: dict = {}
    mcp = MagicMock()
    mcp.tool.side_effect = lambda: lambda fn: registered.__setitem__(fn.__name__, fn) or fn
    module = mod.ChroniclerModule()
    module._db = MagicMock(pool=pool)
    mod._register_tools(mcp, module)
    return registered


async def test_tool_and_job_share_one_ask_and_pass_status_through(monkeypatch, ask):
    pool = MagicMock()
    _general_timezone(monkeypatch, "Asia/Singapore")
    tool = _register_tools(pool)["chronicler_gap_interview"]

    tool_status = await tool(date_label="2026-10-21", timezone="Asia/Singapore")
    job_status = await run_gap_interview_job(
        pool, None, now=datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    )

    assert ask.await_count == 2
    tool_call, job_call = ask.await_args_list
    assert tool_call.args == job_call.args == (pool,)
    assert (
        tool_call.kwargs
        == job_call.kwargs
        == {
            "date_label": "2026-10-21",
            "timezone": "Asia/Singapore",
        }
    )
    assert tool_status == job_status == {"status": "no_gap", "date": "sentinel"}


async def test_started_module_uses_selected_store_for_the_real_ask(monkeypatch):
    """A shared-only token reaches the real resolver, decision engine and HTTP boundary."""
    from butlers.credential_store import CredentialStore
    from butlers.modules.registry import default_registry

    default_registry()
    mod = importlib.import_module("butlers.modules._roster_chronicler")
    local = MagicMock()
    local_conn = AsyncMock()
    local_conn.fetchrow.return_value = None
    local.acquire.return_value.__aenter__ = AsyncMock(return_value=local_conn)
    shared = MagicMock()
    shared_conn = AsyncMock()
    shared_conn.fetchrow.return_value = {"secret_value": "synthetic-shared-token"}
    shared.acquire.return_value.__aenter__ = AsyncMock(return_value=shared_conn)
    store = CredentialStore(local, fallback_pools=[shared])
    writes = AsyncMock()
    monkeypatch.setattr("butlers.core.state.state_get", AsyncMock(return_value=None))
    monkeypatch.setattr("butlers.core.state.state_set", writes)
    monkeypatch.setattr("butlers.chronicler.storage.list_episodes", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "butlers.core.temporal.delivery_db.get_delivery_preferences", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "butlers.chronicler.gap_interview_telegram.resolve_owner_chat_id",
        AsyncMock(return_value="synthetic-owner-chat"),
    )
    http = MagicMock()
    response = MagicMock(status_code=200)
    response.json.return_value = {"result": {"message_id": 7}}
    http.post = AsyncMock(return_value=response)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=http)
    client.__aexit__ = AsyncMock(return_value=False)
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr("httpx.AsyncClient", client_factory)
    module = mod.ChroniclerModule()
    monkeypatch.setattr(module, "_register_default_schedules", AsyncMock())
    await module.on_startup({}, MagicMock(pool=local), credential_store=store)
    registered = {}
    mcp = MagicMock()
    mcp.tool.side_effect = lambda: lambda fn: registered.__setitem__(fn.__name__, fn) or fn
    await module.register_tools(mcp, {}, module._db, "chronicler")
    try:
        result = await registered["chronicler_gap_interview"]("2026-10-21", "UTC")
        assert result["status"] == "asked"
        assert http.post.await_count == 1
        assert http.post.await_args.args[0].endswith("synthetic-shared-token/sendMessage")
        assert shared_conn.fetchrow.await_count == 1
        assert writes.await_count == 2
        client_factory.assert_called_once_with(timeout=30.0)
    finally:
        await module.on_shutdown()
    assert module._db is None
    assert module._credential_store is None
