"""Tests for the healing dashboard API routes (bu-xp0x0.5).

Condensed to 3 tests (bu-2yw2d) from 11 (bu-egmz6).

Covers:
- Phase fields (current_phase / workflow_deadline_at) round-trip via parametrize
- Dispatch events list structure + 503 fallback
- 404 for unknown attempt
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.healing import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 9, 12, 0, 0, tzinfo=UTC)


def _make_attempt_row(
    *,
    attempt_id: uuid.UUID | None = None,
    status: str = "investigating",
    current_phase: str | None = None,
    workflow_deadline_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "id": attempt_id or uuid.uuid4(),
        "fingerprint": "a" * 64,
        "butler_name": "general",
        "status": status,
        "severity": 2,
        "exception_type": "KeyError",
        "call_site": "src/foo.py:bar",
        "sanitized_msg": None,
        "branch_name": None,
        "worktree_path": None,
        "pr_url": None,
        "pr_number": None,
        "session_ids": [],
        "healing_session_id": None,
        "current_phase": current_phase,
        "workflow_deadline_at": workflow_deadline_at,
        "created_at": _NOW,
        "updated_at": _NOW,
        "closed_at": None,
        "error_detail": None,
    }


def _make_dispatch_event_row(
    *,
    decision: str = "cooldown",
    reason: str | None = "within cooldown window",
    attempt_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "fingerprint": "b" * 64,
        "butler_name": "general",
        "decision": decision,
        "reason": reason,
        "attempt_id": attempt_id,
        "created_at": _NOW,
    }


class _MockRecord(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = 0,
    db_pool_raises: Any = None,
) -> tuple[Any, MagicMock]:
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_MockRecord(r) for r in (fetch_rows or [])])
    mock_pool.fetchrow = AsyncMock(
        return_value=_MockRecord(fetchrow_result) if fetchrow_result else None
    )
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value="OK")

    mock_db = MagicMock(spec=DatabaseManager)
    if db_pool_raises:
        mock_db.credential_shared_pool.side_effect = db_pool_raises
    else:
        mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Phase fields round-trip (parametrized: null + populated + each phase label)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "current_phase,deadline,expect_phase,expect_deadline",
    [
        (None, None, None, None),
        ("implement", datetime(2026, 4, 9, 13, 0, 0, tzinfo=UTC), "implement", True),
        ("diagnose", None, "diagnose", None),
        ("verify", None, "verify", None),
    ],
    ids=["null-phase", "populated-phase", "diagnose", "verify"],
)
async def test_list_attempts_phase_fields(current_phase, deadline, expect_phase, expect_deadline):
    row = _make_attempt_row(current_phase=current_phase, workflow_deadline_at=deadline)
    app, _ = _build_app(fetch_rows=[row], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/healing/attempts")
    assert resp.status_code == 200
    attempt = resp.json()["data"][0]
    assert attempt["current_phase"] == expect_phase
    if expect_deadline:
        assert attempt["workflow_deadline_at"] is not None
    else:
        assert attempt["workflow_deadline_at"] is None


# ---------------------------------------------------------------------------
# Dispatch events — paginated list structure + 503 fallback
# ---------------------------------------------------------------------------


async def test_dispatch_events_structure_and_503():
    # Happy path: list returns data/meta
    event = _make_dispatch_event_row(decision="cooldown")
    app_ok, _ = _build_app(fetch_rows=[event], fetchval_result=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_ok), base_url="http://test"
    ) as client:
        resp_ok = await client.get("/api/healing/dispatch-events")
    assert resp_ok.status_code == 200
    body = resp_ok.json()
    assert "data" in body and "meta" in body
    assert body["data"][0]["decision"] == "cooldown"

    # 503 when pool unavailable
    app_503, _ = _build_app(db_pool_raises=KeyError("no pool"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_503), base_url="http://test"
    ) as client:
        resp_503 = await client.get("/api/healing/dispatch-events")
    assert resp_503.status_code == 503


# ---------------------------------------------------------------------------
# Attempt detail — 404 for unknown attempt
# ---------------------------------------------------------------------------


async def test_get_attempt_detail_404():
    app, _ = _build_app(fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/healing/attempts/{uuid.uuid4()}")
    assert resp.status_code == 404
