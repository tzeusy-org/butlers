"""Tests for the QA Staffer module (task 6.8).

Covers:
- Module ABC compliance, config validation, tool registration, sensitivity
- on_startup source registration and stale patrol recovery
- on_shutdown: cancels watchdog tasks
- wire_runtime, report_finding, force_patrol, get_qa_status handlers
- Patrol overlap prevention, source failure isolation, full cycle
- Prometheus metrics: patrol_total, investigations_active, investigation_duration
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
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


def _make_pool(
    patrol_id: uuid.UUID | None = None,
    skip_patrol_rows: list[dict] | None = None,
) -> MagicMock:
    pool = MagicMock()
    if patrol_id is None:
        patrol_id = uuid.uuid4()

    pool.fetchval = AsyncMock(return_value=patrol_id)
    pool.fetch = AsyncMock(return_value=skip_patrol_rows or [])
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


def _make_db(pool: object | None = None) -> MagicMock:
    db = MagicMock()
    db.pool = pool
    return db


# ---------------------------------------------------------------------------
# Module ABC + config + tool registration + sensitivity
# ---------------------------------------------------------------------------


class TestModuleABCAndConfig:
    def test_module_contract(self):
        mod = _make_module()
        assert issubclass(QaModule, Module) and isinstance(mod, Module)
        assert mod.name == "qa"
        assert mod.config_schema is QaConfig and issubclass(QaConfig, BaseModel)
        assert mod.dependencies == []

    def test_config_defaults_and_validation(self):
        cfg = QaConfig()
        assert cfg.enabled is True and cfg.patrol_interval_minutes == 10
        assert cfg.enabled_sources == ["log_scanner", "session_records", "butler_reports"]

        with pytest.raises(ValidationError):
            QaConfig(unknown_field=True)
        for field in ["patrol_interval_minutes", "log_lookback_minutes",
                      "max_concurrent_investigations"]:
            with pytest.raises(ValidationError):
                QaConfig(**{field: 0})
        with pytest.raises(ValidationError):
            QaConfig(enabled_sources=["unknown_source"])

        assert QaConfig(enabled_sources=["log_scanner"]).enabled_sources == ["log_scanner"]

    def test_tool_metadata_and_registration(self):
        mod = _make_module()
        meta = mod.tool_metadata()
        assert isinstance(meta, dict) and "report_finding" in meta
        rm = meta["report_finding"]
        assert isinstance(rm, ToolMeta)
        assert rm.arg_sensitivities.get("context") is True
        assert "fingerprint" not in rm.arg_sensitivities

    async def test_registers_all_tools(self):
        mod = _make_module()
        registered_tools: list[str] = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered_tools.append(fn.__name__)
                    return fn
                return decorator

        await mod.register_tools(FakeMCP(), QaConfig(), _make_db())
        for name in ["report_finding", "force_patrol", "get_qa_status"]:
            assert name in registered_tools


# ---------------------------------------------------------------------------
# on_startup / on_shutdown
# ---------------------------------------------------------------------------


class TestOnStartup:
    async def test_source_registration_with_and_without_pool(self):
        """Sources register based on pool availability and enabled_sources config."""
        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=None))
        assert mod._pool is None
        source_names = [s.name for s in mod._sources]
        assert "session_records" not in source_names

        mod2 = _make_module()
        pool = _make_pool()
        await mod2.on_startup(QaConfig(), _make_db(pool=pool))
        source_names2 = [s.name for s in mod2._sources]
        for s in ["butler_reports", "log_scanner", "session_records"]:
            assert s in source_names2

        mod3 = _make_module()
        await mod3.on_startup(
            QaConfig(enabled_sources=["log_scanner"]), _make_db(pool=_make_pool())
        )
        names3 = [s.name for s in mod3._sources]
        assert "butler_reports" not in names3 and "log_scanner" in names3

    async def test_recovers_stale_patrol_rows(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[{"id": uuid.uuid4()}])
        mod = _make_module()
        with (
            patch("butlers.modules.qa.recover_stale_attempts",
                  new_callable=AsyncMock, return_value=(0, [])),
            patch("butlers.modules.qa.reap_stale_worktrees", new_callable=AsyncMock),
        ):
            await mod.on_startup(QaConfig(), _make_db(pool=pool))
        assert pool.execute.called


class TestOnShutdown:
    async def test_cancels_watchdog_tasks(self):
        mod = _make_module()
        task = asyncio.create_task(asyncio.sleep(60))
        await asyncio.sleep(0)
        mod._watchdog_tasks = [task]
        await mod.on_shutdown()
        assert task.done() and task.cancelled()

        # No tasks is fine too
        mod2 = _make_module()
        await mod2.on_shutdown()


# ---------------------------------------------------------------------------
# wire_runtime / report_finding / force_patrol / get_qa_status
# ---------------------------------------------------------------------------


class TestWireRuntime:
    def test_wire_runtime_sets_all_fields(self):
        mod = _make_module()
        spawner = MagicMock()
        mod.wire_runtime("qa", spawner, "/repo/root")
        assert mod._butler_name == "qa" and mod._repo_root == Path("/repo/root")


class TestReportFinding:
    async def test_accepted_and_rejected(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))
        result = await mod._handle_report_finding(
            fingerprint="a" * 64, exception_type="ValueError", call_site="mod.py:func",
            severity=2, event_summary="failed", source_butler="general", context=None,
        )
        assert result["accepted"] is True

        mod2 = _make_module()
        await mod2.on_startup(
            QaConfig(enabled_sources=["log_scanner"]), _make_db(pool=_make_pool())
        )
        r2 = await mod2._handle_report_finding(
            fingerprint="a" * 64, exception_type="ValueError", call_site="mod.py:func",
            severity=2, event_summary="failed", source_butler="general", context=None,
        )
        assert r2["accepted"] is False

    @pytest.mark.parametrize("severity,should_schedule", [(0, True), (2, False)])
    async def test_severity_mini_patrol_scheduling(self, severity, should_schedule):
        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=_make_pool()))
        scheduled = False

        def mock_schedule(fp: str) -> None:
            nonlocal scheduled
            scheduled = True

        mod._schedule_mini_patrol = mock_schedule
        await mod._handle_report_finding(
            fingerprint="a" * 64, exception_type="CriticalError", call_site="x.py:y",
            severity=severity, event_summary="critical", source_butler="health", context=None,
        )
        assert scheduled is should_schedule


class TestForcePatrol:
    @pytest.mark.parametrize(
        "setup,reason",
        [
            ({"enabled": False, "pool": True}, "qa_module_disabled"),
            ({"enabled": True, "pool": False}, "no_db_pool"),
        ],
        ids=["disabled", "no-pool"],
    )
    async def test_returns_skipped(self, setup, reason):
        mod = _make_module()
        mod._config = QaConfig(enabled=setup["enabled"])
        mod._pool = _make_pool() if setup["pool"] else None
        result = await mod._handle_force_patrol()
        assert result["status"] == "skipped" and result["reason"] == reason

    async def test_returns_skipped_when_patrol_running(self):
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        mod._pool = _make_pool()
        await mod._patrol_lock.acquire()
        try:
            result = await mod._handle_force_patrol()
        finally:
            mod._patrol_lock.release()
        assert result["reason"] == "patrol_already_running"


class TestGetQaStatus:
    def test_returns_correct_fields_and_defaults(self):
        mod = _make_module()
        status = mod._handle_get_qa_status()
        for k in ["enabled", "last_patrol_at", "last_patrol_status", "last_patrol_findings",
                   "last_patrol_novel", "active_watchdog_tasks", "enabled_sources",
                   "butler_reports_buffer_size"]:
            assert k in status
        assert status["enabled"] is True and status["last_patrol_at"] is None

    async def test_prunes_completed_tasks(self):
        mod = _make_module()
        task = asyncio.create_task(asyncio.sleep(0))
        await task
        mod._watchdog_tasks = [task]
        assert mod._handle_get_qa_status()["active_watchdog_tasks"] == 0


# ---------------------------------------------------------------------------
# Patrol overlap, source failure, full cycle
# ---------------------------------------------------------------------------


class TestPatrolOverlapAndSkip:
    @pytest.mark.parametrize("setup", ["disabled", "no_pool", "overlap"])
    async def test_run_patrol_tick_skips(self, setup):
        mod = _make_module()
        if setup == "disabled":
            mod._config = QaConfig(enabled=False)
            mod._pool = _make_pool()
        elif setup == "no_pool":
            mod._config = QaConfig(enabled=True)
            mod._pool = None
        else:  # overlap
            mod._config = QaConfig(enabled=True)
            mod._pool = _make_pool()
            await mod._patrol_lock.acquire()

        try:
            await mod.run_patrol_tick()
        finally:
            if setup == "overlap":
                mod._patrol_lock.release()


class TestSourceFailureIsolation:
    async def test_failing_source_does_not_abort_patrol(self):
        class FailingSource:
            name = "failing_source"
            async def discover(self, lookback_minutes): raise RuntimeError("down")

        class GoodSource:
            name = "good_source"
            async def discover(self, lookback_minutes): return []

        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        mod._pool = _make_pool()
        mod._sources = [FailingSource(), GoodSource()]

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mt,
            patch("butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock) as md,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
        ):
            mt.return_value = MagicMock(all_findings=[], novel_findings=[], dedup_counts={})
            md.return_value = []
            result = await mod._run_patrol_cycle()
        assert result["status"] == "error"


class TestFullCycleNoFindings:
    async def test_clean_patrol_when_no_findings(self):
        class EmptySource:
            name = "butler_reports"
            async def discover(self, lookback_minutes): return []

        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        mod._pool = _make_pool()
        mod._sources = [EmptySource()]
        mod._butler_reports_source = None

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mt,
            patch("butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock) as md,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
        ):
            mt.return_value = MagicMock(all_findings=[], novel_findings=[], dedup_counts={})
            md.return_value = []
            result = await mod._run_patrol_cycle()
        assert result["status"] == "clean" and result["findings_count"] == 0


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    async def test_patrol_total_incremented(self):
        import butlers.modules.qa as qa_module

        counter_calls = []

        class FakeCounter:
            def labels(self, *, status):
                counter_calls.append(status)
                return self
            def inc(self): pass

        original = qa_module._qa_patrol_total
        try:
            qa_module._qa_patrol_total = FakeCounter()
            mod = _make_module()
            mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
            pool = _make_pool()
            pool.fetchval = AsyncMock(side_effect=[uuid.uuid4(), 0])
            pool.fetch = AsyncMock(return_value=[])
            mod._pool = pool
            mod._sources = []
            with (
                patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mt,
                patch("butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock) as md,
                patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
            ):
                mt.return_value = MagicMock(all_findings=[], novel_findings=[], dedup_counts={})
                md.return_value = []
                await mod._run_patrol_cycle()
            assert "clean" in counter_calls
        finally:
            qa_module._qa_patrol_total = original

    async def test_investigations_active_and_duration(self):
        import butlers.modules.qa as qa_module

        gauge_values, observed = [], []

        class FakeGauge:
            def set(self, value): gauge_values.append(value)

        class FakeHistogram:
            def labels(self, *, status):
                self._status = status
                return self
            def observe(self, value): observed.append((self._status, value))

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=3)
        pool.fetch = AsyncMock(return_value=[
            {"status": "pr_merged", "duration_seconds": 120.5},
        ])

        orig_g = qa_module._qa_investigations_active
        orig_h = qa_module._qa_investigation_duration_seconds
        try:
            qa_module._qa_investigations_active = FakeGauge()
            qa_module._qa_investigation_duration_seconds = FakeHistogram()
            mod = _make_module()
            mod._config = QaConfig()
            await mod._record_investigation_metrics(pool)
            assert gauge_values == [3]
            assert ("pr_merged", 120.5) in observed
        finally:
            qa_module._qa_investigations_active = orig_g
            qa_module._qa_investigation_duration_seconds = orig_h

    async def test_db_error_swallowed_but_cancelled_error_propagates(self):
        import butlers.modules.qa as qa_module

        pool = _make_pool()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
        pool.fetch = AsyncMock(side_effect=RuntimeError("db down"))

        orig = qa_module._qa_investigations_active
        try:
            qa_module._qa_investigations_active = None
            mod = _make_module()
            mod._config = QaConfig()
            await mod._record_investigation_metrics(pool)  # must not raise
        finally:
            qa_module._qa_investigations_active = orig

        # CancelledError must propagate
        pool2 = _make_pool()
        pool2.fetchval = AsyncMock(side_effect=asyncio.CancelledError())
        mod2 = _make_module()
        mod2._config = QaConfig()
        with pytest.raises(asyncio.CancelledError):
            await mod2._record_investigation_metrics(pool2)

    async def test_duration_uses_last_patrol_at_as_high_water_mark(self):
        fetch_calls = []

        async def capturing_fetch(sql, *args):
            fetch_calls.append({"args": args})
            return []

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=0)
        pool.fetch = capturing_fetch

        mod = _make_module()
        mod._config = QaConfig()
        last_at = datetime.now(UTC)
        mod._last_patrol_at = last_at
        await mod._record_investigation_metrics(pool)
        assert any(last_at in c["args"] for c in fetch_calls)
