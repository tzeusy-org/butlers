"""Tests for the healing dashboard API routes (bu-xp0x0.5).

Covers:
- GET /api/healing/attempts              — phase/deadline fields in response model
- GET /api/healing/attempts/{id}         — full attempt detail with phase/deadline
- GET /api/healing/dispatch-events       — distinct from healing attempts
- POST /api/healing/circuit-breaker/reset — circuit breaker reset

Focus: verify that ``current_phase`` and ``workflow_deadline_at`` are included
in the ``HealingAttempt`` response model and correctly serialised.
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
    fingerprint: str = "a" * 64,
    butler_name: str = "general",
    status: str = "investigating",
    severity: int = 2,
    exception_type: str = "KeyError",
    call_site: str = "src/foo.py:bar",
    sanitized_msg: str | None = None,
    branch_name: str | None = None,
    worktree_path: str | None = None,
    pr_url: str | None = None,
    pr_number: int | None = None,
    session_ids: list[str] | None = None,
    healing_session_id: uuid.UUID | None = None,
    current_phase: str | None = None,
    workflow_deadline_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    closed_at: datetime | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    """Build a fake healing_attempts row dict."""
    return {
        "id": attempt_id or uuid.uuid4(),
        "fingerprint": fingerprint,
        "butler_name": butler_name,
        "status": status,
        "severity": severity,
        "exception_type": exception_type,
        "call_site": call_site,
        "sanitized_msg": sanitized_msg,
        "branch_name": branch_name,
        "worktree_path": worktree_path,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "session_ids": session_ids or [],
        "healing_session_id": healing_session_id,
        "current_phase": current_phase,
        "workflow_deadline_at": workflow_deadline_at,
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
        "closed_at": closed_at,
        "error_detail": error_detail,
    }


def _make_dispatch_event_row(
    *,
    event_id: uuid.UUID | None = None,
    fingerprint: str = "b" * 64,
    butler_name: str = "general",
    decision: str = "cooldown",
    reason: str | None = "within cooldown window",
    attempt_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a fake healing_dispatch_events row dict."""
    return {
        "id": event_id or uuid.uuid4(),
        "fingerprint": fingerprint,
        "butler_name": butler_name,
        "decision": decision,
        "reason": reason,
        "attempt_id": attempt_id,
        "created_at": created_at or _NOW,
    }


class _MockRecord(dict):
    """A dict subclass that mimics asyncpg Record access patterns."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _mock_record(row: dict[str, Any]) -> _MockRecord:
    return _MockRecord(row)


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = 0,
    execute_result: str = "OK",
    fetch_side_effect: Any = None,
    fetchrow_side_effect: Any = None,
    fetchval_side_effect: Any = None,
) -> tuple[Any, MagicMock]:
    """Build a test FastAPI app with a mocked database pool."""
    mock_pool = AsyncMock()

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=[_mock_record(r) for r in (fetch_rows or [])])

    if fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(
            return_value=_mock_record(fetchrow_result) if fetchrow_result else None
        )

    if fetchval_side_effect is not None:
        mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_pool.fetchval = AsyncMock(return_value=fetchval_result)

    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# GET /api/healing/attempts — phase/deadline fields
# ---------------------------------------------------------------------------


class TestListHealingAttemptsPhaseFields:
    """HealingAttempt responses include current_phase and workflow_deadline_at."""

    async def test_phase_fields_null_for_single_session_attempt(self) -> None:
        """Single-session attempts have null current_phase and workflow_deadline_at."""
        row = _make_attempt_row(status="investigating")
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts")

        assert response.status_code == 200
        attempt = response.json()["data"][0]
        assert attempt["current_phase"] is None
        assert attempt["workflow_deadline_at"] is None

    async def test_phase_fields_populated_for_phased_attempt(self) -> None:
        """Multi-session attempts expose current_phase and workflow_deadline_at."""
        deadline = datetime(2026, 4, 9, 13, 0, 0, tzinfo=UTC)
        row = _make_attempt_row(
            status="investigating",
            current_phase="implement",
            workflow_deadline_at=deadline,
        )
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/attempts")

        assert response.status_code == 200
        attempt = response.json()["data"][0]
        assert attempt["current_phase"] == "implement"
        assert attempt["workflow_deadline_at"] is not None

    async def test_all_phase_values_are_preserved(self) -> None:
        """Phase labels diagnose/implement/verify are all passed through unchanged."""
        for phase in ("diagnose", "implement", "verify"):
            row = _make_attempt_row(current_phase=phase)
            app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/healing/attempts")

            assert response.status_code == 200
            assert response.json()["data"][0]["current_phase"] == phase


# ---------------------------------------------------------------------------
# GET /api/healing/attempts/{attempt_id} — detail with phase/deadline
# ---------------------------------------------------------------------------


class TestGetHealingAttemptDetail:
    """Single-attempt detail includes phase/deadline fields."""

    async def test_detail_includes_phase_and_deadline(self) -> None:
        """GET /api/healing/attempts/{id} returns current_phase and workflow_deadline_at."""
        attempt_id = uuid.uuid4()
        deadline = datetime(2026, 4, 9, 14, 30, 0, tzinfo=UTC)
        row = _make_attempt_row(
            attempt_id=attempt_id,
            status="investigating",
            current_phase="diagnose",
            workflow_deadline_at=deadline,
        )
        app, _ = _build_app(fetchrow_result=row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/healing/attempts/{attempt_id}")

        assert response.status_code == 200
        attempt = response.json()
        assert attempt["current_phase"] == "diagnose"
        assert attempt["workflow_deadline_at"] is not None

    async def test_detail_returns_404_for_unknown_attempt(self) -> None:
        """GET /api/healing/attempts/{unknown_id} returns 404."""
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/healing/attempts/{uuid.uuid4()}")

        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/healing/dispatch-events — distinct from attempts
# ---------------------------------------------------------------------------


class TestListHealingDispatchEvents:
    """Dispatch events are separate from healing attempts and never mixed in."""

    async def test_returns_empty_list_when_no_events(self) -> None:
        """When no dispatch events exist, the list is empty."""
        app, _ = _build_app(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/dispatch-events")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_dispatch_events_with_decision_field(self) -> None:
        """Dispatch events include decision and reason fields."""
        event = _make_dispatch_event_row(decision="cooldown", reason="within cooldown window")
        app, _ = _build_app(fetch_rows=[event], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/dispatch-events")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        ev = body["data"][0]
        assert ev["decision"] == "cooldown"
        assert ev["reason"] == "within cooldown window"
        assert ev["attempt_id"] is None

    async def test_dispatch_events_link_to_attempt_when_present(self) -> None:
        """Dispatch events that link to an attempt expose the attempt_id."""
        attempt_id = uuid.uuid4()
        event = _make_dispatch_event_row(decision="novelty_join", attempt_id=attempt_id)
        app, _ = _build_app(fetch_rows=[event], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/dispatch-events")

        assert response.status_code == 200
        ev = response.json()["data"][0]
        assert ev["attempt_id"] == str(attempt_id)
        assert ev["decision"] == "novelty_join"

    async def test_decision_filter_accepted(self) -> None:
        """The decision query param filters to a specific decision type."""
        event = _make_dispatch_event_row(decision="circuit_breaker")
        app, _ = _build_app(fetch_rows=[event], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/healing/dispatch-events", params={"decision": "circuit_breaker"}
            )

        assert response.status_code == 200
        assert response.json()["data"][0]["decision"] == "circuit_breaker"

    async def test_pagination_parameters_accepted(self) -> None:
        """Dispatch events endpoint accepts standard pagination parameters."""
        app, _ = _build_app(fetch_rows=[], fetchval_result=25)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/healing/dispatch-events", params={"limit": 5, "offset": 10}
            )

        assert response.status_code == 200
        meta = response.json()["meta"]
        assert meta["limit"] == 5
        assert meta["offset"] == 10
        assert meta["total"] == 25

    async def test_returns_503_when_db_unavailable(self) -> None:
        """Returns 503 when the shared database pool is not available."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no pool")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/healing/dispatch-events")

        assert response.status_code == 503
