"""Unit regressions for relationship pending-actions curation."""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import ModuleType
from typing import Any

import pytest

_MODULE_KEY = "butlers.jobs._roster.relationship_jobs"


def _get_rjobs() -> ModuleType:
    mod = sys.modules.get(_MODULE_KEY)
    if mod is None:
        from butlers.jobs._roster_loader import load_roster_jobs

        mod = load_roster_jobs("relationship")
    return mod


pytestmark = pytest.mark.unit


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._rows

    async def fetchval(self, *_args: Any, **_kwargs: Any) -> int:
        return 1


@pytest.mark.asyncio
async def test_pending_actions_curation_tolerates_string_and_scalar_tool_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed tool_args JSONB value must not abort the scheduled job."""
    rjobs = _get_rjobs()
    now = datetime.now(UTC)
    rows = [
        {
            "id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "tool_name": "channel_add",
            "tool_args": '{"contact_id":"contact-1","type":"email"}',
            "agent_summary": None,
            "why": "needs owner review",
            "requested_at": now,
            "expires_at": now + timedelta(hours=6),
        },
        {
            "id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "tool_name": "channel_add",
            "tool_args": ["unexpected"],
            "agent_summary": None,
            "why": "needs owner review",
            "requested_at": now,
            "expires_at": now + timedelta(hours=8),
        },
    ]
    proposed: list[dict[str, Any]] = []

    async def fake_propose_insight_candidate(_pool: Any, **kwargs: Any) -> dict[str, str]:
        proposed.append(kwargs)
        return {"status": "accepted"}

    broker = import_module("butlers.tools.switchboard.insight.broker")
    monkeypatch.setattr(broker, "propose_insight_candidate", fake_propose_insight_candidate)

    result = await rjobs.run_pending_actions_curation(_FakePool(rows))

    assert result["scanned"] == 2
    assert result["surfaced"] == 2
    assert result["errors"] == 0
    assert '"contact_id": "contact-1"' in proposed[0]["message"]
    assert 'Args: ["unexpected"]' in proposed[1]["message"]
