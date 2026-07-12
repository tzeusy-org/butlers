"""Tests for the issues dismiss/undismiss (server-side ack) endpoints.

The Issues feed is derived/ephemeral (live reachability + grouped audit-log
errors), so dismissal is persisted in ``public.dismissed_issues`` keyed by the
issue's stable ``issue_key``. These tests exercise the POST/DELETE endpoints and
the list-endpoint filtering against a mocked DB pool.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.audit_grouping import audit_group_key
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_butler_configs, get_mcp_manager
from butlers.api.models import compute_issue_key
from butlers.api.routers.issues import _get_db_manager

pytestmark = pytest.mark.unit


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    execute_result: str = "INSERT 0 1",
) -> tuple[Any, MagicMock]:
    """Build a test app whose switchboard pool is mocked."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=list(fetch_rows or []))
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    # The list endpoint needs the MCP manager + butler configs; for dismiss/
    # undismiss tests we only need them to be present and empty.
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[get_butler_configs] = lambda: []
    return app, mock_pool


async def _call(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await getattr(client, method)(path, **kwargs)


class TestDismissIssue:
    async def test_dismiss_persists_via_upsert(self) -> None:
        app, pool = _build_app()
        key = compute_issue_key("audit_error_group:keyerror", "general")

        resp = await _call(app, "post", "/api/issues/dismiss", json={"issue_key": key})

        assert resp.status_code == 200
        assert resp.json()["data"] == {"issue_key": key, "dismissed": True}
        # The dismissal is written to the persistent ack table (not localStorage).
        insert_query = pool.execute.await_args.args[0]
        assert "INSERT INTO public.dismissed_issues" in insert_query
        assert "ON CONFLICT (issue_key) DO UPDATE" in insert_query
        assert pool.execute.await_args.args[1] == key

    async def test_dismiss_persists_last_seen_at_watermark(self) -> None:
        """Acknowledge-until-recurrence (bu-86c4c.15): the ack call stores the
        issue's last_seen_at so a later recurrence can be detected."""
        app, pool = _build_app()
        key = compute_issue_key("unreachable", "general")
        watermark = "2026-07-01T12:00:00Z"

        resp = await _call(
            app,
            "post",
            "/api/issues/dismiss",
            json={"issue_key": key, "last_seen_at": watermark},
        )

        assert resp.status_code == 200
        insert_query = pool.execute.await_args.args[0]
        assert "last_seen_at" in insert_query
        assert pool.execute.await_args.args[3] is not None

    async def test_dismiss_without_last_seen_at_stores_none(self) -> None:
        app, pool = _build_app()
        key = compute_issue_key("unreachable", "general")

        resp = await _call(app, "post", "/api/issues/dismiss", json={"issue_key": key})

        assert resp.status_code == 200
        assert pool.execute.await_args.args[3] is None

    async def test_dismiss_requires_issue_key(self) -> None:
        app, _ = _build_app()
        resp = await _call(app, "post", "/api/issues/dismiss", json={"issue_key": "   "})
        assert resp.status_code == 422

    async def test_dismiss_missing_field_is_422(self) -> None:
        app, _ = _build_app()
        resp = await _call(app, "post", "/api/issues/dismiss", json={})
        assert resp.status_code == 422


class TestUndismissIssue:
    async def test_undismiss_deletes_row(self) -> None:
        app, pool = _build_app(execute_result="DELETE 1")
        key = compute_issue_key("unreachable", "general")

        resp = await _call(app, "delete", f"/api/issues/dismiss/{key}")

        assert resp.status_code == 200
        assert resp.json()["data"] == {"issue_key": key, "deleted": True}
        delete_query = pool.execute.await_args.args[0]
        assert "DELETE FROM public.dismissed_issues" in delete_query

    async def test_undismiss_unknown_key_is_404(self) -> None:
        app, _ = _build_app(execute_result="DELETE 0")
        resp = await _call(app, "delete", "/api/issues/dismiss/nope::general")
        assert resp.status_code == 404


class TestListFiltersDismissed:
    async def test_dismissed_issue_excluded_from_feed(self) -> None:
        """A dismissed audit issue must not appear in GET /api/issues."""
        audit_row = {
            "error_summary": "boom",
            "first_seen_at": None,
            "last_seen_at": None,
            "occurrences": 3,
            "butlers": ["general"],
            "has_schedule": False,
            "schedule_names": [],
        }

        # The router issues two pool.fetch() calls on the same pool: one for the
        # audit-group CTE, one for dismissed keys. Branch on the query text.
        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "dismissed_issues" in query:
                # Dismiss the audit_error_group key for this error.
                key = audit_group_key("boom")
                return [{"issue_key": key, "last_seen_at": None}]
            return [audit_row]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        resp = await _call(app, "get", "/api/issues")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestListIncludeDismissed:
    async def test_include_dismissed_returns_only_dismissed_issues(self) -> None:
        """GET /api/issues?include_dismissed=true surfaces only dismissed issues."""
        audit_row = {
            "error_summary": "boom",
            "first_seen_at": None,
            "last_seen_at": None,
            "occurrences": 3,
            "butlers": ["general"],
            "has_schedule": False,
            "schedule_names": [],
        }
        dismissed_key = audit_group_key("boom")

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "dismissed_issues" in query:
                return [{"issue_key": dismissed_key, "last_seen_at": None}]
            return [audit_row]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        resp = await _call(app, "get", "/api/issues?include_dismissed=true")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["issue_key"] == dismissed_key
        # The dismissed flag lets the UI render a restore affordance.
        assert data[0]["dismissed"] is True

    async def test_include_dismissed_empty_when_nothing_dismissed(self) -> None:
        """With no dismissals, the dismissed view is empty even if issues exist."""
        audit_row = {
            "error_summary": "boom",
            "first_seen_at": None,
            "last_seen_at": None,
            "occurrences": 3,
            "butlers": ["general"],
            "has_schedule": False,
            "schedule_names": [],
        }

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "dismissed_issues" in query:
                return []
            return [audit_row]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        resp = await _call(app, "get", "/api/issues?include_dismissed=true")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestAcknowledgeUntilRecurrence:
    """Acknowledge-until-recurrence (JARVIS audit move 6, bu-86c4c.15):

    An ack recorded with a ``last_seen_at`` watermark should only hold while
    the issue group hasn't recurred since. A later occurrence (a newer
    ``last_seen_at`` on the same group) must un-ack it automatically.
    """

    def _audit_row(self, *, last_seen_at: str | None) -> dict[str, Any]:
        return {
            "error_summary": "boom",
            "first_seen_at": None,
            "last_seen_at": last_seen_at,
            "occurrences": 3,
            "butlers": ["general"],
            "has_schedule": False,
            "schedule_names": [],
        }

    async def test_recurred_issue_reappears_in_active_feed(self) -> None:
        """last_seen_at newer than the ack watermark => back in the active feed."""
        key = audit_group_key("boom")

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "dismissed_issues" in query:
                # Acked while last_seen_at was 12:00; the group has since
                # recurred at 13:00 (see the audit_row below).
                return [
                    {
                        "issue_key": key,
                        "last_seen_at": datetime.fromisoformat("2026-07-01T12:00:00+00:00"),
                    }
                ]
            return [
                self._audit_row(last_seen_at=datetime.fromisoformat("2026-07-01T13:00:00+00:00"))
            ]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        active_resp = await _call(app, "get", "/api/issues")
        assert active_resp.status_code == 200
        active_data = active_resp.json()["data"]
        assert len(active_data) == 1
        assert active_data[0]["issue_key"] == key
        assert active_data[0]["dismissed"] is False

        dismissed_resp = await _call(app, "get", "/api/issues?include_dismissed=true")
        assert dismissed_resp.status_code == 200
        assert dismissed_resp.json()["data"] == []

    async def test_not_recurred_issue_stays_acked(self) -> None:
        """last_seen_at unchanged (or older) since ack => still hidden from the active feed."""
        key = audit_group_key("boom")

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "dismissed_issues" in query:
                return [
                    {
                        "issue_key": key,
                        "last_seen_at": datetime.fromisoformat("2026-07-01T12:00:00+00:00"),
                    }
                ]
            return [
                self._audit_row(last_seen_at=datetime.fromisoformat("2026-07-01T12:00:00+00:00"))
            ]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        active_resp = await _call(app, "get", "/api/issues")
        assert active_resp.status_code == 200
        assert active_resp.json()["data"] == []

        dismissed_resp = await _call(app, "get", "/api/issues?include_dismissed=true")
        assert dismissed_resp.status_code == 200
        dismissed_data = dismissed_resp.json()["data"]
        assert len(dismissed_data) == 1
        assert dismissed_data[0]["issue_key"] == key
        assert dismissed_data[0]["dismissed"] is True

    async def test_legacy_ack_with_no_watermark_stays_dismissed_forever(self) -> None:
        """A NULL watermark (legacy ack) preserves the old dismiss-forever behavior."""
        key = audit_group_key("boom")

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "dismissed_issues" in query:
                return [{"issue_key": key, "last_seen_at": None}]
            return [
                self._audit_row(last_seen_at=datetime.fromisoformat("2026-07-01T13:00:00+00:00"))
            ]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        active_resp = await _call(app, "get", "/api/issues")
        assert active_resp.status_code == 200
        assert active_resp.json()["data"] == []


class TestListIssueOccurrences:
    """GET /api/issues/{issue_key}/occurrences (JARVIS audit move 6).

    The endpoint re-derives the group's grouping parameters from a fresh
    ``build_audit_group_query()`` call (branch on query text: no
    ``normalized_errors`` -- err, both queries share that CTE text, so we
    branch on the tell-tale ``grouped_errors`` alias instead, which only the
    grouped query selects from).
    """

    def _audit_row(self, **overrides: Any) -> dict[str, Any]:
        return {
            "error_summary": "OAuth token expired",
            "first_seen_at": None,
            "last_seen_at": None,
            "occurrences": 3,
            "butlers": ["calendar"],
            "has_schedule": False,
            "schedule_names": [],
            **overrides,
        }

    def _occurrence_row(self, **overrides: Any) -> dict[str, Any]:
        return {
            "id": 1,
            "ts": datetime.fromisoformat("2026-07-01T12:00:00+00:00"),
            "actor": "calendar",
            "action": "oauth_refresh",
            "target": None,
            "note": None,
            "ip": None,
            "request_id": None,
            "metadata": None,
            "result": "error",
            "error": "OAuth token expired",
            **overrides,
        }

    async def test_found_group_returns_its_occurrences(self) -> None:
        key = audit_group_key("OAuth token expired")
        occurrence = self._occurrence_row()

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "grouped_errors" in query:
                return [self._audit_row()]
            return [occurrence]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        resp = await _call(app, "get", f"/api/issues/{key}/occurrences")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["actor"] == "calendar"
        assert body["data"][0]["action"] == "oauth_refresh"
        assert body["meta"]["total"] == 3

        # The occurrences query binds (error_summary, butlers, limit, offset) --
        # NOT is_schedule: a group's identity is error_summary ALONE
        # (has_schedule is just a BOOL_OR aggregate over it), so filtering
        # occurrences on that aggregate would silently drop rows on the other
        # side of the flag for a group that straddles both.
        occ_call = [
            c for c in mock_pool.fetch.await_args_list if "grouped_errors" not in c.args[0]
        ][0]
        assert occ_call.args[1] == "OAuth token expired"
        assert occ_call.args[2] == ["calendar"]

    async def test_unknown_key_returns_404(self) -> None:
        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            return [self._audit_row()]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        resp = await _call(app, "get", "/api/issues/nope::nowhere/occurrences")
        assert resp.status_code == 404

    async def test_multi_butler_group_restricts_to_its_butlers(self) -> None:
        key = audit_group_key("OAuth token expired")

        async def fetch_side_effect(query: str, *args: Any) -> list[Any]:
            if "grouped_errors" in query:
                return [self._audit_row(butlers=["calendar", "health"])]
            return [self._occurrence_row()]

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db
        app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
        app.dependency_overrides[get_butler_configs] = lambda: []

        resp = await _call(app, "get", f"/api/issues/{key}/occurrences")
        assert resp.status_code == 200

        occ_call = [
            c for c in mock_pool.fetch.await_args_list if "grouped_errors" not in c.args[0]
        ][0]
        assert occ_call.args[2] == ["calendar", "health"]

    async def test_empty_issue_key_is_422(self) -> None:
        app, _ = _build_app()
        resp = await _call(app, "get", "/api/issues/%20/occurrences")
        assert resp.status_code == 422


class TestIssueKeyComputation:
    def test_audit_group_key_uses_type(self) -> None:
        assert compute_issue_key("audit_error_group:foo", "general") == (
            "audit_error_group:foo::general"
        )

    def test_reachability_key_includes_butler(self) -> None:
        k1 = compute_issue_key("unreachable", "general")
        k2 = compute_issue_key("unreachable", "health")
        assert k1 != k2
