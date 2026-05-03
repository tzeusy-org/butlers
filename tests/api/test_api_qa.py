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
from butlers.api.routers.qa import _get_credentials_status_fn, _get_db_manager, _get_force_patrol_fn

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)


def _make_patrol_row(*, patrol_id: uuid.UUID | None = None, **overrides: Any) -> dict[str, Any]:
    return {
        "id": patrol_id or uuid.uuid4(),
        "status": "clean",
        "findings_count": 0,
        "novel_count": 0,
        "dispatched_count": 0,
        "started_at": _NOW,
        "completed_at": _NOW,
        "log_lookback_minutes": 15,
        "sources_polled": ["log_scanner"],
        "error_detail": None,
        **overrides,
    }


def _make_finding_row(*, patrol_id: uuid.UUID | None = None, **overrides: Any) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "patrol_id": patrol_id or uuid.uuid4(),
        "fingerprint": "a" * 64,
        "source_type": "log_scanner",
        "source_butler": "general",
        "severity": 2,
        "exception_type": "KeyError",
        "event_summary": "missing key",
        "call_site": "src/foo.py:bar",
        "occurrence_count": 1,
        "first_seen": _NOW,
        "last_seen": _NOW,
        "dedup_reason": None,
        "healing_attempt_id": None,
        "source_session_trigger_source": None,
        "structured_evidence": None,
        "created_at": _NOW,
        **overrides,
    }


def _make_dismissal_row(**overrides: Any) -> dict[str, Any]:
    return {
        "fingerprint": "a" * 64,
        "dismissed_until": datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC),
        "dismissed_by": "dashboard_user",
        "created_at": _NOW,
        **overrides,
    }


_INV_NONE_FIELDS = (
    "pr_url",
    "pr_number",
    "healing_session_id",
    "current_phase",
    "workflow_deadline_at",
    "closed_at",
    "error_detail",
    "review_state",
    "last_review_check_at",
    "review_feedback_summary",
    "follow_up_cycle_patrol_id",
    "last_follow_up_status",
    "last_follow_up_session_id",
    "last_follow_up_error",
    "last_follow_up_at",
)
_INVESTIGATION_DEFAULTS: dict[str, Any] = {
    "fingerprint": "a" * 64,
    "butler_name": "general",
    "status": "investigating",
    "severity": 2,
    "exception_type": "KeyError",
    "call_site": "src/foo.py:bar",
    "sanitized_msg": "error msg",
    "follow_up_count": 0,
    "follow_up_cycle_count": 0,
    **dict.fromkeys(_INV_NONE_FIELDS, None),
}


def _make_investigation_row(**overrides: Any) -> dict[str, Any]:
    row = {**_INVESTIGATION_DEFAULTS, **overrides}
    row.setdefault("id", uuid.uuid4())
    row.setdefault("qa_patrol_id", uuid.uuid4())
    row["created_at"] = _NOW
    row["updated_at"] = _NOW
    return row


class _MockRecord(dict):
    """A dict subclass that mimics asyncpg Record access patterns."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _r(row: dict[str, Any]) -> _MockRecord:
    return _MockRecord(row)


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = 0,
    execute_result: str = "DELETE 1",
    fetch_side_effect: Any = None,
    fetchrow_side_effect: Any = None,
    fetchval_side_effect: Any = None,
) -> tuple[Any, MagicMock]:
    """Build a test FastAPI app with a mocked database pool."""
    mock_pool = AsyncMock()
    mock_pool.fetch = (
        AsyncMock(side_effect=fetch_side_effect)
        if fetch_side_effect is not None
        else AsyncMock(return_value=[_r(row) for row in (fetch_rows or [])])
    )
    mock_pool.fetchrow = (
        AsyncMock(side_effect=fetchrow_side_effect)
        if fetchrow_side_effect is not None
        else AsyncMock(return_value=_r(fetchrow_result) if fetchrow_result else None)
    )
    mock_pool.fetchval = (
        AsyncMock(side_effect=fetchval_side_effect)
        if fetchval_side_effect is not None
        else AsyncMock(return_value=fetchval_result)
    )
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
    prs_merged: int = 0, prs_failed: int = 0, total_dispatched: int = 0
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
    return _build_app(
        fetchrow_side_effect=[
            _r(last_patrol) if last_patrol is not None else None,
            _r(stats_24h or _make_stats_row()),
            _r(all_time_stats or _make_stats_row()),
            _r(pr_stats or _make_pr_stats_row()),
        ],
        fetchval_side_effect=[prs_opened_24h],
        fetch_side_effect=[
            [_r(row) for row in (cb_rows or [])],
            [_r(row) for row in (source_rows or [])],
        ],
    )


def _make_503_app() -> Any:
    """Build an app that raises KeyError on pool access, exercising the 503 path."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.side_effect = KeyError("no pool")
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


async def _call(app: Any, method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Make a single HTTP call to the test app and return the response."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await getattr(client, method)(path, **kwargs)


class TestGetQaSummary:
    async def test_summary_shape_and_stats(self) -> None:
        """Empty DB → nulls/zeros; with patrol data → last_patrol + stats populated; PR stats included; 503 on DB failure."""
        # Empty DB
        app, _ = _build_summary_app()
        body = (await _call(app, "get", "/api/qa/summary")).json()
        assert body["data"]["last_patrol"] is None
        assert body["data"]["stats_24h"]["patrols_completed"] == 0
        assert body["data"]["active_sources"] == []

        # With patrol and stats
        patrol_id = uuid.uuid4()
        patrol = _make_patrol_row(patrol_id=patrol_id, status="clean", findings_count=3)
        stats = _make_stats_row(patrols_completed=5, total_findings=10)
        pr_stats = _make_pr_stats_row(prs_merged=10, prs_failed=2, total_dispatched=20)
        app2, _ = _build_summary_app(
            last_patrol=patrol,
            stats_24h=stats,
            all_time_stats=stats,
            prs_opened_24h=3,
            pr_stats=pr_stats,
            source_rows=[{"sources_polled": ["log_scanner"]}],
        )
        body2 = (await _call(app2, "get", "/api/qa/summary")).json()["data"]
        assert body2["last_patrol"]["id"] == str(patrol_id)
        assert body2["stats_24h"]["patrols_completed"] == 5
        assert body2["stats_24h"]["prs_opened"] == 3
        assert body2["stats_all_time"]["prs_merged"] == 10
        assert body2["stats_all_time"]["success_rate"] == 0.5
        assert "log_scanner" in body2["active_sources"]

        # 503 on DB failure
        assert (await _call(_make_503_app(), "get", "/api/qa/summary")).status_code == 503

    async def test_circuit_breaker_logic(self) -> None:
        """CB trips on 5 consecutive failures; resets on success; anonymization_failed counts."""
        # tripped
        app, _ = _build_summary_app(cb_rows=[{"status": "failed"} for _ in range(5)])
        body = (await _call(app, "get", "/api/qa/summary")).json()
        assert body["data"]["circuit_breaker"]["tripped"] is True
        assert body["data"]["circuit_breaker"]["consecutive_failures"] == 5
        assert body["data"]["staffer_status"] == "circuit_breaker_tripped"

        # success resets consecutive count
        app2, _ = _build_summary_app(
            cb_rows=[
                {"status": "failed"},
                {"status": "failed"},
                {"status": "pr_merged"},
                {"status": "failed"},
            ]
        )
        body2 = (await _call(app2, "get", "/api/qa/summary")).json()
        assert body2["data"]["circuit_breaker"]["tripped"] is False
        assert body2["data"]["circuit_breaker"]["consecutive_failures"] == 2

        # anonymization_failed counts as failure
        app3, _ = _build_summary_app(
            cb_rows=[
                {"status": "anonymization_failed"},
                {"status": "timeout"},
                {"status": "failed"},
                {"status": "anonymization_failed"},
                {"status": "failed"},
            ]
        )
        body3 = (await _call(app3, "get", "/api/qa/summary")).json()
        assert body3["data"]["circuit_breaker"]["tripped"] is True
        assert body3["data"]["circuit_breaker"]["consecutive_failures"] == 5

    async def test_summary_uses_manual_reset_aware_breaker_filter(self) -> None:
        """Summary must mirror dispatch semantics: launched attempts plus manual_reset sentinel."""

        def _fetch_side_effect(query: str, *_args: Any):
            if "status = 'manual_reset'" in query and "healing_session_id IS NOT NULL" in query:
                return [
                    _r({"status": "failed"}),
                    _r({"status": "failed"}),
                    _r({"status": "failed"}),
                    _r({"status": "failed"}),
                    _r({"status": "manual_reset"}),
                ]
            return [_r({"sources_polled": []})]

        app, _ = _build_app(
            fetchrow_side_effect=[
                None,
                _r(_make_stats_row()),
                _r(_make_stats_row()),
                _r(_make_pr_stats_row()),
            ],
            fetchval_side_effect=[0],
            fetch_side_effect=_fetch_side_effect,
        )
        response = await _call(app, "get", "/api/qa/summary")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["circuit_breaker"]["tripped"] is False
        assert body["data"]["circuit_breaker"]["consecutive_failures"] == 4


class TestListPatrols:
    async def test_list_patrols_empty_and_with_data(self) -> None:
        """Empty list with meta; with data: row fields mapped, pagination/has_more computed."""
        assert (
            await _call(_build_app(fetch_rows=[], fetchval_result=0)[0], "get", "/api/qa/patrols")
        ).json()["meta"]["limit"] == 20

        patrol_id = uuid.uuid4()
        app, _ = _build_app(
            fetch_rows=[
                _make_patrol_row(
                    patrol_id=patrol_id, status="findings_dispatched", findings_count=5
                )
            ],
            fetchval_result=50,
        )
        response = await _call(app, "get", "/api/qa/patrols", params={"limit": 10, "offset": 5})
        assert response.status_code == 200
        meta = response.json()["meta"]
        assert meta["total"] == 50 and meta["has_more"] is True
        assert response.json()["data"][0]["id"] == str(patrol_id)
        assert response.json()["data"][0]["status"] == "findings_dispatched"

    async def test_status_filter_valid_accepted_invalid_rejected(self) -> None:
        assert (
            await _call(
                _build_app(fetch_rows=[_make_patrol_row(status="clean")], fetchval_result=1)[0],
                "get",
                "/api/qa/patrols",
                params={"status": "clean"},
            )
        ).status_code == 200

        app2, _ = _build_app()
        r = await _call(app2, "get", "/api/qa/patrols", params={"status": "not_valid"})
        assert r.status_code == 422 and "not_valid" in r.json()["detail"]


class TestGetCircuitBreakerStatus:
    async def test_counts_manual_reset_without_session_as_chain_break(self) -> None:
        """Status endpoint must match dispatch semantics for QA breaker reset rows."""

        def _fetch_side_effect(query: str, *_args: Any):
            if "status = 'manual_reset'" in query and "healing_session_id IS NOT NULL" in query:
                return [
                    _r({"id": uuid.uuid4(), "status": "failed", "closed_at": _NOW}),
                    _r({"id": uuid.uuid4(), "status": "failed", "closed_at": _NOW}),
                    _r({"id": uuid.uuid4(), "status": "failed", "closed_at": _NOW}),
                    _r({"id": uuid.uuid4(), "status": "failed", "closed_at": _NOW}),
                    _r({"id": uuid.uuid4(), "status": "manual_reset", "closed_at": _NOW}),
                ]
            return [_r({"status": "failed"}) for _ in range(5)]

        app, _ = _build_app(fetch_side_effect=_fetch_side_effect)
        response = await _call(app, "get", "/api/qa/circuit-breaker")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["tripped"] is False
        assert body["data"]["recent_statuses"][-1] == "manual_reset"


class TestGetPatrol:
    async def test_patrol_detail_happy_path_and_error_cases(self) -> None:
        """Returns findings when found; 404 when missing; 422 for invalid UUID."""
        patrol_id = uuid.uuid4()
        patrol = _make_patrol_row(patrol_id=patrol_id, findings_count=2)
        finding1 = _make_finding_row(patrol_id=patrol_id, fingerprint="a" * 64)
        finding2 = _make_finding_row(
            patrol_id=patrol_id, fingerprint="b" * 64, dedup_reason="active_attempt"
        )
        app, _ = _build_app(fetchrow_result=patrol, fetch_rows=[finding1, finding2])
        response = await _call(app, "get", f"/api/qa/patrols/{patrol_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["id"] == str(patrol_id)
        fp_list = [f["fingerprint"] for f in body["data"]["findings"]]
        assert "a" * 64 in fp_list and "b" * 64 in fp_list

        # Empty findings
        app2, _ = _build_app(fetchrow_result=_make_patrol_row(patrol_id=patrol_id), fetch_rows=[])
        assert (await _call(app2, "get", f"/api/qa/patrols/{patrol_id}")).json()["data"][
            "findings"
        ] == []

        # 404 when missing
        assert (
            await _call(
                _build_app(fetchrow_result=None)[0], "get", f"/api/qa/patrols/{uuid.uuid4()}"
            )
        ).status_code == 404

        # 422 for invalid UUID
        assert (
            await _call(_build_app()[0], "get", "/api/qa/patrols/not-a-uuid")
        ).status_code == 422


class TestListPatrolFindings:
    async def test_findings_for_patrol(self) -> None:
        """Returns findings when patrol exists; 404 when not; novel_only and pagination accepted."""
        patrol_id = uuid.uuid4()
        finding = _make_finding_row(patrol_id=patrol_id, fingerprint="c" * 64)
        app, _ = _build_app(
            fetchval_side_effect=[1, 1], fetch_side_effect=[[_r(finding)], [_r(finding)]]
        )
        response = await _call(app, "get", f"/api/qa/patrols/{patrol_id}/findings")
        assert response.status_code == 200
        assert response.json()["data"][0]["fingerprint"] == "c" * 64

        # 404 when patrol not found
        app2, _ = _build_app(fetchval_result=None)
        assert (
            await _call(app2, "get", f"/api/qa/patrols/{uuid.uuid4()}/findings")
        ).status_code == 404

        # novel_only filter and pagination
        app3, _ = _build_app(fetchval_side_effect=[1, 1], fetch_side_effect=[[_r(finding)]])
        assert (
            await _call(
                app3, "get", f"/api/qa/patrols/{patrol_id}/findings", params={"novel_only": "true"}
            )
        ).status_code == 200

        app4, _ = _build_app(fetchval_side_effect=[1, 0], fetch_side_effect=[[]])
        r = await _call(
            app4, "get", f"/api/qa/patrols/{patrol_id}/findings", params={"offset": 20, "limit": 10}
        )
        assert r.json()["meta"]["offset"] == 20 and r.json()["meta"]["limit"] == 10


class TestGetFindingByAttempt:
    async def test_finding_by_attempt(self) -> None:
        """Returns finding with evidence when found; 404 when missing; 422 for invalid UUID."""
        attempt_id = uuid.uuid4()
        finding = _make_finding_row(
            healing_attempt_id=attempt_id,
            dedup_reason="novel",
            source_session_trigger_source="scheduler",
            structured_evidence={"trace_id": "abc"},
        )
        app, _ = _build_app(fetchrow_result=finding)
        response = await _call(app, "get", f"/api/qa/findings/by-attempt/{attempt_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["healing_attempt_id"] == str(attempt_id)
        assert body["data"]["dedup_reason"] == "novel"
        assert body["data"]["source_session_trigger_source"] == "scheduler"
        assert body["data"]["structured_evidence"]["trace_id"] == "abc"

        assert (
            await _call(
                _build_app(fetchrow_result=None)[0],
                "get",
                f"/api/qa/findings/by-attempt/{uuid.uuid4()}",
            )
        ).status_code == 404
        assert (
            await _call(_build_app()[0], "get", "/api/qa/findings/by-attempt/not-a-uuid")
        ).status_code == 422


class TestListInvestigations:
    async def test_returns_investigations_shape_and_filters(self) -> None:
        """Empty list; PR info populated; status filter: valid accepted, invalid/removed rejected; 503 on DB failure."""
        assert (
            await _call(
                _build_app(fetch_rows=[], fetchval_result=0)[0], "get", "/api/qa/investigations"
            )
        ).json()["data"] == []

        attempt_id, patrol_id = uuid.uuid4(), uuid.uuid4()
        row = _make_investigation_row(
            id=attempt_id,
            qa_patrol_id=patrol_id,
            status="pr_open",
            pr_url="https://github.com/foo/bar/pull/42",
            pr_number=42,
        )
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)
        inv = (await _call(app, "get", "/api/qa/investigations")).json()["data"][0]
        assert inv["id"] == str(attempt_id)
        assert inv["status"] == "pr_open"
        assert inv["pr_url"] == "https://github.com/foo/bar/pull/42"
        assert inv["pr_number"] == 42
        assert inv["qa_patrol_id"] == str(patrol_id)

        # Status filter: valid accepted
        r_valid = await _call(
            _build_app(
                fetch_rows=[_make_investigation_row(status="anonymization_failed")],
                fetchval_result=1,
            )[0],
            "get",
            "/api/qa/investigations",
            params={"status": "anonymization_failed"},
        )
        assert r_valid.status_code == 200

        # Invalid status and removed status both rejected
        for bad_status in ("not_a_status", "dispatch_pending"):
            r_bad = await _call(
                _build_app()[0], "get", "/api/qa/investigations", params={"status": bad_status}
            )
            assert r_bad.status_code == 422
            assert bad_status in r_bad.json()["detail"]

        # 503 on DB failure
        assert (await _call(_make_503_app(), "get", "/api/qa/investigations")).status_code == 503

    async def test_optional_fields_present_and_null_when_absent(self) -> None:
        """Review tracking, follow-up cycle, and phase fields exposed; null/0 when not set."""
        cycle_patrol_id, followup_session_id = uuid.uuid4(), uuid.uuid4()
        row = _make_investigation_row(
            status="pr_open",
            review_state="changes_requested",
            follow_up_count=2,
            follow_up_cycle_patrol_id=cycle_patrol_id,
            follow_up_cycle_count=1,
            last_follow_up_status="succeeded",
            last_follow_up_session_id=followup_session_id,
            current_phase="diagnose",
            workflow_deadline_at=datetime(2026, 4, 9, 14, 0, 0, tzinfo=UTC),
        )
        app, _ = _build_app(fetch_rows=[row], fetchval_result=1)
        inv = (await _call(app, "get", "/api/qa/investigations")).json()["data"][0]
        assert inv["review_state"] == "changes_requested"
        assert inv["follow_up_count"] == 2
        assert inv["follow_up_cycle_patrol_id"] == str(cycle_patrol_id)
        assert inv["follow_up_cycle_count"] == 1
        assert inv["last_follow_up_status"] == "succeeded"
        assert inv["last_follow_up_session_id"] == str(followup_session_id)
        assert inv["current_phase"] == "diagnose"
        assert inv["workflow_deadline_at"] is not None

        # Absent → null/0
        inv2 = (
            await _call(
                _build_app(
                    fetch_rows=[_make_investigation_row(status="investigating")], fetchval_result=1
                )[0],
                "get",
                "/api/qa/investigations",
            )
        ).json()["data"][0]
        null_fields = (
            "review_state",
            "review_feedback_summary",
            "last_review_check_at",
            "current_phase",
            "workflow_deadline_at",
            "follow_up_cycle_patrol_id",
            "last_follow_up_status",
            "last_follow_up_session_id",
            "last_follow_up_error",
            "last_follow_up_at",
        )
        for field in null_fields:
            assert inv2[field] is None, f"{field} should be None"
        assert inv2["follow_up_count"] == 0
        assert inv2["follow_up_cycle_count"] == 0


def _make_agg_row(
    fingerprint: str = "d" * 64,
    source_butler: str = "general",
    severity: int = 2,
    occurrence_count: int = 7,
) -> dict[str, Any]:
    return {
        "fingerprint": fingerprint,
        "source_butler": source_butler,
        "source_type": "log_scanner",
        "severity": severity,
        "exception_type": "ValueError",
        "event_summary": "bad value",
        "call_site": "src/finance.py:compute",
        "occurrence_count": occurrence_count,
        "first_seen": _NOW,
        "last_seen": _NOW,
        "patrol_count": 3,
        "healing_attempt_id": None,
    }


class TestListKnownIssues:
    async def test_returns_known_issues_with_stats_and_optional_dismissal(self) -> None:
        """Empty list when no issues; aggregated stats returned; dismissal when present."""
        app_empty, _ = _build_app(fetchval_result=0, fetch_side_effect=[[]])
        assert (await _call(app_empty, "get", "/api/qa/known-issues")).json()["data"] == []

        fp = "d" * 64
        agg_row = _make_agg_row(fingerprint=fp)

        # No dismissal
        app, _ = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], []])
        body = (await _call(app, "get", "/api/qa/known-issues")).json()
        assert body["data"][0]["fingerprint"] == fp
        assert body["data"][0]["occurrence_count"] == 7
        assert body["data"][0]["patrol_count"] == 3
        assert body["data"][0]["dismissal"] is None

        # With dismissal
        dismissal = _make_dismissal_row(fingerprint=fp)
        app2, _ = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], [_r(dismissal)]])
        body2 = (await _call(app2, "get", "/api/qa/known-issues")).json()
        assert body2["data"][0]["dismissal"]["fingerprint"] == fp
        assert body2["data"][0]["dismissal"]["dismissed_by"] == "dashboard_user"

    async def test_filters_forwarded_to_db_and_pagination(self) -> None:
        """source_butler, severity, dismissed filters forwarded; meta.total reflects count query."""
        fp = "l" * 64
        agg_row = _make_agg_row(fingerprint=fp, source_butler="finance", severity=1)
        dismissal = _make_dismissal_row(fingerprint=fp)

        app, pool = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], []])
        r = await _call(app, "get", "/api/qa/known-issues", params={"source_butler": "finance"})
        assert r.json()["data"][0]["source_butler"] == "finance"
        assert "finance" in pool.fetchval.call_args.args or "finance" in str(
            pool.fetchval.call_args
        )

        app2, pool2 = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], []])
        r2 = await _call(app2, "get", "/api/qa/known-issues", params={"severity": 1})
        assert r2.json()["data"][0]["severity"] == 1
        assert 1 in pool2.fetchval.call_args.args or 1 in str(pool2.fetchval.call_args)

        app3, _ = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], [_r(dismissal)]])
        assert (
            await _call(app3, "get", "/api/qa/known-issues", params={"dismissed": "true"})
        ).json()["data"][0]["dismissal"] is not None

        app4, _ = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], []])
        assert (
            await _call(app4, "get", "/api/qa/known-issues", params={"dismissed": "false"})
        ).json()["data"][0]["dismissal"] is None

        app5, _ = _build_app(fetchval_result=42, fetch_side_effect=[[], []])
        meta = (await _call(app5, "get", "/api/qa/known-issues", params={"limit": 10})).json()[
            "meta"
        ]
        assert meta["total"] == 42 and meta["limit"] == 10


class TestKnownIssueDismissal:
    async def test_dismiss_and_undismiss(self) -> None:
        """POST creates dismissal (or 500 on failure); DELETE removes it (or 404 if absent); 503 on DB failure."""
        fp = "f" * 64
        app, _ = _build_app(
            fetchrow_result=_make_dismissal_row(fingerprint=fp, dismissed_by="owner")
        )
        r = await _call(
            app, "post", f"/api/qa/known-issues/{fp}/dismiss", json={"dismissed_by": "owner"}
        )
        assert r.status_code == 200
        assert r.json()["data"]["dismissed_by"] == "owner"

        # explicit dismissed_until expiry accepted and round-tripped
        expiry = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
        fp_exp = "g" * 64
        app_exp, _ = _build_app(
            fetchrow_result=_make_dismissal_row(fingerprint=fp_exp, dismissed_until=expiry)
        )
        r_exp = await _call(
            app_exp,
            "post",
            f"/api/qa/known-issues/{fp_exp}/dismiss",
            json={"dismissed_until": expiry.isoformat(), "dismissed_by": "owner"},
        )
        assert r_exp.status_code == 200
        assert r_exp.json()["data"]["fingerprint"] == fp_exp

        # empty body → defaults → 200
        fp2 = "i" * 64
        app2, _ = _build_app(fetchrow_result=_make_dismissal_row(fingerprint=fp2))
        assert (
            await _call(app2, "post", f"/api/qa/known-issues/{fp2}/dismiss", json={})
        ).status_code == 200

        # insert fails → 500
        assert (
            await _call(
                _build_app(fetchrow_result=None)[0],
                "post",
                f"/api/qa/known-issues/{'h' * 64}/dismiss",
                json={},
            )
        ).status_code == 500

        # DELETE success
        fp3 = "j" * 64
        app3, _ = _build_app(execute_result="DELETE 1")
        r3 = await _call(app3, "delete", f"/api/qa/known-issues/{fp3}/dismiss")
        assert r3.status_code == 200 and r3.json()["data"]["deleted"] is True

        # DELETE 404 when not found
        assert (
            await _call(
                _build_app(execute_result="DELETE 0")[0],
                "delete",
                f"/api/qa/known-issues/{'k' * 64}/dismiss",
            )
        ).status_code == 404

        # 503 on DB failure
        assert (
            await _call(_make_503_app(), "delete", "/api/qa/known-issues/abc/dismiss")
        ).status_code == 503


class TestForcePatrol:
    async def test_force_patrol_standalone_and_with_fn(self) -> None:
        """Standalone (no fn): 202 accepted=False. With fn: accepted=True with status in message.
        Skipped fn: accepted=False. Raising fn: 503."""
        # Standalone mode
        app, _ = _build_app()
        r = await _call(app, "post", "/api/qa/force-patrol")
        assert r.status_code == 202
        assert r.json()["data"]["accepted"] is False
        assert "message" in r.json()["data"]

        # With callable
        async def _fake_force_patrol() -> dict:
            return {
                "status": "findings_dispatched",
                "patrol_id": str(uuid.uuid4()),
                "findings_count": 3,
                "novel_count": 1,
                "dispatched_count": 1,
                "sources_polled": ["log_scanner"],
            }

        app2, _ = _build_app()
        app2.dependency_overrides[_get_force_patrol_fn] = lambda: _fake_force_patrol
        r2 = await _call(app2, "post", "/api/qa/force-patrol")
        assert r2.json()["data"]["accepted"] is True
        assert "findings_dispatched" in r2.json()["data"]["message"]

        # Skipped
        async def _skipped() -> dict:
            return {"status": "skipped", "reason": "patrol_already_running"}

        app3, _ = _build_app()
        app3.dependency_overrides[_get_force_patrol_fn] = lambda: _skipped
        assert (await _call(app3, "post", "/api/qa/force-patrol")).json()["data"][
            "accepted"
        ] is False

        # Exception → 503
        async def _failing() -> dict:
            raise RuntimeError("daemon not available")

        app4, _ = _build_app()
        app4.dependency_overrides[_get_force_patrol_fn] = lambda: _failing
        assert (await _call(app4, "post", "/api/qa/force-patrol")).status_code == 503


class TestInjectSyntheticFinding:
    async def test_rejects_when_synthetic_findings_are_disabled(self, monkeypatch) -> None:
        """The operator-only synthetic finding hook must be explicitly enabled."""
        monkeypatch.delenv("QA_ALLOW_SYNTHETIC_FINDINGS", raising=False)
        app, pool = _build_app()
        response = await _call(app, "post", "/api/qa/dev/synthetic-findings", json={})
        assert response.status_code == 403
        assert "QA_ALLOW_SYNTHETIC_FINDINGS" in response.json()["detail"]
        pool.execute.assert_not_called()
        pool.fetchrow.assert_not_called()

    async def test_queues_synthetic_finding_for_next_patrol(self, monkeypatch) -> None:
        """When enabled, the endpoint should create a placeholder patrol and a queued finding."""
        monkeypatch.setenv("QA_ALLOW_SYNTHETIC_FINDINGS", "true")
        patrol_id = uuid.uuid4()
        finding_id = uuid.uuid4()
        monkeypatch.setattr("butlers.api.routers.qa.uuid.uuid4", lambda: patrol_id)
        app, pool = _build_app(
            fetchrow_result={"id": finding_id, "fingerprint": "a" * 64, "patrol_id": patrol_id}
        )
        response = await _call(
            app, "post", "/api/qa/dev/synthetic-findings", json={"source_butler": "general"}
        )
        assert response.status_code == 202
        body = response.json()
        assert body["data"]["accepted"] is True
        assert body["data"]["patrol_id"] == str(patrol_id)
        assert body["data"]["finding_id"] == str(finding_id)
        assert "next scheduled patrol" in body["data"]["message"]

        pool.execute.assert_called_once()
        patrol_sql, inserted_patrol_id, patrol_error_detail = pool.execute.call_args.args
        assert "INSERT INTO public.qa_patrols" in patrol_sql
        assert inserted_patrol_id == patrol_id
        assert "Synthetic validation placeholder patrol" in patrol_error_detail

        pool.fetchrow.assert_called_once()
        finding_sql = pool.fetchrow.call_args.args[0]
        finding_args = pool.fetchrow.call_args.args[1:]
        assert "INSERT INTO public.qa_findings" in finding_sql
        assert "dispatch_queued" in finding_sql
        assert patrol_id in finding_args
        assert "general" in finding_args


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


def _make_source_row(source_type: str = "log_scanner", count: int = 7) -> dict[str, Any]:
    return {"source_type": source_type, "count": count}


class TestGetQaTrends:
    async def test_trends_empty_success_rate_and_source_breakdown(self) -> None:
        """Empty DB returns empty lists; success_rate computed; source_breakdown returned; days param accepted."""
        app, _ = _build_app(fetch_side_effect=[[], []])
        assert (await _call(app, "get", "/api/qa/trends")).json()["data"]["days"] == []

        # success_rate = clean_count / patrols_completed; 0.0 when none
        app2, _ = _build_app(
            fetch_side_effect=[[_r(_make_trend_row(patrols_completed=4, clean_count=3))], []]
        )
        assert (await _call(app2, "get", "/api/qa/trends")).json()["data"]["days"][0][
            "success_rate"
        ] == pytest.approx(0.75, abs=0.001)

        app3, _ = _build_app(
            fetch_side_effect=[[_r(_make_trend_row(patrols_completed=0, clean_count=0))], []]
        )
        assert (await _call(app3, "get", "/api/qa/trends")).json()["data"]["days"][0][
            "success_rate"
        ] == 0.0

        app4, _ = _build_app(fetch_side_effect=[[], [_r(_make_source_row("log_scanner", 12))]])
        breakdown = (await _call(app4, "get", "/api/qa/trends", params={"days": 14})).json()[
            "data"
        ]["source_breakdown"]
        assert breakdown[0]["source_type"] == "log_scanner" and breakdown[0]["count"] == 12


class TestListDismissals:
    async def test_list_dismissals(self) -> None:
        """Empty DB returns empty list; non-empty returns records with pagination meta; 503 on DB failure."""
        assert (
            await _call(
                _build_app(fetch_rows=[], fetchval_result=0)[0], "get", "/api/qa/dismissals"
            )
        ).json()["data"] == []

        fp = "p" * 64
        app, _ = _build_app(fetch_rows=[_make_dismissal_row(fingerprint=fp)], fetchval_result=50)
        r = await _call(app, "get", "/api/qa/dismissals", params={"limit": 10, "offset": 5})
        body = r.json()
        assert body["data"][0]["fingerprint"] == fp
        assert (
            body["meta"]["total"] == 50
            and body["meta"]["limit"] == 10
            and body["meta"]["offset"] == 5
        )

        assert (await _call(_make_503_app(), "get", "/api/qa/dismissals")).status_code == 503


class TestDeleteDismissal:
    async def test_delete_dismissal(self) -> None:
        """DELETE returns {deleted: true} on success; 404 when not found; 503 on DB failure."""
        fp = "q" * 64
        app, _ = _build_app(execute_result="DELETE 1")
        r = await _call(app, "delete", f"/api/qa/dismissals/{fp}")
        assert r.status_code == 200
        assert r.json()["data"]["fingerprint"] == fp
        assert r.json()["data"]["deleted"] is True

        assert (
            await _call(
                _build_app(execute_result="DELETE 0")[0], "delete", f"/api/qa/dismissals/{'r' * 64}"
            )
        ).status_code == 404
        assert (await _call(_make_503_app(), "delete", "/api/qa/dismissals/abc")).status_code == 503


class TestGetQaSummaryCredentialsStatus:
    async def test_credentials_status_all_states(self) -> None:
        """Unknown when not wired; token present → no hint; token missing → actionable hint;
        exception in fn is non-fatal (returns 200 with defaults)."""
        # Unknown when fn not wired
        app, _ = _build_summary_app()
        creds = (await _call(app, "get", "/api/qa/summary")).json()["data"]["credentials_status"]
        assert creds["gh_token_present"] is None
        assert creds["provisioning_hint"] is None

        # Token present → gh_token_present=True, no hint
        async def _token_present():
            return {"gh_token_present": True}

        app2, _ = _build_summary_app()
        app2.dependency_overrides[_get_credentials_status_fn] = lambda: _token_present
        creds2 = (await _call(app2, "get", "/api/qa/summary")).json()["data"]["credentials_status"]
        assert creds2["gh_token_present"] is True
        assert creds2["provisioning_hint"] is None

        # Token missing → hint with BUTLERS_QA_GH_TOKEN
        async def _token_missing():
            return {"gh_token_present": False}

        app3, _ = _build_summary_app()
        app3.dependency_overrides[_get_credentials_status_fn] = lambda: _token_missing
        creds3 = (await _call(app3, "get", "/api/qa/summary")).json()["data"]["credentials_status"]
        assert creds3["gh_token_present"] is False
        assert "BUTLERS_QA_GH_TOKEN" in creds3["provisioning_hint"]
        assert "butler secrets set" in creds3["provisioning_hint"]

        # Exception in fn is non-fatal — returns 200, gh_token_present defaults to None
        async def _failing_fn():
            raise RuntimeError("credential store unavailable")

        app4, _ = _build_summary_app()
        app4.dependency_overrides[_get_credentials_status_fn] = lambda: _failing_fn
        response = await _call(app4, "get", "/api/qa/summary")
        assert response.status_code == 200
        assert response.json()["data"]["credentials_status"]["gh_token_present"] is None


class TestFindingEvidenceFields:
    """Findings include source_session_trigger_source and structured_evidence (core_067)."""

    async def test_evidence_fields_null_then_populated_dict_and_string(self) -> None:
        """Null when absent; populated when present; parses from asyncpg string form (JSONB)."""
        import json as _json

        patrol_id = uuid.uuid4()

        # Null by default
        app, _ = _build_app(
            fetchrow_result=_make_patrol_row(patrol_id=patrol_id, findings_count=1),
            fetch_rows=[_make_finding_row(patrol_id=patrol_id)],
        )
        f = (await _call(app, "get", f"/api/qa/patrols/{patrol_id}")).json()["data"]["findings"][0]
        assert f["source_session_trigger_source"] is None
        assert f["structured_evidence"] is None

        # Dict and string form
        evidence = {"session_id": "abc", "runtime_type": "codex", "tool_call_count": 5}
        for ev in (evidence, _json.dumps(evidence)):
            patrol = _make_patrol_row(patrol_id=patrol_id, findings_count=1)
            finding = _make_finding_row(
                patrol_id=patrol_id, source_session_trigger_source="healing", structured_evidence=ev
            )
            app2, _ = _build_app(fetchrow_result=patrol, fetch_rows=[finding])
            f2 = (await _call(app2, "get", f"/api/qa/patrols/{patrol_id}")).json()["data"][
                "findings"
            ][0]
            assert f2["source_session_trigger_source"] == "healing"
            assert f2["structured_evidence"]["runtime_type"] == "codex"
            assert f2["structured_evidence"]["tool_call_count"] == 5


class TestListMetaReviewFindings:
    """The meta-review lane surfaces QA-self-recursive findings for operator review."""

    async def test_meta_review_all_trigger_sources_and_pagination(self) -> None:
        """Empty list, then findings appear for trigger_source in {healing, qa, None};
        pagination accepted; 503 on DB failure.

        Covers dispatch barrier: butlers.core.qa.dispatch checks ``trigger_src in {"healing", "qa"}``;
        null trigger_source from QA butler is also treated as potentially recursive.
        """
        assert (
            await _call(
                _build_app(fetch_rows=[], fetchval_result=0)[0], "get", "/api/qa/meta-review"
            )
        ).json()["data"] == []

        for trigger in ("healing", "qa", None):
            finding = _make_finding_row(source_butler="qa", source_session_trigger_source=trigger)
            app, _ = _build_app(fetch_rows=[finding], fetchval_result=1)
            r = await _call(app, "get", "/api/qa/meta-review")
            assert r.status_code == 200, f"failed for trigger={trigger!r}"
            assert r.json()["data"][0]["source_butler"] == "qa"
            assert r.json()["data"][0]["source_session_trigger_source"] == trigger

        app_pg, _ = _build_app(fetch_rows=[], fetchval_result=50)
        meta = (
            await _call(app_pg, "get", "/api/qa/meta-review", params={"limit": 10, "offset": 5})
        ).json()["meta"]
        assert meta["limit"] == 10 and meta["offset"] == 5 and meta["total"] == 50

        assert (await _call(_make_503_app(), "get", "/api/qa/meta-review")).status_code == 503

    async def test_structured_evidence_dict_and_string(self) -> None:
        """Meta-review findings parse structured_evidence in both dict and string (JSONB) forms."""
        import json as _json

        evidence = {"session_id": "def456", "runtime_type": "codex", "tool_call_count": 3}
        for ev in (evidence, _json.dumps(evidence)):
            finding = _make_finding_row(source_butler="qa", structured_evidence=ev)
            app, _ = _build_app(fetch_rows=[finding], fetchval_result=1)
            f = (await _call(app, "get", "/api/qa/meta-review")).json()["data"][0]
            assert f["structured_evidence"]["runtime_type"] == "codex"
            assert f["structured_evidence"]["tool_call_count"] == 3
