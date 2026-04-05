"""Tests for QA dashboard API routes.

Covers:
- GET /api/qa/summary                           — staffer status, last/next patrol, stats
- GET /api/qa/patrols                           — paginated patrol list
- GET /api/qa/patrols/{patrolId}                — full patrol with nested findings
- GET /api/qa/patrols/{patrolId}/findings       — findings for a patrol
- GET /api/qa/investigations                    — paginated QA-originated healing attempts
- GET /api/qa/known-issues                      — known issue tracker
- POST /api/qa/known-issues/{fp}/dismiss        — dismiss a known issue
- DELETE /api/qa/known-issues/{fp}/dismiss      — un-dismiss a known issue
- POST /api/qa/force-patrol                     — trigger immediate patrol
- GET  /api/qa/trends                           — daily aggregated stats
- GET  /api/qa/dismissals                       — list active dismissals
- DELETE /api/qa/dismissals/{fingerprint}       — remove a dismissal
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
from butlers.api.routers.qa import _get_db_manager, _get_force_patrol_fn

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


def _make_investigation_row(
    *,
    attempt_id: uuid.UUID | None = None,
    fingerprint: str = "a" * 64,
    butler_name: str = "general",
    status: str = "investigating",
    severity: int = 2,
    exception_type: str = "KeyError",
    call_site: str = "src/foo.py:bar",
    sanitized_msg: str | None = "error msg",
    pr_url: str | None = None,
    pr_number: int | None = None,
    healing_session_id: uuid.UUID | None = None,
    qa_patrol_id: uuid.UUID | None = None,
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
        "pr_url": pr_url,
        "pr_number": pr_number,
        "healing_session_id": healing_session_id,
        "qa_patrol_id": qa_patrol_id or uuid.uuid4(),
        "created_at": created_at or _NOW,
        "updated_at": updated_at or _NOW,
        "closed_at": closed_at,
        "error_detail": error_detail,
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


def _make_pr_stats_row(
    prs_merged: int = 0,
    prs_failed: int = 0,
    total_dispatched: int = 0,
) -> dict[str, Any]:
    return {
        "prs_merged": prs_merged,
        "prs_failed": prs_failed,
        "total_dispatched": total_dispatched,
    }


def _build_summary_app(
    *,
    last_patrol: dict[str, Any] | None = None,
    stats_24h: dict[str, Any] | None = None,
    prs_opened_24h: int = 0,
    all_time_stats: dict[str, Any] | None = None,
    pr_stats: dict[str, Any] | None = None,
    cb_rows: list[dict[str, Any]] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
) -> tuple[Any, MagicMock]:
    """Build a test app with mocks wired to the summary endpoint's call sequence."""
    if stats_24h is None:
        stats_24h = _make_stats_row()
    if all_time_stats is None:
        all_time_stats = _make_stats_row()
    if pr_stats is None:
        pr_stats = _make_pr_stats_row()

    return _build_app(
        fetchrow_side_effect=[
            _mock_record(last_patrol) if last_patrol is not None else None,
            _mock_record(stats_24h),
            _mock_record(all_time_stats),
            _mock_record(pr_stats),
        ],
        fetchval_side_effect=[prs_opened_24h],
        fetch_side_effect=[
            [_mock_record(r) for r in (cb_rows or [])],  # circuit breaker rows
            [_mock_record(r) for r in (source_rows or [])],  # active sources
        ],
    )


# ---------------------------------------------------------------------------
# GET /api/qa/summary
# ---------------------------------------------------------------------------


class TestGetQaSummary:
    async def test_returns_summary_with_empty_db(self) -> None:
        """When no patrols exist, summary should return zeros and no last_patrol."""
        app, _ = _build_summary_app()

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
        app, _ = _build_summary_app(
            last_patrol=patrol,
            stats_24h=stats,
            all_time_stats=stats,
            source_rows=[{"sources_polled": ["log_scanner", "session_records"]}],
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

    async def test_returns_circuit_breaker_tripped_when_enough_failures(self) -> None:
        """Circuit breaker should trip when 5+ consecutive failures exist."""
        cb_rows = [{"status": "failed"} for _ in range(5)]
        app, _ = _build_summary_app(cb_rows=cb_rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["circuit_breaker"]["tripped"] is True
        assert body["data"]["circuit_breaker"]["consecutive_failures"] == 5
        assert body["data"]["staffer_status"] == "circuit_breaker_tripped"

    async def test_returns_circuit_breaker_not_tripped_after_success(self) -> None:
        """Circuit breaker should not trip when failures are interrupted by a success."""
        cb_rows = [
            {"status": "failed"},
            {"status": "failed"},
            {"status": "pr_merged"},  # resets the count
            {"status": "failed"},
        ]
        app, _ = _build_summary_app(cb_rows=cb_rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["circuit_breaker"]["tripped"] is False
        assert body["data"]["circuit_breaker"]["consecutive_failures"] == 2

    async def test_circuit_breaker_counts_anonymization_failed_as_failure(self) -> None:
        """anonymization_failed must count as a CB failure (aligns with dispatch.py)."""
        cb_rows = [
            {"status": "anonymization_failed"},
            {"status": "timeout"},
            {"status": "failed"},
            {"status": "anonymization_failed"},
            {"status": "failed"},
        ]
        app, _ = _build_summary_app(cb_rows=cb_rows)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["circuit_breaker"]["tripped"] is True
        assert body["data"]["circuit_breaker"]["consecutive_failures"] == 5

    async def test_summary_includes_prs_opened_24h(self) -> None:
        """stats_24h.prs_opened should reflect the fetchval result."""
        app, _ = _build_summary_app(prs_opened_24h=3)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        assert response.json()["data"]["stats_24h"]["prs_opened"] == 3

    async def test_summary_includes_all_time_pr_stats(self) -> None:
        """stats_all_time should include prs_merged, prs_failed, success_rate."""
        pr_stats = _make_pr_stats_row(prs_merged=10, prs_failed=2, total_dispatched=20)
        app, _ = _build_summary_app(pr_stats=pr_stats)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/summary")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["stats_all_time"]["prs_merged"] == 10
        assert data["stats_all_time"]["prs_failed"] == 2
        assert data["stats_all_time"]["success_rate"] == 0.5

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
# GET /api/qa/investigations
# ---------------------------------------------------------------------------


class TestListInvestigations:
    async def test_returns_empty_list_when_no_investigations(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/investigations")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_investigations_with_pr_info(self) -> None:
        attempt_id = uuid.uuid4()
        patrol_id = uuid.uuid4()
        row = _make_investigation_row(
            attempt_id=attempt_id,
            qa_patrol_id=patrol_id,
            status="pr_open",
            pr_url="https://github.com/foo/bar/pull/42",
            pr_number=42,
        )
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/investigations")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        inv = body["data"][0]
        assert inv["id"] == str(attempt_id)
        assert inv["status"] == "pr_open"
        assert inv["pr_url"] == "https://github.com/foo/bar/pull/42"
        assert inv["pr_number"] == 42
        assert inv["qa_patrol_id"] == str(patrol_id)
        assert body["meta"]["total"] == 1

    async def test_accepts_valid_status_filter(self) -> None:
        row = _make_investigation_row(status="pr_merged")
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/investigations", params={"status": "pr_merged"})

        assert response.status_code == 200
        assert response.json()["data"][0]["status"] == "pr_merged"

    async def test_accepts_anonymization_failed_status_filter(self) -> None:
        """anonymization_failed must be a valid filter value (it is in VALID_STATUSES)."""
        row = _make_investigation_row(status="anonymization_failed")
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/qa/investigations", params={"status": "anonymization_failed"}
            )

        assert response.status_code == 200
        assert response.json()["data"][0]["status"] == "anonymization_failed"

    async def test_rejects_invalid_status_filter(self) -> None:
        app, _ = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/investigations", params={"status": "not_a_status"})

        assert response.status_code == 422
        assert "not_a_status" in response.json()["detail"]

    async def test_pagination_parameters_accepted(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=100)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/investigations", params={"limit": 5, "offset": 10})

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["limit"] == 5
        assert body["meta"]["offset"] == 10
        assert body["meta"]["total"] == 100

    async def test_returns_503_when_db_unavailable(self) -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no pool")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/investigations")

        assert response.status_code == 503


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


# ---------------------------------------------------------------------------
# POST /api/qa/force-patrol
# ---------------------------------------------------------------------------


class TestForcePatrol:
    async def test_returns_202_not_accepted_in_standalone_mode(self) -> None:
        """Without an in-process force_patrol_fn, returns 202 with accepted=False."""
        app, _ = _build_app()

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/qa/force-patrol")

        assert response.status_code == 202
        body = response.json()
        assert body["data"]["accepted"] is False
        assert "message" in body["data"]

    async def test_calls_force_patrol_fn_when_provided(self) -> None:
        """When an in-process callable is provided, it should be called."""
        patrol_result = {
            "status": "findings_dispatched",
            "patrol_id": str(uuid.uuid4()),
            "findings_count": 3,
            "novel_count": 1,
            "dispatched_count": 1,
            "sources_polled": ["log_scanner"],
        }

        async def _fake_force_patrol() -> dict:
            return patrol_result

        app, _ = _build_app()
        app.dependency_overrides[_get_force_patrol_fn] = lambda: _fake_force_patrol

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/qa/force-patrol")

        assert response.status_code == 202
        body = response.json()
        assert body["data"]["accepted"] is True
        assert "findings_dispatched" in body["data"]["message"]

    async def test_skipped_patrol_returns_accepted_false(self) -> None:
        """When the callable returns status=skipped, accepted should be False."""

        async def _skipped() -> dict:
            return {"status": "skipped", "reason": "patrol_already_running"}

        app, _ = _build_app()
        app.dependency_overrides[_get_force_patrol_fn] = lambda: _skipped

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/qa/force-patrol")

        assert response.status_code == 202
        body = response.json()
        assert body["data"]["accepted"] is False

    async def test_returns_503_when_force_patrol_fn_raises(self) -> None:
        """When the callable raises, the endpoint should return 503."""

        async def _failing() -> dict:
            raise RuntimeError("daemon not available")

        app, _ = _build_app()
        app.dependency_overrides[_get_force_patrol_fn] = lambda: _failing

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/api/qa/force-patrol")

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/qa/trends
# ---------------------------------------------------------------------------


def _make_trend_row(
    *,
    date: str = "2026-04-05",
    patrols_completed: int = 5,
    total_findings: int = 10,
    novel_findings: int = 3,
    dispatched_count: int = 2,
    clean_count: int = 4,
) -> dict[str, Any]:
    return {
        "date": date,
        "patrols_completed": patrols_completed,
        "total_findings": total_findings,
        "novel_findings": novel_findings,
        "dispatched_count": dispatched_count,
        "clean_count": clean_count,
    }


def _make_source_row(
    *,
    source_type: str = "log_scanner",
    count: int = 7,
) -> dict[str, Any]:
    return {"source_type": source_type, "count": count}


class TestGetQaTrends:
    async def test_returns_empty_trends_when_no_data(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetch_side_effect=[[], []])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/trends")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["days"] == []
        assert body["data"]["source_breakdown"] == []

    async def test_returns_trend_days_with_success_rate(self) -> None:
        trend_row = _make_trend_row(
            date="2026-04-05",
            patrols_completed=4,
            clean_count=3,
        )
        app, _ = _build_app(
            fetch_side_effect=[
                [_mock_record(trend_row)],
                [],  # source breakdown
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/trends")

        assert response.status_code == 200
        body = response.json()
        days = body["data"]["days"]
        assert len(days) == 1
        assert days[0]["date"] == "2026-04-05"
        assert days[0]["patrols_completed"] == 4
        assert days[0]["success_rate"] == pytest.approx(0.75, abs=0.001)

    async def test_returns_source_breakdown(self) -> None:
        source_row = _make_source_row(source_type="log_scanner", count=12)
        app, _ = _build_app(
            fetch_side_effect=[
                [],  # trend days
                [_mock_record(source_row)],
            ],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/trends")

        assert response.status_code == 200
        body = response.json()
        breakdown = body["data"]["source_breakdown"]
        assert len(breakdown) == 1
        assert breakdown[0]["source_type"] == "log_scanner"
        assert breakdown[0]["count"] == 12

    async def test_success_rate_is_zero_when_no_completed_patrols(self) -> None:
        trend_row = _make_trend_row(patrols_completed=0, clean_count=0)
        app, _ = _build_app(
            fetch_side_effect=[[_mock_record(trend_row)], []],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/trends")

        assert response.status_code == 200
        days = response.json()["data"]["days"]
        assert days[0]["success_rate"] == 0.0

    async def test_accepts_days_query_param(self) -> None:
        app, _ = _build_app(fetch_side_effect=[[], []])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/trends", params={"days": 14})

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/qa/dismissals
# ---------------------------------------------------------------------------


class TestListDismissals:
    async def test_returns_empty_list_when_no_dismissals(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=0)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/dismissals")

        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_active_dismissals(self) -> None:
        fp = "p" * 64
        dismissal = _make_dismissal_row(fingerprint=fp)
        app, _ = _build_app(fetch_rows=[dismissal], fetchval_result=1)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/dismissals")

        assert response.status_code == 200
        body = response.json()
        assert len(body["data"]) == 1
        assert body["data"][0]["fingerprint"] == fp
        assert body["meta"]["total"] == 1

    async def test_pagination_parameters_accepted(self) -> None:
        app, _ = _build_app(fetch_rows=[], fetchval_result=50)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/dismissals", params={"limit": 10, "offset": 5})

        assert response.status_code == 200
        body = response.json()
        assert body["meta"]["limit"] == 10
        assert body["meta"]["offset"] == 5
        assert body["meta"]["total"] == 50

    async def test_returns_503_when_db_unavailable(self) -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no pool")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/qa/dismissals")

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /api/qa/dismissals/{fingerprint}
# ---------------------------------------------------------------------------


class TestDeleteDismissal:
    async def test_deletes_existing_dismissal(self) -> None:
        fp = "q" * 64
        app, _ = _build_app(execute_result="DELETE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/qa/dismissals/{fp}")

        assert response.status_code == 200
        body = response.json()
        assert body["data"]["fingerprint"] == fp
        assert body["data"]["deleted"] is True

    async def test_returns_404_when_not_found(self) -> None:
        fp = "r" * 64
        app, _ = _build_app(execute_result="DELETE 0")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete(f"/api/qa/dismissals/{fp}")

        assert response.status_code == 404

    async def test_returns_503_when_db_unavailable(self) -> None:
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.credential_shared_pool.side_effect = KeyError("no pool")

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.delete("/api/qa/dismissals/abc")

        assert response.status_code == 503
