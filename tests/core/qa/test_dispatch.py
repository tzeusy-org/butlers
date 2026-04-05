"""Tests for butlers.core.qa.dispatch engine.

Covers:
- build_sandbox_env: strips BUTLERS_* and other blocked prefixes
- build_sandbox_env: injects GH_TOKEN from credential store argument
- build_sandbox_env: removes GH_TOKEN when gh_token is None
- build_sandbox_env: allows PATH, HOME, UV_CACHE_DIR, etc.
- QaDispatchConfig: default values
- dispatch_qa_investigation: Gate 5 — severity above threshold → rejected
- dispatch_qa_investigation: Gate 6 — already investigating → rejected
- dispatch_qa_investigation: Gate 7 — cooldown → rejected
- dispatch_qa_investigation: Gate 8 — concurrency cap → rejected
- dispatch_qa_investigation: Gate 9 — circuit breaker → rejected
- dispatch_qa_investigation: Gate 10 — no model available → rejected
- dispatch_qa_investigation: worktree creation failure → rejected
- dispatch_qa_investigation: success → accepted, tasks spawned
- dispatch_qa_investigation: never raises (internal_error result on exception)
- dispatch_novel_findings: processes findings in order, returns all results
- check_open_pr_statuses: no gh_token → empty counts, skip
- check_open_pr_statuses: MERGED state → pr_merged transition
- check_open_pr_statuses: CLOSED state → failed transition
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    fingerprint: str | None = None,
    severity: int = 1,
    occurrence_count: int = 3,
    source_butler: str = "finance",
    source_type: str = "log_scanner",
) -> QaFinding:
    now = datetime.now(UTC)
    if fingerprint is None:
        fingerprint = uuid.uuid4().hex * 2  # 64 chars
    return QaFinding(
        fingerprint=fingerprint,
        source_type=source_type,
        source_butler=source_butler,
        severity=severity,
        exception_type="ValueError",
        event_summary="Test event",
        call_site="module:1",
        occurrence_count=occurrence_count,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_triaged(finding: QaFinding | None = None) -> TriagedFinding:
    if finding is None:
        finding = _make_finding()
    return TriagedFinding(
        finding=finding,
        dedup_reason=None,
        finding_id=uuid.uuid4(),
    )


def _make_pool():
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# build_sandbox_env tests
# ---------------------------------------------------------------------------


def test_sandbox_env_injects_gh_token():
    """build_sandbox_env includes GH_TOKEN when gh_token is provided."""
    env = build_sandbox_env("mytoken123")
    assert env.get("GH_TOKEN") == "mytoken123"


def test_sandbox_env_no_gh_token_removes_it(monkeypatch):
    """build_sandbox_env excludes GH_TOKEN when gh_token is None."""
    monkeypatch.setenv("GH_TOKEN", "env_token")
    env = build_sandbox_env(None)
    assert "GH_TOKEN" not in env


def test_sandbox_env_strips_butlers_prefix(monkeypatch):
    """build_sandbox_env strips BUTLERS_* environment variables."""
    monkeypatch.setenv("BUTLERS_DB_URL", "postgres://...")
    monkeypatch.setenv("BUTLERS_SECRET", "hunter2")
    env = build_sandbox_env(None)
    assert "BUTLERS_DB_URL" not in env
    assert "BUTLERS_SECRET" not in env


def test_sandbox_env_strips_database_prefix(monkeypatch):
    """build_sandbox_env strips DATABASE_* environment variables."""
    monkeypatch.setenv("DATABASE_URL", "postgres://...")
    env = build_sandbox_env(None)
    assert "DATABASE_URL" not in env


def test_sandbox_env_strips_pg_prefix(monkeypatch):
    """build_sandbox_env strips PG* environment variables."""
    monkeypatch.setenv("PGPASSWORD", "secret")
    monkeypatch.setenv("PGHOST", "localhost")
    env = build_sandbox_env(None)
    assert "PGPASSWORD" not in env
    assert "PGHOST" not in env


def test_sandbox_env_strips_anthropic_prefix(monkeypatch):
    """build_sandbox_env strips ANTHROPIC_* environment variables."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xxx")
    env = build_sandbox_env(None)
    assert "ANTHROPIC_API_KEY" not in env


def test_sandbox_env_allows_path(monkeypatch):
    """build_sandbox_env allows PATH."""
    monkeypatch.setenv("PATH", "/usr/bin:/usr/local/bin")
    env = build_sandbox_env(None)
    assert "PATH" in env


def test_sandbox_env_allows_home(monkeypatch):
    """build_sandbox_env allows HOME."""
    monkeypatch.setenv("HOME", "/home/user")
    env = build_sandbox_env(None)
    assert "HOME" in env


def test_sandbox_env_allows_uv_cache_dir(monkeypatch):
    """build_sandbox_env allows UV_CACHE_DIR."""
    monkeypatch.setenv("UV_CACHE_DIR", "/tmp/uv-cache")
    env = build_sandbox_env(None)
    assert "UV_CACHE_DIR" in env


def test_sandbox_env_empty_gh_token_not_injected():
    """build_sandbox_env with empty string gh_token does not set GH_TOKEN."""
    env = build_sandbox_env("")
    assert "GH_TOKEN" not in env


# ---------------------------------------------------------------------------
# QaDispatchConfig tests
# ---------------------------------------------------------------------------


def test_qa_dispatch_config_defaults():
    """QaDispatchConfig has expected default values."""
    config = QaDispatchConfig()
    assert config.severity_threshold == 2
    assert config.cooldown_minutes == 60
    assert config.max_concurrent == 2
    assert config.circuit_breaker_threshold == 5
    assert config.timeout_minutes == 30
    assert config.dashboard_base_url is None
    assert "self-healing" in config.pr_labels
    assert "automated" in config.pr_labels


# ---------------------------------------------------------------------------
# dispatch_qa_investigation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_severity_above_threshold():
    """Gate 5: finding with severity > threshold is rejected."""
    pool = _make_pool()
    finding = _make_finding(severity=3)
    triaged = _make_triaged(finding)
    config = QaDispatchConfig(severity_threshold=2)

    result = await dispatch_qa_investigation(
        pool=pool,
        triaged_finding=triaged,
        patrol_id=uuid.uuid4(),
        config=config,
        repo_root=Path("/tmp/repo"),
        spawner=MagicMock(),
        gh_token=None,
    )

    assert result.accepted is False
    assert result.reason == "severity_above_threshold"


@pytest.mark.asyncio
async def test_dispatch_already_investigating():
    """Gate 6: atomic novelty check returns is_new=False → rejected."""
    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    config = QaDispatchConfig()

    with patch(
        "butlers.core.qa.dispatch.create_or_join_attempt",
        new_callable=AsyncMock,
        return_value=(uuid.uuid4(), False),
    ):
        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "already_investigating"


@pytest.mark.asyncio
async def test_dispatch_cooldown_gate():
    """Gate 7: recent attempt within cooldown window → rejected."""
    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    attempt_id = uuid.uuid4()
    config = QaDispatchConfig()

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt",
            new_callable=AsyncMock,
            return_value={"id": uuid.uuid4()},
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
    ):
        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "cooldown"


@pytest.mark.asyncio
async def test_dispatch_concurrency_cap():
    """Gate 8: active_count > max_concurrent → rejected."""
    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    attempt_id = uuid.uuid4()
    config = QaDispatchConfig(max_concurrent=2)

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=5,  # > max_concurrent=2
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
    ):
        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "concurrency_cap"


@pytest.mark.asyncio
async def test_dispatch_circuit_breaker():
    """Gate 9: circuit breaker tripped → rejected."""
    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    attempt_id = uuid.uuid4()
    config = QaDispatchConfig(circuit_breaker_threshold=3)

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ),
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
            return_value=True,
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
    ):
        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "circuit_breaker"


@pytest.mark.asyncio
async def test_dispatch_no_model():
    """Gate 10: no self_healing model available → rejected."""
    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    attempt_id = uuid.uuid4()
    config = QaDispatchConfig()

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ),
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
            return_value=None,
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
    ):
        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "no_model"


@pytest.mark.asyncio
async def test_dispatch_worktree_creation_failure():
    """Worktree creation failure → rejected with worktree_creation_failed reason."""
    from butlers.core.healing.worktree import WorktreeCreationError

    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    attempt_id = uuid.uuid4()
    config = QaDispatchConfig()

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ),
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
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec,
        patch(
            "butlers.core.qa.dispatch.create_healing_worktree",
            new_callable=AsyncMock,
            side_effect=WorktreeCreationError("git error", git_output="fatal: ..."),
        ),
    ):
        # Mock git fetch process
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "worktree_creation_failed"


@pytest.mark.asyncio
async def test_dispatch_success_spawns_tasks():
    """All gates pass → accepted=True, tasks are created."""
    pool = _make_pool()
    finding = _make_finding(severity=1)
    triaged = _make_triaged(finding)
    attempt_id = uuid.uuid4()
    config = QaDispatchConfig()
    task_registry: list[asyncio.Task] = []

    worktree_path = Path("/tmp/qa-worktree")
    branch_name = "qa/finance/abcdef123456"

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ),
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
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as mock_exec,
        patch(
            "butlers.core.qa.dispatch.create_healing_worktree",
            new_callable=AsyncMock,
            return_value=(worktree_path, branch_name),
        ),
        patch(
            "butlers.core.qa.dispatch._run_investigation_session",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch._qa_timeout_watchdog",
            new_callable=AsyncMock,
        ),
    ):
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token="ghtoken",
            task_registry=task_registry,
        )

    assert result.accepted is True
    assert result.reason == "dispatched"
    assert result.attempt_id == attempt_id
    # One watchdog task should have been added to task_registry
    assert len(task_registry) == 1
    # Clean up the background tasks
    for task in task_registry:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_dispatch_never_raises():
    """dispatch_qa_investigation returns internal_error result on unexpected exception."""
    pool = _make_pool()
    finding = _make_finding()
    triaged = _make_triaged(finding)
    config = QaDispatchConfig()

    with patch(
        "butlers.core.qa.dispatch.create_or_join_attempt",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db connection lost"),
    ):
        result = await dispatch_qa_investigation(
            pool=pool,
            triaged_finding=triaged,
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "internal_error"


# ---------------------------------------------------------------------------
# dispatch_novel_findings tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_novel_findings_returns_all_results():
    """dispatch_novel_findings returns one result per input finding."""
    pool = _make_pool()
    patrol_id = uuid.uuid4()
    config = QaDispatchConfig()

    findings = [_make_triaged(_make_finding(severity=1)) for _ in range(3)]

    with patch(
        "butlers.core.qa.dispatch.dispatch_qa_investigation",
        new_callable=AsyncMock,
        return_value=QaDispatchResult(
            accepted=False, fingerprint="a" * 64, reason="severity_above_threshold"
        ),
    ) as mock_dispatch:
        results = await dispatch_novel_findings(
            pool=pool,
            novel_findings=findings,
            patrol_id=patrol_id,
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert len(results) == 3
    assert mock_dispatch.call_count == 3


@pytest.mark.asyncio
async def test_dispatch_novel_findings_empty_list():
    """dispatch_novel_findings with empty list returns empty results."""
    pool = _make_pool()

    with patch(
        "butlers.core.qa.dispatch.dispatch_qa_investigation",
        new_callable=AsyncMock,
    ) as mock_dispatch:
        results = await dispatch_novel_findings(
            pool=pool,
            novel_findings=[],
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    assert results == []
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_novel_findings_stops_after_concurrency_cap():
    """dispatch_novel_findings stops calling dispatch_qa_investigation after cap is hit.

    Once the concurrency cap is hit, remaining findings are skipped without calling
    dispatch (preventing spurious failed attempt rows that could trigger cooldown).
    """
    pool = _make_pool()
    patrol_id = uuid.uuid4()
    config = QaDispatchConfig()

    findings = [_make_triaged(_make_finding()) for _ in range(4)]
    call_count = 0

    async def dispatch_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call: concurrency cap hit
            return QaDispatchResult(
                accepted=False,
                fingerprint=findings[0].finding.fingerprint,
                reason="concurrency_cap",
            )
        # Should never get here for findings[1..3]
        return QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched")

    with patch(
        "butlers.core.qa.dispatch.dispatch_qa_investigation",
        side_effect=dispatch_side_effect,
    ):
        results = await dispatch_novel_findings(
            pool=pool,
            novel_findings=findings,
            patrol_id=patrol_id,
            config=config,
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    # dispatch_qa_investigation called only once (for the first finding that hit the cap)
    assert call_count == 1
    # All 4 results returned, remaining 3 synthesized as concurrency_cap rejections
    assert len(results) == 4
    assert all(r.reason == "concurrency_cap" for r in results)


# ---------------------------------------------------------------------------
# check_open_pr_statuses tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_open_pr_statuses_no_token():
    """check_open_pr_statuses returns empty counts when gh_token is None."""
    pool = _make_pool()

    counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token=None)

    assert counts == {"merged": 0, "closed": 0, "errors": 0}
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_merged():
    """check_open_pr_statuses transitions pr_open → pr_merged when PR is MERGED."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    pool.fetch = AsyncMock(
        return_value=[
            {"id": attempt_id, "pr_url": "https://github.com/org/repo/pull/42", "pr_number": 42}
        ]
    )

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"MERGED\n", b""))
    mock_proc.returncode = 0

    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")

    assert counts["merged"] == 1
    assert counts["closed"] == 0
    mock_update.assert_called_once_with(pool, attempt_id, "pr_merged")


@pytest.mark.asyncio
async def test_check_open_pr_statuses_closed():
    """check_open_pr_statuses transitions pr_open → failed when PR is CLOSED."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    pool.fetch = AsyncMock(
        return_value=[
            {"id": attempt_id, "pr_url": "https://github.com/org/repo/pull/99", "pr_number": 99}
        ]
    )

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"CLOSED\n", b""))
    mock_proc.returncode = 0

    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")

    assert counts["merged"] == 0
    assert counts["closed"] == 1
    mock_update.assert_called_once()
    call_kwargs = mock_update.call_args
    assert "failed" in call_kwargs.args


@pytest.mark.asyncio
async def test_check_open_pr_statuses_no_open_prs():
    """check_open_pr_statuses returns zero counts when no pr_open rows exist."""
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])

    counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")

    assert counts == {"merged": 0, "closed": 0, "errors": 0}


# ---------------------------------------------------------------------------
# OTel qa.investigation root span
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investigation_otel_span_created(tmp_path):
    """_run_investigation_session creates a qa.investigation root span with expected attributes."""
    import opentelemetry.trace as real_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import butlers.core.qa.dispatch as dispatch_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_trace.set_tracer_provider(provider)

    dispatch_mod._tracer = provider.get_tracer("butlers.qa")
    dispatch_mod._HAS_OTEL = True

    from butlers.core.qa.dispatch import _run_investigation_session

    pool = _make_pool()
    finding = _make_finding(severity=1, source_butler="email")
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    branch_name = "qa/email/abc123"
    config = QaDispatchConfig()

    spawner = MagicMock()

    from dataclasses import dataclass

    @dataclass
    class _SpawnerResult:
        success: bool
        session_id: uuid.UUID | None
        error: str | None = None
        output: str | None = None

    spawner.trigger = AsyncMock(return_value=_SpawnerResult(success=True, session_id=uuid.uuid4()))

    with (
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=("https://github.com/org/repo/pull/1", 1, None),
        ),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=tmp_path,
            attempt_id=attempt_id,
            finding_id=finding_id,
            branch_name=branch_name,
            worktree_path=worktree_path,
            finding=finding,
            config=config,
            spawner=spawner,
            gh_token=None,
        )

    finished = exporter.get_finished_spans()
    inv_spans = [s for s in finished if s.name == "qa.investigation"]
    assert inv_spans, "Expected qa.investigation span to be created"

    span = inv_spans[0]
    assert span.attributes.get("butler.name") == "qa"
    assert span.attributes.get("qa.attempt_id") == str(attempt_id)
    assert span.attributes.get("qa.fingerprint") == finding.fingerprint
    assert span.attributes.get("qa.source_butler") == "email"
    assert span.attributes.get("qa.severity") == 1


@pytest.mark.asyncio
async def test_investigation_otel_span_is_root(tmp_path):
    """qa.investigation span is an independent root span (not a child of patrol)."""
    import opentelemetry.trace as real_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import butlers.core.qa.dispatch as dispatch_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_trace.set_tracer_provider(provider)

    dispatch_mod._tracer = provider.get_tracer("butlers.qa")
    dispatch_mod._HAS_OTEL = True

    from butlers.core.qa.dispatch import _run_investigation_session

    pool = _make_pool()
    finding = _make_finding(severity=1)
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    config = QaDispatchConfig()

    spawner = MagicMock()

    from dataclasses import dataclass

    @dataclass
    class _SpawnerResult:
        success: bool
        session_id: uuid.UUID | None
        error: str | None = None
        output: str | None = None

    spawner.trigger = AsyncMock(return_value=_SpawnerResult(success=True, session_id=uuid.uuid4()))

    with (
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=("https://github.com/org/repo/pull/2", 2, None),
        ),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=tmp_path,
            attempt_id=attempt_id,
            finding_id=finding_id,
            branch_name="qa/finance/xyz",
            worktree_path=worktree_path,
            finding=finding,
            config=config,
            spawner=spawner,
            gh_token=None,
        )

    finished = exporter.get_finished_spans()
    inv_spans = [s for s in finished if s.name == "qa.investigation"]
    assert inv_spans

    # A root span has an invalid (zero) parent span id
    span = inv_spans[0]
    parent = span.parent
    # Root span: parent is None or has an invalid span context
    assert parent is None or not parent.is_valid


@pytest.mark.asyncio
async def test_investigation_traceparent_injected_into_sandbox_env(tmp_path):
    """TRACEPARENT is injected into the investigation sandbox env when OTel is active."""
    import opentelemetry.trace as real_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import butlers.core.qa.dispatch as dispatch_mod

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    real_trace.set_tracer_provider(provider)

    dispatch_mod._tracer = provider.get_tracer("butlers.qa")
    dispatch_mod._HAS_OTEL = True

    from butlers.core.qa.dispatch import _run_investigation_session

    pool = _make_pool()
    finding = _make_finding(severity=1)
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    worktree_path = tmp_path / "wt"
    worktree_path.mkdir()
    config = QaDispatchConfig()

    captured_env: dict | None = None

    spawner = MagicMock()

    from dataclasses import dataclass

    @dataclass
    class _SpawnerResult:
        success: bool
        session_id: uuid.UUID | None
        error: str | None = None
        output: str | None = None

    async def mock_trigger(*args, **kwargs):
        nonlocal captured_env
        captured_env = kwargs.get("env_override") or {}
        return _SpawnerResult(success=True, session_id=uuid.uuid4())

    spawner.trigger = mock_trigger

    with (
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=("https://github.com/org/repo/pull/3", 3, None),
        ),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=tmp_path,
            attempt_id=attempt_id,
            finding_id=finding_id,
            branch_name="qa/finance/xyz",
            worktree_path=worktree_path,
            finding=finding,
            config=config,
            spawner=spawner,
            gh_token=None,
        )

    assert captured_env is not None
    assert "TRACEPARENT" in captured_env, (
        "TRACEPARENT should be injected into investigation sandbox env"
    )
    assert captured_env["TRACEPARENT"].startswith("00-"), (
        f"Expected W3C traceparent format, got: {captured_env.get('TRACEPARENT')}"
    )
