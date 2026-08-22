"""Exact Audit -> Issues evidence door (bu-6jv4m.3).

``AuditLogTable`` used to link a failure row to ``/issues?q=<first line of the
error>``, which ``IssuesPage`` then substring-matched against a feed it had
already fetched under a default seven-day window.  That hop was fuzzy in two
independent ways -- the needle was an approximation of the backend's grouping
normalization, and the haystack was whatever the default window happened to
contain -- so it could land on an empty page that read as an all-clear.

``GET /api/issues/group-for-audit/{audit_id}`` replaces it with a
server-computed answer: the exact ``issue_key`` of the group that row belongs
to, the window in which that group actually exists, or an EXPLICIT statement
that no current group exists.  Absence is never implied by an empty payload.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.audit_grouping import audit_group_key
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_mcp_manager
from butlers.api.routers.issues import (
    _REASON_NO_CURRENT_GROUP,
    _REASON_NOT_A_FAILURE,
    _get_db_manager,
)

pytestmark = pytest.mark.unit

ERROR_SUMMARY = "KeyError: 'access_token'"


def _is_group_for_row_query(sql: str) -> bool:
    return "target_row" in sql


def _is_audit_row_lookup(sql: str) -> bool:
    # Checked only AFTER _is_group_for_row_query: the group query's ``target_row``
    # CTE also selects ``FROM public.audit_log ... WHERE id = $1``, so this
    # predicate alone does not distinguish the two statements.
    return "FROM public.audit_log" in sql and "WHERE id = $1" in sql


def _group_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "error_summary": ERROR_SUMMARY,
        "first_seen_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        "last_seen_at": datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
        "occurrences": 12,
        "butlers": ["calendar"],
        "has_schedule": False,
        "schedule_names": [],
    }
    row.update(overrides)
    return row


def _build_app(
    *,
    audit_row: dict[str, Any] | None,
    group_rows: list[dict[str, Any]] | None = None,
    error: Exception | None = None,
) -> tuple[Any, AsyncMock]:
    async def fetch(sql: str, *_args: Any) -> list[Any]:
        if error is not None:
            raise error
        if _is_group_for_row_query(sql):
            return list(group_rows or [])
        if _is_audit_row_lookup(sql):
            return [audit_row] if audit_row is not None else []
        return []

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(side_effect=fetch)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_butler_configs] = lambda: []
    return app, mock_pool


async def _resolve(app: Any, audit_id: int, query: str = "") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(f"/api/issues/group-for-audit/{audit_id}{query}")


def _failure_row(ts: datetime, audit_id: int = 42) -> dict[str, Any]:
    return {"id": audit_id, "ts": ts, "result": "error", "error": ERROR_SUMMARY}


class TestExactGroupIdentity:
    async def test_recent_failure_resolves_to_the_exact_group_key(self) -> None:
        app, _ = _build_app(
            audit_row=_failure_row(datetime.now(UTC) - timedelta(hours=2)),
            group_rows=[_group_row()],
        )

        resp = await _resolve(app, 42)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["found"] is True
        assert data["reason"] is None
        # The exact server-side identity, not a slug or a substring needle.
        assert data["issue_key"] == audit_group_key(ERROR_SUMMARY)
        assert data["occurrences"] == 12
        assert data["severity"] == "warning"
        assert data["butlers"] == ["calendar"]

    async def test_href_carries_the_group_and_the_preserved_window(self) -> None:
        app, _ = _build_app(
            audit_row=_failure_row(datetime.now(UTC) - timedelta(hours=2)),
            group_rows=[_group_row()],
        )

        resp = await _resolve(app, 42)

        data = resp.json()["data"]
        assert data["window"] == "24h"
        assert data["issues_href"] is not None
        assert "window=24h" in data["issues_href"]
        assert "group=" in data["issues_href"]

    async def test_old_failure_widens_the_window_instead_of_missing_its_group(self) -> None:
        """A row older than the Issues page's 7d default must not resolve to
        an empty seven-day view; the server preserves a window that contains
        it."""
        app, _ = _build_app(
            audit_row=_failure_row(datetime.now(UTC) - timedelta(days=45)),
            group_rows=[_group_row()],
        )

        resp = await _resolve(app, 42)

        data = resp.json()["data"]
        assert data["window"] == "all"
        assert "window=all" in data["issues_href"]

    async def test_explicit_window_is_honoured(self) -> None:
        app, _ = _build_app(
            audit_row=_failure_row(datetime.now(UTC) - timedelta(hours=2)),
            group_rows=[_group_row()],
        )

        resp = await _resolve(app, 42, "?window=30d")

        assert resp.json()["data"]["window"] == "30d"

    async def test_scheduled_failure_group_is_classified_critical(self) -> None:
        app, _ = _build_app(
            audit_row=_failure_row(datetime.now(UTC) - timedelta(hours=2)),
            group_rows=[_group_row(has_schedule=True, schedule_names=["nightly"])],
        )

        resp = await _resolve(app, 42)

        assert resp.json()["data"]["severity"] == "critical"


class TestAbsenceIsExplicit:
    async def test_no_current_group_is_reported_not_implied(self) -> None:
        """AC3: 'no current group exists' is a stated fact, not an empty list."""
        app, _ = _build_app(
            audit_row=_failure_row(datetime.now(UTC) - timedelta(hours=2)),
            group_rows=[],
        )

        resp = await _resolve(app, 42)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["found"] is False
        assert data["reason"] == _REASON_NO_CURRENT_GROUP
        assert data["issue_key"] is None
        # No door is offered to a group that does not exist.
        assert data["issues_href"] is None
        # The window the claim is scoped to is still reported.
        assert data["window"] == "24h"

    async def test_non_failure_row_reports_why_it_has_no_group(self) -> None:
        app, _ = _build_app(
            audit_row={
                "id": 42,
                "ts": datetime.now(UTC),
                "result": "success",
                "error": None,
            }
        )

        resp = await _resolve(app, 42)

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["found"] is False
        assert data["reason"] == _REASON_NOT_A_FAILURE
        assert data["issues_href"] is None

    async def test_unknown_audit_row_is_a_404(self) -> None:
        app, _ = _build_app(audit_row=None)

        resp = await _resolve(app, 999)

        assert resp.status_code == 404


class TestUnavailableLookup:
    async def test_query_failure_is_a_503_not_an_empty_group(self) -> None:
        """AC5 (unavailable case): a failed lookup must be distinguishable from
        'this failure has no group', or the caller renders calm it never
        established."""
        app, _ = _build_app(audit_row=None, error=ConnectionError("connection reset by peer"))

        resp = await _resolve(app, 42)

        assert resp.status_code == 503

    async def test_invalid_window_is_rejected(self) -> None:
        app, _ = _build_app(audit_row=_failure_row(datetime.now(UTC)))

        resp = await _resolve(app, 42, "?window=banana")

        assert resp.status_code == 422
