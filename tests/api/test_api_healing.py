"""Tests for the healing dashboard API routes (bu-xp0x0.5).

Condensed to 3 tests (bu-2yw2d) from 11 (bu-egmz6).

Covers:
- Phase fields (current_phase / workflow_deadline_at) round-trip via parametrize
- Dispatch events list structure + 503 fallback
- 404 for unknown attempt
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager
from butlers.api.routers.healing import _get_db_manager, _get_dispatch_fn

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


def _make_mcp_manager(*, butler_names: list[str] | None = None) -> MagicMock:
    """Build a mock MCPClientManager with no reachable daemons by default."""
    mgr = MagicMock()
    mgr.butler_names = butler_names or []
    # No daemons reachable by default — get_client raises for every name.
    mgr.get_client = AsyncMock(side_effect=RuntimeError("no daemon"))
    mgr.invalidate_client = AsyncMock()
    return mgr


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = 0,
    db_pool_raises: Any = None,
    mcp_manager: MagicMock | None = None,
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
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager or _make_mcp_manager()
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


# ---------------------------------------------------------------------------
# Retry dispatch (bu-cnvg7.2)
#
# The retry endpoint previously *always* claimed an investigation was
# re-dispatched even though no agent was ever spawned in the typical dashboard
# deployment (no in-process spawner → _get_dispatch_fn returns None). These
# tests pin the two truths:
#   1. With NO dispatch fn: response reports dispatched=False (no agent spawned).
#   2. With a dispatch fn override: the dispatch callable is actually invoked
#      with the new attempt's metadata, and the response reports dispatched=True.
# ---------------------------------------------------------------------------


def _retry_fetchrow_sequence(
    *,
    original_status: str = "failed",
) -> list[Any]:
    """Build the 3-call fetchrow sequence the retry path consumes.

    1. get_attempt(original)   -> the original (terminal) attempt row
    2. get_active_attempt(fp)  -> None (no active row for the fingerprint)
    3. INSERT ... RETURNING     -> the freshly-created attempt row
    """
    original = _make_attempt_row(status=original_status)
    new_id = uuid.uuid4()
    inserted = {
        "id": new_id,
        "fingerprint": original["fingerprint"],
        "status": "investigating",
    }
    return [_MockRecord(original), None, _MockRecord(inserted)]


async def test_retry_without_dispatch_fn_daemon_unreachable_reports_not_dispatched():
    """No in-process spawner AND no reachable daemon → retry must NOT claim re-dispatch."""
    # Mock manager registers a butler but its daemon is unreachable.
    mgr = _make_mcp_manager(butler_names=["general"])
    app, mock_pool = _build_app(mcp_manager=mgr)
    mock_pool.fetchrow = AsyncMock(side_effect=_retry_fetchrow_sequence())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/healing/attempts/{uuid.uuid4()}/retry")

    assert resp.status_code == 201
    body = resp.json()
    # The bug: response used to omit `dispatched` and the UI always claimed
    # "Investigation re-dispatched." Now the API tells the truth.
    assert body["dispatched"] is False
    assert "re-dispatched" not in body["detail"].lower()
    assert body["status"] == "investigating"
    # We attempted the owning butler's daemon before giving up.
    mgr.get_client.assert_awaited()


async def test_retry_redispatches_via_daemon_mcp_and_reports_dispatched():
    """Cross-process path: retry invokes the daemon retry_healing MCP tool and reports True."""
    mgr = _make_mcp_manager(butler_names=["general"])
    app, mock_pool = _build_app(mcp_manager=mgr)
    seq = _retry_fetchrow_sequence()
    mock_pool.fetchrow = AsyncMock(side_effect=seq)
    new_attempt_id = seq[2]["id"]

    # The daemon's MCP client accepts the re-dispatch.
    accepted_block = MagicMock()
    accepted_block.text = json.dumps({"accepted": True, "reason": "dispatched"})
    tool_result = MagicMock()
    tool_result.is_error = False
    tool_result.content = [accepted_block]

    client_mock = MagicMock()
    client_mock.call_tool = AsyncMock(return_value=tool_result)
    mgr.get_client = AsyncMock(return_value=client_mock)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/healing/attempts/{uuid.uuid4()}/retry")

    assert resp.status_code == 201
    body = resp.json()
    assert body["dispatched"] is True
    assert body["detail"] == "Investigation re-dispatched."

    # The regression assertion: the daemon retry_healing tool was actually
    # called with the NEW attempt's id — not a silent no-op.
    mgr.get_client.assert_awaited_with("general")
    client_mock.call_tool.assert_awaited_once_with(
        "retry_healing", {"attempt_id": str(new_attempt_id)}
    )


async def test_retry_with_dispatch_fn_invokes_dispatch_and_reports_dispatched():
    """When a dispatch callable is wired, it MUST actually be invoked."""
    app, mock_pool = _build_app()
    seq = _retry_fetchrow_sequence()
    mock_pool.fetchrow = AsyncMock(side_effect=seq)
    new_attempt_id = seq[2]["id"]
    fingerprint = seq[2]["fingerprint"]

    spy = AsyncMock()
    app.dependency_overrides[_get_dispatch_fn] = lambda: spy

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/healing/attempts/{uuid.uuid4()}/retry")

    assert resp.status_code == 201
    body = resp.json()
    assert body["dispatched"] is True

    # The regression assertion: the dispatch fn was actually called (spawned),
    # with the NEW attempt's id and fingerprint — not a silent no-op.
    spy.assert_awaited_once()
    _, kwargs = spy.await_args
    assert kwargs["attempt_id"] == new_attempt_id
    assert kwargs["fingerprint"] == fingerprint
    assert kwargs["butler_name"] == "general"


async def test_retry_rejects_non_terminal_attempt():
    """Retry on an still-active attempt is a 409 (no row created, no dispatch)."""
    app, mock_pool = _build_app()
    mock_pool.fetchrow = AsyncMock(
        return_value=_MockRecord(_make_attempt_row(status="investigating"))
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/healing/attempts/{uuid.uuid4()}/retry")

    assert resp.status_code == 409
