"""Real-Postgres integration test for the dashboard ``file_bug_report`` ->
QA relay -> ``public.qa_findings`` write path (bu-u0zmu, gen-1 reconciliation
gap G5, bu-p6ey8.5).

Every existing ``file_bug_report`` test (``tests/daemon/test_dashboard_lane_tools.py``)
mocks both the Switchboard ``route()`` call and the QA staffer's
``report_finding`` handler — they assert on the *arguments* passed to
``route()``, never on what actually happens once those arguments reach QA.
That leaves the entire "does a dashboard bug report actually become a
QA-visible finding" claim unverified end to end.

This test exercises the real chain against a live database:

1. Calls the real ``file_bug_report`` MCP tool (extracted via
   ``register_switchboard_tools`` — mirrors the direct-registration pattern
   in ``tests/core_tools/test_switchboard_ingest_policy_tier.py``, no full
   daemon bootstrap required since ``file_bug_report`` only touches
   ``ctx.pool`` and the routing context var).
2. Forwards its ``route()`` call to a **real** ``QaModule._handle_report_finding``
   (not a stub of the handler itself) wired to a real ``ButlerReportsSource``
   buffer — mirrors ``TestSelfHealingToQaRelayIntegration`` in
   ``tests/integration/test_qa_pipeline.py``, which proves the same relay
   shape for ``self_healing`` but only asserts against the in-memory buffer.
3. Drains the buffer and persists the resulting finding via the real
   ``insert_finding()`` CRUD function against a live ``public.qa_findings``
   table, then asserts the persisted row's fingerprint is byte-for-byte the
   canonical fingerprint independently recomputed from the same inputs
   (proving fingerprint continuity from the dashboard report all the way to
   the QA-visible DB row, not just "some row got inserted").
4. Asserts the in-thread ``conversation_reply`` ack is a real
   ``public.dashboard_messages`` row (the same DB-write path covered by
   ``tests/integration/test_conversation_reply_db.py``), carrying the exact
   case reference the owner would see.

``migrated_core_postgres_pool()`` runs the core Alembic chain against the
fresh provisioned database, so the relay is exercised against the production
schema instead of hand-provisioned tables.
"""

from __future__ import annotations

import shutil
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.core.qa.findings import insert_finding
from butlers.core.qa.sources.butler_reports import ButlerReportsSource
from butlers.core_tools._base import ToolContext
from butlers.core_tools._switchboard import register_switchboard_tools
from butlers.modules.qa import QaModule

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]


class _FakeIngestionPolicyEvaluator:
    """No-op stand-in for IngestionPolicyEvaluator.

    ``register_switchboard_tools`` eagerly constructs a global-scope
    evaluator and fires ``ensure_loaded()`` in the background — irrelevant
    to ``file_bug_report`` (which never uses it) but real construction would
    query ``switchboard.ingestion_rules``, a table this minimal schema does
    not provision. Faking it keeps the test focused on the QA relay path
    (mirrors ``tests/core_tools/test_switchboard_ingest_policy_tier.py``).
    """

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def ensure_loaded(self) -> None:
        return None


def _register_file_bug_report(monkeypatch, pool, mock_route) -> Any:
    """Register switchboard tools against a real pool and capture ``file_bug_report``.

    No full ``ButlerDaemon`` bootstrap is needed — ``file_bug_report`` only
    reads ``ctx.pool`` and the routing context var (unlike ``route_to_butler``,
    it never touches ``ctx.daemon``'s pipeline/buffer or the permissions
    matrix), so registering directly against a minimal ``ToolContext`` is
    sufficient and far cheaper than booting a daemon.
    """
    import butlers.ingestion_policy as _ip_mod

    monkeypatch.setattr(_ip_mod, "IngestionPolicyEvaluator", _FakeIngestionPolicyEvaluator)

    registered: dict[str, Any] = {}

    def _core_tool(_group: str, **_kwargs: Any):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    ctx = ToolContext(
        daemon=SimpleNamespace(_pipeline=None, _buffer=None),
        pool=pool,
        spawner=None,
        butler_name="switchboard",
        butler_type=None,
        is_switchboard=True,
        is_messenger=False,
        route_metrics=None,
    )
    # register_switchboard_tools re-imports `route` fresh from the module at
    # call time, so the patch only needs to be active for this call (mirrors
    # tests/daemon/test_dashboard_lane_tools.py's route_patch).
    with patch("butlers.tools.switchboard.routing.route.route", new=mock_route):
        register_switchboard_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["file_bug_report"]


async def test_file_bug_report_lands_qa_finding_with_correct_fingerprint(
    monkeypatch, migrated_core_postgres_pool
) -> None:
    """A dashboard bug report must reach a real ``public.qa_findings`` row.

    Real chain: file_bug_report -> route() -> QaModule._handle_report_finding
    -> ButlerReportsSource buffer -> insert_finding() -> public.qa_findings.
    """
    from butlers.core.routing_context import _routing_ctx_var

    async with migrated_core_postgres_pool() as pool:
        # Real QA module, wired with a real (in-memory) reactive buffer —
        # nothing about report_finding's handling is mocked.
        qa_module = QaModule()
        qa_module._butler_reports_source = ButlerReportsSource()

        async def _fake_route(
            _pool: Any,
            *,
            target_butler: str,
            tool_name: str,
            args: dict[str, Any],
            source_butler: str,
        ) -> dict[str, Any]:
            assert target_butler == "qa"
            assert tool_name == "report_finding"
            result = await qa_module._handle_report_finding(**args)
            return {"result": result}

        file_bug_report = _register_file_bug_report(monkeypatch, pool, _fake_route)

        # A real conversation row for the in-thread ack.
        conv_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.dashboard_conversations
                (id, butler_name, title, status, created_at, updated_at)
            VALUES ($1, 'switchboard', 'Bug report thread', 'active', now(), now())
            """,
            conv_id,
        )

        _routing_ctx_var.set(
            {
                "source_metadata": {"channel": "dashboard", "identity": "dashboard:operator"},
                "request_context": None,
                "request_id": "unknown",
                "dashboard_context": {
                    "conversation_id": str(conv_id),
                    "page_context": {"route": "/entities/concentration"},
                },
            }
        )
        try:
            summary = "The concentration chart is empty for child-of"
            result = await file_bug_report(summary=summary, severity=2)
        finally:
            _routing_ctx_var.set(None)

        assert result["status"] == "ok"
        assert result["filed"] is True
        case_reference = result["case_reference"]
        assert len(case_reference) == 12

        # The canonical fingerprint depends only on exception_type, call_site,
        # and the sanitized message — independently recomputed here (not
        # borrowed from the production code under test) to prove the DB row
        # carries the *correct* fingerprint, not merely *a* fingerprint.
        expected_fp = compute_fingerprint_from_report(
            error_type="DashboardBugReport",
            error_message=summary,
            call_site="dashboard:/entities/concentration",
            traceback_str=None,
        ).fingerprint
        assert case_reference == expected_fp[:12]

        # The finding landed in QA's reactive buffer (source_type=butler_reports).
        buffered = await qa_module._butler_reports_source.discover(lookback_minutes=15)
        assert len(buffered) == 1
        finding = buffered[0]
        assert finding.fingerprint == expected_fp
        assert finding.source_type == "butler_reports"
        assert finding.source_butler == "switchboard"
        assert finding.event_summary == summary

        # Persist it for real, as a patrol cycle's dispatch would, and assert
        # the QA-visible row carries the correct fingerprint end to end.
        patrol_id = await pool.fetchval("INSERT INTO public.qa_patrols DEFAULT VALUES RETURNING id")
        finding_id = await insert_finding(pool, patrol_id, finding, dedup_reason=None)

        row = await pool.fetchrow("SELECT * FROM public.qa_findings WHERE id = $1", finding_id)
        assert row is not None
        assert row["fingerprint"] == expected_fp
        assert row["source_type"] == "butler_reports"
        assert row["source_butler"] == "switchboard"
        assert row["event_summary"] == summary
        assert row["patrol_id"] == patrol_id

        # The owner sees a real in-thread ack carrying the case reference —
        # a genuine public.dashboard_messages row, not a mocked call.
        ack = await pool.fetchrow(
            """
            SELECT role, content FROM public.dashboard_messages
            WHERE conversation_id = $1 AND role = 'assistant'
            """,
            conv_id,
        )
        assert ack is not None
        assert case_reference in ack["content"]


async def test_file_bug_report_relay_failure_does_not_create_qa_finding(
    monkeypatch, migrated_core_postgres_pool
) -> None:
    """When QA is unreachable, no finding is buffered — but the owner still
    gets an honest in-thread ack (never a silent drop)."""
    from butlers.core.routing_context import _routing_ctx_var

    async with migrated_core_postgres_pool() as pool:

        async def _fake_route_error(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"error": "Butler 'qa' not found in registry"}

        file_bug_report = _register_file_bug_report(monkeypatch, pool, _fake_route_error)

        conv_id = uuid.uuid4()
        await pool.execute(
            """
            INSERT INTO public.dashboard_conversations
                (id, butler_name, title, status, created_at, updated_at)
            VALUES ($1, 'switchboard', 'Bug report thread', 'active', now(), now())
            """,
            conv_id,
        )

        _routing_ctx_var.set(
            {
                "source_metadata": {"channel": "dashboard", "identity": "dashboard:operator"},
                "request_context": None,
                "request_id": "unknown",
                "dashboard_context": {"conversation_id": str(conv_id), "page_context": None},
            }
        )
        try:
            result = await file_bug_report(summary="Something is broken")
        finally:
            _routing_ctx_var.set(None)

        assert result["status"] == "error"
        assert result["filed"] is False

        ack = await pool.fetchrow(
            """
            SELECT content FROM public.dashboard_messages
            WHERE conversation_id = $1 AND role = 'assistant'
            """,
            conv_id,
        )
        assert ack is not None
        assert "couldn't file" in ack["content"].lower()

        finding_count = await pool.fetchval("SELECT count(*) FROM public.qa_findings")
        assert finding_count == 0
