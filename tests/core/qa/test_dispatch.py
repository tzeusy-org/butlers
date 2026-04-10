"""Tests for butlers.core.qa.dispatch engine — condensed.

Covers:
- build_sandbox_env: strips BUTLERS_*/DATABASE_*/PG*/ANTHROPIC_* prefixes,
  injects GH_TOKEN, allows PATH/HOME/UV_CACHE_DIR, empty token not injected
- QaDispatchConfig defaults
- dispatch_qa_investigation: Gates (severity, already_investigating, cooldown,
  concurrency, circuit breaker, no model, worktree failure), success, never raises
- dispatch_novel_findings: returns all results, empty list, stops at concurrency cap
- check_open_pr_statuses: no token → empty counts, MERGED/CLOSED states, review tracking
- _extract_review_state: state extraction from gh pr view JSON
- _dispatch_pr_review_followup: anonymization failure, dispatch success
"""

from __future__ import annotations

import asyncio
import json as _test_json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import (
    QaDispatchConfig,
    QaDispatchResult,
    _create_qa_pr,
    _detect_no_op_branch,
    _dispatch_pr_review_followup,
    _extract_review_state,
    _is_circuit_breaker_tripped,
    _resolve_pr_by_head,
    _run_investigation_session,
    _run_review_followup_session,
    build_git_auth_env,
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
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_triaged(finding: QaFinding | None = None) -> TriagedFinding:
    return TriagedFinding(
        finding=finding or _make_finding(), dedup_reason=None, finding_id=uuid.uuid4()
    )


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
def test_git_auth_env_configures_noninteractive_push(monkeypatch):
    """Git auth env adds askpass and disables terminal prompts when token is present."""
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/user")

    env = build_git_auth_env("mytoken123")

    assert env["GH_TOKEN"] == "mytoken123"
    assert env["BUTLERS_QA_GIT_TOKEN"] == "mytoken123"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_ASKPASS"]

    env_without_token = build_git_auth_env(None)
    assert env_without_token["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_ASKPASS" not in env_without_token
    assert "BUTLERS_QA_GIT_TOKEN" not in env_without_token


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
async def test_circuit_breaker_helper_counts_manual_reset_without_session() -> None:
    """manual_reset rows must break the QA failure chain even without healing_session_id."""

    async def _fetch(query: str, threshold: int):
        assert threshold == 5
        if "status = 'manual_reset'" in query:
            return [
                {"status": "failed"},
                {"status": "failed"},
                {"status": "failed"},
                {"status": "failed"},
                {"status": "manual_reset"},
            ]
        return [{"status": "failed"} for _ in range(5)]

    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=_fetch)

    assert await _is_circuit_breaker_tripped(pool, 5) is False


@pytest.mark.asyncio
async def test_dispatch_qa_gate_rejections():
    """severity_above_threshold, already_investigating, cooldown, no_model all reject."""
    config = QaDispatchConfig(severity_threshold=2)

    # Severity above threshold
    r1 = await dispatch_qa_investigation(
        pool=_make_pool(),
        triaged_finding=_make_triaged(_make_finding(severity=3)),
        patrol_id=uuid.uuid4(),
        config=config,
        repo_root=Path("/tmp/repo"),
        spawner=MagicMock(),
        gh_token=None,
    )
    assert r1.accepted is False and r1.reason == "severity_above_threshold"

    # Already investigating
    with patch(
        "butlers.core.qa.dispatch.create_or_join_attempt",
        new_callable=AsyncMock,
        return_value=(uuid.uuid4(), False),
    ):
        r2 = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert r2.accepted is False and r2.reason == "already_investigating"

    # Cooldown
    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(uuid.uuid4(), True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt",
            new_callable=AsyncMock,
            return_value={"status": "failed"},
        ),
    ):
        r3 = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert r3.accepted is False and r3.reason == "cooldown"

    # No model
    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(uuid.uuid4(), True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts", new_callable=AsyncMock, return_value=1
        ),
        patch(
            "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("butlers.core.qa.dispatch.resolve_model", new_callable=AsyncMock, return_value=None),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        r4 = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
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
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts", new_callable=AsyncMock, return_value=1
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
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ),
        patch(
            "butlers.core.qa.dispatch.create_healing_worktree",
            new_callable=AsyncMock,
            return_value=(worktree_path, branch_name),
        ),
        patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token="ghtoken",
            task_registry=task_registry,
        )
    assert result.accepted is True and result.reason == "dispatched"
    for task in task_registry:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Never raises
    with patch(
        "butlers.core.qa.dispatch.create_or_join_attempt",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        r_err = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert r_err.accepted is False and r_err.reason == "internal_error"


@pytest.mark.asyncio
async def test_dispatch_novel_findings():
    """Returns all results; empty list → empty; stops at concurrency cap."""
    pool = _make_pool()

    # Returns one result per finding
    findings = [_make_triaged() for _ in range(3)]
    with patch(
        "butlers.core.qa.dispatch.dispatch_qa_investigation",
        new_callable=AsyncMock,
        return_value=QaDispatchResult(
            accepted=False, fingerprint="a" * 64, reason="severity_above_threshold"
        ),
    ) as mock_d:
        results = await dispatch_novel_findings(
            pool=pool,
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert len(results) == 3 and mock_d.call_count == 3

    # Empty list
    with patch(
        "butlers.core.qa.dispatch.dispatch_qa_investigation", new_callable=AsyncMock
    ) as mock_d2:
        results2 = await dispatch_novel_findings(
            pool=pool,
            novel_findings=[],
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
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
            pool=pool,
            novel_findings=cap_findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )
    assert call_count == 1 and len(results3) == 4
    assert all(r.reason == "concurrency_cap" for r in results3)


@pytest.mark.asyncio
async def test_dispatch_novel_findings_cap_skipped_get_dedup_reason_and_queued():
    """Findings synthetically skipped due to concurrency cap get dedup_reason and queued updates.

    When a patrol hits the concurrency cap mid-batch, findings beyond the first
    cap result must have their qa_findings rows updated with:
      - dedup_reason = "concurrency_cap" (accurate suppression reason)
      - dispatch_queued = True (durable retry queue)

    This prevents one-shot findings from being silently lost after cap pressure.
    """
    pool = _make_pool()

    # 3 findings; first hits cap, remaining 2 are synthetically skipped
    triaged_list = [_make_triaged() for _ in range(3)]

    async def cap_side_effect(*args, **kwargs):
        return QaDispatchResult(accepted=False, fingerprint="x" * 64, reason="concurrency_cap")

    with (
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation", side_effect=cap_side_effect),
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason", new_callable=AsyncMock
        ) as mock_dedup,
        patch(
            "butlers.core.qa.dispatch.update_finding_dispatch_queued", new_callable=AsyncMock
        ) as mock_queued,
    ):
        results = await dispatch_novel_findings(
            pool=pool,
            novel_findings=triaged_list,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    assert len(results) == 3
    assert all(r.reason == "concurrency_cap" for r in results)

    # The two synthetically-skipped findings (index 1 and 2) must have been
    # marked with dedup_reason and dispatch_queued.  The first finding went
    # through dispatch_qa_investigation which is responsible for its own updates.
    assert mock_dedup.call_count == 2
    assert mock_queued.call_count == 2

    # Verify the correct finding_ids were passed
    skipped_ids = {triaged_list[1].finding_id, triaged_list[2].finding_id}
    dedup_call_ids = {call.args[1] for call in mock_dedup.call_args_list}
    queued_call_ids = {call.args[1] for call in mock_queued.call_args_list}
    assert dedup_call_ids == skipped_ids
    assert queued_call_ids == skipped_ids

    # dedup_reason must be "concurrency_cap"; dispatch_queued must be True
    assert all(call.args[2] == "concurrency_cap" for call in mock_dedup.call_args_list)
    assert all(call.args[2] is True for call in mock_queued.call_args_list)


@pytest.mark.asyncio
async def test_dispatch_novel_findings_no_queued_updates_without_cap():
    """No dedup_reason or queued updates when concurrency cap is never hit."""
    pool = _make_pool()
    findings = [_make_triaged() for _ in range(3)]

    with (
        patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation",
            new_callable=AsyncMock,
            return_value=QaDispatchResult(accepted=True, fingerprint="a" * 64, reason="dispatched"),
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason", new_callable=AsyncMock
        ) as mock_dedup,
        patch(
            "butlers.core.qa.dispatch.update_finding_dispatch_queued", new_callable=AsyncMock
        ) as mock_queued,
    ):
        results = await dispatch_novel_findings(
            pool=pool,
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    assert len(results) == 3
    assert all(r.accepted for r in results)
    # No synthetic skips → no dedup/queued updates from batch layer
    mock_dedup.assert_not_called()
    mock_queued.assert_not_called()


def _make_pr_row(
    *,
    attempt_id: uuid.UUID | None = None,
    pr_url: str = "https://github.com/org/repo/pull/42",
    pr_number: int = 42,
    fingerprint: str = "a" * 64,
    butler_name: str = "general",
    follow_up_count: int = 0,
    branch_name: str | None = "qa/general/abcdef",
    follow_up_cycle_patrol_id: uuid.UUID | None = None,
    follow_up_cycle_count: int = 0,
) -> dict:
    return {
        "id": attempt_id or uuid.uuid4(),
        "pr_url": pr_url,
        "pr_number": pr_number,
        "fingerprint": fingerprint,
        "butler_name": butler_name,
        "follow_up_count": follow_up_count,
        "branch_name": branch_name,
        "follow_up_cycle_patrol_id": follow_up_cycle_patrol_id,
        "follow_up_cycle_count": follow_up_cycle_count,
    }


@pytest.mark.asyncio
async def test_check_open_pr_statuses_no_token():
    """No token → empty counts, fetch not called."""
    pool = _make_pool()
    counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token=None)
    assert counts == {"merged": 0, "closed": 0, "errors": 0, "follow_ups_dispatched": 0}
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_merged():
    """MERGED state → pr_merged transition."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[_make_pr_row(attempt_id=attempt_id)])
    pr_data = {"state": "MERGED", "reviews": [], "latestReviews": [], "reviewThreads": []}
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock
        ) as mock_update,
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")
    assert counts["merged"] == 1
    assert counts["closed"] == 0
    assert counts["errors"] == 0
    mock_update.assert_awaited_once()
    call_args = mock_update.call_args
    assert call_args[0][2] == "pr_merged"


@pytest.mark.asyncio
async def test_check_open_pr_statuses_closed():
    """CLOSED state → failed transition."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[_make_pr_row(attempt_id=attempt_id)])
    pr_data = {"state": "CLOSED", "reviews": [], "latestReviews": [], "reviewThreads": []}
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock
        ) as mock_update,
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")
    assert counts["closed"] == 1
    call_args = mock_update.call_args
    assert call_args[0][2] == "failed"


@pytest.mark.asyncio
async def test_check_open_pr_statuses_open_no_review():
    """OPEN with no reviews → review tracking columns updated, no follow-up."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[_make_pr_row(attempt_id=attempt_id)])
    pr_data = {"state": "OPEN", "reviews": [], "latestReviews": [], "reviewThreads": []}
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")
    assert counts["merged"] == 0
    assert counts["closed"] == 0
    assert counts["follow_ups_dispatched"] == 0
    # Review update executed
    pool.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_changes_requested_dispatches_followup():
    """OPEN with changes_requested + spawner → follow-up dispatched."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[_make_pr_row(attempt_id=attempt_id, follow_up_count=0)])
    pr_data = {
        "state": "OPEN",
        "reviews": [
            {
                "state": "CHANGES_REQUESTED",
                "body": "Please fix the tests.",
                "author": {"login": "alice"},
            }
        ],
        "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "alice"}}],
        "reviewThreads": [],
    }
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    spawner = MagicMock()
    config = QaDispatchConfig()

    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._dispatch_pr_review_followup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_followup,
    ):
        counts = await check_open_pr_statuses(
            pool,
            Path("/tmp/repo"),
            gh_token="ghtoken",
            spawner=spawner,
            config=config,
        )
    assert counts["follow_ups_dispatched"] == 1
    mock_followup.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_rate_limit_respected():
    """Per-cycle follow_up_cycle_count >= _MAX_FOLLOW_UP_PER_CYCLE → no follow-up dispatched."""
    attempt_id = uuid.uuid4()
    patrol_id = uuid.uuid4()
    pool = _make_pool()
    # Same patrol_id and cycle_count=1 equals the limit (_MAX_FOLLOW_UP_PER_CYCLE=1)
    pool.fetch = AsyncMock(
        return_value=[
            _make_pr_row(
                attempt_id=attempt_id,
                follow_up_count=1,
                follow_up_cycle_patrol_id=patrol_id,
                follow_up_cycle_count=1,
            )
        ]
    )
    pr_data = {
        "state": "OPEN",
        "reviews": [],
        "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "bob"}}],
        "reviewThreads": [],
    }
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    spawner = MagicMock()
    config = QaDispatchConfig()

    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._dispatch_pr_review_followup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_followup,
    ):
        counts = await check_open_pr_statuses(
            pool,
            Path("/tmp/repo"),
            gh_token="ghtoken",
            spawner=spawner,
            config=config,
            patrol_id=patrol_id,
        )
    assert counts["follow_ups_dispatched"] == 0
    mock_followup.assert_not_called()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_no_spawner_no_followup():
    """OPEN with changes_requested but no spawner → no follow-up dispatched."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[_make_pr_row(attempt_id=attempt_id, follow_up_count=0)])
    pr_data = {
        "state": "OPEN",
        "reviews": [],
        "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "alice"}}],
        "reviewThreads": [],
    }
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0

    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        # No spawner or config → review tracking only
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")
    assert counts["follow_ups_dispatched"] == 0


# ---------------------------------------------------------------------------
# Per-cycle follow-up budgeting tests (bu-0025a.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_open_pr_statuses_cycle_reset_allows_followup():
    """New patrol_id resets the cycle counter: follow-up dispatched even when lifetime count > 0."""
    attempt_id = uuid.uuid4()
    old_patrol_id = uuid.uuid4()
    new_patrol_id = uuid.uuid4()
    pool = _make_pool()
    # Simulates a PR that had a follow-up in the prior cycle (cycle_count=1 under old patrol)
    pool.fetch = AsyncMock(
        return_value=[
            _make_pr_row(
                attempt_id=attempt_id,
                follow_up_count=1,  # lifetime counter: already dispatched once
                follow_up_cycle_patrol_id=old_patrol_id,
                follow_up_cycle_count=1,  # used up in the old cycle
            )
        ]
    )
    pr_data = {
        "state": "OPEN",
        "reviews": [],
        "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "alice"}}],
        "reviewThreads": [],
    }
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    spawner = MagicMock()
    config = QaDispatchConfig()

    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._dispatch_pr_review_followup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_followup,
    ):
        counts = await check_open_pr_statuses(
            pool,
            Path("/tmp/repo"),
            gh_token="ghtoken",
            spawner=spawner,
            config=config,
            patrol_id=new_patrol_id,  # new cycle!
        )
    # The new patrol_id means cycle_count effectively resets to 0, so follow-up is allowed
    assert counts["follow_ups_dispatched"] == 1
    mock_followup.assert_awaited_once()
    # patrol_id should be forwarded to _dispatch_pr_review_followup
    call_kwargs = mock_followup.call_args.kwargs
    assert call_kwargs["patrol_id"] == new_patrol_id


@pytest.mark.asyncio
async def test_check_open_pr_statuses_same_cycle_blocks_second_followup():
    """Same patrol_id with cycle_count=1 blocks a second follow-up in the same cycle."""
    attempt_id = uuid.uuid4()
    patrol_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(
        return_value=[
            _make_pr_row(
                attempt_id=attempt_id,
                follow_up_count=1,
                follow_up_cycle_patrol_id=patrol_id,
                follow_up_cycle_count=1,  # already dispatched this cycle
            )
        ]
    )
    pr_data = {
        "state": "OPEN",
        "reviews": [],
        "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "alice"}}],
        "reviewThreads": [],
    }
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    spawner = MagicMock()
    config = QaDispatchConfig()

    with (
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._dispatch_pr_review_followup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_followup,
    ):
        counts = await check_open_pr_statuses(
            pool,
            Path("/tmp/repo"),
            gh_token="ghtoken",
            spawner=spawner,
            config=config,
            patrol_id=patrol_id,  # same cycle
        )
    assert counts["follow_ups_dispatched"] == 0
    mock_followup.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_persists_cycle_counter():
    """_dispatch_pr_review_followup UPDATE sets cycle fields and last_follow_up_status."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    patrol_id = uuid.uuid4()
    branch_name = "qa/general/abcdef-1234"

    mock_proc_ok = MagicMock()
    mock_proc_ok.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc_ok.returncode = 0

    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe feedback"),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc_ok,
        ),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("butlers.core.qa.dispatch._run_review_followup_session", new_callable=AsyncMock),
    ):
        result = await _dispatch_pr_review_followup(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name=branch_name,
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token="token",
            patrol_id=patrol_id,
        )
    assert result is True
    pool.execute.assert_awaited_once()
    call_sql = pool.execute.call_args[0][0]
    # Cycle fields are updated
    assert "follow_up_cycle_patrol_id" in call_sql
    assert "follow_up_cycle_count" in call_sql
    # Dispatch marker set
    assert "last_follow_up_status" in call_sql
    assert "last_follow_up_at" in call_sql
    # patrol_id was passed as $2
    assert pool.execute.call_args[0][2] == patrol_id


@pytest.mark.asyncio
async def test_run_review_followup_session_persists_failure():
    """Failed agent run → last_follow_up_status='failed' + error persisted on the row."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=False,
            error="agent_timeout",
            session_id=None,
        )
    )

    with patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock):
        await _run_review_followup_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            followup_branch="qa/general/abcdef-1234",
            worktree_path=Path("/tmp/qa-followup-wt"),
            prompt="review prompt",
            config=QaDispatchConfig(),
            spawner=mock_spawner,
            sandbox_env={},
        )

    pool.execute.assert_awaited_once()
    call_sql = pool.execute.call_args[0][0]
    assert "last_follow_up_status" in call_sql
    # Check that 'failed' is the value being set
    assert "failed" in call_sql
    # Error arg passed
    call_args = pool.execute.call_args[0]
    assert "agent_timeout" in (call_args[3] or "")


@pytest.mark.asyncio
async def test_run_review_followup_session_persists_success():
    """Successful agent run + push → last_follow_up_status='succeeded' + session_id persisted."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    session_id = uuid.uuid4()
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=True,
            error=None,
            session_id=session_id,
        )
    )

    # Mock successful push
    mock_push_proc = MagicMock()
    mock_push_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_push_proc.returncode = 0

    with (
        patch("butlers.core.qa.dispatch.build_git_auth_env", return_value={}),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_push_proc,
        ),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
    ):
        await _run_review_followup_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            followup_branch="qa/general/abcdef-1234",
            worktree_path=Path("/tmp/qa-followup-wt"),
            prompt="review prompt",
            config=QaDispatchConfig(),
            spawner=mock_spawner,
            sandbox_env={},
        )

    pool.execute.assert_awaited_once()
    call_sql = pool.execute.call_args[0][0]
    assert "last_follow_up_status" in call_sql
    assert "succeeded" in call_sql
    # session_id forwarded
    call_args = pool.execute.call_args[0]
    assert call_args[2] == session_id


@pytest.mark.asyncio
async def test_run_review_followup_session_persists_push_failure():
    """Successful agent but push failure → last_follow_up_status='failed'."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=True,
            error=None,
            session_id=None,
        )
    )

    # Mock push failure
    mock_push_proc = MagicMock()
    mock_push_proc.communicate = AsyncMock(return_value=(b"", b"error: rejected"))
    mock_push_proc.returncode = 1

    with (
        patch("butlers.core.qa.dispatch.build_git_auth_env", return_value={}),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_push_proc,
        ),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._classify_git_push_error",
            return_value="push_rejected",
        ),
    ):
        await _run_review_followup_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            followup_branch="qa/general/abcdef-1234",
            worktree_path=Path("/tmp/qa-followup-wt"),
            prompt="review prompt",
            config=QaDispatchConfig(),
            spawner=mock_spawner,
            sandbox_env={},
        )

    pool.execute.assert_awaited_once()
    call_sql = pool.execute.call_args[0][0]
    assert "failed" in call_sql
    call_args = pool.execute.call_args[0]
    assert "push_rejected" in (call_args[3] or "")


# ---------------------------------------------------------------------------
# _extract_review_state tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_review_state_no_reviews():
    """No reviews → (None, None)."""
    pr_data = {"reviews": [], "latestReviews": [], "reviewThreads": []}
    state, summary = _extract_review_state(pr_data)
    assert state is None
    assert summary is None


@pytest.mark.unit
def test_extract_review_state_approved():
    """Single APPROVED review → (approved, None)."""
    pr_data = {
        "reviews": [],
        "latestReviews": [{"state": "APPROVED", "author": {"login": "alice"}}],
        "reviewThreads": [],
    }
    state, summary = _extract_review_state(pr_data)
    assert state == "approved"
    assert summary is None


@pytest.mark.unit
def test_extract_review_state_changes_requested_dominant():
    """CHANGES_REQUESTED dominates APPROVED in priority."""
    pr_data = {
        "reviews": [
            {"state": "CHANGES_REQUESTED", "body": "Fix the tests", "author": {"login": "alice"}}
        ],
        "latestReviews": [
            {"state": "CHANGES_REQUESTED", "author": {"login": "alice"}},
            {"state": "APPROVED", "author": {"login": "bob"}},
        ],
        "reviewThreads": [],
    }
    state, summary = _extract_review_state(pr_data)
    assert state == "changes_requested"
    assert summary is not None and "Fix the tests" in summary


@pytest.mark.unit
def test_extract_review_state_unresolved_threads():
    """Unresolved review threads produce feedback summary."""
    pr_data = {
        "reviews": [],
        "latestReviews": [],
        "reviewThreads": [
            {
                "isResolved": False,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {
                            "body": "Please add a test for this case.",
                            "author": {"login": "reviewer"},
                        }
                    ]
                },
            }
        ],
    }
    state, summary = _extract_review_state(pr_data)
    assert state is None  # No latestReviews → no dominant state
    assert summary is not None
    assert "Please add a test" in summary


@pytest.mark.unit
def test_extract_review_state_resolved_threads_ignored():
    """Resolved threads are excluded from feedback summary."""
    pr_data = {
        "reviews": [],
        "latestReviews": [],
        "reviewThreads": [
            {
                "isResolved": True,
                "isOutdated": False,
                "comments": {
                    "nodes": [
                        {"body": "Already resolved comment.", "author": {"login": "reviewer"}}
                    ]
                },
            }
        ],
    }
    state, summary = _extract_review_state(pr_data)
    assert state is None
    assert summary is None


@pytest.mark.unit
def test_extract_review_state_changes_requested_no_body():
    """CHANGES_REQUESTED with no body text still produces a fallback summary."""
    pr_data = {
        "reviews": [{"state": "CHANGES_REQUESTED", "body": "", "author": {"login": "alice"}}],
        "latestReviews": [{"state": "CHANGES_REQUESTED", "author": {"login": "alice"}}],
        "reviewThreads": [],
    }
    state, summary = _extract_review_state(pr_data)
    assert state == "changes_requested"
    assert summary is not None  # Fallback message applied


# ---------------------------------------------------------------------------
# _dispatch_pr_review_followup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_anonymization_failure():
    """Anonymization validation failure → returns False, no task created."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()

    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe text"),
        patch(
            "butlers.core.qa.dispatch.validate_anonymized",
            return_value=(False, ["violation"]),
        ),
    ):
        result = await _dispatch_pr_review_followup(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name="qa/general/abcdef",
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token="token",
        )
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_missing_branch():
    """Missing pr_branch_name → returns False immediately."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()

    result = await _dispatch_pr_review_followup(
        pool=pool,
        repo_root=Path("/tmp/repo"),
        attempt_id=attempt_id,
        pr_number=42,
        pr_url="https://github.com/org/repo/pull/42",
        fingerprint="a" * 64,
        butler_name="general",
        pr_branch_name=None,
        feedback_summary="reviewer comment",
        config=QaDispatchConfig(),
        spawner=MagicMock(),
        gh_token="token",
    )
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_success():
    """Successful anonymization + worktree anchored to origin → True, follow_up_count incremented."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    branch_name = "qa/general/abcdef-1234"

    # Mock git subprocess (fetch + worktree add both succeed)
    mock_proc_ok = MagicMock()
    mock_proc_ok.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc_ok.returncode = 0

    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe feedback"),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc_ok,
        ),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("butlers.core.qa.dispatch._run_review_followup_session", new_callable=AsyncMock),
    ):
        result = await _dispatch_pr_review_followup(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name=branch_name,
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token="token",
        )
    assert result is True
    # follow_up_count increment executed
    pool.execute.assert_awaited_once()
    call_sql = pool.execute.call_args[0][0]
    assert "follow_up_count" in call_sql


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_branch_prep_uses_auth_env():
    """Branch prep subprocesses (fetch + worktree add) receive authenticated git env."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    branch_name = "qa/general/abcdef-1234"
    gh_token = "ghp_testtoken"
    fake_auth_env = {"GIT_TERMINAL_PROMPT": "0", "GH_TOKEN": gh_token, "SENTINEL": "auth"}

    mock_proc_ok = MagicMock()
    mock_proc_ok.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc_ok.returncode = 0

    captured_envs: list[dict[str, str] | None] = []

    async def _capture_exec(*args: object, **kwargs: object) -> MagicMock:
        captured_envs.append(kwargs.get("env"))
        return mock_proc_ok

    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe feedback"),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
        patch("butlers.core.qa.dispatch.build_git_auth_env", return_value=fake_auth_env),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            side_effect=_capture_exec,
        ),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("butlers.core.qa.dispatch._run_review_followup_session", new_callable=AsyncMock),
    ):
        result = await _dispatch_pr_review_followup(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name=branch_name,
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token=gh_token,
        )
    assert result is True
    # Both fetch and worktree-add must have received the authenticated env
    assert len(captured_envs) == 2, f"expected 2 git calls, got {len(captured_envs)}"
    for env in captured_envs:
        assert env is not None, "git subprocess called without env"
        assert env.get("SENTINEL") == "auth", "git subprocess did not receive auth env"


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_worktree_anchored_to_origin():
    """Worktree is created with -B and origin/<branch> regardless of local branch existence.

    This prevents stale local-branch state from driving the follow-up session.
    The show-ref check has been removed; ``-B`` resets any existing local branch
    to ``origin/<branch>`` atomically.
    """
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    branch_name = "qa/general/stale-local"

    mock_proc_ok = MagicMock()
    mock_proc_ok.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc_ok.returncode = 0

    captured_calls: list[tuple[str, ...]] = []

    async def _capture_exec(*args: str, **kwargs: object) -> MagicMock:
        captured_calls.append(args)
        return mock_proc_ok

    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe feedback"),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            side_effect=_capture_exec,
        ),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("butlers.core.qa.dispatch._run_review_followup_session", new_callable=AsyncMock),
    ):
        await _dispatch_pr_review_followup(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name=branch_name,
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token="token",
        )

    # There must be exactly 2 git subprocess calls: fetch + worktree add
    assert len(captured_calls) == 2, (
        f"expected 2 git calls, got {len(captured_calls)}: {captured_calls}"
    )
    # show-ref must NOT appear — we no longer check for local branch existence
    assert not any("show-ref" in " ".join(call) for call in captured_calls), (
        "show-ref call found; local-branch reuse check should have been removed"
    )
    # worktree add must use -B and origin/<branch> to anchor to remote head
    worktree_call = captured_calls[1]
    assert "-B" in worktree_call, "worktree add must use -B to create-or-reset the local branch"
    assert f"origin/{branch_name}" in worktree_call, (
        "worktree add must anchor to origin/<branch> to prevent stale-local-branch reuse"
    )


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_worktree_failure():
    """Worktree creation failure (git fetch error) → returns False."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()

    # Mock git subprocess: fetch fails
    mock_proc_fail = MagicMock()
    mock_proc_fail.communicate = AsyncMock(return_value=(b"error: branch not found", b""))
    mock_proc_fail.returncode = 128

    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe"),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc_fail,
        ),
        patch("pathlib.Path.mkdir"),
    ):
        result = await _dispatch_pr_review_followup(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name="qa/general/abcdef",
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token="token",
        )
    assert result is False


# ---------------------------------------------------------------------------
# timeout_override propagation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_investigation_session_passes_timeout_override():
    """_run_investigation_session passes timeout_override=config.timeout_minutes*60 to spawner."""
    pool = _make_pool()
    config = QaDispatchConfig(timeout_minutes=25)
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=False,
            error="test abort",
            session_id=None,
        )
    )

    with (
        patch("butlers.core.qa.dispatch.build_sandbox_env", return_value={}),
        patch("butlers.core.qa.dispatch.build_investigation_prompt", return_value="prompt"),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=uuid.uuid4(),
            finding_id=uuid.uuid4(),
            branch_name="qa/finance/abc123",
            worktree_path=Path("/tmp/qa-wt"),
            finding=_make_finding(),
            config=config,
            spawner=mock_spawner,
            gh_token="ghtoken",
        )

    mock_spawner.trigger.assert_awaited_once()
    call_kwargs = mock_spawner.trigger.call_args.kwargs
    assert call_kwargs["timeout_override"] == 25 * 60


@pytest.mark.asyncio
async def test_review_followup_session_passes_timeout_override():
    """_run_review_followup_session passes timeout_override=config.timeout_minutes*60."""
    pool = _make_pool()
    config = QaDispatchConfig(timeout_minutes=20)
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=False,
            error="test abort",
            session_id=None,
        )
    )

    with patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock):
        await _run_review_followup_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=uuid.uuid4(),
            pr_number=42,
            followup_branch="qa/general/abcdef-1234",
            worktree_path=Path("/tmp/qa-followup-wt"),
            prompt="review prompt",
            config=config,
            spawner=mock_spawner,
            sandbox_env={},
        )

    mock_spawner.trigger.assert_awaited_once()
    call_kwargs = mock_spawner.trigger.call_args.kwargs
    assert call_kwargs["timeout_override"] == 20 * 60


# ---------------------------------------------------------------------------
# QA admission-control accounting: gate rejections use dispatch events and
# dedup_reason writeback, not failed healing_attempts rows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qa_dispatch_cooldown_uses_delete_event_and_dedup_reason():
    """Cooldown gate: orphaned attempt deleted, dispatch event recorded, dedup_reason written."""
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
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
            return_value={"status": "failed"},  # cooldown active
        ),
        patch(
            "butlers.core.qa.dispatch.delete_orphaned_attempt",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_delete,
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason",
            new_callable=AsyncMock,
        ) as mock_dedup,
        patch(
            "butlers.core.qa.dispatch.create_dispatch_event",
            new_callable=AsyncMock,
        ) as mock_event,
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=TriagedFinding(
                finding=_make_finding(severity=1),
                dedup_reason=None,
                finding_id=finding_id,
            ),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "cooldown"
    # Orphaned row deleted, not failed
    mock_delete.assert_awaited_once()
    # Dispatch event recorded with correct decision
    mock_event.assert_awaited_once()
    assert mock_event.call_args.kwargs.get("decision") == "cooldown"
    # Authoritative dedup_reason written back to the finding
    mock_dedup.assert_awaited_once()
    assert mock_dedup.call_args[0][2] == "cooldown"
    # update_attempt_status NOT called (no failed row created)
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_qa_dispatch_concurrency_cap_uses_delete_event_and_dedup_reason():
    """Concurrency cap gate: orphaned attempt deleted, dispatch event recorded, dedup_reason set."""
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
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
            return_value=10,  # way above max_concurrent=2
        ),
        patch(
            "butlers.core.qa.dispatch.delete_orphaned_attempt",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_delete,
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason",
            new_callable=AsyncMock,
        ) as mock_dedup,
        patch(
            "butlers.core.qa.dispatch.create_dispatch_event",
            new_callable=AsyncMock,
        ) as mock_event,
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
        ) as mock_update,
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=TriagedFinding(
                finding=_make_finding(severity=1),
                dedup_reason=None,
                finding_id=finding_id,
            ),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "concurrency_cap"
    mock_delete.assert_awaited_once()
    mock_event.assert_awaited_once()
    assert mock_event.call_args.kwargs.get("decision") == "concurrency_cap"
    mock_dedup.assert_awaited_once()
    assert mock_dedup.call_args[0][2] == "concurrency_cap"
    mock_update.assert_not_called()


@pytest.mark.asyncio
async def test_qa_dispatch_already_investigating_emits_event_and_dedup_reason():
    """Already-investigating (novelty join): dispatch event + dedup_reason + attempt link."""
    existing_attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(existing_attempt_id, False),  # is_new=False → join
        ),
        patch(
            "butlers.core.qa.dispatch.update_finding_attempt",
            new_callable=AsyncMock,
        ) as mock_link,
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason",
            new_callable=AsyncMock,
        ) as mock_dedup,
        patch(
            "butlers.core.qa.dispatch.create_dispatch_event",
            new_callable=AsyncMock,
        ) as mock_event,
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=TriagedFinding(
                finding=_make_finding(severity=1),
                dedup_reason=None,
                finding_id=finding_id,
            ),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "already_investigating"
    assert result.attempt_id == existing_attempt_id
    # Finding must be linked to the existing active attempt
    mock_link.assert_awaited_once()
    assert mock_link.call_args[0][2] == existing_attempt_id
    mock_dedup.assert_awaited_once()
    assert mock_dedup.call_args[0][2] == "already_investigating"
    mock_event.assert_awaited_once()
    assert mock_event.call_args.kwargs.get("decision") == "novelty_join"


@pytest.mark.asyncio
async def test_concurrency_gate_uses_qa_only_scope():
    """Gate 8 must call count_active_attempts with qa_only=True.

    Regression test: self-healing-only active attempts must not consume the
    QA concurrency budget. Without qa_only=True the gate counted all active
    attempts (including self-healing), blocking QA investigations even when no
    QA slots were in use.
    """
    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
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
            return_value=10,  # above max_concurrent=2, triggers the gate
        ) as mock_count,
        patch(
            "butlers.core.qa.dispatch.delete_orphaned_attempt",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch("butlers.core.qa.dispatch.update_finding_dedup_reason", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=TriagedFinding(
                finding=_make_finding(severity=1),
                dedup_reason=None,
                finding_id=finding_id,
            ),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False and result.reason == "concurrency_cap"
    # Gate 8 must use qa_only=True so that self-healing attempts are excluded
    # from the QA concurrency budget.
    mock_count.assert_awaited_once()
    _, call_kwargs = mock_count.call_args
    assert call_kwargs.get("qa_only") is True, (
        "count_active_attempts must be called with qa_only=True in Gate 8"
    )


# ---------------------------------------------------------------------------
# base_ref selection: QA dispatch uses origin/main when fetch succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_qa_uses_origin_main_after_successful_fetch():
    """When git fetch succeeds, dispatch_qa_investigation passes base_ref='origin/main'."""
    attempt_id = uuid.uuid4()
    worktree_path = Path("/tmp/qa-worktree")
    branch_name = "qa/finance/abcdef123456"

    mock_proc_ok = MagicMock()
    mock_proc_ok.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc_ok.returncode = 0

    create_wt_mock = AsyncMock(return_value=(worktree_path, branch_name))

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts", new_callable=AsyncMock, return_value=1
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
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc_ok,
        ),
        patch(
            "butlers.core.qa.dispatch.create_healing_worktree",
            create_wt_mock,
        ),
        patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is True
    _, call_kwargs = create_wt_mock.call_args
    assert call_kwargs.get("base_ref") == "origin/main", (
        "QA dispatch must pass base_ref='origin/main' after a successful fetch"
    )


@pytest.mark.asyncio
async def test_dispatch_qa_falls_back_to_local_main_when_fetch_fails(caplog):
    """When git fetch fails, dispatch_qa_investigation falls back to base_ref='main' with a warning."""
    import logging

    attempt_id = uuid.uuid4()
    worktree_path = Path("/tmp/qa-worktree")
    branch_name = "qa/finance/abcdef123456"

    mock_proc_fail = MagicMock()
    mock_proc_fail.communicate = AsyncMock(return_value=(b"", b"fetch failed"))
    mock_proc_fail.returncode = 1

    create_wt_mock = AsyncMock(return_value=(worktree_path, branch_name))

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt", new_callable=AsyncMock, return_value=None
        ),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts", new_callable=AsyncMock, return_value=1
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
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=mock_proc_fail,
        ),
        patch(
            "butlers.core.qa.dispatch.create_healing_worktree",
            create_wt_mock,
        ),
        patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
        caplog.at_level(logging.WARNING, logger="butlers.core.qa.dispatch"),
    ):
        result = await dispatch_qa_investigation(
            pool=_make_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is True
    _, call_kwargs = create_wt_mock.call_args
    assert call_kwargs.get("base_ref") == "main", (
        "QA dispatch must fall back to base_ref='main' when fetch fails"
    )
    # Warning must include the chosen base ref for postmortem visibility
    log_text = " ".join(r.message for r in caplog.records)
    assert "main" in log_text, "Warning must name the fallback base ref"


# ---------------------------------------------------------------------------
# AC1/AC5: PR creation — non-canonical gh output triggers fallback lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_qa_pr_pr_number_fallback_on_non_canonical_stdout():
    """Non-canonical gh stdout (e.g. extra lines) falls back to head-branch lookup."""
    finding = _make_finding()
    repo_root = Path("/tmp/repo")
    branch_name = "qa/finance/abc123"
    attempt_id = uuid.uuid4()

    # Simulate non-standard gh pr create stdout that contains neither a PR number
    # nor a PR URL, forcing the code down the fallback head-branch lookup path.
    unparseable_stdout = b"some-unexpected-output\n"

    def _make_ok_proc(stdout: bytes):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.returncode = 0
        return proc

    # gh pr list returns a single PR result for the fallback
    fallback_json = _test_json.dumps(
        [{"number": 77, "url": "https://github.com/org/repo/pull/77"}]
    ).encode()

    # Subprocess call order inside _create_qa_pr:
    #   1. git remote get-url origin (whitelist check, returns a valid origin)
    #   2. git log main..branch --oneline (no-op check, returns one line = has commits)
    #   3. git push origin branch_name (push OK)
    #   4. gh pr create (returns unparseable stdout)
    #   5. gh pr list --head branch_name (fallback lookup)
    procs = [
        _make_ok_proc(b"https://github.com/org/repo.git"),  # remote get-url
        _make_ok_proc(b"abc1234 fix: something\n"),  # git log (has commits)
        _make_ok_proc(b""),  # git push
        _make_ok_proc(unparseable_stdout),  # gh pr create
        _make_ok_proc(fallback_json),  # gh pr list fallback
    ]
    proc_iter = iter(procs)

    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            side_effect=lambda *a, **kw: next(proc_iter),
        ),
        patch("butlers.core.qa.dispatch.anonymize", side_effect=lambda text, _root: text),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=repo_root,
            branch_name=branch_name,
            finding=finding,
            attempt_id=attempt_id,
            labels=[],
            gh_token="ghtoken",
            whitelist=MagicMock(
                ensure_loaded=AsyncMock(),
                is_allowed=MagicMock(return_value=(True, "allowed")),
            ),
        )

    assert error is None
    assert pr_number == 77
    assert pr_url == "https://github.com/org/repo/pull/77"


# ---------------------------------------------------------------------------
# AC3/AC5: No-op detection — empty branch blocked before push
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_no_op_branch_returns_true_when_no_commits():
    """_detect_no_op_branch returns True when git log emits no output."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0

    with patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=proc):
        result = await _detect_no_op_branch(Path("/tmp/repo"), "qa/test/abc", {})

    assert result is True


@pytest.mark.asyncio
async def test_detect_no_op_branch_returns_false_when_commits_exist():
    """_detect_no_op_branch returns False when git log emits commit lines."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"abc1234 fix: something\n", b""))
    proc.returncode = 0

    with patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=proc):
        result = await _detect_no_op_branch(Path("/tmp/repo"), "qa/test/abc", {})

    assert result is False


@pytest.mark.asyncio
async def test_detect_no_op_branch_returns_false_on_subprocess_failure():
    """_detect_no_op_branch returns False (safe: let push proceed) when git fails."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b"error"))
    proc.returncode = 128

    with patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=proc):
        result = await _detect_no_op_branch(Path("/tmp/repo"), "qa/test/abc", {})

    assert result is False


@pytest.mark.asyncio
async def test_run_investigation_session_no_op_branch_marks_unfixable():
    """no_op_branch error from _create_qa_pr → attempt transitions to unfixable, not failed."""
    pool = _make_pool()
    config = QaDispatchConfig(timeout_minutes=30)
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock(
        return_value=MagicMock(success=True, error=None, session_id=uuid.uuid4())
    )
    attempt_id = uuid.uuid4()

    with (
        patch("butlers.core.qa.dispatch.build_sandbox_env", return_value={}),
        patch("butlers.core.qa.dispatch.build_investigation_prompt", return_value="prompt"),
        patch("butlers.core.qa.dispatch.record_phase_session", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock
        ) as mock_update,
        patch("butlers.core.qa.dispatch.update_phase_session_status", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=(None, None, "no_op_branch"),
        ),
        # unfixable sentinel file must NOT exist (so the sentinel check doesn't fire first)
        patch("pathlib.Path.exists", return_value=False),
    ):
        await _run_investigation_session(
            pool=pool,
            repo_root=Path("/tmp/repo"),
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name="qa/finance/abc123",
            worktree_path=Path("/tmp/qa-wt"),
            finding=_make_finding(),
            config=config,
            spawner=mock_spawner,
            gh_token="ghtoken",
        )

    # Must transition to unfixable, NOT failed
    mock_update.assert_awaited_once()
    call_args = mock_update.call_args
    assert call_args[0][2] == "unfixable"
    assert "no_op_branch" in (call_args[1].get("error_detail") or "")


# ---------------------------------------------------------------------------
# AC4/AC5: Push succeeded, gh pr create failed — remote branch cleanup attempted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_qa_pr_gh_create_failure_cleans_up_remote_branch():
    """If push succeeds but gh pr create fails, best-effort remote branch delete is attempted."""
    finding = _make_finding()
    repo_root = Path("/tmp/repo")
    branch_name = "qa/finance/abc123"

    def _make_ok_proc(stdout: bytes = b""):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.returncode = 0
        return proc

    def _make_fail_proc(stderr: bytes):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", stderr))
        proc.returncode = 1
        return proc

    # Call order:
    #   1. git remote get-url origin
    #   2. git log (has commits)
    #   3. git push origin branch → succeeds
    #   4. gh pr create → fails with non-auth error
    #   5. git push origin --delete branch → best-effort cleanup
    procs = [
        _make_ok_proc(b"https://github.com/org/repo.git"),  # remote get-url
        _make_ok_proc(b"abc1234 fix: something\n"),  # git log
        _make_ok_proc(b""),  # git push
        _make_fail_proc(b"GraphQL: something went wrong"),  # gh pr create fails
        _make_ok_proc(b""),  # git push --delete cleanup
    ]
    proc_iter = iter(procs)

    subprocess_calls: list[tuple] = []

    def _capture_proc(*args, **kwargs):
        subprocess_calls.append(args)
        return next(proc_iter)

    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            side_effect=_capture_proc,
        ),
        patch("butlers.core.qa.dispatch.anonymize", side_effect=lambda text, _root: text),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
    ):
        pr_url, pr_number, error = await _create_qa_pr(
            repo_root=repo_root,
            branch_name=branch_name,
            finding=finding,
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="ghtoken",
            whitelist=MagicMock(
                ensure_loaded=AsyncMock(),
                is_allowed=MagicMock(return_value=(True, "allowed")),
            ),
        )

    assert pr_url is None
    assert pr_number is None
    assert error is not None and "gh_pr_create_failed" in error

    # Verify the cleanup delete was attempted (5th subprocess call)
    assert len(subprocess_calls) == 5
    cleanup_args = subprocess_calls[4]
    assert "--delete" in cleanup_args


# ---------------------------------------------------------------------------
# AC2/AC5: check_open_pr_statuses — pr_number=None triggers repair or transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_open_pr_statuses_repairs_missing_pr_number():
    """pr_number=NULL → head-branch lookup resolves and patches DB; polling continues normally."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    # Row with pr_number=None but branch_name present
    row = {
        "id": attempt_id,
        "pr_url": "https://github.com/org/repo/pull/55",
        "pr_number": None,
        "fingerprint": "a" * 64,
        "butler_name": "general",
        "follow_up_count": 0,
        "branch_name": "qa/general/abcdef",
        "follow_up_cycle_patrol_id": None,
        "follow_up_cycle_count": 0,
    }
    pool.fetch = AsyncMock(return_value=[row])

    # Subprocess calls:
    #   1. gh pr list --head branch → returns repaired PR identity
    #   2. gh pr view 55 --json ... → OPEN, no reviews
    repaired_list_json = _test_json.dumps(
        [{"number": 55, "url": "https://github.com/org/repo/pull/55"}]
    ).encode()
    pr_view_data = {"state": "OPEN", "reviews": [], "latestReviews": [], "reviewThreads": []}

    def _make_ok_proc(stdout: bytes):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.returncode = 0
        return proc

    procs = [
        _make_ok_proc(repaired_list_json),  # gh pr list (repair)
        _make_ok_proc(_test_json.dumps(pr_view_data).encode()),  # gh pr view (polling)
    ]
    proc_iter = iter(procs)

    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            side_effect=lambda *a, **kw: next(proc_iter),
        ),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")

    assert counts["errors"] == 0
    # DB repair was executed
    pool.execute.assert_awaited()
    repair_call = pool.execute.call_args_list[0]
    assert "pr_number" in repair_call[0][0]


@pytest.mark.asyncio
async def test_check_open_pr_statuses_transitions_to_failed_when_repair_fails():
    """pr_number=NULL and head-branch lookup returns nothing → attempt transitions to failed."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    row = {
        "id": attempt_id,
        "pr_url": "https://github.com/org/repo/pull/55",
        "pr_number": None,
        "fingerprint": "a" * 64,
        "butler_name": "general",
        "follow_up_count": 0,
        "branch_name": "qa/general/abcdef",
        "follow_up_cycle_patrol_id": None,
        "follow_up_cycle_count": 0,
    }
    pool.fetch = AsyncMock(return_value=[row])

    # gh pr list returns empty → no PR found for head branch
    empty_list_json = _test_json.dumps([]).encode()

    def _make_ok_proc(stdout: bytes):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        proc.returncode = 0
        return proc

    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
            return_value=_make_ok_proc(empty_list_json),
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock
        ) as mock_update,
    ):
        counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")

    # No errors incremented; but attempt was transitioned to failed
    assert counts["errors"] == 0
    mock_update.assert_awaited_once()
    call_args = mock_update.call_args
    assert call_args[0][2] == "failed"
    assert "pr_number_missing" in (call_args[1].get("error_detail") or "")


@pytest.mark.asyncio
async def test_check_open_pr_statuses_transitions_to_failed_when_no_branch():
    """pr_number=NULL and branch_name=NULL → cannot repair, transitions to failed."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    row = {
        "id": attempt_id,
        "pr_url": "https://github.com/org/repo/pull/55",
        "pr_number": None,
        "fingerprint": "a" * 64,
        "butler_name": "general",
        "follow_up_count": 0,
        "branch_name": None,  # branch_name also missing
        "follow_up_cycle_patrol_id": None,
        "follow_up_cycle_count": 0,
    }
    pool.fetch = AsyncMock(return_value=[row])

    with (
        patch(
            "butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock
        ) as mock_update,
    ):
        await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token="ghtoken")

    mock_update.assert_awaited_once()
    call_args = mock_update.call_args
    assert call_args[0][2] == "failed"
    assert "pr_number_missing" in (call_args[1].get("error_detail") or "")


# ---------------------------------------------------------------------------
# AC5: _resolve_pr_by_head helper unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_pr_by_head_returns_url_and_number():
    """Single open PR for head branch → (url, number) returned."""
    import json as _json

    result_json = _json.dumps(
        [{"number": 42, "url": "https://github.com/org/repo/pull/42"}]
    ).encode()
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(result_json, b""))
    proc.returncode = 0

    with patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=proc):
        url, number = await _resolve_pr_by_head(Path("/tmp/repo"), "qa/general/abc", {})

    assert url == "https://github.com/org/repo/pull/42"
    assert number == 42


@pytest.mark.asyncio
async def test_resolve_pr_by_head_returns_none_when_multiple_prs():
    """Multiple PRs for same head branch → (None, None) — ambiguous result."""
    import json as _json

    result_json = _json.dumps(
        [
            {"number": 42, "url": "https://github.com/org/repo/pull/42"},
            {"number": 43, "url": "https://github.com/org/repo/pull/43"},
        ]
    ).encode()
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(result_json, b""))
    proc.returncode = 0

    with patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=proc):
        url, number = await _resolve_pr_by_head(Path("/tmp/repo"), "qa/general/abc", {})

    assert url is None
    assert number is None


@pytest.mark.asyncio
async def test_resolve_pr_by_head_returns_none_on_empty_list():
    """No open PRs for head branch → (None, None)."""
    import json as _json

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(_json.dumps([]).encode(), b""))
    proc.returncode = 0

    with patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=proc):
        url, number = await _resolve_pr_by_head(Path("/tmp/repo"), "qa/general/abc", {})

    assert url is None
    assert number is None
