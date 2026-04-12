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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import (
    QaDispatchConfig,
    QaDispatchResult,
    _dispatch_pr_review_followup,
    _extract_review_state,
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
    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation",
            new_callable=AsyncMock,
            return_value=QaDispatchResult(
                accepted=False, fingerprint="a" * 64, reason="severity_above_threshold"
            ),
        ) as mock_d,
    ):
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
    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation", new_callable=AsyncMock
        ) as mock_d2,
    ):
        results2 = await dispatch_novel_findings(
            pool=pool,
            novel_findings=[],
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )
    assert results2 == [] and not mock_d2.called

    # Stops at concurrency cap and queues the remainder without creating rows
    cap_findings = [_make_triaged() for _ in range(4)]
    call_count = 0

    async def cap_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched")

    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            side_effect=[1, 2, 2, 2],
        ),
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation", side_effect=cap_side_effect),
    ):
        results3 = await dispatch_novel_findings(
            pool=pool,
            novel_findings=cap_findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(max_concurrent=2),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )
    assert call_count == 1 and len(results3) == 4
    assert results3[0].reason == "dispatched"
    assert all(r.reason == "concurrency_cap" for r in results3[1:])


def _make_pr_row(
    *,
    attempt_id: uuid.UUID | None = None,
    pr_url: str = "https://github.com/org/repo/pull/42",
    pr_number: int = 42,
    fingerprint: str = "a" * 64,
    butler_name: str = "general",
    follow_up_count: int = 0,
    branch_name: str | None = "qa/general/abcdef",
    last_follow_up_at: datetime | None = None,
) -> dict:
    return {
        "id": attempt_id or uuid.uuid4(),
        "pr_url": pr_url,
        "pr_number": pr_number,
        "fingerprint": fingerprint,
        "butler_name": butler_name,
        "follow_up_count": follow_up_count,
        "branch_name": branch_name,
        "last_follow_up_at": last_follow_up_at,
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
    """Recent follow-up dispatches are staggered with exponential backoff."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(
        return_value=[
            _make_pr_row(
                attempt_id=attempt_id,
                follow_up_count=1,
                last_follow_up_at=datetime.now(UTC),
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
        )
    assert counts["follow_ups_dispatched"] == 0
    mock_followup.assert_not_called()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_followup_due_after_backoff():
    """Older follow-ups become eligible again after the exponential delay."""
    attempt_id = uuid.uuid4()
    pool = _make_pool()
    pool.fetch = AsyncMock(
        return_value=[
            _make_pr_row(
                attempt_id=attempt_id,
                follow_up_count=1,
                last_follow_up_at=datetime.now(UTC) - timedelta(minutes=11),
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
            spawner=MagicMock(),
            config=QaDispatchConfig(),
        )
    assert counts["follow_ups_dispatched"] == 1
    mock_followup.assert_awaited_once()


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
    """Successful anonymization + existing-branch worktree → True, follow_up_count incremented."""
    pool = _make_pool()
    attempt_id = uuid.uuid4()
    branch_name = "qa/general/abcdef-1234"

    # Mock git subprocess (fetch + show-ref + worktree add all succeed)
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
    assert "last_follow_up_at" in call_sql


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
