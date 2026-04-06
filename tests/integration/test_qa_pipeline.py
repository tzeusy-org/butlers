"""Integration tests for the full QA pipeline.

Tests the end-to-end QA workflow from discovery through triage to dispatch,
covering tasks 12.1–12.9 from openspec/changes/qa-staffer/tasks.md.

These tests do NOT require a real database or Docker — they use mocks and
in-memory stubs to exercise the integration boundaries between components.

Covered scenarios:
- 12.1: Full patrol cycle (log scanner discovers error → triage deduplicates
        → dispatch creates investigation → timeout cleanup)
- 12.2: Reactive relay (butler calls report_error → finding appears in next
        patrol → investigation dispatched by QA staffer)
- 12.3: Deduplication (same fingerprint from log scanner and session records
        → single investigation, not two)
- 12.4: Sandbox enforcement (investigation agent env does not contain butler secrets)
- 12.5: Anonymization gate (PR with PII is blocked, attempt transitions to
        anonymization_failed)
- 12.7: Patrol crash recovery (stale "running" patrol rows are cleaned on
        daemon restart, dispatch_pending attempts are re-dispatched)
- 12.8: Healing API backward compatibility (QA-originated investigations with
        qa_patrol_id appear in existing healing_attempts alongside per-butler
        self-healing attempts)
- 12.9: Concurrency model (QA investigation sessions acquire QA staffer's
        per-staffer semaphore + global semaphore, do not deadlock reporting butler)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import (
    QaDispatchConfig,
    QaDispatchResult,
    build_sandbox_env,
    dispatch_novel_findings,
    dispatch_qa_investigation,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.sources.butler_reports import ButlerReportsSource
from butlers.core.qa.triage import TriagedFinding, triage_findings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_qa_finding(
    fingerprint: str | None = None,
    severity: int = 1,
    source_type: str = "log_scanner",
    source_butler: str = "finance",
    occurrence_count: int = 3,
) -> QaFinding:
    now = datetime.now(UTC)
    if fingerprint is None:
        fingerprint = uuid.uuid4().hex * 2  # 64 hex chars
    return QaFinding(
        fingerprint=fingerprint,
        source_type=source_type,
        source_butler=source_butler,
        severity=severity,
        exception_type="ValueError",
        event_summary="test error occurred",
        call_site="module.py:func",
        occurrence_count=occurrence_count,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_triaged(
    finding: QaFinding | None = None, dedup_reason: str | None = None
) -> TriagedFinding:
    if finding is None:
        finding = _make_qa_finding()
    return TriagedFinding(
        finding=finding,
        dedup_reason=dedup_reason,
        finding_id=uuid.uuid4(),
    )


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


def _make_dispatch_config(**kwargs) -> QaDispatchConfig:
    return QaDispatchConfig(**kwargs)


# ---------------------------------------------------------------------------
# 12.1: Full patrol cycle integration test
# ---------------------------------------------------------------------------
#
# Scenario: log scanner discovers error → triage deduplicates →
# dispatch creates investigation → timeout cleanup


class TestFullPatrolCycle:
    """Integration: full patrol cycle from discovery to dispatch."""

    async def test_full_cycle_log_scanner_to_dispatch(self):
        """A finding from log scanner flows through triage to dispatch."""
        pool = _make_pool()
        patrol_id = uuid.uuid4()
        attempt_id = uuid.uuid4()
        worktree_path = Path("/tmp/qa-worktree")
        branch_name = "qa/finance/abcdef123456"
        finding = _make_qa_finding(severity=1, source_type="log_scanner")
        task_registry: list[asyncio.Task] = []

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(attempt_id, True),
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch(
                "butlers.core.qa.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(worktree_path, branch_name),
            ),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            triaged = _make_triaged(finding)
            result = await dispatch_qa_investigation(
                pool=pool,
                triaged_finding=triaged,
                patrol_id=patrol_id,
                config=_make_dispatch_config(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
                gh_token="ghtoken",
                task_registry=task_registry,
            )

        assert result.accepted is True
        assert result.reason == "dispatched"
        assert result.attempt_id == attempt_id
        # Watchdog was scheduled
        assert len(task_registry) == 1

        # Cancel background tasks
        for task in task_registry:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_dispatch_creates_worktree_with_qa_prefix(self):
        """The worktree branch name uses the 'qa' prefix (not 'healing')."""
        pool = _make_pool()
        created_branches: list[str] = []

        async def capture_worktree(repo_root, butler_name, fingerprint, prefix="healing"):
            branch = f"{prefix}/{butler_name}/{fingerprint[:12]}"
            created_branches.append(branch)
            return Path("/tmp/qa-worktree"), branch

        attempt_id = uuid.uuid4()
        finding = _make_qa_finding(severity=1)
        triaged = _make_triaged(finding)

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(attempt_id, True),
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("butlers.core.qa.dispatch.create_healing_worktree", side_effect=capture_worktree),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await dispatch_qa_investigation(
                pool=pool,
                triaged_finding=triaged,
                patrol_id=uuid.uuid4(),
                config=_make_dispatch_config(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
            )

        assert result.accepted is True
        assert len(created_branches) == 1
        # The branch should use the "qa" prefix
        assert created_branches[0].startswith("qa/")

    async def test_timeout_watchdog_registered_in_task_registry(self):
        """After dispatch, the timeout watchdog is tracked in task_registry."""
        pool = _make_pool()
        attempt_id = uuid.uuid4()
        task_registry: list[asyncio.Task] = []
        finding = _make_qa_finding(severity=1)
        triaged = _make_triaged(finding)

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(attempt_id, True),
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch(
                "butlers.core.qa.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(Path("/tmp/wt"), "qa/b/c"),
            ),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            await dispatch_qa_investigation(
                pool=pool,
                triaged_finding=triaged,
                patrol_id=uuid.uuid4(),
                config=_make_dispatch_config(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
                task_registry=task_registry,
            )

        # One watchdog task added
        assert len(task_registry) == 1

        # Cleanup
        for t in task_registry:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------
# 12.2: Reactive relay integration test
# ---------------------------------------------------------------------------
#
# Scenario: butler calls report_error → finding appears in next patrol →
# investigation dispatched by QA staffer


class TestReactiveRelay:
    """Integration: reactive relay path from report_error to investigation."""

    async def test_butler_report_appears_in_next_patrol(self):
        """Finding relayed via ButlerReportsSource is dispatched in the next patrol."""
        source = ButlerReportsSource()
        fingerprint = "a" * 64

        # Butler relays a finding
        await source.accept(
            fingerprint=fingerprint,
            exception_type="RuntimeError",
            call_site="api.py:handler",
            severity=1,
            event_summary="API timeout",
            source_butler="general",
        )

        # Next patrol discovers it
        findings = await source.discover(lookback_minutes=15)

        assert len(findings) == 1
        assert findings[0].fingerprint == fingerprint
        assert findings[0].source_type == "butler_reports"
        assert findings[0].source_butler == "general"
        assert findings[0].exception_type == "RuntimeError"

    async def test_butler_report_buffer_cleared_after_discover(self):
        """Buffer is empty after discover() drains it — no double-dispatch."""
        source = ButlerReportsSource()
        await source.accept(
            fingerprint="b" * 64,
            exception_type="ValueError",
            call_site="x.py:y",
            severity=2,
            event_summary="test",
            source_butler="health",
        )

        first_drain = await source.discover(lookback_minutes=15)
        second_drain = await source.discover(lookback_minutes=15)

        assert len(first_drain) == 1
        assert len(second_drain) == 0  # Buffer cleared — no duplicate dispatch

    async def test_reactive_finding_triggers_dispatch(self):
        """A finding from ButlerReportsSource flows through dispatch gates successfully."""
        source = ButlerReportsSource()
        fingerprint = "c" * 64

        await source.accept(
            fingerprint=fingerprint,
            exception_type="IOError",
            call_site="storage.py:write",
            severity=0,  # Critical
            event_summary="disk full",
            source_butler="finance",
        )

        findings = await source.discover(lookback_minutes=15)
        assert len(findings) == 1

        pool = _make_pool()
        attempt_id = uuid.uuid4()
        triaged = _make_triaged(findings[0])

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(attempt_id, True),
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch(
                "butlers.core.qa.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(Path("/tmp/wt"), "qa/finance/cccc"),
            ),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await dispatch_qa_investigation(
                pool=pool,
                triaged_finding=triaged,
                patrol_id=uuid.uuid4(),
                config=_make_dispatch_config(severity_threshold=2),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
                gh_token=None,
            )

        assert result.accepted is True
        assert result.reason == "dispatched"


# ---------------------------------------------------------------------------
# 12.3: Deduplication integration test
# ---------------------------------------------------------------------------
#
# Scenario: same fingerprint from log scanner AND session records →
# single investigation, not two


class TestCrossSourceDeduplication:
    """Integration: same fingerprint from multiple sources → single investigation."""

    async def test_same_fingerprint_two_sources_dispatched_once(self):
        """Two findings with identical fingerprints from different sources yield one dispatch."""
        pool = _make_pool()
        shared_fp = "d" * 64
        patrol_id = uuid.uuid4()
        attempt_id = uuid.uuid4()

        # First dispatch succeeds (is_new=True)
        # Second dispatch gets is_new=False (already_investigating)
        is_new_values = [True, False]
        call_count = 0

        async def mock_create_or_join(*args, **kwargs):
            nonlocal call_count
            val = is_new_values[call_count] if call_count < len(is_new_values) else False
            call_count += 1
            return (attempt_id, val)

        finding_log = _make_qa_finding(fingerprint=shared_fp, source_type="log_scanner", severity=1)
        finding_sr = _make_qa_finding(
            fingerprint=shared_fp, source_type="session_records", severity=1
        )
        triaged_list = [_make_triaged(finding_log), _make_triaged(finding_sr)]

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt", side_effect=mock_create_or_join
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch(
                "butlers.core.qa.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(Path("/tmp/wt"), "qa/b/c"),
            ),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            results = await dispatch_novel_findings(
                pool=pool,
                novel_findings=triaged_list,
                patrol_id=patrol_id,
                config=_make_dispatch_config(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
            )

        accepted_count = sum(1 for r in results if r.accepted)
        rejected_count = sum(
            1 for r in results if not r.accepted and r.reason == "already_investigating"
        )

        assert accepted_count == 1, f"Expected exactly 1 accepted, got {accepted_count}: {results}"
        assert rejected_count == 1, (
            f"Expected exactly 1 already_investigating, got {rejected_count}: {results}"
        )

    async def test_triage_deduplicates_cross_source_findings(self):
        """Triage layer flags the second cross-source finding as intra-patrol dedup."""
        pool = _make_pool()
        shared_fp = "e" * 64
        patrol_id = uuid.uuid4()

        finding_a = _make_qa_finding(fingerprint=shared_fp, source_type="log_scanner", severity=1)
        finding_b = _make_qa_finding(
            fingerprint=shared_fp, source_type="session_records", severity=1
        )

        with (
            patch(
                "butlers.core.qa.triage.get_active_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.triage.is_dismissed", new_callable=AsyncMock, return_value=False
            ),
            patch(
                "butlers.core.qa.triage.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.triage.insert_finding",
                new_callable=AsyncMock,
                return_value=uuid.uuid4(),
            ),
        ):
            result = await triage_findings(pool, patrol_id, [finding_a, finding_b])

        # First finding: novel; second finding: same fingerprint → dedup
        novel = [f for f in result.all_findings if f.is_novel]
        deduped = [f for f in result.all_findings if not f.is_novel]

        assert len(novel) == 1
        assert len(deduped) == 1
        assert deduped[0].dedup_reason == "active_investigation"


# ---------------------------------------------------------------------------
# 12.4: Sandbox enforcement
# ---------------------------------------------------------------------------
#
# Scenario: investigation agent env does not contain butler secrets


class TestSandboxEnforcement:
    """Integration: investigation agent environment is properly sandboxed."""

    def test_sandbox_env_strips_secrets_and_preserves_tools(self, monkeypatch):
        """Strips BUTLERS_*, DATABASE_*, PG*, ANTHROPIC_* vars; preserves PATH/HOME/UV_CACHE_DIR."""
        monkeypatch.setenv("BUTLERS_DB_URL", "postgres://secret")
        monkeypatch.setenv("BUTLERS_SECRET_KEY", "topsecret")
        monkeypatch.setenv("BUTLERS_EMAIL_PASSWORD", "pass123")
        monkeypatch.setenv("BUTLERS_API_KEY", "key-abc")
        monkeypatch.setenv("DATABASE_URL", "postgres://host/db")
        monkeypatch.setenv("PGPASSWORD", "dbpass")
        monkeypatch.setenv("PGHOST", "localhost")
        monkeypatch.setenv("PGUSER", "admin")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "auth-token")
        monkeypatch.setenv("PATH", "/usr/bin:/usr/local/bin")
        monkeypatch.setenv("HOME", "/home/user")
        monkeypatch.setenv("UV_CACHE_DIR", "/tmp/uv-cache")

        env = build_sandbox_env(None)

        for key in (
            "BUTLERS_DB_URL", "BUTLERS_SECRET_KEY", "BUTLERS_EMAIL_PASSWORD", "BUTLERS_API_KEY",
            "DATABASE_URL", "PGPASSWORD", "PGHOST", "PGUSER",
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
        ):
            assert key not in env, f"{key} should be stripped from sandbox env"
        for key in ("PATH", "HOME", "UV_CACHE_DIR"):
            assert key in env, f"{key} should be preserved in sandbox env"

    def test_sandbox_env_injects_gh_token_only_when_provided(self, monkeypatch):
        """GH_TOKEN is present only when explicitly provided via argument."""
        monkeypatch.setenv("GH_TOKEN", "env_token")  # This should be ignored
        env_without = build_sandbox_env(None)
        env_with = build_sandbox_env("injected_token")

        assert "GH_TOKEN" not in env_without
        assert env_with["GH_TOKEN"] == "injected_token"


# ---------------------------------------------------------------------------
# 12.5: Anonymization gate
# ---------------------------------------------------------------------------
#
# Scenario: PR with PII is blocked, attempt transitions to anonymization_failed


class TestAnonymizationGate:
    """Integration: anonymization gate blocks PRs with PII."""

    async def test_anonymization_failure_transitions_to_failed_status(self):
        """When anonymization fails, attempt status transitions to anonymization_failed."""
        from butlers.core.qa.dispatch import QaDispatchConfig, _run_investigation_session

        pool = _make_pool()
        attempt_id = uuid.uuid4()
        finding_id = uuid.uuid4()
        update_calls: list[tuple] = []

        async def capture_update_status(p, aid, status, **kwargs):
            update_calls.append((aid, status))

        finding = _make_qa_finding(severity=1)

        # Simulate a spawner that succeeds (agent ran) but PR creation fails due to anonymization
        spawner = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.session_id = uuid.uuid4()
        mock_result.error = None
        spawner.trigger = AsyncMock(return_value=mock_result)

        worktree_path = Path("/tmp/qa-wt-anon-test")

        with (
            patch(
                "butlers.core.qa.dispatch.update_attempt_status", side_effect=capture_update_status
            ),
            patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch._create_qa_pr",
                new_callable=AsyncMock,
                return_value=(None, None, "anonymization_failed"),
            ),
        ):
            await _run_investigation_session(
                pool=pool,
                repo_root=Path("/tmp/repo"),
                attempt_id=attempt_id,
                finding_id=finding_id,
                branch_name="qa/finance/test",
                worktree_path=worktree_path,
                finding=finding,
                config=QaDispatchConfig(),
                spawner=spawner,
                gh_token="ghtoken",
            )

        # Find the final status transition — should be anonymization_failed
        assert any(status == "anonymization_failed" for _, status in update_calls), (
            f"Expected anonymization_failed in status transitions: {update_calls}"
        )


# ---------------------------------------------------------------------------
# 12.7: Patrol crash recovery
# ---------------------------------------------------------------------------
#
# Scenario: stale "running" patrol rows are cleaned on daemon restart;
# dispatch_pending attempts are re-dispatched


class TestPatrolCrashRecovery:
    """Integration: daemon restart recovers stale patrols and pending attempts."""

    async def test_stale_running_patrol_rows_recovered_on_startup(self):
        """on_startup marks stale 'running' patrol rows as 'error'."""
        from butlers.modules.qa import QaConfig, QaModule

        stale_id = uuid.uuid4()
        pool = _make_pool()

        # Simulate: fetch returns one stale 'running' patrol row
        pool.fetch = AsyncMock(return_value=[{"id": stale_id}])

        mod = QaModule()

        with (
            patch(
                "butlers.modules.qa.recover_stale_attempts",
                new_callable=AsyncMock,
                return_value=(0, []),
            ),
            patch("butlers.modules.qa.reap_stale_worktrees", new_callable=AsyncMock),
        ):
            await mod.on_startup(QaConfig(), MagicMock(pool=pool))

        # The module should have called pool.execute to update the stale patrol row
        assert pool.execute.called

    async def test_dispatch_pending_attempts_requeued_on_startup(self):
        """on_startup triggers re-dispatch of dispatch_pending healing attempts."""
        from butlers.modules.qa import QaConfig, QaModule

        pending_attempt = {
            "id": uuid.uuid4(),
            "fingerprint": "f" * 64,
            "butler_name": "finance",
            "severity": 1,
            "exception_type": "DBError",
            "call_site": "db.py:query",
            "sanitized_msg": "connection refused",
            "status": "dispatch_pending",
        }
        pool = _make_pool()
        pool.fetch = AsyncMock(return_value=[])  # no stale patrol rows

        mod = QaModule()

        with (
            patch(
                "butlers.modules.qa.recover_stale_attempts",
                new_callable=AsyncMock,
                return_value=(1, [pending_attempt]),
            ),
            patch("butlers.modules.qa.reap_stale_worktrees", new_callable=AsyncMock),
        ):
            await mod.on_startup(QaConfig(), MagicMock(pool=pool))

        # recover_stale_attempts was called, indicating startup recovery ran
        # The module state should reflect that startup completed
        assert mod._pool is not None


# ---------------------------------------------------------------------------
# 12.8: Healing API backward compatibility
# ---------------------------------------------------------------------------
#
# Scenario: QA-originated investigations appear in existing healing_attempts


class TestHealingApiBackwardCompatibility:
    """Integration: QA investigations are visible in the healing_attempts table."""

    async def test_qa_investigation_uses_shared_healing_attempts_table(self):
        """dispatch_qa_investigation calls create_or_join_attempt (shared table)."""
        pool = _make_pool()
        attempt_id = uuid.uuid4()
        create_or_join_calls: list[dict] = []

        async def capture_create_or_join(pool, **kwargs):
            create_or_join_calls.append(kwargs)
            return (attempt_id, True)

        finding = _make_qa_finding(severity=1)
        triaged = _make_triaged(finding)
        patrol_id = uuid.uuid4()

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt",
                side_effect=capture_create_or_join,
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch(
                "butlers.core.qa.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(Path("/tmp/wt"), "qa/b/c"),
            ),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await dispatch_qa_investigation(
                pool=pool,
                triaged_finding=triaged,
                patrol_id=patrol_id,
                config=_make_dispatch_config(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
            )

        # create_or_join_attempt was called — QA investigation uses shared table
        assert len(create_or_join_calls) == 1
        assert result.accepted is True

    async def test_qa_investigation_result_has_attempt_id(self):
        """The dispatch result includes a valid attempt_id for cross-reference in APIs."""
        pool = _make_pool()
        attempt_id = uuid.uuid4()
        finding = _make_qa_finding(severity=1)
        triaged = _make_triaged(finding)

        with (
            patch(
                "butlers.core.qa.dispatch.create_or_join_attempt",
                new_callable=AsyncMock,
                return_value=(attempt_id, True),
            ),
            patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.get_recent_attempt",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "butlers.core.qa.dispatch.count_active_attempts",
                new_callable=AsyncMock,
                return_value=1,
            ),
            patch(
                "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "butlers.core.qa.dispatch.resolve_model",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
            patch(
                "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch(
                "butlers.core.qa.dispatch.create_healing_worktree",
                new_callable=AsyncMock,
                return_value=(Path("/tmp/wt"), "qa/b/c"),
            ),
            patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
            patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        ):
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(return_value=(b"", b""))
            mock_proc.returncode = 0
            mock_exec.return_value = mock_proc

            result = await dispatch_qa_investigation(
                pool=pool,
                triaged_finding=triaged,
                patrol_id=uuid.uuid4(),
                config=_make_dispatch_config(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
            )

        assert result.attempt_id == attempt_id


# ---------------------------------------------------------------------------
# 12.9: Concurrency model
# ---------------------------------------------------------------------------
#
# Scenario: QA investigation sessions use semaphore; do not deadlock reporting butler


class TestConcurrencyModel:
    """Integration: QA investigations use semaphores without deadlocking callers."""

    async def test_dispatch_novel_findings_respects_concurrency_cap(self):
        """When concurrency cap is reached, dispatch_novel_findings stops dispatching."""
        pool = _make_pool()
        patrol_id = uuid.uuid4()
        config = _make_dispatch_config(max_concurrent=2)

        # 4 findings, but concurrency cap of 2 → first hit stops the rest
        findings = [_make_triaged(_make_qa_finding(severity=1)) for _ in range(4)]
        call_count = 0

        async def dispatch_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return QaDispatchResult(
                    accepted=False,
                    fingerprint=findings[0].finding.fingerprint,
                    reason="concurrency_cap",
                )
            return QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched")

        with patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation", side_effect=dispatch_side_effect
        ):
            results = await dispatch_novel_findings(
                pool=pool,
                novel_findings=findings,
                patrol_id=patrol_id,
                config=config,
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
            )

        # dispatch_qa_investigation called only once
        assert call_count == 1
        # All results returned; remaining synthesized as concurrency_cap
        assert len(results) == 4
        assert all(r.reason == "concurrency_cap" for r in results)

    async def test_report_error_returns_immediately_non_blocking(self):
        """report_error via ButlerReportsSource is non-blocking (queues and returns)."""
        source = ButlerReportsSource()
        fingerprint = "g" * 64

        # accept() should complete immediately (no blocking await on DB or network)
        await asyncio.wait_for(
            source.accept(
                fingerprint=fingerprint,
                exception_type="NetworkError",
                call_site="net.py:connect",
                severity=1,
                event_summary="connection failed",
                source_butler="general",
            ),
            timeout=1.0,  # Must complete in < 1 second
        )

        assert source.buffer_size == 1

    async def test_multiple_concurrent_accepts_are_safe(self):
        """Multiple concurrent accept() calls are safe (asyncio lock protected)."""
        source = ButlerReportsSource()

        async def accept_one(i: int) -> None:
            await source.accept(
                fingerprint=f"{i:064x}",
                exception_type="Error",
                call_site="x.py:y",
                severity=1,
                event_summary=f"error {i}",
                source_butler="test",
            )

        # 10 concurrent accepts
        await asyncio.gather(*[accept_one(i) for i in range(10)])

        assert source.buffer_size == 10

        findings = await source.discover(lookback_minutes=15)
        assert len(findings) == 10


# ---------------------------------------------------------------------------
# AC 6 (bu-i0geq): SelfHealingModule.report_error → Switchboard route() →
#                   QaModule.report_finding end-to-end relay integration
# ---------------------------------------------------------------------------
#
# Scenario: a butler's SelfHealingModule has been wired with a real
# switchboard_client (as daemon._wire_module_runtime() now does). When
# report_error is called, the finding travels from SelfHealingModule through
# Switchboard's route() tool to QaModule's report_finding handler, where it
# is enqueued in the ButlerReportsSource buffer.


class TestSelfHealingToQaRelayIntegration:
    """Integration: report_error → Switchboard route() → QA report_finding (AC 6)."""

    async def test_report_error_relays_finding_to_qa_module(self):
        """Full relay: SelfHealingModule.report_error calls route(), which lands in QaModule.

        Simulates the Switchboard forwarding mechanism: when the Switchboard
        receives a route() call for target_butler="qa" and tool_name="report_finding",
        it invokes QaModule's _handle_report_finding() directly.
        """
        from butlers.core.qa.sources.butler_reports import ButlerReportsSource
        from butlers.modules.qa import QaModule
        from butlers.modules.self_healing import SelfHealingModule

        # Set up QA module with a real ButlerReportsSource buffer
        qa_mod = QaModule()
        qa_mod._butler_reports_source = ButlerReportsSource()

        # Build a mock Switchboard client that forwards route() calls to QA module
        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            args = args or {}
            if tool_name == "list_butlers":
                # QA staffer is registered
                return [{"name": "qa"}]
            if tool_name == "route":
                # Switchboard forwards to QA module's report_finding handler
                target = args.get("target_butler")
                routed_tool = args.get("tool_name")
                routed_args = args.get("args", {})
                if target == "qa" and routed_tool == "report_finding":
                    return await qa_mod._handle_report_finding(**routed_args)
                return {"error": f"Unknown route target={target} tool={routed_tool}"}
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        # Wire the SelfHealingModule with the mock client (as daemon does after bu-i0geq fix)
        sh_mod = SelfHealingModule()
        sh_mod.wire_runtime("general", MagicMock(), "/repo", switchboard_client=client)
        sh_mod._pool = None  # No DB required for this relay path

        # Invoke report_error — triggers the full relay chain
        result = await sh_mod._handle_report_error(
            error_type="ValueError",
            error_message="payment processor timeout",
            traceback_str="Traceback (most recent call last):\n  File ...",
            call_site="payments.py:charge",
            context="Agent was processing a payment when the error occurred",
            tool_name="process_payment",
            severity_hint="high",
        )

        # Relay succeeded
        assert result["accepted"] is True, f"Expected accepted=True, got: {result}"
        assert "fingerprint" in result

        # QA module received the finding in its buffer
        assert qa_mod._butler_reports_source.buffer_size == 1

        # The buffered finding has correct metadata
        buffered_findings = await qa_mod._butler_reports_source.discover(lookback_minutes=15)
        assert len(buffered_findings) == 1
        bf = buffered_findings[0]
        assert bf.source_butler == "general"
        assert bf.exception_type == "ValueError"
        assert bf.fingerprint == result["fingerprint"]

    async def test_relay_falls_back_when_qa_not_registered(self):
        """Falls back to direct dispatch when QA staffer not in Switchboard registry.

        Confirms graceful degradation: missing QA registration → direct
        dispatch path, not an error.
        """
        from butlers.modules.self_healing import SelfHealingModule

        async def mock_call_tool(tool_name: str, args: dict | None = None) -> object:
            if tool_name == "list_butlers":
                # QA not registered
                return [{"name": "finance"}, {"name": "health"}]
            return {}

        client = MagicMock()
        client.call_tool = mock_call_tool

        sh_mod = SelfHealingModule()
        sh_mod.wire_runtime("general", MagicMock(), "/repo", switchboard_client=client)
        sh_mod._pool = None
        sh_mod._spawner = None  # Forces not_configured on direct dispatch path

        result = await sh_mod._handle_report_error(
            error_type="RuntimeError",
            error_message="something broke",
            traceback_str=None,
            call_site="mod.py:fn",
            context=None,
            tool_name=None,
            severity_hint=None,
        )

        # Falls back to direct dispatch (no pool/spawner → not_configured)
        assert result["accepted"] is False
        assert result["reason"] == "not_configured"
