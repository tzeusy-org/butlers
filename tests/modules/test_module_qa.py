"""Tests for the QA Staffer module (task 6.8).

Covers:
- Module ABC compliance, config validation, tool registration, sensitivity
- on_startup source registration and stale patrol recovery
- on_shutdown: cancels watchdog tasks
- wire_runtime, report_finding, force_patrol, get_qa_status handlers
- Patrol overlap prevention, source failure isolation, full cycle
- Prometheus metrics: patrol_total, investigations_active, investigation_duration
- wire_runtime: accepts optional notify_fn
- _notify_missing_gh_token: calls notify_fn when token is absent
- _notify_missing_gh_token: rate-limits to once per patrol cycle
- _notify_missing_gh_token: no-op when notify_fn is None
- _run_patrol_body: calls _notify_missing_gh_token when gh_token is None
- report_finding: canonicalizes fingerprint (ignores caller-supplied)
- report_finding: normalizes out-of-range severity values
- report_finding: stable dedup fingerprint across repeated reports of same error
- _qa_finding_from_row: handles structured_evidence as dict, JSON string, or None
- _qa_finding_from_row: uses None for timestamp when last_seen is None
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ValidationError

from butlers.core.healing.fingerprint import compute_fingerprint_from_report
from butlers.modules.base import Module, ToolMeta
from butlers.modules.qa import QaConfig, QaModule, _qa_finding_from_row

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
        assert cfg.enabled_sources == [
            "log_scanner",
            "session_records",
            "butler_reports",
            "tool_call_failures",
        ]

        with pytest.raises(ValidationError):
            QaConfig(unknown_field=True)
        for field in [
            "patrol_interval_minutes",
            "log_lookback_minutes",
            "max_concurrent_investigations",
        ]:
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

        await mod.register_tools(FakeMCP(), QaConfig(), _make_db(), "test-butler")
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
        assert mod._log_scanner_source is not None
        assert mod._log_scanner_source._suppress_session_duplicate_timeouts is False

        mod2 = _make_module()
        pool = _make_pool()
        await mod2.on_startup(QaConfig(), _make_db(pool=pool))
        source_names2 = [s.name for s in mod2._sources]
        for s in ["butler_reports", "log_scanner", "session_records"]:
            assert s in source_names2
        assert mod2._log_scanner_source is not None
        assert mod2._log_scanner_source._suppress_session_duplicate_timeouts is True

        mod3 = _make_module()
        await mod3.on_startup(
            QaConfig(enabled_sources=["log_scanner"]), _make_db(pool=_make_pool())
        )
        names3 = [s.name for s in mod3._sources]
        assert "butler_reports" not in names3 and "log_scanner" in names3
        assert mod3._log_scanner_source is not None
        assert mod3._log_scanner_source._suppress_session_duplicate_timeouts is False

    async def test_recovers_stale_patrol_rows(self):
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[{"id": uuid.uuid4()}])
        mod = _make_module()
        with (
            patch(
                "butlers.modules.qa.recover_stale_attempts",
                new_callable=AsyncMock,
                return_value=0,
            ),
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
        mod.wire_runtime(spawner, "/repo/root")
        assert mod._repo_root == Path("/repo/root")


class TestReportFinding:
    async def test_accepted_and_rejected(self):
        mod = _make_module()
        pool = _make_pool()
        await mod.on_startup(QaConfig(), _make_db(pool=pool))
        result = await mod._handle_report_finding(
            fingerprint="a" * 64,
            exception_type="ValueError",
            call_site="mod.py:func",
            severity=2,
            event_summary="failed",
            source_butler="general",
            context=None,
        )
        assert result["accepted"] is True

        mod2 = _make_module()
        await mod2.on_startup(
            QaConfig(enabled_sources=["log_scanner"]), _make_db(pool=_make_pool())
        )
        r2 = await mod2._handle_report_finding(
            fingerprint="a" * 64,
            exception_type="ValueError",
            call_site="mod.py:func",
            severity=2,
            event_summary="failed",
            source_butler="general",
            context=None,
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
            fingerprint="a" * 64,
            exception_type="CriticalError",
            call_site="x.py:y",
            severity=severity,
            event_summary="critical",
            source_butler="health",
            context=None,
        )
        assert scheduled is should_schedule

    async def test_fingerprint_canonicalization_ignores_caller_value(self):
        """Caller-supplied fingerprint is replaced by canonical computation."""
        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=_make_pool()))

        bogus_fingerprint = "b" * 64
        canonical = compute_fingerprint_from_report(
            error_type="ValueError",
            error_message="something went wrong",
            call_site="mod.py:do_work",
            traceback_str=None,
            severity_hint="medium",
        )

        await mod._handle_report_finding(
            fingerprint=bogus_fingerprint,
            exception_type="ValueError",
            call_site="mod.py:do_work",
            severity=2,
            event_summary="something went wrong",
            source_butler="finance",
            context=None,
        )

        findings = await mod._butler_reports_source.discover(lookback_minutes=15)
        assert len(findings) == 1
        assert findings[0].fingerprint == canonical.fingerprint
        assert findings[0].fingerprint != bogus_fingerprint

    async def test_stable_dedup_fingerprint_across_repeated_reports(self):
        """Same error reported twice always produces the same canonical fingerprint."""
        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=_make_pool()))

        common_args = dict(
            exception_type="asyncpg.PostgresError",
            call_site="src/butlers/core/db.py:connect",
            severity=2,
            event_summary="connection refused",
            source_butler="finance",
            context=None,
        )

        await mod._handle_report_finding(fingerprint="x" * 64, **common_args)
        await mod._handle_report_finding(fingerprint="y" * 64, **common_args)

        findings = await mod._butler_reports_source.discover(lookback_minutes=15)
        assert len(findings) == 2
        # Both findings should share the same canonical fingerprint
        assert findings[0].fingerprint == findings[1].fingerprint

    @pytest.mark.parametrize(
        "bad_severity,expected_accepted",
        [
            (-1, True),
            (5, True),
            (99, True),
            (-100, True),
        ],
    )
    async def test_out_of_range_severity_clamped(self, bad_severity, expected_accepted, caplog):
        """Out-of-range severity is clamped; report is still accepted and no crash occurs."""
        import logging

        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=_make_pool()))

        with caplog.at_level(logging.WARNING):
            result = await mod._handle_report_finding(
                fingerprint="a" * 64,
                exception_type="ValueError",
                call_site="mod.py:func",
                severity=bad_severity,
                event_summary="some error",
                source_butler="general",
                context=None,
            )

        assert result["accepted"] is expected_accepted
        # A warning must be logged for out-of-range values
        warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("out-of-range" in m.lower() or "clamping" in m.lower() for m in warn_msgs)

        # The persisted severity must be within the valid DB range 0–4
        findings = await mod._butler_reports_source.discover(lookback_minutes=15)
        assert len(findings) == 1
        assert 0 <= findings[0].severity <= 4

    async def test_canonical_severity_drives_mini_patrol_not_caller_severity(self):
        """When canonical severity is 0 (critical), mini-patrol fires even if caller passed higher severity."""
        mod = _make_module()
        await mod.on_startup(QaConfig(), _make_db(pool=_make_pool()))
        scheduled_fps = []

        def mock_schedule(fp: str) -> None:
            scheduled_fps.append(fp)

        mod._schedule_mini_patrol = mock_schedule

        # asyncpg errors are auto-scored as critical (0) regardless of hint
        await mod._handle_report_finding(
            fingerprint="z" * 64,
            exception_type="asyncpg.PostgresError",
            call_site="src/butlers/core/db.py:connect",
            severity=2,  # caller says medium
            event_summary="connection refused",
            source_butler="general",
            context=None,
        )

        # canonical severity for asyncpg is 0 → mini-patrol must have been scheduled
        assert len(scheduled_fps) == 1


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

    async def test_succeeds_when_lock_free(self):
        """Regression (bu-tg9n9): force-patrol must run when no patrol is
        actually running.  The old ``wait_for(acquire(), timeout=0)`` acquire
        always raised TimeoutError even for a *free* lock, falsely reporting
        ``patrol_already_running``.
        """
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        mod._pool = _make_pool()
        mod._run_patrol_body = AsyncMock(
            return_value={
                "status": "completed",
                "patrol_id": "abc",
                "findings_count": 2,
                "novel_count": 1,
                "dispatched_count": 0,
                "sources_polled": ["log_scanner"],
            }
        )
        assert not mod._patrol_lock.locked()
        result = await mod._handle_force_patrol()
        mod._run_patrol_body.assert_awaited_once()
        assert result["status"] == "completed"
        assert result["patrol_id"] == "abc"
        assert result.get("reason") != "patrol_already_running"
        # Lock must be released after the cycle so the next patrol can run.
        assert not mod._patrol_lock.locked()

    async def test_lock_released_on_error(self):
        """The patrol lock must be released even when the patrol body raises,
        otherwise a single failure would permanently wedge force-patrol with a
        false ``patrol_already_running``.
        """
        mod = _make_module()
        mod._config = QaConfig(enabled=True)
        mod._pool = _make_pool()
        mod._run_patrol_body = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            await mod._handle_force_patrol()
        assert not mod._patrol_lock.locked()
        # A subsequent run with the lock free must NOT be falsely rejected.
        mod._run_patrol_body = AsyncMock(return_value={"status": "completed"})
        result = await mod._handle_force_patrol()
        assert result["status"] == "completed"


class TestGetQaStatus:
    def test_returns_correct_fields_and_defaults(self):
        mod = _make_module()
        status = mod._handle_get_qa_status()
        for k in [
            "enabled",
            "last_patrol_at",
            "last_patrol_status",
            "last_patrol_findings",
            "last_patrol_novel",
            "active_watchdog_tasks",
            "enabled_sources",
            "butler_reports_buffer_size",
        ]:
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

            async def discover(self, lookback_minutes):
                raise RuntimeError("down")

        class GoodSource:
            name = "good_source"

            async def discover(self, lookback_minutes):
                return []

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

            async def discover(self, lookback_minutes):
                return []

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

            def inc(self):
                pass

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
            def set(self, value):
                gauge_values.append(value)

        class FakeHistogram:
            def labels(self, *, status):
                self._status = status
                return self

            def observe(self, value):
                observed.append((self._status, value))

        pool = _make_pool()
        pool.fetchval = AsyncMock(return_value=3)
        pool.fetch = AsyncMock(
            return_value=[
                {"status": "pr_merged", "duration_seconds": 120.5},
            ]
        )

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


# ---------------------------------------------------------------------------
# wire_runtime: notify_fn injection
# ---------------------------------------------------------------------------


class TestWireRuntimeNotifyFn:
    def test_wire_runtime_accepts_notify_fn(self):
        """wire_runtime stores notify_fn when provided."""
        mod = _make_module()
        spawner = MagicMock()

        async def _fake_notify(**kwargs):
            return {}

        mod.wire_runtime(spawner, "/repo/root", notify_fn=_fake_notify)
        assert mod._notify_fn is _fake_notify

    def test_wire_runtime_notify_fn_defaults_none(self):
        """wire_runtime notify_fn defaults to None when not provided."""
        mod = _make_module()
        spawner = MagicMock()
        mod.wire_runtime(spawner, "/repo/root")
        assert mod._notify_fn is None


# ---------------------------------------------------------------------------
# _notify_missing_gh_token
# ---------------------------------------------------------------------------


class TestNotifyMissingGhToken:
    async def test_calls_notify_fn_when_provided(self):
        """_notify_missing_gh_token calls notify_fn with telegram/high."""
        mod = _make_module()
        notify_calls: list[dict] = []

        async def _fake_notify(**kwargs):
            notify_calls.append(kwargs)
            return {}

        mod._notify_fn = _fake_notify
        patrol_id = uuid.uuid4()
        await mod._notify_missing_gh_token(patrol_id)

        assert len(notify_calls) == 1
        call = notify_calls[0]
        assert call["channel"] == "telegram"
        assert call["priority"] == "high"
        assert "BUTLERS_QA_GH_TOKEN" in call["message"]
        assert "butler secrets set" in call["message"]

    async def test_noop_when_notify_fn_is_none(self):
        """_notify_missing_gh_token is a no-op when notify_fn is None."""
        mod = _make_module()
        mod._notify_fn = None
        patrol_id = uuid.uuid4()
        # Should not raise
        await mod._notify_missing_gh_token(patrol_id)

    async def test_rate_limited_to_once_per_patrol_id(self):
        """Second call with the same patrol_id skips the notification."""
        mod = _make_module()
        notify_calls: list[dict] = []

        async def _fake_notify(**kwargs):
            notify_calls.append(kwargs)
            return {}

        mod._notify_fn = _fake_notify
        patrol_id = uuid.uuid4()

        await mod._notify_missing_gh_token(patrol_id)
        await mod._notify_missing_gh_token(patrol_id)  # same patrol_id - deduplicated

        assert len(notify_calls) == 1

    async def test_new_patrol_id_triggers_new_notification(self):
        """A different patrol_id after the first triggers a fresh notification."""
        mod = _make_module()
        notify_calls: list[dict] = []

        async def _fake_notify(**kwargs):
            notify_calls.append(kwargs)
            return {}

        mod._notify_fn = _fake_notify
        first_patrol = uuid.uuid4()
        second_patrol = uuid.uuid4()

        await mod._notify_missing_gh_token(first_patrol)
        await mod._notify_missing_gh_token(second_patrol)

        assert len(notify_calls) == 2

    async def test_notify_fn_exception_is_swallowed(self):
        """If notify_fn raises, the error is caught and does not propagate."""
        mod = _make_module()

        async def _failing_notify(**kwargs):
            raise RuntimeError("delivery failed")

        mod._notify_fn = _failing_notify
        patrol_id = uuid.uuid4()
        # Should not raise
        await mod._notify_missing_gh_token(patrol_id)


# ---------------------------------------------------------------------------
# _run_patrol_body: notifies when GH token is missing
# ---------------------------------------------------------------------------


class TestRunPatrolBodyNotifyOnMissingToken:
    async def test_notify_called_when_gh_token_none(self):
        """_run_patrol_body calls _notify_missing_gh_token when gh_token is None."""
        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        pool = _make_pool()
        mod._pool = pool
        mod._sources = []

        notify_calls: list[dict] = []

        async def _fake_notify(**kwargs):
            notify_calls.append(kwargs)
            return {}

        mod._notify_fn = _fake_notify

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mock_triage,
            patch(
                "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
            ) as mock_dispatch,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
            patch.object(mod, "_resolve_gh_token", new_callable=AsyncMock, return_value=None),
        ):
            mock_triage.return_value = MagicMock(
                all_findings=[], novel_findings=[], dedup_counts={}
            )
            mock_dispatch.return_value = []
            await mod._run_patrol_cycle()

        # Should have been notified exactly once
        assert len(notify_calls) == 1
        assert notify_calls[0]["channel"] == "telegram"
        assert notify_calls[0]["priority"] == "high"

    async def test_notify_not_called_when_gh_token_present(self):
        """_run_patrol_body does NOT call notify when gh_token is resolved."""
        mod = _make_module()
        mod._config = QaConfig(enabled=True, enabled_sources=["butler_reports"])
        pool = _make_pool()
        mod._pool = pool
        mod._sources = []

        notify_calls: list[dict] = []

        async def _fake_notify(**kwargs):
            notify_calls.append(kwargs)
            return {}

        mod._notify_fn = _fake_notify

        with (
            patch("butlers.modules.qa.triage_findings", new_callable=AsyncMock) as mock_triage,
            patch(
                "butlers.modules.qa.dispatch_novel_findings", new_callable=AsyncMock
            ) as mock_dispatch,
            patch("butlers.modules.qa.check_open_pr_statuses", new_callable=AsyncMock),
            patch.object(
                mod,
                "_resolve_gh_token",
                new_callable=AsyncMock,
                return_value="ghp_token123",
            ),
        ):
            mock_triage.return_value = MagicMock(
                all_findings=[], novel_findings=[], dedup_counts={}
            )
            mock_dispatch.return_value = []
            await mod._run_patrol_cycle()

        assert len(notify_calls) == 0


# Note: per-patrol_id rate-limiting is guarded at the helper level by
# TestNotifyMissingGhToken.test_rate_limited_to_once_per_patrol_id.


# ---------------------------------------------------------------------------
# _qa_finding_from_row helpers
# ---------------------------------------------------------------------------

_BASE_ROW = {
    "fingerprint": "a" * 64,
    "source_type": "log_scanner",
    "source_butler": "qa-staffer",
    "severity": 1,
    "exception_type": "ValueError",
    "event_summary": "something broke",
    "call_site": "module.py:42",
    "occurrence_count": 3,
    "first_seen": datetime(2026, 1, 1, tzinfo=UTC),
    "last_seen": datetime(2026, 1, 2, tzinfo=UTC),
    "source_session_trigger_source": None,
    "structured_evidence": None,
}


class TestQaFindingFromRow:
    def test_happy_path_no_evidence(self):
        """Reconstitutes a finding when structured_evidence is None."""
        finding = _qa_finding_from_row({**_BASE_ROW})
        assert finding.fingerprint == "a" * 64
        assert finding.structured_evidence is None
        assert finding.timestamp == _BASE_ROW["last_seen"]

    def test_structured_evidence_dict(self):
        """structured_evidence is preserved when asyncpg already decoded it as a dict."""
        row = {**_BASE_ROW, "structured_evidence": {"key": "value"}}
        finding = _qa_finding_from_row(row)
        assert finding.structured_evidence == {"key": "value"}

    def test_structured_evidence_json_string(self):
        """structured_evidence is parsed when asyncpg returns it as a JSON string."""
        row = {**_BASE_ROW, "structured_evidence": json.dumps({"key": "value"})}
        finding = _qa_finding_from_row(row)
        assert finding.structured_evidence == {"key": "value"}

    def test_structured_evidence_invalid_string_discarded(self):
        """Non-JSON string for structured_evidence is silently discarded (logged)."""
        row = {**_BASE_ROW, "structured_evidence": "not-json"}
        finding = _qa_finding_from_row(row)
        assert finding.structured_evidence is None

    def test_last_seen_none_yields_none_timestamp(self):
        """When last_seen is None, timestamp is None (not datetime.now())."""
        row = {**_BASE_ROW, "last_seen": None}
        finding = _qa_finding_from_row(row)
        assert finding.timestamp is None
        assert finding.last_seen is None
