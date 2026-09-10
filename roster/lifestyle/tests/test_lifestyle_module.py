"""Unit tests for LifestyleModule's five taste-ledger MCP tools (bu-2jtfw.10).

Mocks the asyncpg pool at the module boundary (``module._get_pool()``); the
real SQL is exercised end-to-end by ``roster/lifestyle/tests/test_taste_ledger.py``.

Issue: bu-2jtfw.10
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from butlers.modules._roster_lifestyle import LifestyleModule

pytestmark = pytest.mark.unit


def _make_mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        declared_name = kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


class _FakeDB:
    def __init__(self, pool: Any) -> None:
        self.pool = pool


async def _registered_module(pool: Any) -> tuple[LifestyleModule, dict[str, Any]]:
    module = LifestyleModule()
    mcp = _make_mock_mcp()
    await module.register_tools(mcp=mcp, config={}, db=_FakeDB(pool), butler_name="lifestyle")
    return module, mcp._registered_tools


async def test_registers_exactly_five_tools() -> None:
    _, tools = await _registered_module(AsyncMock())
    assert set(tools) == {
        "taste_backfill_ledger",
        "taste_list_works",
        "taste_get_work",
        "taste_get_summary",
        "taste_add_verdict",
    }


async def test_taste_list_works_filters_by_kind_and_clamps_limit() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    _, tools = await _registered_module(pool)

    await tools["taste_list_works"](kind="track", limit=10_000)

    query, params = pool.fetch.call_args.args[0], pool.fetch.call_args.args[1:]
    assert "WHERE kind = $1" in query
    assert params[0] == "track"
    assert params[1] == 200  # clamped


async def test_taste_get_work_returns_error_for_unknown_id() -> None:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    _, tools = await _registered_module(pool)

    result = await tools["taste_get_work"](work_id=str(uuid4()))
    assert "error" in result


async def test_taste_get_work_includes_signals_and_verdicts() -> None:
    work_id = uuid4()
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={
            "id": work_id,
            "kind": "track",
            "title": "Song A",
            "external_ids": {"primary": "spotify:track:a"},
            "metadata": {},
            "created_at": datetime(2026, 9, 1, tzinfo=UTC),
        }
    )
    pool.fetch = AsyncMock(
        side_effect=[
            [
                {
                    "signal_kind": "listen_completed",
                    "source_table": "spotify_track_plays",
                    "occurred_at": datetime(2026, 9, 1, tzinfo=UTC),
                    "strength": 0.95,
                    "metadata": {},
                }
            ],
            [
                {
                    "predicate": "opinion",
                    "verdict_text": "one of my favorites",
                    "source": "owner_assertion",
                    "created_at": datetime(2026, 9, 1, tzinfo=UTC),
                }
            ],
        ]
    )
    _, tools = await _registered_module(pool)

    result = await tools["taste_get_work"](work_id=str(work_id))
    assert result["work"]["title"] == "Song A"
    assert len(result["signals"]) == 1
    assert result["signals"][0]["signal_kind"] == "listen_completed"
    assert len(result["verdicts"]) == 1
    assert result["verdicts"][0]["verdict_text"] == "one of my favorites"


async def test_taste_get_summary_aggregates_counts() -> None:
    pool = AsyncMock()
    pool.fetch = AsyncMock(
        side_effect=[
            [{"kind": "track", "n": 5}],
            [{"signal_kind": "listen_completed", "n": 3}],
        ]
    )
    pool.fetchval = AsyncMock(side_effect=[5, 3, 1])
    _, tools = await _registered_module(pool)

    result = await tools["taste_get_summary"]()
    assert result["total_works"] == 5
    assert result["total_signals"] == 3
    assert result["total_verdicts"] == 1
    assert result["works_by_kind"] == {"track": 5}
    assert result["signals_by_kind"] == {"listen_completed": 3}


async def test_taste_add_verdict_never_written_without_explicit_call() -> None:
    """taste_add_verdict is a distinct write path from the resolver — the
    resolver never calls it, so no verdict is ever attributed to the owner
    without an explicit assertion."""
    verdict_id = uuid4()
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={"id": verdict_id, "created_at": datetime(2026, 9, 1, tzinfo=UTC)}
    )
    _, tools = await _registered_module(pool)

    result = await tools["taste_add_verdict"](verdict_text="loves this artist")
    assert result["id"] == str(verdict_id)
    query, params = pool.fetchrow.call_args.args[0], pool.fetchrow.call_args.args[1:]
    assert "'owner_assertion'" in query
    assert params == (None, "opinion", "loves this artist")


async def test_taste_backfill_ledger_runs_both_evidence_sources() -> None:
    from butlers.tools.lifestyle import taste_ledger as ledger

    module = LifestyleModule()
    mcp = _make_mock_mcp()
    pool = AsyncMock()
    await module.register_tools(mcp=mcp, config={}, db=_FakeDB(pool), butler_name="lifestyle")

    sessions_result = ledger.BackfillResult(works_created=2, signals_created=2)
    plays_result = ledger.BackfillResult(works_created=1, signals_created=3)

    from unittest.mock import patch

    with (
        patch.object(
            ledger, "backfill_from_listening_sessions", AsyncMock(return_value=sessions_result)
        ),
        patch.object(ledger, "backfill_from_track_plays", AsyncMock(return_value=plays_result)),
    ):
        result = await mcp._registered_tools["taste_backfill_ledger"]()

    assert result["sessions"]["works_created"] == 2
    assert result["track_plays"]["signals_created"] == 3
