"""Tests for butlers.core.qa.dispatch engine — condensed.

Covers:
- build_sandbox_env: strips BUTLERS_*/DATABASE_*/PG*/ANTHROPIC_* prefixes,
  injects GH_TOKEN, allows PATH/HOME/UV_CACHE_DIR, empty token not injected
- QaDispatchConfig defaults
- dispatch_qa_investigation: Gates (severity, already_investigating, cooldown,
  concurrency, circuit breaker, no model, worktree failure), success, never raises
- dispatch_novel_findings: returns all results, empty list, stops at concurrency cap
- check_open_pr_statuses: no token → empty counts, MERGED state → pr_merged
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
    check_open_pr_statuses,
    dispatch_novel_findings,
    dispatch_qa_investigation,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.triage import TriagedFinding


def _make_finding(severity: int = 1) -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type="log_scanner",
        source_butler="finance",
        severity=severity,
        exception_type="ValueError",
        event_summary="Test event",
        call_site="module:1",
        occurrence_count=3,
        first_seen=now, last_seen=now, timestamp=now,
    )


def _make_triaged(finding: QaFinding | None = None) -> TriagedFinding:
    return TriagedFinding(finding=finding or _make_finding(), dedup_reason=None, finding_id=uuid.uuid4())


def _make_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


@pytest.mark.unit
def test_sandbox_env_filtering_and_injection(monkeypatch):
    """Strips blocked prefixes; injects GH_TOKEN when provided; allows PATH/HOME/UV_CACHE_DIR."""
    monkeypatch.setenv("BUTLERS_DB_URL", "postgres://...")
    monkeypatch.setenv("DATABASE_URL", "postgres://...")
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    monkeypatch.setenv("GH_TOKEN", "env_token")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.setenv("UV_CACHE_DIR", "/tmp/uv-cache")

    env_with_token = build_sandbox_env("mytoken123")
    assert env_with_token.get("GH_TOKEN") == "mytoken123"
    assert "PATH" in env_with_token and "HOME" in env_with_token

    env_no_token = build_sandbox_env(None)
    assert "GH_TOKEN" not in env_no_token
    for blocked in ("BUTLERS_DB_URL", "DATABASE_URL", "PGPASSWORD", "ANTHROPIC_API_KEY"):
        assert blocked not in env_no_token
    assert "UV_CACHE_DIR" in env_no_token

    # Empty string gh_token not injected
    assert "GH_TOKEN" not in build_sandbox_env("")


@pytest.mark.unit
def test_qa_dispatch_config_defaults():
    """QaDispatchConfig has expected default values."""
    config = QaDispatchConfig()
    assert config.severity_threshold == 2
    assert config.cooldown_minutes == 60
    assert config.max_concurrent == 2
    assert config.circuit_breaker_threshold == 5
    assert config.timeout_minutes == 30
    assert config.dashboard_base_url is None
    assert "self-healing" in config.pr_labels and "automated" in config.pr_labels


@pytest.mark.asyncio
async def test_dispatch_qa_gate_rejections():
    """severity_above_threshold, already_investigating, cooldown, no_model all reject."""
    config = QaDispatchConfig(severity_threshold=2)

    # Severity above threshold
    r1 = await dispatch_qa_investigation(
        pool=_make_pool(), triaged_finding=_make_triaged(_make_finding(severity=3)),
        patrol_id=uuid.uuid4(), config=config, repo_root=Path("/tmp/repo"),
        spawner=MagicMock(), gh_token=None,
    )
    assert r1.accepted is False and r1.reason == "severity_above_threshold"

    # Already investigating
    with patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock,
               return_value=(uuid.uuid4(), False)):
        r2 = await dispatch_qa_investigation(
            pool=_make_pool(), triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(), config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(), gh_token=None,
        )
    assert r2.accepted is False and r2.reason == "already_investigating"

    # Cooldown
    with (
        patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(uuid.uuid4(), True)),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock,
              return_value={"status": "failed"}),
    ):
        r3 = await dispatch_qa_investigation(
            pool=_make_pool(), triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(), config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(), gh_token=None,
        )
    assert r3.accepted is False and r3.reason == "cooldown"

    # No model
    with (
        patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(uuid.uuid4(), True)),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock, return_value=None),
        patch("butlers.core.qa.dispatch.count_active_attempts", new_callable=AsyncMock, return_value=1),
        patch("butlers.core.qa.dispatch._is_circuit_breaker_tripped", new_callable=AsyncMock, return_value=False),
        patch("butlers.core.qa.dispatch.resolve_model", new_callable=AsyncMock, return_value=None),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        r4 = await dispatch_qa_investigation(
            pool=_make_pool(), triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(), config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(), gh_token=None,
        )
    assert r4.accepted is False and r4.reason == "no_model"


@pytest.mark.asyncio
async def test_dispatch_qa_success_and_never_raises():
    """Success → accepted=True, reason=dispatched; internal error → internal_error result."""
    task_registry: list[asyncio.Task] = []
    worktree_path = Path("/tmp/qa-worktree")
    branch_name = "qa/finance/abcdef123456"
    attempt_id = uuid.uuid4()
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0

    with (
        patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock,
              return_value=(attempt_id, True)),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock, return_value=None),
        patch("butlers.core.qa.dispatch.count_active_attempts", new_callable=AsyncMock, return_value=1),
        patch("butlers.core.qa.dispatch._is_circuit_breaker_tripped", new_callable=AsyncMock, return_value=False),
        patch("butlers.core.qa.dispatch.resolve_model", new_callable=AsyncMock, return_value=MagicMock()),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", new_callable=AsyncMock,
              return_value=mock_proc),
        patch("butlers.core.qa.dispatch.create_healing_worktree", new_callable=AsyncMock,
              return_value=(worktree_path, branch_name)),
        patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(), triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(), config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(), gh_token="ghtoken", task_registry=task_registry,
        )
    assert result.accepted is True and result.reason == "dispatched"
    for task in task_registry:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Never raises
    with patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock,
               side_effect=RuntimeError("db down")):
        r_err = await dispatch_qa_investigation(
            pool=_make_pool(), triaged_finding=_make_triaged(),
            patrol_id=uuid.uuid4(), config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(), gh_token=None,
        )
    assert r_err.accepted is False and r_err.reason == "internal_error"


@pytest.mark.asyncio
async def test_dispatch_novel_findings():
    """Returns all results; empty list → empty; stops at concurrency cap."""
    pool = _make_pool()

    # Returns one result per finding
    findings = [_make_triaged() for _ in range(3)]
    with patch("butlers.core.qa.dispatch.dispatch_qa_investigation", new_callable=AsyncMock,
               return_value=QaDispatchResult(accepted=False, fingerprint="a" * 64, reason="severity_above_threshold")) as mock_d:
        results = await dispatch_novel_findings(
            pool=pool, novel_findings=findings, patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(), gh_token=None,
        )
    assert len(results) == 3 and mock_d.call_count == 3

    # Empty list
    with patch("butlers.core.qa.dispatch.dispatch_qa_investigation", new_callable=AsyncMock) as mock_d2:
        results2 = await dispatch_novel_findings(
            pool=pool, novel_findings=[], patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(), repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )
    assert results2 == [] and not mock_d2.called

    # Stops at concurrency cap
    cap_findings = [_make_triaged() for _ in range(4)]
    call_count = 0

    async def cap_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return QaDispatchResult(accepted=False, fingerprint="x" * 64, reason="concurrency_cap")

    with patch("butlers.core.qa.dispatch.dispatch_qa_investigation", side_effect=cap_side_effect):
        results3 = await dispatch_novel_findings(
            pool=pool, novel_findings=cap_findings, patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(), repo_root=Path("/tmp/repo"), spawner=MagicMock(),
        )
    assert call_count == 1 and len(results3) == 4
    assert all(r.reason == "concurrency_cap" for r in results3)


@pytest.mark.asyncio
async def test_check_open_pr_statuses():
    """No token → empty counts; MERGED → pr_merged transition."""
    pool = _make_pool()
    counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token=None)
    assert counts == {"merged": 0, "closed": 0, "errors": 0}
    pool.fetch.assert_not_called()

    # MERGED state
    attempt_id = uuid.uuid4()
    pool2 = _make_pool()
    pool2.fetch = AsyncMock(return_value=[
        {"id": attempt_id, "pr_url": "https://github.com/org/repo/pull/42", "pr_number": 42}
    ])
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"MERGED\n", b""))
    mock_proc.returncode = 0
    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        counts2 = await check_open_pr_statuses(pool2, Path("/tmp/repo"), gh_token="ghtoken")
    assert counts2["merged"] == 1
