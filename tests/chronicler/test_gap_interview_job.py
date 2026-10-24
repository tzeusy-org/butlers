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
