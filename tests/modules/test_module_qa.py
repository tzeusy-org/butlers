"""Tests for the QA Staffer module (task 6.8).

Covers:
- Module ABC compliance (name, config_schema, dependencies, migration_revisions)
- QaConfig defaults, validation, extra fields rejected
- Tool registration (report_finding, force_patrol, get_qa_status)
- Sensitivity metadata (context and event_summary are sensitive on report_finding)
- on_startup: registers sources, skips recovery when no pool
- on_startup: recovers stale patrol rows
- on_shutdown: cancels watchdog tasks
- wire_runtime: wires butler_name, spawner, repo_root
- report_finding: accepted=True, queues in butler_reports source
- report_finding: severity-0 triggers mini-patrol scheduling
- report_finding: butler_reports not registered → accepted=False
- force_patrol: locked → skipped
- force_patrol: no pool → skipped
- force_patrol: disabled → skipped
- get_qa_status: returns correct fields
- run_patrol_tick: overlap prevention (skipped_overlap)
- run_patrol_tick: disabled module → skip
- _run_patrol_cycle: source failure is isolated (other sources still run)
- _run_patrol_cycle: full cycle with no findings → clean
- _run_patrol_cycle: clean status when novel but no dispatches
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.qa import QaConfig, QaModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_module() -> QaModule:
    return QaModule()


def _make_config(**kwargs) -> QaConfig:
    return QaConfig(**kwargs)


def _make_pool(
    patrol_id: uuid.UUID | None = None,
    skip_patrol_rows: list[dict] | None = None,
) -> MagicMock:
    """Mock asyncpg Pool for module tests."""
    pool = MagicMock()
    if patrol_id is None:
        patrol_id = uuid.uuid4()

    async def fetchval(*args, **kwargs):
        return patrol_id

    async def fetch(*args, **kwargs):
        return skip_patrol_rows or []

    async def execute(*args, **kwargs):
        pass

    pool.fetchval = AsyncMock(side_effect=fetchval)
    pool.fetch = AsyncMock(side_effect=fetch)
    pool.execute = AsyncMock(side_effect=execute)
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


def _make_db(pool: object | None = None) -> MagicMock:
    db = MagicMock()
    db.pool = pool
    return db


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABC:
    def test_is_subclass_of_module(self):
        assert issubclass(QaModule, Module)

    def test_instantiates(self):
        mod = _make_module()
        assert isinstance(mod, Module)

    def test_name(self):
        mod = _make_module()
        assert mod.name == "qa"

    def test_config_schema_is_qa_config(self):
        mod = _make_module()
        assert mod.config_schema is QaConfig

    def test_config_schema_is_pydantic_model(self):
        mod = _make_module()
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_is_empty_list(self):
        mod = _make_module()
        assert mod.dependencies == []

    def test_migration_revisions_is_none(self):
        mod = _make_module()
        assert mod.migration_revisions() is None


# ---------------------------------------------------------------------------
# QaConfig schema
# ---------------------------------------------------------------------------


class TestQaConfig:
    def test_defaults(self):
        cfg = QaConfig()
        assert cfg.enabled is True
        assert cfg.patrol_interval_minutes == 10
        assert cfg.log_lookback_minutes == 15
        assert cfg.max_concurrent_investigations == 2
        assert cfg.severity_threshold == 2
        assert cfg.enabled_sources == ["log_scanner", "session_records", "butler_reports"]
        assert cfg.max_reactive_buffer == 50
        assert cfg.dashboard_base_url is None

    def test_enabled_false(self):
        cfg = QaConfig(enabled=False)
        assert cfg.enabled is False

    def test_override_all_fields(self):
        cfg = QaConfig(
            patrol_interval_minutes=5,
            log_lookback_minutes=30,
            max_concurrent_investigations=4,
            severity_threshold=1,
            enabled_sources=["log_scanner"],
            max_reactive_buffer=25,
            dashboard_base_url="https://example.com",
        )
        assert cfg.patrol_interval_minutes == 5
        assert cfg.log_lookback_minutes == 30
        assert cfg.max_concurrent_investigations == 4
        assert cfg.severity_threshold == 1
        assert cfg.enabled_sources == ["log_scanner"]
        assert cfg.max_reactive_buffer == 25
        assert cfg.dashboard_base_url == "https://example.com"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            QaConfig(unknown_field=True)

    def test_from_dict(self):
        cfg = QaConfig(**{"enabled": False, "patrol_interval_minutes": 5})
        assert cfg.enabled is False
        assert cfg.patrol_interval_minutes == 5

    def test_patrol_interval_zero_invalid(self):
        with pytest.raises(ValidationError):
            QaConfig(patrol_interval_minutes=0)

    def test_log_lookback_zero_invalid(self):
        with pytest.raises(ValidationError):
            QaConfig(log_lookback_minutes=0)

    def test_max_concurrent_zero_invalid(self):
        with pytest.raises(ValidationError):
            QaConfig(max_concurrent_investigations=0)

    def test_unknown_source_invalid(self):
        with pytest.raises(ValidationError):
            QaConfig(enabled_sources=["unknown_source"])

    def test_valid_sources_subset(self):
        cfg = QaConfig(enabled_sources=["log_scanner"])
        assert cfg.enabled_sources == ["log_scanner"]


# ---------------------------------------------------------------------------
# Sensitivity metadata
# ---------------------------------------------------------------------------


class TestToolMetadata:
    def test_tool_metadata_returns_dict(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        assert isinstance(meta, dict)

    def test_report_finding_has_sensitive_args(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        assert "report_finding" in meta
        report_meta = meta["report_finding"]
        assert isinstance(report_meta, ToolMeta)
        assert report_meta.arg_sensitivities.get("context") is True
        assert report_meta.arg_sensitivities.get("event_summary") is True

    def test_non_sensitive_fields_not_listed(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        report_meta = meta["report_finding"]
        assert "fingerprint" not in report_meta.arg_sensitivities
        assert "exception_type" not in report_meta.arg_sensitivities


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestRegisterTools:
    async def test_registers_report_finding(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        await mod.register_tools(FakeMCP(), QaConfig(), _make_db())
        assert "report_finding" in registered_tools

    async def test_registers_force_patrol(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        await mod.register_tools(FakeMCP(), QaConfig(), _make_db())
        assert "force_patrol" in registered_tools

    async def test_registers_get_qa_status(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        await mod.register_tools(FakeMCP(), QaConfig(), _make_db())
        assert "get_qa_status" in registered_tools

    async def test_accepts_dict_config(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn

                return decorator

        await mod.register_tools(FakeMCP(), {"patrol_interval_minutes": 5}, _make_db())
        assert mod._config.patrol_interval_minutes == 5


# ---------------------------------------------------------------------------
# on_startup
# ---------------------------------------------------------------------------


class TestOnStartup:
    async def test_no_pool_skips_recovery(self):
        """on_startup without a pool should not raise and skips recovery."""
        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=None))
        # Should not raise; no sources requiring a pool are registered
        assert mod._pool is None

    async def test_registers_butler_reports_source(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))
        source_names = [s.name for s in mod._sources]
        assert "butler_reports" in source_names

    async def test_registers_log_scanner_source(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))
        source_names = [s.name for s in mod._sources]
        assert "log_scanner" in source_names

    async def test_registers_session_records_source_with_pool(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))
        source_names = [s.name for s in mod._sources]
        assert "session_records" in source_names

    async def test_session_records_skipped_without_pool(self):
        """session_records source requires a pool and is skipped when absent."""
        mod = _make_module()
        # on_startup with pool=None: session_records skipped
        await mod.on_startup(QaConfig(), _make_db(pool=None))
        source_names = [s.name for s in mod._sources]
        # butler_reports and log_scanner don't need a pool at construction time
        assert "session_records" not in source_names

    async def test_disabled_sources_not_registered(self):
        mod = _make_module()
        pool = _make_pool()
        cfg = QaConfig(enabled_sources=["log_scanner"])
        await mod.on_startup(cfg, _make_db(pool=pool))
        source_names = [s.name for s in mod._sources]
        assert "butler_reports" not in source_names
        assert "session_records" not in source_names
        assert "log_scanner" in source_names

    async def test_recovers_stale_patrol_rows(self):
        """Stale 'running' patrol rows are recovered on startup."""
        stale_id = uuid.uuid4()
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[{"id": stale_id}])

        mod = _make_module()

        with (
            patch(
                "butlers.modules.qa.recover_stale_attempts",
                new_callable=AsyncMock,
            ) as mock_recover,
            patch("butlers.modules.qa.reap_stale_worktrees", new_callable=AsyncMock),
        ):
            mock_recover.return_value = (0, [])
            await mod.on_startup(QaConfig(), _make_db(pool=pool))

        # Should have called execute to update stale rows
        assert pool.execute.called

    async def test_dict_config_is_coerced(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup({"patrol_interval_minutes": 7}, _make_db(pool=pool))
        assert mod._config.patrol_interval_minutes == 7


# ---------------------------------------------------------------------------
# on_shutdown
# ---------------------------------------------------------------------------


class TestOnShutdown:
    async def test_cancels_watchdog_tasks(self):
        mod = _make_module()

        async def _bg_task():
            await asyncio.sleep(60)

        task = asyncio.create_task(_bg_task())
        # Yield to let the task start
        await asyncio.sleep(0)
        mod._watchdog_tasks = [task]
        await mod.on_shutdown()

        assert task.done()
        assert task.cancelled()
        assert len(mod._watchdog_tasks) == 0

    async def test_no_tasks_is_ok(self):
        mod = _make_module()
        await mod.on_shutdown()  # should not raise


# ---------------------------------------------------------------------------
# wire_runtime
# ---------------------------------------------------------------------------


class TestWireRuntime:
    def test_wire_runtime_sets_butler_name(self):
        mod = _make_module()
        mod.wire_runtime("qa", MagicMock(), "/repo")
        assert mod._butler_name == "qa"

    def test_wire_runtime_sets_spawner(self):
        mod = _make_module()
        spawner = MagicMock()
        mod.wire_runtime("qa", spawner, "/repo")
        assert mod._spawner is spawner

    def test_wire_runtime_sets_repo_root(self):
        mod = _make_module()
        mod.wire_runtime("qa", MagicMock(), "/repo/root")
        assert mod._repo_root == Path("/repo/root")


# ---------------------------------------------------------------------------
# report_finding tool handler
# ---------------------------------------------------------------------------


class TestReportFinding:
    async def test_accepted_queues_in_butler_reports(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))

        result = await mod._handle_report_finding(
            fingerprint="a" * 64,
            exception_type="ValueError",
            call_site="mod.py:func",
            severity=2,
            event_summary="something failed",
            source_butler="general",
            context=None,
        )

        assert result["accepted"] is True
        assert mod._butler_reports_source is not None
        assert mod._butler_reports_source.buffer_size == 1

    async def test_butler_reports_not_registered(self):
        """When butler_reports is not in enabled_sources, report_finding rejects."""
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(
            QaConfig(enabled_sources=["log_scanner"]),
            _make_db(pool=pool),
        )

        result = await mod._handle_report_finding(
            fingerprint="a" * 64,
            exception_type="ValueError",
            call_site="mod.py:func",
            severity=2,
            event_summary="something failed",
            source_butler="general",
            context=None,
        )

        assert result["accepted"] is False
        assert result["reason"] == "butler_reports_disabled"

    async def test_severity_zero_schedules_mini_patrol(self):
        """Severity-0 finding triggers a mini-patrol scheduling."""
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))

        mini_patrol_scheduled = False

        def mock_schedule(fp: str) -> None:
            nonlocal mini_patrol_scheduled
            mini_patrol_scheduled = True
            # Don't actually create a task
            pass

        mod._schedule_mini_patrol = mock_schedule

        await mod._handle_report_finding(
            fingerprint="a" * 64,
            exception_type="CriticalError",
            call_site="critical.py:boom",
            severity=0,
            event_summary="critical failure",
            source_butler="health",
            context=None,
        )

        assert mini_patrol_scheduled is True

    async def test_severity_nonzero_no_mini_patrol(self):
        """Non-critical findings do not trigger mini-patrol."""
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))

        mini_patrol_scheduled = False

        def mock_schedule(fp: str) -> None:
            nonlocal mini_patrol_scheduled
            mini_patrol_scheduled = True

        mod._schedule_mini_patrol = mock_schedule

        await mod._handle_report_finding(
            fingerprint="b" * 64,
            exception_type="ValueError",
            call_site="x.py:y",
            severity=2,
            event_summary="medium error",
            source_butler="finance",
            context=None,
        )

        assert mini_patrol_scheduled is False


# ---------------------------------------------------------------------------
# force_patrol tool handler
# ---------------------------------------------------------------------------


class TestForcePatrol:
    async def test_returns_skipped_when_disabled(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=False)
        result = await mod._handle_force_patrol()
        assert result["status"] == "skipped"
        assert result["reason"] == "qa_module_disabled"

    async def test_returns_skipped_when_no_pool(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        mod._pool = None
        result = await mod._handle_force_patrol()
        assert result["status"] == "skipped"
        assert result["reason"] == "no_db_pool"

    async def test_returns_skipped_when_patrol_running(self):
        """force_patrol skips if a patrol is already running (lock held)."""
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        pool = _make_pool()
        mod._pool = pool

        # Acquire the lock to simulate a running patrol
        await mod._patrol_lock.acquire()
        try:
            result = await mod._handle_force_patrol()
        finally:
            mod._patrol_lock.release()

        assert result["status"] == "skipped"
        assert result["reason"] == "patrol_already_running"


# ---------------------------------------------------------------------------
# get_qa_status tool handler
# ---------------------------------------------------------------------------


class TestGetQaStatus:
    def test_returns_all_expected_fields(self):
        mod = _make_module()
        status = mod._handle_get_qa_status()
        assert "enabled" in status
        assert "last_patrol_at" in status
        assert "last_patrol_status" in status
        assert "last_patrol_findings" in status
        assert "last_patrol_novel" in status
        assert "last_patrol_dispatched" in status
        assert "active_watchdog_tasks" in status
        assert "enabled_sources" in status
        assert "patrol_interval_minutes" in status
        assert "log_lookback_minutes" in status
        assert "max_concurrent_investigations" in status
        assert "severity_threshold" in status
        assert "butler_reports_buffer_size" in status

    def test_returns_correct_defaults(self):
        mod = _make_module()
        status = mod._handle_get_qa_status()
        assert status["enabled"] is True
        assert status["last_patrol_at"] is None
        assert status["last_patrol_status"] is None
        assert status["last_patrol_findings"] == 0
        assert status["active_watchdog_tasks"] == 0
        assert status["butler_reports_buffer_size"] == 0

    async def test_prunes_completed_tasks(self):
        mod = _make_module()

        # Add a completed task
        async def _noop():
            pass

        task = asyncio.create_task(_noop())
        await task  # Let it complete
        mod._watchdog_tasks = [task]

        status = mod._handle_get_qa_status()
        assert status["active_watchdog_tasks"] == 0
        assert len(mod._watchdog_tasks) == 0


# ---------------------------------------------------------------------------
# Patrol overlap prevention
# ---------------------------------------------------------------------------


class TestPatrolOverlapPrevention:
    async def test_run_patrol_tick_disabled_skips(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=False)
        mod._pool = _make_pool()

        # Should not raise or call any DB methods
        await mod.run_patrol_tick()

    async def test_run_patrol_tick_no_pool_skips(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        mod._pool = None

        await mod.run_patrol_tick()  # Should not raise

    async def test_run_patrol_tick_overlap_records_skip(self):
        """When patrol is already running, new tick records skipped_overlap."""
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        pool = _make_pool()
        mod._pool = pool

        # Hold the patrol lock to simulate running patrol
        await mod._patrol_lock.acquire()
        try:
            await mod.run_patrol_tick()
        finally:
            mod._patrol_lock.release()

        # Should have attempted to insert a skipped_overlap row
        assert pool.execute.called


# ---------------------------------------------------------------------------
# Source failure isolation
# ---------------------------------------------------------------------------


class TestSourceFailureIsolation:
    async def test_failing_source_does_not_abort_patrol(self):
        """A source that raises is logged but the patrol continues."""

        class FailingSource:
            @property
            def name(self) -> str:
                return "failing_source"

            async def discover(self, lookback_minutes: int):
                raise RuntimeError("Source is down")

        class GoodSource:
            @property
            def name(self) -> str:
                return "good_source"

            async def discover(self, lookback_minutes: int):
                return []  # empty findings

        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        pool = _make_pool()
        mod._pool = pool
        mod._sources = [FailingSource(), GoodSource()]

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mock_triage,
            patch(
                "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
            ) as mock_dispatch,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
        ):
            mock_triage.return_value = MagicMock(
                all_findings=[], novel_findings=[], dedup_counts={}
            )
            mock_dispatch.return_value = []

            result = await mod._run_patrol_cycle()

        # Patrol should complete (with error status due to failing source)
        assert result["status"] == "error"
        # good_source should still have been polled
        assert "good_source" in result.get("sources_polled", [])


# ---------------------------------------------------------------------------
# Full cycle — no findings
# ---------------------------------------------------------------------------


class TestFullCycleNoFindings:
    async def test_clean_patrol_when_no_findings(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        pool = _make_pool()
        mod._pool = pool

        class EmptySource:
            @property
            def name(self) -> str:
                return "butler_reports"

            async def discover(self, lookback_minutes: int):
                return []

        mod._sources = [EmptySource()]
        mod._butler_reports_source = None  # prevent mini-patrol interactions

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mock_triage,
            patch(
                "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
            ) as mock_dispatch,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
        ):
            mock_triage.return_value = MagicMock(
                all_findings=[], novel_findings=[], dedup_counts={}
            )
            mock_dispatch.return_value = []

            result = await mod._run_patrol_cycle()

        assert result["status"] == "clean"
        assert result["findings_count"] == 0
        assert result["novel_count"] == 0
        assert result["dispatched_count"] == 0

    async def test_state_updated_after_clean_patrol(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        pool = _make_pool()
        mod._pool = pool
        mod._sources = []

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mock_triage,
            patch(
                "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
            ) as mock_dispatch,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
        ):
            mock_triage.return_value = MagicMock(
                all_findings=[], novel_findings=[], dedup_counts={}
            )
            mock_dispatch.return_value = []

            await mod._run_patrol_cycle()

        assert mod._last_patrol_at is not None
        assert mod._last_patrol_status == "clean"
        assert mod._last_patrol_findings == 0


# ---------------------------------------------------------------------------
# OTel span instrumentation
# ---------------------------------------------------------------------------


class TestQaPatrolOtelSpans:
    """Verify qa.patrol, qa.discover.*, qa.triage, and qa.dispatch spans are created."""

    async def _run_clean_patrol_with_otel(self, tmp_path) -> object:
        """Run a clean patrol cycle with an in-memory OTel exporter and return the exporter."""
        import opentelemetry.trace as real_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        import butlers.modules.qa as qa_mod

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        real_trace.set_tracer_provider(provider)

        # Wire the module-level tracer to our test provider
        qa_mod._tracer = provider.get_tracer("butlers.qa")
        qa_mod._HAS_OTEL = True

        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        pool = _make_pool()
        mod._pool = pool

        class EmptySource:
            @property
            def name(self) -> str:
                return "butler_reports"

            async def discover(self, lookback_minutes: int):
                return []

        mod._sources = [EmptySource()]
        mod._butler_reports_source = None

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mock_triage,
            patch(
                "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
            ) as mock_dispatch,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
        ):
            mock_triage.return_value = MagicMock(
                all_findings=[], novel_findings=[], dedup_counts={}
            )
            mock_dispatch.return_value = []

            await mod._run_patrol_cycle()

        return exporter

    async def test_patrol_span_created(self, tmp_path) -> None:
        """qa.patrol span is created for each patrol cycle."""
        exporter = await self._run_clean_patrol_with_otel(tmp_path)
        finished = exporter.get_finished_spans()
        patrol_spans = [s for s in finished if s.name == "qa.patrol"]
        assert patrol_spans, "Expected qa.patrol span to be created"

    async def test_patrol_span_attributes(self, tmp_path) -> None:
        """qa.patrol span has patrol_id and butler.name attributes."""
        exporter = await self._run_clean_patrol_with_otel(tmp_path)
        finished = exporter.get_finished_spans()
        patrol_spans = [s for s in finished if s.name == "qa.patrol"]
        assert patrol_spans
        span = patrol_spans[0]
        assert span.attributes.get("butler.name") == "qa"
        assert "qa.patrol_id" in span.attributes
        # sources_polled set to 1 (one source polled)
        assert span.attributes.get("qa.sources_polled") == 1

    async def test_discover_source_span_created(self, tmp_path) -> None:
        """qa.discover.<source> child span is created for each source polled."""
        exporter = await self._run_clean_patrol_with_otel(tmp_path)
        finished = exporter.get_finished_spans()
        discover_spans = [s for s in finished if s.name.startswith("qa.discover.")]
        assert discover_spans, "Expected at least one qa.discover.* span"
        assert discover_spans[0].name == "qa.discover.butler_reports"

    async def test_triage_span_created(self, tmp_path) -> None:
        """qa.triage child span is created for the triage phase."""
        exporter = await self._run_clean_patrol_with_otel(tmp_path)
        finished = exporter.get_finished_spans()
        triage_spans = [s for s in finished if s.name == "qa.triage"]
        assert triage_spans, "Expected qa.triage span to be created"

    async def test_dispatch_span_created(self, tmp_path) -> None:
        """qa.dispatch child span is created for the dispatch phase."""
        exporter = await self._run_clean_patrol_with_otel(tmp_path)
        finished = exporter.get_finished_spans()
        dispatch_spans = [s for s in finished if s.name == "qa.dispatch"]
        assert dispatch_spans, "Expected qa.dispatch span to be created"

    async def test_patrol_span_ends(self, tmp_path) -> None:
        """qa.patrol span is ended (recorded in exporter) after patrol cycle."""
        exporter = await self._run_clean_patrol_with_otel(tmp_path)
        finished = exporter.get_finished_spans()
        patrol_spans = [s for s in finished if s.name == "qa.patrol"]
        # All returned spans are already finished (exported)
        assert len(patrol_spans) == 1

    async def test_no_otel_no_error(self, tmp_path) -> None:
        """Patrol cycle completes without error when _HAS_OTEL is False."""
        import butlers.modules.qa as qa_mod

        orig = qa_mod._HAS_OTEL
        qa_mod._HAS_OTEL = False
        try:
            mod = _make_module()
            mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
            pool = _make_pool()
            mod._pool = pool
            mod._sources = []
            mod._butler_reports_source = None

            with (
                patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mt,
                patch(
                    "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
                ) as md,
                patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
            ):
                mt.return_value = MagicMock(all_findings=[], novel_findings=[], dedup_counts={})
                md.return_value = []

                result = await mod._run_patrol_cycle()

            assert result["status"] == "clean"
        finally:
            qa_mod._HAS_OTEL = orig
