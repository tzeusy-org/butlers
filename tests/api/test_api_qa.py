"""Tests for QA dashboard API routes (summary, patrols, findings, investigations, known-issues, force-patrol, trends, dismissals)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager
from butlers.api.routers.qa import (
    _fetch_model_from_catalog,
    _get_credentials_status_fn,
    _get_db_manager,
    _get_force_patrol_fn,
    _get_staffer_info_fn,
)

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
    **dict.fromkeys(
        (
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
        ),
        None,
    ),
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


def _make_mcp_manager(*, butler_names: list[str] | None = None) -> MagicMock:
    """Build a mock MCPClientManager with no reachable daemons by default."""
    mgr = MagicMock()
    mgr.butler_names = butler_names or []
    # No daemons reachable by default — get_client raises for every name.
    mgr.get_client = AsyncMock(side_effect=RuntimeError("no daemon"))
    mgr.invalidate_client = AsyncMock()
    return mgr


def _make_mcp_manager_with_tool_result(
    payload: dict[str, Any],
    *,
    butler_names: list[str] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Build a mock MCPClientManager whose client returns ``payload`` from call_tool.

    Returns ``(manager, client_mock)`` so tests can assert on the call_tool spy.
    """
    block = MagicMock()
    block.text = json.dumps(payload)
    tool_result = MagicMock()
    tool_result.is_error = False
    tool_result.content = [block]

    client_mock = MagicMock()
    client_mock.call_tool = AsyncMock(return_value=tool_result)

    mgr = MagicMock()
    mgr.butler_names = butler_names or ["qa"]
    mgr.get_client = AsyncMock(return_value=client_mock)
    mgr.invalidate_client = AsyncMock()
    return mgr, client_mock


def _build_app(
    *,
    fetch_rows: list[dict[str, Any]] | None = None,
    fetchrow_result: dict[str, Any] | None = None,
    fetchval_result: Any = 0,
    execute_result: str = "DELETE 1",
    fetch_side_effect: Any = None,
    fetchrow_side_effect: Any = None,
    fetchval_side_effect: Any = None,
    mcp_manager: MagicMock | None = None,
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
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager or _make_mcp_manager()
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


def _make_kpi_row(
    prs_landed_24h: int = 0,
    mttr_24h_seconds: float | None = None,
    self_resolved_7d_pct: float | None = None,
    active_cases_now: int = 0,
    prs_landed_prior_24h: int = 0,
    mttr_prior_24h_seconds: float | None = None,
    self_resolved_prior_7d_pct: float | None = None,
) -> dict[str, Any]:
    return {
        "prs_landed_24h": prs_landed_24h,
        "mttr_24h_seconds": mttr_24h_seconds,
        "self_resolved_7d_pct": self_resolved_7d_pct,
        "active_cases_now": active_cases_now,
        "prs_landed_prior_24h": prs_landed_prior_24h,
        "mttr_prior_24h_seconds": mttr_prior_24h_seconds,
        "self_resolved_prior_7d_pct": self_resolved_prior_7d_pct,
    }


def _make_active_breakdown_row(
    awaiting_ci: int = 0, escalated_open_cases: int = 0
) -> dict[str, Any]:
    return {
        "awaiting_ci": awaiting_ci,
        "escalated_open_cases": escalated_open_cases,
    }


def _single_fetchrow_query_containing(pool: MagicMock, needle: str) -> str:
    queries = [call.args[0] for call in pool.fetchrow.await_args_list if needle in call.args[0]]
    assert len(queries) == 1
    return queries[0]


def _build_summary_app(
    *,
    last_patrol: dict[str, Any] | None = None,
    stats_24h: dict[str, Any] | None = None,
    prs_opened_24h: int = 0,
    kpis: dict[str, Any] | None = None,
    active_breakdown: dict[str, Any] | None = None,
    all_time_stats: dict[str, Any] | None = None,
    pr_stats: dict[str, Any] | None = None,
    cb_rows: list[dict[str, Any]] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    staffer_info: dict[str, Any] | None = None,
) -> tuple[Any, MagicMock]:
    """Build a test app with mocks wired to the summary endpoint's call sequence.

    ``staffer_info`` overrides the injected staffer-info callable.  When ``None``
    (the default), a no-op async callable is injected so that the existing DB-mock
    sequence is not disturbed by the standalone catalog query path.
    """
    app, pool = _build_app(
        fetchrow_side_effect=[
            _r(last_patrol) if last_patrol is not None else None,
            _r(stats_24h or _make_stats_row()),
            _r(kpis or _make_kpi_row()),
            _r(active_breakdown or _make_active_breakdown_row()),
            _r(all_time_stats or _make_stats_row()),
            _r(pr_stats or _make_pr_stats_row()),
        ],
        fetchval_side_effect=[prs_opened_24h],
        fetch_side_effect=[
            [_r(row) for row in (cb_rows or [])],
            [_r(row) for row in (source_rows or [])],
        ],
    )

    # Wire the staffer_info_fn dependency so the standalone catalog-query path
    # is not exercised by default (it would need additional fetchrow mocks).
    effective_info = staffer_info if staffer_info is not None else {}

    async def _staffer_info_fn() -> dict[str, Any]:
        return effective_info

    app.dependency_overrides[_get_staffer_info_fn] = lambda: _staffer_info_fn
    return app, pool


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
    async def test_summary_shape_stats_and_circuit_breaker(self) -> None:
        """Empty DB → nulls/zeros; patrol+stats populated; 503 on DB failure;
        CB trips on 5 consecutive failures."""
        # Empty DB
        body = (await _call(_build_summary_app()[0], "get", "/api/qa/summary")).json()
        assert (
            body["data"]["last_patrol"] is None
            and body["data"]["stats_24h"]["patrols_completed"] == 0
            and body["data"]["active_sources"] == []
        )
        assert body["data"]["kpis"] == {
            "prs_landed_24h": 0,
            "mttr_24h_seconds": None,
            "self_resolved_7d_pct": 0.0,
            "active_cases_now": 0,
            "prs_landed_prior_24h": 0,
            "mttr_prior_24h_seconds": None,
            "self_resolved_prior_7d_pct": None,
        }
        assert body["data"]["active_breakdown"] == {
            "awaiting_ci": 0,
            "escalated_open_cases": 0,
        }

        # With patrol and stats
        patrol_id = uuid.uuid4()
        app2, _ = _build_summary_app(
            last_patrol=_make_patrol_row(patrol_id=patrol_id, status="clean", findings_count=3),
            stats_24h=_make_stats_row(patrols_completed=5, total_findings=10),
            all_time_stats=_make_stats_row(patrols_completed=5, total_findings=10),
            prs_opened_24h=3,
            kpis=_make_kpi_row(
                prs_landed_24h=4,
                mttr_24h_seconds=312.75,
                self_resolved_7d_pct=80.0,
                active_cases_now=6,
            ),
            active_breakdown=_make_active_breakdown_row(awaiting_ci=2, escalated_open_cases=1),
            pr_stats=_make_pr_stats_row(prs_merged=10, prs_failed=2, total_dispatched=20),
            source_rows=[{"sources_polled": ["log_scanner"]}],
        )
        body2 = (await _call(app2, "get", "/api/qa/summary")).json()["data"]
        assert body2["last_patrol"]["id"] == str(patrol_id)
        assert (
            body2["stats_24h"]["patrols_completed"] == 5 and body2["stats_24h"]["prs_opened"] == 3
        )
        assert (
            body2["stats_all_time"]["prs_merged"] == 10
            and body2["stats_all_time"]["success_rate"] == 0.5
        )
        assert body2["kpis"] == {
            "prs_landed_24h": 4,
            "mttr_24h_seconds": 312.75,
            "self_resolved_7d_pct": 80.0,
            "active_cases_now": 6,
            "prs_landed_prior_24h": 0,
            "mttr_prior_24h_seconds": None,
            "self_resolved_prior_7d_pct": None,
        }
        assert body2["active_breakdown"] == {"awaiting_ci": 2, "escalated_open_cases": 1}
        assert "log_scanner" in body2["active_sources"]

        assert (await _call(_make_503_app(), "get", "/api/qa/summary")).status_code == 503

        # CB tripped → staffer_status reflects it
        body3 = (
            await _call(
                _build_summary_app(cb_rows=[{"status": "failed"} for _ in range(5)])[0],
                "get",
                "/api/qa/summary",
            )
        ).json()
        assert body3["data"]["circuit_breaker"]["tripped"] is True
        assert body3["data"]["staffer_status"] == "circuit_breaker_tripped"

        # credentials_status: token_present=True → gh_token_present reflected; no hint.
        # Git author identity presence is surfaced independently of the GH token.
        async def _creds_fn() -> dict:
            return {
                "gh_token_present": True,
                "git_author_name_present": True,
                "git_author_email_present": False,
            }

        app4, _ = _build_summary_app()
        app4.dependency_overrides[_get_credentials_status_fn] = lambda: _creds_fn
        creds = (await _call(app4, "get", "/api/qa/summary")).json()["data"]["credentials_status"]
        assert creds["gh_token_present"] is True
        assert creds["git_author_name_present"] is True
        assert creds["git_author_email_present"] is False
        assert creds["provisioning_hint"] is None

    async def test_summary_kpi_mttr_is_null_without_terminal_24h_sample(self) -> None:
        app, _ = _build_summary_app(
            kpis=_make_kpi_row(
                prs_landed_24h=0,
                mttr_24h_seconds=None,
                self_resolved_7d_pct=None,
                active_cases_now=3,
            ),
            active_breakdown=_make_active_breakdown_row(awaiting_ci=1, escalated_open_cases=0),
        )

        body = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert body["kpis"]["mttr_24h_seconds"] is None
        assert body["kpis"]["self_resolved_7d_pct"] == 0.0
        assert body["kpis"]["active_cases_now"] == 3
        assert body["active_breakdown"] == {"awaiting_ci": 1, "escalated_open_cases": 0}

    async def test_summary_kpi_sql_scopes_to_qa_origin_and_active_status_list(self) -> None:
        """The summary KPI query is QA-scoped and uses the active-status IN-list.

        Two invariants on the SQL shape recorded from the mock pool:

        1. Both the KPI aggregate query (the one containing ``active_cases_now``)
           and the active-breakdown query (``AS escalated_open_cases``) scope to
           QA-originated attempts via ``qa_patrol_id IS NOT NULL`` — they must not
           count non-QA healing attempts.
        2. ``active_cases_now`` is computed over the in-flight status set
           ``status IN ('dispatch_pending', 'investigating', 'pr_open')`` — terminal
           human-action cases are excluded from the live caseload.
        """
        app, pool = _build_summary_app()

        assert (await _call(app, "get", "/api/qa/summary")).status_code == 200

        kpi_sql = _single_fetchrow_query_containing(pool, "active_cases_now")
        active_breakdown_sql = _single_fetchrow_query_containing(pool, "AS escalated_open_cases")

        # (1) QA-origin scoping on both queries.
        assert "qa_patrol_id IS NOT NULL" in kpi_sql
        assert "qa_patrol_id IS NOT NULL" in active_breakdown_sql

        # (2) active_cases_now uses the in-flight status IN-list.
        assert "status IN ('dispatch_pending', 'investigating', 'pr_open')" in kpi_sql

    async def test_summary_escalated_count_uses_terminal_human_action_sql(self) -> None:
        app, pool = _build_summary_app(
            active_breakdown=_make_active_breakdown_row(awaiting_ci=1, escalated_open_cases=3)
        )

        body = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert body["active_breakdown"] == {"awaiting_ci": 1, "escalated_open_cases": 3}
        active_breakdown_sql = _single_fetchrow_query_containing(pool, "AS escalated_open_cases")
        assert "status IN ('unfixable', 'failed')" in active_breakdown_sql
        assert "error_detail ILIKE '%human action%'" in active_breakdown_sql
        assert "error_detail ILIKE '%operator%'" in active_breakdown_sql
        assert "error_detail ILIKE '%escalat%'" in active_breakdown_sql
        assert "closed_at IS NULL OR closed_at >= now() - INTERVAL '7 days'" in (
            active_breakdown_sql
        )
        assert "AS escalated_open_cases" in active_breakdown_sql
        assert " AS escalated\n" not in active_breakdown_sql
        assert " AS escalated," not in active_breakdown_sql

    async def test_summary_kpi_prior_period_fields_present_in_response(self) -> None:
        """Prior-period fields are included in the kpis block and default to zero/null."""
        app, _ = _build_summary_app()

        body = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert "prs_landed_prior_24h" in body["kpis"]
        assert "mttr_prior_24h_seconds" in body["kpis"]
        assert "self_resolved_prior_7d_pct" in body["kpis"]
        assert body["kpis"]["prs_landed_prior_24h"] == 0
        assert body["kpis"]["mttr_prior_24h_seconds"] is None
        assert body["kpis"]["self_resolved_prior_7d_pct"] is None

    async def test_summary_kpi_prior_period_values_propagated(self) -> None:
        """Prior-period values set in fixture are reflected in the response."""
        app, _ = _build_summary_app(
            kpis=_make_kpi_row(
                prs_landed_24h=3,
                mttr_24h_seconds=240.0,
                self_resolved_7d_pct=75.0,
                active_cases_now=2,
                prs_landed_prior_24h=1,
                mttr_prior_24h_seconds=480.0,
                self_resolved_prior_7d_pct=60.0,
            )
        )

        body = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert body["kpis"]["prs_landed_prior_24h"] == 1
        assert body["kpis"]["mttr_prior_24h_seconds"] == 480.0
        assert body["kpis"]["self_resolved_prior_7d_pct"] == 60.0

    async def test_summary_exposes_port_model_patrol_interval_via_staffer_info_fn(self) -> None:
        """port, model, patrol_interval_minutes are populated from the injected callable."""
        app, _ = _build_summary_app(
            staffer_info={
                "port": 41110,
                "model": "claude-sonnet-4-5",
                "patrol_interval_minutes": 10,
            }
        )

        data = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert data["port"] == 41110
        assert data["model"] == "claude-sonnet-4-5"
        assert data["patrol_interval_minutes"] == 10

    async def test_summary_port_model_patrol_interval_null_when_info_empty(self) -> None:
        """Fields are null when the staffer_info callable returns an empty dict."""
        app, _ = _build_summary_app(staffer_info={})

        data = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert data["port"] is None
        assert data["model"] is None
        assert data["patrol_interval_minutes"] is None

    async def test_summary_staffer_info_fn_failure_is_non_fatal(self) -> None:
        """A raising staffer_info_fn is swallowed; fields fall back to null."""

        async def _broken_fn() -> dict[str, Any]:
            raise RuntimeError("boom")

        app, _ = _build_summary_app()
        # Override the dependency with a broken callable.
        app.dependency_overrides[_get_staffer_info_fn] = lambda: _broken_fn

        data = (await _call(app, "get", "/api/qa/summary")).json()["data"]

        assert data["port"] is None
        assert data["model"] is None
        assert data["patrol_interval_minutes"] is None


class TestFetchModelFromCatalog:
    """Direct tests for ``_fetch_model_from_catalog``.

    The query must pick the highest-priority enabled candidate whose
    *effective* (override-merged) ``complexity_tier`` is ``'workhorse'``, mirroring
    spawn-time resolution in ``butlers.core.model_routing._RESOLVE_SQL`` for
    the ``qa`` butler at ``Complexity.WORKHORSE``.

    These tests pass an in-memory candidate set into a stub pool and run the
    same SQL the implementation runs, asserting:
    - the SQL filters by ``COALESCE(bmo.complexity_tier, mc.complexity_tier) = 'workhorse'``,
    - it filters by ``COALESCE(bmo.enabled, mc.enabled) = TRUE``,
    - it orders by ``COALESCE(bmo.priority, mc.priority) DESC`` then
      ``mc.created_at ASC, mc.id ASC``,
    - it returns ``None`` when the result is empty.
    """

    @staticmethod
    def _stub_pool(rows: list[dict[str, Any]] | None) -> AsyncMock:
        """A pool whose ``fetchrow`` returns the first row (or ``None``)."""
        pool = AsyncMock()
        first = _r(rows[0]) if rows else None
        pool.fetchrow = AsyncMock(return_value=first)
        return pool

    async def test_qa_override_with_workhorse_tier_and_high_priority_wins(self) -> None:
        """A QA override at tier=workhorse with the highest effective priority wins."""
        # Simulated SQL result: the override-merged candidate at top priority.
        pool = self._stub_pool([{"alias": "qa-override-workhorse"}])

        result = await _fetch_model_from_catalog(pool)

        assert result == "qa-override-workhorse"
        assert pool.fetchrow.await_count == 1
        sql, butler_name = pool.fetchrow.await_args.args
        assert butler_name == "qa"
        assert "COALESCE(bmo.complexity_tier, mc.complexity_tier) = 'workhorse'" in sql
        assert "COALESCE(bmo.enabled, mc.enabled) = TRUE" in sql
        assert "COALESCE(bmo.priority, mc.priority) DESC" in sql
        assert "mc.created_at ASC" in sql
        assert "mc.id ASC" in sql
        # The query must NOT mutate the round-robin counter.
        assert "model_round_robin_counters" not in sql
        assert "INSERT" not in sql.upper()

    async def test_non_workhorse_override_is_ignored_even_if_higher_priority(self) -> None:
        """A QA override at tier!=workhorse does not displace workhorse-tier candidates.

        The SQL filter ``COALESCE(bmo.complexity_tier, mc.complexity_tier) = 'workhorse'``
        excludes any row whose effective tier is not 'workhorse'.  We assert the
        clause is present in the executed query and that the stub returning
        only a workhorse row is what surfaces.
        """
        pool = self._stub_pool([{"alias": "global-workhorse-default"}])

        result = await _fetch_model_from_catalog(pool)

        assert result == "global-workhorse-default"
        sql = pool.fetchrow.await_args.args[0]
        # Tier filter must apply to the *effective* tier, so a `bmo` row with
        # tier='reasoning' (and bmo.complexity_tier non-null) is filtered out.
        assert "COALESCE(bmo.complexity_tier, mc.complexity_tier) = 'workhorse'" in sql

    async def test_no_qa_override_falls_back_to_global_workhorse_top_priority(self) -> None:
        """With no QA override, the highest-priority enabled global workhorse entry wins."""
        pool = self._stub_pool([{"alias": "claude-sonnet-4-5"}])

        result = await _fetch_model_from_catalog(pool)

        assert result == "claude-sonnet-4-5"
        sql = pool.fetchrow.await_args.args[0]
        # LEFT JOIN keeps the catalog-only path live when no bmo row exists.
        assert "LEFT JOIN public.butler_model_overrides bmo" in sql

    async def test_returns_none_when_no_medium_entries_enabled(self) -> None:
        """No enabled medium-tier candidate → ``None``."""
        pool = self._stub_pool([])

        assert await _fetch_model_from_catalog(pool) is None

    async def test_returns_none_on_query_failure(self) -> None:
        """A raising pool returns ``None`` (debug-logged, non-fatal)."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("db down"))

        assert await _fetch_model_from_catalog(pool) is None


class TestListPatrols:
    async def test_list_patrols_pagination_and_status_filter(self) -> None:
        """Empty list with meta; pagination/has_more computed; valid status accepted, invalid rejected."""
        assert (
            await _call(_build_app(fetch_rows=[], fetchval_result=0)[0], "get", "/api/qa/patrols")
        ).json()["meta"]["limit"] == 20

        patrol_id = uuid.uuid4()
        response = await _call(
            _build_app(
                fetch_rows=[
                    _make_patrol_row(
                        patrol_id=patrol_id, status="findings_dispatched", findings_count=5
                    )
                ],
                fetchval_result=50,
            )[0],
            "get",
            "/api/qa/patrols",
            params={"limit": 10, "offset": 5},
        )
        assert response.status_code == 200
        meta = response.json()["meta"]
        assert meta["total"] == 50 and meta["has_more"] is True
        assert response.json()["data"][0]["id"] == str(patrol_id)
        assert response.json()["data"][0]["status"] == "findings_dispatched"

        assert (
            await _call(
                _build_app(fetch_rows=[_make_patrol_row(status="clean")], fetchval_result=1)[0],
                "get",
                "/api/qa/patrols",
                params={"status": "clean"},
            )
        ).status_code == 200
        r = await _call(_build_app()[0], "get", "/api/qa/patrols", params={"status": "not_valid"})
        assert r.status_code == 422 and "not_valid" in r.json()["detail"]


class TestGetCircuitBreakerStatus:
    async def test_counts_manual_reset_without_session_as_chain_break(self) -> None:
        """Status endpoint must match dispatch semantics for QA breaker reset rows."""

        def _fetch_side_effect(query: str, *_args: Any):
            if "status = 'manual_reset'" in query and "healing_session_id IS NOT NULL" in query:
                return [
                    _r({"id": uuid.uuid4(), "status": s, "closed_at": _NOW})
                    for s in ("failed", "failed", "failed", "failed", "manual_reset")
                ]
            return [_r({"status": "failed"}) for _ in range(5)]

        body = (
            await _call(
                _build_app(fetch_side_effect=_fetch_side_effect)[0],
                "get",
                "/api/qa/circuit-breaker",
            )
        ).json()
        assert body["data"]["tripped"] is False
        assert body["data"]["recent_statuses"][-1] == "manual_reset"


class TestGetPatrol:
    async def test_patrol_detail_happy_path_and_error_cases(self) -> None:
        """Returns findings when found; 404 when missing; 422 for invalid UUID."""
        patrol_id = uuid.uuid4()
        finding1 = _make_finding_row(patrol_id=patrol_id, fingerprint="a" * 64)
        finding2 = _make_finding_row(
            patrol_id=patrol_id, fingerprint="b" * 64, dedup_reason="active_attempt"
        )
        response = await _call(
            _build_app(
                fetchrow_result=_make_patrol_row(patrol_id=patrol_id, findings_count=2),
                fetch_rows=[finding1, finding2],
            )[0],
            "get",
            f"/api/qa/patrols/{patrol_id}",
        )
        assert response.status_code == 200
        fp_list = [f["fingerprint"] for f in response.json()["data"]["findings"]]
        assert "a" * 64 in fp_list and "b" * 64 in fp_list

        assert (
            await _call(
                _build_app(fetchrow_result=_make_patrol_row(patrol_id=patrol_id), fetch_rows=[])[0],
                "get",
                f"/api/qa/patrols/{patrol_id}",
            )
        ).json()["data"]["findings"] == []
        assert (
            await _call(
                _build_app(fetchrow_result=None)[0], "get", f"/api/qa/patrols/{uuid.uuid4()}"
            )
        ).status_code == 404
        assert (
            await _call(_build_app()[0], "get", "/api/qa/patrols/not-a-uuid")
        ).status_code == 422


class TestListPatrolFindings:
    async def test_findings_for_patrol(self) -> None:
        """Returns findings when patrol exists; 404 when not; novel_only and pagination accepted."""
        patrol_id = uuid.uuid4()
        finding = _make_finding_row(patrol_id=patrol_id, fingerprint="c" * 64)
        response = await _call(
            _build_app(
                fetchval_side_effect=[1, 1], fetch_side_effect=[[_r(finding)], [_r(finding)]]
            )[0],
            "get",
            f"/api/qa/patrols/{patrol_id}/findings",
        )
        assert response.status_code == 200 and response.json()["data"][0]["fingerprint"] == "c" * 64

        assert (
            await _call(
                _build_app(fetchval_result=None)[0],
                "get",
                f"/api/qa/patrols/{uuid.uuid4()}/findings",
            )
        ).status_code == 404
        assert (
            await _call(
                _build_app(fetchval_side_effect=[1, 1], fetch_side_effect=[[_r(finding)]])[0],
                "get",
                f"/api/qa/patrols/{patrol_id}/findings",
                params={"novel_only": "true"},
            )
        ).status_code == 200

        r = await _call(
            _build_app(fetchval_side_effect=[1, 0], fetch_side_effect=[[]])[0],
            "get",
            f"/api/qa/patrols/{patrol_id}/findings",
            params={"offset": 20, "limit": 10},
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
        body = (
            await _call(
                _build_app(fetchrow_result=finding)[0],
                "get",
                f"/api/qa/findings/by-attempt/{attempt_id}",
            )
        ).json()
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
    async def test_investigations_shape_filters_and_optional_fields(self) -> None:
        """Empty list; PR info populated; status filter valid/invalid; 503; optional fields serialized when present."""
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
        inv = (
            await _call(
                _build_app(fetch_rows=[row], fetchval_result=1)[0], "get", "/api/qa/investigations"
            )
        ).json()["data"][0]
        assert inv["id"] == str(attempt_id)
        assert inv["status"] == "pr_open"
        assert inv["pr_url"] == "https://github.com/foo/bar/pull/42"
        assert inv["pr_number"] == 42
        assert inv["qa_patrol_id"] == str(patrol_id)

        # valid status filter accepted
        r_valid = await _call(
            _build_app(fetch_rows=[], fetchval_result=0)[0],
            "get",
            "/api/qa/investigations",
            params={"status": "anonymization_failed"},
        )
        assert r_valid.status_code == 200

        # invalid/removed status rejected
        for bad_status in ("not_a_status", "dispatch_pending"):
            r_bad = await _call(
                _build_app()[0], "get", "/api/qa/investigations", params={"status": bad_status}
            )
            assert r_bad.status_code == 422 and bad_status in r_bad.json()["detail"]

        assert (await _call(_make_503_app(), "get", "/api/qa/investigations")).status_code == 503

        # optional fields serialized when present
        cycle_patrol_id = uuid.uuid4()
        row2 = _make_investigation_row(
            status="pr_open",
            review_state="changes_requested",
            follow_up_count=2,
            follow_up_cycle_patrol_id=cycle_patrol_id,
            follow_up_cycle_count=1,
            last_follow_up_status="succeeded",
            current_phase="diagnose",
            workflow_deadline_at=datetime(2026, 4, 9, 14, 0, 0, tzinfo=UTC),
        )
        inv2 = (
            await _call(
                _build_app(fetch_rows=[row2], fetchval_result=1)[0], "get", "/api/qa/investigations"
            )
        ).json()["data"][0]
        assert inv2["review_state"] == "changes_requested"
        assert inv2["follow_up_count"] == 2
        assert inv2["current_phase"] == "diagnose"
        assert inv2["workflow_deadline_at"] is not None


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
    async def test_known_issues_shape_filters_and_pagination(self) -> None:
        """Empty list; stats and dismissal returned; source_butler/severity/dismissed filters forwarded; meta.total reflects count."""
        assert (
            await _call(
                _build_app(fetchval_result=0, fetch_side_effect=[[]])[0],
                "get",
                "/api/qa/known-issues",
            )
        ).json()["data"] == []

        fp = "d" * 64
        agg_row = _make_agg_row(fingerprint=fp)
        dismissal = _make_dismissal_row(fingerprint=fp)

        # No dismissal
        body = (
            await _call(
                _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], []])[0],
                "get",
                "/api/qa/known-issues",
            )
        ).json()
        assert body["data"][0]["fingerprint"] == fp
        assert body["data"][0]["occurrence_count"] == 7
        assert body["data"][0]["patrol_count"] == 3
        assert body["data"][0]["dismissal"] is None

        # With dismissal
        body2 = (
            await _call(
                _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], [_r(dismissal)]])[
                    0
                ],
                "get",
                "/api/qa/known-issues",
            )
        ).json()
        assert body2["data"][0]["dismissal"]["fingerprint"] == fp
        assert body2["data"][0]["dismissal"]["dismissed_by"] == "dashboard_user"

        # source_butler filter forwarded
        fp2 = "l" * 64
        agg_row2 = _make_agg_row(fingerprint=fp2, source_butler="finance", severity=1)
        app, pool = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row2)], []])
        r = await _call(app, "get", "/api/qa/known-issues", params={"source_butler": "finance"})
        assert r.json()["data"][0]["source_butler"] == "finance"
        assert "finance" in pool.fetchval.call_args.args or "finance" in str(
            pool.fetchval.call_args
        )

        # severity filter
        app2, pool2 = _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row2)], []])
        r2 = await _call(app2, "get", "/api/qa/known-issues", params={"severity": 1})
        assert r2.json()["data"][0]["severity"] == 1
        assert 1 in pool2.fetchval.call_args.args or 1 in str(pool2.fetchval.call_args)

        # pagination meta
        meta = (
            await _call(
                _build_app(fetchval_result=42, fetch_side_effect=[[], []])[0],
                "get",
                "/api/qa/known-issues",
                params={"limit": 10},
            )
        ).json()["meta"]
        assert meta["total"] == 42 and meta["limit"] == 10

        # dismissed filter forwarded (True = show only dismissed; request succeeds)
        r_dismissed = await _call(
            _build_app(fetchval_result=1, fetch_side_effect=[[_r(agg_row)], [_r(dismissal)]])[0],
            "get",
            "/api/qa/known-issues",
            params={"dismissed": "true"},
        )
        assert r_dismissed.status_code == 200


class TestKnownIssueDismissal:
    async def test_dismiss_and_undismiss(self) -> None:
        """POST creates dismissal (or 500 on failure); DELETE removes it (or 404 if absent); 503 on DB failure."""
        fp = "f" * 64
        r = await _call(
            _build_app(fetchrow_result=_make_dismissal_row(fingerprint=fp, dismissed_by="owner"))[
                0
            ],
            "post",
            f"/api/qa/known-issues/{fp}/dismiss",
            json={"dismissed_by": "owner"},
        )
        assert r.status_code == 200 and r.json()["data"]["dismissed_by"] == "owner"

        # empty body → defaults → 200; insert fails → 500
        assert (
            await _call(
                _build_app(fetchrow_result=_make_dismissal_row(fingerprint="i" * 64))[0],
                "post",
                f"/api/qa/known-issues/{'i' * 64}/dismiss",
                json={},
            )
        ).status_code == 200
        assert (
            await _call(
                _build_app(fetchrow_result=None)[0],
                "post",
                f"/api/qa/known-issues/{'h' * 64}/dismiss",
                json={},
            )
        ).status_code == 500

        # DELETE success; 404 when not found; 503 on DB failure
        r3 = await _call(
            _build_app(execute_result="DELETE 1")[0],
            "delete",
            f"/api/qa/known-issues/{'j' * 64}/dismiss",
        )
        assert r3.status_code == 200 and r3.json()["data"]["deleted"] is True
        assert (
            await _call(
                _build_app(execute_result="DELETE 0")[0],
                "delete",
                f"/api/qa/known-issues/{'k' * 64}/dismiss",
            )
        ).status_code == 404
        assert (
            await _call(_make_503_app(), "delete", "/api/qa/known-issues/abc/dismiss")
        ).status_code == 503


class TestForcePatrol:
    async def test_force_patrol_standalone_and_with_fn(self) -> None:
        """Standalone (no fn, no daemon): 202 triggered=False. With fn: triggered=True
        with status in message. Raising fn: 503."""
        # Standalone mode — no in-process fn AND no reachable daemon.
        r = await _call(_build_app()[0], "post", "/api/qa/force-patrol")
        assert (
            r.status_code == 202
            and r.json()["data"]["accepted"] is False
            and r.json()["data"]["triggered"] is False
            and "message" in r.json()["data"]
        )

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
        assert (
            r2.json()["data"]["accepted"] is True
            and r2.json()["data"]["triggered"] is True
            and "findings_dispatched" in r2.json()["data"]["message"]
        )

        async def _failing() -> dict:
            raise RuntimeError("daemon not available")

        app3, _ = _build_app()
        app3.dependency_overrides[_get_force_patrol_fn] = lambda: _failing
        assert (await _call(app3, "post", "/api/qa/force-patrol")).status_code == 503

    async def test_force_patrol_dispatches_via_daemon_mcp_and_reports_triggered(self) -> None:
        """Cross-process path: with no in-process fn, force-patrol invokes the QA daemon
        force_patrol MCP tool and reports triggered=True on success.

        Pre-fix this endpoint was a silent no-op in the standalone dashboard process
        (it returned accepted=False without ever calling the daemon). Post-fix it
        crosses the process boundary via MCPClientManager.call_tool.
        """
        mgr, client_mock = _make_mcp_manager_with_tool_result(
            {
                "status": "findings_dispatched",
                "patrol_id": str(uuid.uuid4()),
                "findings_count": 2,
                "novel_count": 1,
                "dispatched_count": 1,
                "sources_polled": ["log_scanner"],
            },
            butler_names=["qa"],
        )
        app, _ = _build_app(mcp_manager=mgr)

        r = await _call(app, "post", "/api/qa/force-patrol")

        assert r.status_code == 202
        body = r.json()["data"]
        assert body["triggered"] is True
        assert body["accepted"] is True
        assert "findings_dispatched" in body["message"]

        # The regression assertion: the daemon force_patrol tool was actually
        # invoked (on the QA butler) — not a silent no-op.
        mgr.get_client.assert_awaited_with("qa")
        client_mock.call_tool.assert_awaited_once_with("force_patrol", {})

    async def test_force_patrol_daemon_unreachable_reports_not_triggered(self) -> None:
        """No in-process fn AND no reachable daemon → force-patrol must NOT claim it ran."""
        mgr = _make_mcp_manager(butler_names=["qa"])
        app, _ = _build_app(mcp_manager=mgr)

        r = await _call(app, "post", "/api/qa/force-patrol")

        assert r.status_code == 202
        body = r.json()["data"]
        assert body["triggered"] is False
        assert body["accepted"] is False
        assert "unreachable" in body["message"].lower()
        # We attempted to reach the QA daemon before giving up.
        mgr.get_client.assert_awaited()

    async def test_force_patrol_daemon_skip_reports_not_triggered(self) -> None:
        """Daemon reachable but patrol already running → status 'skipped' → triggered=False."""
        mgr, client_mock = _make_mcp_manager_with_tool_result(
            {"status": "skipped", "reason": "patrol_already_running"},
            butler_names=["qa"],
        )
        app, _ = _build_app(mcp_manager=mgr)

        r = await _call(app, "post", "/api/qa/force-patrol")

        assert r.status_code == 202
        body = r.json()["data"]
        assert body["triggered"] is False
        assert "patrol_already_running" in body["message"]
        client_mock.call_tool.assert_awaited_once_with("force_patrol", {})


class TestListDismissals:
    async def test_list_dismissals(self) -> None:
        """Empty DB returns empty list; non-empty returns records with pagination meta; 503 on DB failure."""
        assert (
            await _call(
                _build_app(fetch_rows=[], fetchval_result=0)[0], "get", "/api/qa/dismissals"
            )
        ).json()["data"] == []

        fp = "p" * 64
        body = (
            await _call(
                _build_app(fetch_rows=[_make_dismissal_row(fingerprint=fp)], fetchval_result=50)[0],
                "get",
                "/api/qa/dismissals",
                params={"limit": 10, "offset": 5},
            )
        ).json()
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
        r = await _call(
            _build_app(execute_result="DELETE 1")[0], "delete", f"/api/qa/dismissals/{fp}"
        )
        assert (
            r.status_code == 200
            and r.json()["data"]["fingerprint"] == fp
            and r.json()["data"]["deleted"] is True
        )

        assert (
            await _call(
                _build_app(execute_result="DELETE 0")[0], "delete", f"/api/qa/dismissals/{'r' * 64}"
            )
        ).status_code == 404
        assert (await _call(_make_503_app(), "delete", "/api/qa/dismissals/abc")).status_code == 503


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
    async def test_trends_shape_and_success_rate(self) -> None:
        """Empty DB returns empty lists; success_rate computed; source_breakdown returned."""
        app, _ = _build_app(fetch_side_effect=[[], []])
        assert (await _call(app, "get", "/api/qa/trends")).json()["data"]["days"] == []

        app2, _ = _build_app(
            fetch_side_effect=[[_r(_make_trend_row(patrols_completed=4, clean_count=3))], []]
        )
        assert (await _call(app2, "get", "/api/qa/trends")).json()["data"]["days"][0][
            "success_rate"
        ] == pytest.approx(0.75, abs=0.001)

        app3, _ = _build_app(fetch_side_effect=[[], [_r(_make_source_row("log_scanner", 12))]])
        breakdown = (await _call(app3, "get", "/api/qa/trends", params={"days": 14})).json()[
            "data"
        ]["source_breakdown"]
        assert breakdown[0]["source_type"] == "log_scanner" and breakdown[0]["count"] == 12


class TestListMetaReviewFindings:
    """The meta-review lane surfaces QA-self-recursive findings for operator review."""

    async def test_meta_review_all_trigger_sources_and_pagination(self) -> None:
        """Empty list; trigger_source in {healing, qa, None} all accepted; pagination; 503."""
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
            assert r.json()["data"][0]["source_session_trigger_source"] == trigger

        app_pg, _ = _build_app(fetch_rows=[], fetchval_result=50)
        meta = (
            await _call(app_pg, "get", "/api/qa/meta-review", params={"limit": 10, "offset": 5})
        ).json()["meta"]
        assert meta["limit"] == 10 and meta["offset"] == 5 and meta["total"] == 50

        assert (await _call(_make_503_app(), "get", "/api/qa/meta-review")).status_code == 503


class _FakeCredentialStore:
    """In-memory CredentialStore double shared between the write endpoint and the
    dispatch read path, proving a round-trip without a live database."""

    def __init__(self, pool: Any = None, **_: Any) -> None:
        self.values: dict[str, str] = {}

    async def store(
        self,
        key: str,
        value: str,
        *,
        category: str = "general",
        description: str | None = None,
        is_sensitive: bool = True,
        expires_at: Any = None,
    ) -> None:
        self.values[key.strip()] = value.strip()

    async def resolve(self, key: str, *, env_fallback: bool = False) -> str | None:
        return self.values.get(key)


class TestUpdateGitAuthor:
    async def test_write_endpoint_stores_identity_and_dispatch_reads_it(self) -> None:
        """PUT /api/qa/settings/git-author stores name+email in the credential
        store, and the dispatch read path (_resolve_git_identity) returns them."""
        from butlers.core.qa.dispatch import (
            QA_GIT_AUTHOR_EMAIL_KEY,
            QA_GIT_AUTHOR_NAME_KEY,
        )
        from butlers.modules.qa import QaModule

        fake_store = _FakeCredentialStore()
        app, _ = _build_app()

        with mock.patch(
            "butlers.credential_store.CredentialStore",
            side_effect=lambda *a, **k: fake_store,
        ):
            resp = await _call(
                app,
                "put",
                "/api/qa/settings/git-author",
                json={"name": "QA Staffer", "email": "qa@butlers.local"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["git_author_name_present"] is True
        assert data["git_author_email_present"] is True

        # Stored under the canonical dispatch keys.
        assert fake_store.values[QA_GIT_AUTHOR_NAME_KEY] == "QA Staffer"
        assert fake_store.values[QA_GIT_AUTHOR_EMAIL_KEY] == "qa@butlers.local"

        # The dispatch read path picks up exactly what the endpoint wrote.
        fake_self = SimpleNamespace(_credential_store=fake_store)
        name, email = await QaModule._resolve_git_identity(fake_self)
        assert name == "QA Staffer"
        assert email == "qa@butlers.local"

    async def test_rejects_blank_name_and_malformed_email(self) -> None:
        app, _ = _build_app()
        # Missing "@" → 422.
        bad_email = await _call(
            app,
            "put",
            "/api/qa/settings/git-author",
            json={"name": "QA Staffer", "email": "not-an-email"},
        )
        assert bad_email.status_code == 422
        # Empty name → 422 (pydantic min_length).
        blank_name = await _call(
            app,
            "put",
            "/api/qa/settings/git-author",
            json={"name": "", "email": "qa@butlers.local"},
        )
        assert blank_name.status_code == 422

    async def test_503_when_shared_pool_unavailable(self) -> None:
        resp = await _call(
            _make_503_app(),
            "put",
            "/api/qa/settings/git-author",
            json={"name": "QA Staffer", "email": "qa@butlers.local"},
        )
        assert resp.status_code == 503
