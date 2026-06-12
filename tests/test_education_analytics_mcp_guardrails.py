"""Regression tests for education analytics MCP guardrails."""

from __future__ import annotations

import importlib.util
import tomllib
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


class _Module:
    def __init__(self, pool) -> None:
        self._pool = pool

    def _get_pool(self):
        return self._pool


def _load_register_tools() -> Any:
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "roster/education/modules/tools.py"
    spec = importlib.util.spec_from_file_location("_education_module_tools", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.register_tools


async def test_analytics_get_snapshot_mcp_returns_ok_envelope() -> None:
    """A found snapshot should come back in a consistent status="ok" envelope.

    snapshot_date is normalized to an ISO-8601 string so callers never have to
    branch on a raw ``datetime.date`` object.
    """
    import datetime
    from unittest.mock import patch

    from butlers.tools.education import analytics as _analytics

    mind_map_id = str(uuid.uuid4())
    snapshot_row = {
        "id": str(uuid.uuid4()),
        "mind_map_id": mind_map_id,
        "snapshot_date": datetime.date(2026, 6, 7),
        "metrics": {"retention_rate_7d": 0.8},
    }
    pool = MagicMock()
    mcp = _CaptureMCP()

    register_tools = _load_register_tools()
    register_tools(mcp, _Module(pool), SimpleNamespace(groups=["analytics"]))

    with patch.object(
        _analytics, "analytics_get_snapshot", new=AsyncMock(return_value=dict(snapshot_row))
    ):
        result = await mcp.tools["analytics_get_snapshot"](mind_map_id=mind_map_id)

    assert result["status"] == "ok"
    assert result["snapshot_date"] == "2026-06-07"
    assert result["metrics"] == {"retention_rate_7d": 0.8}


async def test_analytics_get_snapshot_mcp_returns_terminal_not_found() -> None:
    """Missing snapshots should be explicit so agents do not repeat the same call."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    mcp = _CaptureMCP()

    register_tools = _load_register_tools()
    register_tools(mcp, _Module(pool), SimpleNamespace(groups=["analytics"]))

    mind_map_id = str(uuid.uuid4())
    result = await mcp.tools["analytics_get_snapshot"](mind_map_id=mind_map_id)

    assert result["status"] == "not_found"
    assert result["mind_map_id"] == mind_map_id
    assert result["snapshot_date"] is None
    assert "Do not retry" in result["message"]
    assert "mastery_get_map_summary" in result["message"]
    pool.fetchrow.assert_awaited_once()


def test_weekly_progress_digest_prompt_limits_snapshot_retry() -> None:
    """The scheduled prompt should encode the same no-retry fallback contract."""
    repo_root = Path(__file__).resolve().parents[1]
    with (repo_root / "roster/education/butler.toml").open("rb") as f:
        config = tomllib.load(f)

    schedules = config["butler"]["schedule"]
    digest = next(
        schedule for schedule in schedules if schedule["name"] == "weekly-progress-digest"
    )
    prompt = digest["prompt"]

    assert "analytics_get_snapshot(mind_map_id) at most once per map" in prompt
    assert "do not retry the same call" in prompt
    assert "mastery_get_map_summary()" in prompt
