"""Tests for QA dashboard API routes.

Covers:
- GET /api/qa/summary                           — staffer status, last/next patrol, stats
- GET /api/qa/patrols                           — paginated patrol list
- GET /api/qa/patrols/{patrolId}                — full patrol with nested findings
- GET /api/qa/patrols/{patrolId}/findings       — findings for a patrol
- GET /api/qa/known-issues                      — known issue tracker
- POST /api/qa/known-issues/{fp}/dismiss        — dismiss a known issue
- DELETE /api/qa/known-issues/{fp}/dismiss      — un-dismiss a known issue
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
from butlers.api.routers.qa import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)


def _make_patrol_row(
    *,
    patrol_id: uuid.UUID | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    status: str = "clean",
    findings_count: int = 0,
    novel_count: int = 0,
    dispatched_count: int = 0,
    log_lookback_minutes: int = 15,
    sources_polled: list[str] | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    """Build a fake qa_patrols row dict."""
    return {
        "id": patrol_id or uuid.uuid4(),
        "started_at": started_at or _NOW,
        "completed_at": completed_at or _NOW,
        "status": status,
        "findings_count": findings_count,
        "novel_count": novel_count,
        "dispatched_count": dispatched_count,
        "log_lookback_minutes": log_lookback_minutes,
        "sources_polled": sources_polled or ["log_scanner"],
        "error_detail": error_detail,
    }


def _make_finding_row(
    *,
    finding_id: uuid.UUID | None = None,
    patrol_id: uuid.UUID | None = None,
    fingerprint: str = "a" * 64,
    source_type: str = "log_scanner",
    source_butler: str = "general",
    severity: int = 2,
    exception_type: str = "KeyError",
    event_summary: str = "missing key",
    call_site: str = "src/foo.py:bar",
    occurrence_count: int = 1,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    dedup_reason: str | None = None,
    healing_attempt_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a fake qa_findings row dict."""
    return {
        "id": finding_id or uuid.uuid4(),
        "patrol_id": patrol_id or uuid.uuid4(),
        "fingerprint": fingerprint,
        "source_type": source_type,
        "source_butler": source_butler,
        "severity": severity,
        "exception_type": exception_type,
        "event_summary": event_summary,
        "call_site": call_site,
        "occurrence_count": occurrence_count,
        "first_seen": first_seen or _NOW,
        "last_seen": last_seen or _NOW,
        "dedup_reason": dedup_reason,
        "healing_attempt_id": healing_attempt_id,
        "created_at": created_at or _NOW,
    }


def _make_dismissal_row(
    *,
    fingerprint: str = "a" * 64,
    dismissed_until: datetime | None = None,
    dismissed_by: str = "dashboard_user",
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a fake qa_dismissals row dict."""
    return {
        "fingerprint": fingerprint,
        "dismissed_until": dismissed_until or datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
        "dismissed_by": dismissed_by,
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
    execute_result: str = "DELETE 1",
    # Allow per-call customization via side_effect
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
# GET /api/qa/summary
# ---------------------------------------------------------------------------


class TestGetQaSummary:
    async def test_returns_summary_with_empty_db(self) -> None:
        """When no patrols exist, summary should return zeros and no last_patrol."""
        empty_stats_row = _make_stats_row()
        app, _ = _build_app(
            fetchrow_side_effect=[
                None,  # last patrol
                _mock_record(empty_stats_row),  # 24h stats
                _mock_record(empty_stats_row),  # all-time stats
            ],
            fetch_rows=[],  # active sources
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["last_patrol"] is None
        assert body["data"]["stats_24h"]["patrols_completed"] == 0
        assert body["data"]["stats_all_time"]["total_patrols"] == 0
        assert body["data"]["active_sources"] == []

    async def test_returns_last_patrol_when_present(self) -> None:
        """When patrols exist, last_patrol should be populated."""
        patrol_id = uuid.uuid4()
        patrol = _make_patrol_row(patrol_id=patrol_id, status="clean", findings_count=3)
        stats = _make_stats_row(patrols_completed=5, total_findings=10)
        app, _ = _build_app(
            fetchrow_side_effect=[
                _mock_record(patrol),
                _mock_record(stats),
                _mock_record(stats),
            ],
            fetch_rows=[_mock_record({"sources_polled": ["log_scanner", "session_records"]})],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["last_patrol"]["id"] == str(patrol_id)
        assert body["data"]["last_patrol"]["status"] == "clean"
        assert body["data"]["last_patrol"]["findings_count"] == 3
        assert body["data"]["stats_24h"]["patrols_completed"] == 5
        assert "log_scanner" in body["data"]["active_sources"]

    async def test_returns_503_when_db_unavailable(self) -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no pool")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 503


def _make_stats_row(
    patrols_completed: int = 0,
    total_findings: int = 0,
    novel_findings: int = 0,
    dispatched_investigations: int = 0,
) -> dict[str, Any]:
    return {
        "patrols_completed": patrols_completed,
        "total_findings": total_findings,
        "novel_findings": novel_findings,
        "dispatched_investigations": dispatched_investigations,
        "total_patrols": patrols_completed,
    }


# ---------------------------------------------------------------------------
# GET /api/qa/patrols
# ---------------------------------------------------------------------------


class TestListPatrols:
    async def test_returns_empty_list_when_no_patrols(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
        assert body["meta"]["limit"] == 20

    async def test_returns_patrols_with_pagination(self) -> None:
        patrol_id = uuid.uuid4()
        row = _make_patrol_row(patrol_id=patrol_id, status="findings_dispatched", findings_count=5)
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["id"] == str(patrol_id)
        assert body["data"][0]["status"] == "findings_dispatched"
        assert body["data"][0]["findings_count"] == 5
        assert body["meta"]["total"] == 1

    async def test_accepts_valid_status_filter(self) -> None:
        row = _make_patrol_row(status="clean")
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols", params={"status": "clean"})

        assert response.status_code == 200
        assert response.json()["data"][0]["status"] == "clean"

    async def test_rejects_invalid_status_filter(self) -> None:
        app, _ = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols", params={"status": "not_valid"})

        assert response.status_code == 422
        assert "not_valid" in response.json()["detail"]

    async def test_pagination_parameters_accepted(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols", params={"limit": 5, "offset": 10})

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["limit"] == 5
        assert body["meta"]["offset"] == 10
        assert body["meta"]["total"] == 100

    async def test_has_more_computed_correctly(self) -> None:
        rows = [_make_patrol_row() for _ in range(10)]
        app, _ = _build_app(fetch_rows=rows, fetchval_result=50)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols", params={"limit": 10, "offset": 0})

        assert response.status_code == 200
        assert response.json()["meta"]["has_more"] is True


# ---------------------------------------------------------------------------
# GET /api/qa/patrols/{patrolId}
# ---------------------------------------------------------------------------


class TestGetPatrol:
    async def test_returns_404_for_missing_patrol(self) -> None:
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/qa/patrols/{uuid.uuid4()}")

        assert response.status_code == 404

    async def test_returns_patrol_with_nested_findings(self) -> None:
        patrol_id = uuid.uuid4()
        patrol = _make_patrol_row(patrol_id=patrol_id, findings_count=2)
        finding1 = _make_finding_row(patrol_id=patrol_id, fingerprint="a" * 64)
        finding2 = _make_finding_row(
            patrol_id=patrol_id, fingerprint="b" * 64, dedup_reason="active_attempt"
        )
        app, _ = _build_app(
            fetchrow_result=patrol,
            fetch_rows=[finding1, finding2],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/qa/patrols/{patrol_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["id"] == str(patrol_id)
        assert len(body["data"]["findings"]) == 2
        fp_list = [f["fingerprint"] for f in body["data"]["findings"]]
        assert "a" * 64 in fp_list
        assert "b" * 64 in fp_list

    async def test_returns_patrol_with_empty_findings(self) -> None:
        patrol_id = uuid.uuid4()
        patrol = _make_patrol_row(patrol_id=patrol_id, findings_count=0)
        app, _ = _build_app(fetchrow_result=patrol, fetch_rows=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/qa/patrols/{patrol_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["findings"] == []

    async def test_rejects_invalid_uuid(self) -> None:
        app, _ = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/patrols/not-a-uuid")

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/qa/patrols/{patrolId}/findings
# ---------------------------------------------------------------------------


class TestListPatrolFindings:
    async def test_returns_404_for_unknown_patrol(self) -> None:
        app, _ = _build_app(fetchval_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/qa/patrols/{uuid.uuid4()}/findings")

        assert response.status_code == 404

    async def test_returns_findings_for_valid_patrol(self) -> None:
        patrol_id = uuid.uuid4()
        finding = _make_finding_row(patrol_id=patrol_id, fingerprint="c" * 64)
        app, _ = _build_app(
            fetchval_result=1,  # patrol exists
            fetch_side_effect=[
                [_mock_record(finding)],  # first fetch: findings list (for count)
                [_mock_record(finding)],  # second fetch: paginated findings
            ],
            fetchval_side_effect=[
                1,  # patrol existence check
                1,  # total count
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(f"/api/qa/patrols/{patrol_id}/findings")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["fingerprint"] == "c" * 64

    async def test_novel_only_filter_accepted(self) -> None:
        patrol_id = uuid.uuid4()
        finding = _make_finding_row(patrol_id=patrol_id, dedup_reason=None)
        app, _ = _build_app(
            fetchval_side_effect=[1, 1],
            fetch_side_effect=[[_mock_record(finding)]],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/qa/patrols/{patrol_id}/findings", params={"novel_only": "true"}
            )

        assert response.status_code == 200

    async def test_pagination_parameters_accepted(self) -> None:
        patrol_id = uuid.uuid4()
        app, _ = _build_app(
            fetchval_side_effect=[1, 0],
            fetch_side_effect=[[]],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/qa/patrols/{patrol_id}/findings",
                params={"offset": 20, "limit": 10},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["offset"] == 20
        assert body["meta"]["limit"] == 10


# ---------------------------------------------------------------------------
# GET /api/qa/known-issues
# ---------------------------------------------------------------------------


class TestListKnownIssues:
    async def test_returns_empty_list_when_no_issues(self) -> None:
        app, _ = _build_app(
            fetchval_result=0,
            fetch_side_effect=[
                [],  # aggregation rows
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_known_issues_with_aggregated_stats(self) -> None:
        fp = "d" * 64
        agg_row: dict[str, Any] = {
            "fingerprint": fp,
            "source_butler": "finance",
            "source_type": "log_scanner",
            "severity": 1,
            "exception_type": "ValueError",
            "event_summary": "bad value",
            "call_site": "src/finance.py:compute",
            "occurrence_count": 7,
            "first_seen": _NOW,
            "last_seen": _NOW,
            "patrol_count": 3,
            "healing_attempt_id": None,
        }
        app, _ = _build_app(
            fetchval_result=1,
            fetch_side_effect=[
                [_mock_record(agg_row)],  # aggregation rows
                [],  # dismissals batch fetch
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        issue = body["data"][0]
        assert issue["fingerprint"] == fp
        assert issue["occurrence_count"] == 7
        assert issue["patrol_count"] == 3
        assert issue["dismissal"] is None

    async def test_known_issue_includes_dismissal_when_present(self) -> None:
        fp = "e" * 64
        agg_row: dict[str, Any] = {
            "fingerprint": fp,
            "source_butler": "general",
            "source_type": "session_records",
            "severity": 2,
            "exception_type": "TimeoutError",
            "event_summary": "timed out",
            "call_site": "src/bar.py:fetch",
            "occurrence_count": 2,
            "first_seen": _NOW,
            "last_seen": _NOW,
            "patrol_count": 1,
            "healing_attempt_id": None,
        }
        dismissal = _make_dismissal_row(fingerprint=fp)
        app, _ = _build_app(
            fetchval_result=1,
            fetch_side_effect=[
                [_mock_record(agg_row)],
                [_mock_record(dismissal)],
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues")

        assert response.status_code == 200
        body = response.json()
        assert body["data"][0]["dismissal"] is not None
        assert body["data"][0]["dismissal"]["fingerprint"] == fp
        assert body["data"][0]["dismissal"]["dismissed_by"] == "dashboard_user"

    async def test_source_butler_filter_accepted(self) -> None:
        """source_butler filter must be forwarded to the DB and reflected in results."""
        fp = "l" * 64
        agg_row: dict[str, Any] = {
            "fingerprint": fp,
            "source_butler": "finance",
            "source_type": "log_scanner",
            "severity": 2,
            "exception_type": "ValueError",
            "event_summary": "bad",
            "call_site": "src/x.py:y",
            "occurrence_count": 3,
            "first_seen": _NOW,
            "last_seen": _NOW,
            "patrol_count": 1,
            "healing_attempt_id": None,
        }
        app, mock_pool = _build_app(
            fetchval_result=1,
            fetch_side_effect=[
                [_mock_record(agg_row)],
                [],  # dismissals
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues", params={"source_butler": "finance"})

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["source_butler"] == "finance"
        # Verify the DB was called with "finance" as a parameter
        call_args = mock_pool.fetchval.call_args
        assert "finance" in call_args.args or "finance" in str(call_args)

    async def test_severity_filter_accepted(self) -> None:
        """severity filter must be forwarded to the DB and reflected in results."""
        fp = "m" * 64
        agg_row: dict[str, Any] = {
            "fingerprint": fp,
            "source_butler": "general",
            "source_type": "log_scanner",
            "severity": 1,
            "exception_type": "TypeError",
            "event_summary": "type mismatch",
            "call_site": "src/a.py:b",
            "occurrence_count": 2,
            "first_seen": _NOW,
            "last_seen": _NOW,
            "patrol_count": 1,
            "healing_attempt_id": None,
        }
        app, mock_pool = _build_app(
            fetchval_result=1,
            fetch_side_effect=[
                [_mock_record(agg_row)],
                [],  # dismissals
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues", params={"severity": 1})

        assert response.status_code == 200
        body = response.json()
        assert body["data"][0]["severity"] == 1
        # Verify the DB was called with severity 1 as a parameter
        call_args = mock_pool.fetchval.call_args
        assert 1 in call_args.args or 1 in str(call_args)

    async def test_dismissed_true_filter_accepted(self) -> None:
        """dismissed=true filter should return only dismissed issues."""
        fp = "n" * 64
        agg_row: dict[str, Any] = {
            "fingerprint": fp,
            "source_butler": "general",
            "source_type": "log_scanner",
            "severity": 2,
            "exception_type": "RuntimeError",
            "event_summary": "runtime error",
            "call_site": "src/c.py:d",
            "occurrence_count": 4,
            "first_seen": _NOW,
            "last_seen": _NOW,
            "patrol_count": 1,
            "healing_attempt_id": None,
        }
        dismissal = _make_dismissal_row(fingerprint=fp)
        app, _ = _build_app(
            fetchval_result=1,
            fetch_side_effect=[
                [_mock_record(agg_row)],
                [_mock_record(dismissal)],
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues", params={"dismissed": "true"})

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["dismissal"] is not None

    async def test_dismissed_false_filter_accepted(self) -> None:
        """dismissed=false filter should return only active (non-dismissed) issues."""
        fp = "o" * 64
        agg_row: dict[str, Any] = {
            "fingerprint": fp,
            "source_butler": "general",
            "source_type": "log_scanner",
            "severity": 3,
            "exception_type": "OSError",
            "event_summary": "file not found",
            "call_site": "src/e.py:f",
            "occurrence_count": 1,
            "first_seen": _NOW,
            "last_seen": _NOW,
            "patrol_count": 1,
            "healing_attempt_id": None,
        }
        app, _ = _build_app(
            fetchval_result=1,
            fetch_side_effect=[
                [_mock_record(agg_row)],
                [],  # no dismissals
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues", params={"dismissed": "false"})

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["dismissal"] is None

    async def test_pagination_meta_reflects_total(self) -> None:
        """meta.total must reflect the count query, not just the page size."""
        app, _ = _build_app(
            fetchval_result=42,
            fetch_side_effect=[[], []],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/known-issues", params={"limit": 10, "offset": 0})

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["total"] == 42
        assert body["meta"]["limit"] == 10


# ---------------------------------------------------------------------------
# POST /api/qa/known-issues/{fingerprint}/dismiss
# ---------------------------------------------------------------------------


class TestDismissKnownIssue:
    async def test_creates_dismissal_with_indefinite_expiry(self) -> None:
        fp = "f" * 64
        # Mock returns what the DB would persist — use the provided dismissed_by
        dismissal = _make_dismissal_row(fingerprint=fp, dismissed_by="owner")
        app, _ = _build_app(fetchrow_result=dismissal)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/qa/known-issues/{fp}/dismiss",
                json={"dismissed_by": "owner"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["fingerprint"] == fp
        assert body["data"]["dismissed_by"] == "owner"

    async def test_creates_dismissal_with_explicit_expiry(self) -> None:
        fp = "g" * 64
        expiry = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
        dismissal = _make_dismissal_row(fingerprint=fp, dismissed_until=expiry)
        app, _ = _build_app(fetchrow_result=dismissal)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/qa/known-issues/{fp}/dismiss",
                json={"dismissed_until": expiry.isoformat(), "dismissed_by": "owner"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["fingerprint"] == fp

    async def test_returns_500_when_insert_fails(self) -> None:
        fp = "h" * 64
        app, _ = _build_app(fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/qa/known-issues/{fp}/dismiss",
                json={},
            )

        assert response.status_code == 500

    async def test_empty_body_uses_defaults(self) -> None:
        fp = "i" * 64
        dismissal = _make_dismissal_row(fingerprint=fp)
        app, _ = _build_app(fetchrow_result=dismissal)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/qa/known-issues/{fp}/dismiss",
                json={},
            )

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# DELETE /api/qa/known-issues/{fingerprint}/dismiss
# ---------------------------------------------------------------------------


class TestUndismissKnownIssue:
    async def test_deletes_existing_dismissal(self) -> None:
        fp = "j" * 64
        app, _ = _build_app(execute_result="DELETE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/qa/known-issues/{fp}/dismiss")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["fingerprint"] == fp
        assert body["data"]["deleted"] is True

    async def test_returns_404_when_no_dismissal_exists(self) -> None:
        fp = "k" * 64
        app, _ = _build_app(execute_result="DELETE 0")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/qa/known-issues/{fp}/dismiss")

        assert response.status_code == 404

    async def test_returns_503_when_db_unavailable(self) -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no pool")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete("/api/qa/known-issues/abc/dismiss")

        assert response.status_code == 503
