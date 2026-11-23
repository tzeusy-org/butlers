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
- _dispatch_pr_review_followup: anonymization failure, explicit remote-ref
  materialization, fail-closed fetch failure
"""

from __future__ import annotations

import asyncio
import json as _test_json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa.dispatch import (
    QaDispatchConfig,
    QaDispatchResult,
    _capture_commit_diff_snapshot,
    _dispatch_pr_review_followup,
    _extract_review_state,
    _persist_notes_and_remove_worktree,
    _prepare_agent_workspace,
    _run_investigation_session,
    _run_review_followup_session,
    build_sandbox_env,
    check_open_pr_statuses,
    dispatch_novel_findings,
    dispatch_qa_investigation,
)
from butlers.core.qa.models import QaFinding
from butlers.core.qa.publication.binding import OperationKind
from butlers.core.qa.publication.publisher import (
    PublicationResult,
    PublicationStatus,
    QaPublisher,
)
from butlers.core.qa.triage import TriagedFinding
from butlers.testing.fake_pool import fake_pool
from tests.core.qa.publication_fakes import (
    QA_IDENTITY,
    FakeLedger,
    FakeProvider,
    commit_file,
    git,
    init_repo,
)


def _make_finding(severity: int = 1, source_type: str = "log_scanner") -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type=source_type,
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


def _qa_pool():
    return fake_pool(fetchval=uuid.uuid4(), fetchrow=None, fetch=[], execute=None)


@pytest.mark.asyncio
async def test_dispatch_qa_publication_paused_holds_before_claim(_no_paused_proposal):
    """A retained proposal holds its fingerprint: event written, no claim, no spawn."""
    _no_paused_proposal.return_value = uuid.uuid4()
    spawner = MagicMock()
    with (
        patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock) as claim,
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason", new_callable=AsyncMock
        ) as dedup,
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock) as event,
    ):
        result = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=spawner,
            gh_token=None,
        )
    assert result.accepted is False and result.reason == "publication_paused"
    claim.assert_not_awaited()
    spawner.trigger.assert_not_called()
    dedup.assert_awaited_once_with(ANY, ANY, "publication_paused")
    assert event.await_args.kwargs["decision"] == "publication_paused"
    assert event.await_args.kwargs["attempt_id"] is None
    # The hold window is passed through and capped by the export retention.
    assert _no_paused_proposal.await_args.args[2] == 7


@pytest.mark.asyncio
async def test_dispatch_qa_publication_paused_inert_with_publisher(_no_paused_proposal):
    """With a publisher injected the gate is inert and never consults the store."""
    result = await dispatch_qa_investigation(
        pool=_qa_pool(),
        triaged_finding=_make_triaged(_make_finding(severity=3)),
        patrol_id=uuid.uuid4(),
        config=QaDispatchConfig(severity_threshold=2, publisher=MagicMock()),
        repo_root=Path("/tmp/repo"),
        spawner=MagicMock(),
        gh_token=None,
    )
    assert result.reason == "severity_above_threshold"
    _no_paused_proposal.assert_not_awaited()


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

    env_with_token = build_sandbox_env(
        "mytoken123",
        git_author_name="QA Staffer",
        git_author_email="qa@example.com",
    )
    assert env_with_token.get("GH_TOKEN") == "mytoken123"
    assert "PATH" in env_with_token and "HOME" in env_with_token
    assert env_with_token["GIT_AUTHOR_NAME"] == "QA Staffer"
    assert env_with_token["GIT_COMMITTER_NAME"] == "QA Staffer"
    assert env_with_token["GIT_AUTHOR_EMAIL"] == "qa@example.com"
    assert env_with_token["GIT_COMMITTER_EMAIL"] == "qa@example.com"

    env_no_token = build_sandbox_env(None)
    assert "GH_TOKEN" not in env_no_token
    for blocked in ("BUTLERS_DB_URL", "DATABASE_URL", "PGPASSWORD", "ANTHROPIC_API_KEY"):
        assert blocked not in env_no_token
    assert "UV_CACHE_DIR" in env_no_token

    # Empty string gh_token not injected
    assert "GH_TOKEN" not in build_sandbox_env("")


@pytest.mark.unit
def test_prepare_agent_workspace_creates_override(tmp_path: Path):
    """QA helper cwd is created under ignored .tmp with a local AGENTS override."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "roster").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='qa-test'\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")

    agent_dir = _prepare_agent_workspace(tmp_path)
    assert agent_dir == tmp_path / ".tmp" / "qa-agent"
    agents_md = agent_dir / "AGENTS.md"
    assert agents_md.is_file()
    text = agents_md.read_text(encoding="utf-8")
    assert "Do not run `bd`." in text
    assert "Do not push branches or open PRs yourself." in text
    for name in ("src", "tests", "roster", "frontend", "pyproject.toml", "uv.lock"):
        link_path = agent_dir / name
        assert link_path.exists()
        assert link_path.is_symlink()


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
        pool=_qa_pool(),
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
            pool=_qa_pool(),
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
            "butlers.core.qa.dispatch.get_publication_hold",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "butlers.core.qa.dispatch.get_recent_attempt",
            new_callable=AsyncMock,
            return_value={"status": "failed"},
        ),
    ):
        r3 = await dispatch_qa_investigation(
            pool=_qa_pool(),
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
            "butlers.core.qa.dispatch.get_publication_hold",
            new_callable=AsyncMock,
            return_value=False,
        ),
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
            "butlers.core.qa.dispatch.resolve_model_with_effective_tier",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        r4 = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert r4.accepted is False and r4.reason == "no_model"


@pytest.mark.asyncio
async def test_dispatch_qa_infra_condition_open_suppresses_before_novelty_gate():
    """bu-27dxl.6.4: an active infra_state condition suppresses before create_or_join_attempt.

    Gate 5.5 must reject and record a decision-only dispatch event WITHOUT
    ever reaching Gate 6 (create_or_join_attempt) — asserted directly by
    patching create_or_join_attempt and checking it was never called, per
    AC2 ("the gate occurs before create_or_join_attempt").
    """
    finding = _make_finding(severity=1, source_type="infra_state")
    active_condition = {
        "state": "aging",
        "escalation_level": "L1",
        "first_detected_at": datetime.now(UTC) - timedelta(hours=2),
        "summary": "Connector gmail/owner@example.com is offline",
    }

    with (
        patch(
            "butlers.core.qa.dispatch.get_active_condition",
            new_callable=AsyncMock,
            return_value=active_condition,
        ) as mock_get_condition,
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock
        ) as mock_create_or_join,
        patch(
            "butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock
        ) as mock_dispatch_event,
        patch(
            "butlers.core.qa.dispatch.update_finding_dedup_reason", new_callable=AsyncMock
        ) as mock_dedup,
    ):
        result = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(finding),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    assert result.accepted is False
    assert result.reason == "infra_condition_open"
    assert result.attempt_id is None
    mock_get_condition.assert_awaited_once_with(
        ANY, source="infra_state", fingerprint=finding.fingerprint
    )
    mock_create_or_join.assert_not_awaited()
    mock_dedup.assert_awaited_once_with(ANY, ANY, "infra_condition_open")
    mock_dispatch_event.assert_awaited_once()
    _, dispatch_kwargs = mock_dispatch_event.call_args
    assert dispatch_kwargs["decision"] == "infra_condition_open"
    assert dispatch_kwargs["attempt_id"] is None


@pytest.mark.asyncio
async def test_dispatch_qa_infra_condition_absent_proceeds_to_novelty_gate():
    """No active condition for an infra_state finding → Gate 5.5 is a no-op, Gate 6 still runs."""
    finding = _make_finding(severity=1, source_type="infra_state")

    with (
        patch(
            "butlers.core.qa.dispatch.get_active_condition",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(uuid.uuid4(), False),
        ) as mock_create_or_join,
    ):
        result = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(finding),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    mock_create_or_join.assert_awaited_once()
    assert result.reason == "already_investigating"  # exercised gate 6, not gate 5.5


@pytest.mark.asyncio
async def test_dispatch_qa_non_infra_state_finding_never_checks_condition_ledger():
    """A non-infra_state finding must never consult the condition ledger at all."""
    finding = _make_finding(severity=1, source_type="log_scanner")

    with (
        patch(
            "butlers.core.qa.dispatch.get_active_condition", new_callable=AsyncMock
        ) as mock_get_condition,
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(uuid.uuid4(), False),
        ),
    ):
        await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(finding),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )

    mock_get_condition.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_qa_success_and_never_raises():
    """Success → accepted=True, reason=dispatched; internal error → internal_error result."""
    task_registry: list[asyncio.Task] = []
    worktree_path = Path("/tmp/qa-worktree")
    branch_name = "qa/finance/abcdef123456"
    attempt_id = uuid.uuid4()
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(("c" * 40).encode(), b""))
    mock_proc.returncode = 0

    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(attempt_id, True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_publication_hold",
            new_callable=AsyncMock,
            return_value=False,
        ),
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
            "butlers.core.qa.dispatch.resolve_model_with_effective_tier",
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
        ) as create_worktree,
        patch("butlers.core.qa.dispatch._run_investigation_session", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch._qa_timeout_watchdog", new_callable=AsyncMock),
    ):
        result = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token="ghtoken",
            task_registry=task_registry,
        )
        assert result.accepted is True and result.reason == "dispatched"
        pinned_base = create_worktree.await_args.kwargs["base_ref"]
        for failure in ("fetch", "resolve"):
            create_worktree.reset_mock()
            if failure == "fetch":
                mock_proc.returncode = 1
            else:
                mock_proc.returncode = 0
                mock_proc.communicate.return_value = (b"not-a-commit", b"")
            refused = await dispatch_qa_investigation(
                pool=_qa_pool(),
                triaged_finding=_make_triaged(_make_finding(severity=1)),
                patrol_id=uuid.uuid4(),
                config=QaDispatchConfig(),
                repo_root=Path("/tmp/repo"),
                spawner=MagicMock(),
                gh_token="ghtoken",
                task_registry=task_registry,
            )
            # REQ-testing-043: failure must precede both creation and spawn.
            for task in task_registry:
                task.cancel()
            assert refused.accepted is False and refused.reason == "main_refresh_failed"
            create_worktree.assert_not_awaited()
        assert pinned_base == "c" * 40
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
            pool=_qa_pool(),
            triaged_finding=_make_triaged(),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            gh_token=None,
        )
    assert r_err.accepted is False and r_err.reason == "internal_error"


@pytest.mark.asyncio
async def test_capture_commit_diff_snapshot_runs_git_diff_against_worktree(tmp_path: Path):
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(
        return_value=(
            b"diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
            b"",
        )
    )
    mock_proc.returncode = 0

    with patch(
        "butlers.core.qa.dispatch.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=mock_proc,
    ) as create_proc:
        snapshot = await _capture_commit_diff_snapshot(tmp_path)

    create_proc.assert_awaited_once_with(
        "git",
        "-C",
        str(tmp_path),
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD~1..HEAD",
        "--unified=3",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ANY,
    )
    assert snapshot == [
        {"kind": "meta", "text": "diff --git a/a.py b/a.py"},
        {"kind": "meta", "text": "@@ -1 +1 @@"},
        {"kind": "-", "text": "old"},
        {"kind": "+", "text": "new"},
    ]


@pytest.mark.asyncio
async def test_run_investigation_persists_commit_diff_snapshot(tmp_path: Path):
    """A committed investigation captures git diff before tearing down the worktree."""

    attempt_id = uuid.uuid4()
    finding_id = uuid.uuid4()
    branch_name = "qa/finance/abcdef123456"
    diff_snapshot = [{"kind": "+", "text": "fixed = True"}]
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))

    with (
        patch(
            "butlers.core.qa.dispatch._capture_commit_diff_snapshot",
            new_callable=AsyncMock,
            return_value=diff_snapshot,
        ) as capture_diff,
        patch(
            "butlers.core.qa.dispatch._detect_no_op_branch",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree",
            new_callable=AsyncMock,
        ) as persist_and_remove,
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=tmp_path,
            attempt_id=attempt_id,
            finding_id=finding_id,
            branch_name=branch_name,
            worktree_path=tmp_path,
            finding=_make_finding(),
            config=QaDispatchConfig(repo_whitelist=MagicMock()),
            spawner=spawner,
            gh_token="ghtoken",
        )

    capture_diff.assert_awaited_once_with(tmp_path)
    assert persist_and_remove.await_args.kwargs["diff_snapshot"] == diff_snapshot
    assert persist_and_remove.await_args.kwargs["delete_branch"] is True


@pytest.mark.asyncio
async def test_run_investigation_no_commit_persists_empty_snapshot_without_git_diff(
    tmp_path: Path,
):
    """No-commit investigations store an empty snapshot and skip git diff capture."""

    attempt_id = uuid.uuid4()
    branch_name = "qa/finance/abcdef123456"
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))

    with (
        patch(
            "butlers.core.qa.dispatch._capture_commit_diff_snapshot",
            new_callable=AsyncMock,
        ) as capture_diff,
        patch(
            "butlers.core.qa.dispatch._detect_no_op_branch",
            new_callable=AsyncMock,
            return_value=True,
        ) as detect_no_op,
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree",
            new_callable=AsyncMock,
        ) as persist_and_remove,
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=tmp_path,
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name=branch_name,
            worktree_path=tmp_path,
            finding=_make_finding(),
            config=QaDispatchConfig(repo_whitelist=MagicMock()),
            spawner=spawner,
            gh_token="ghtoken",
        )

    capture_diff.assert_not_awaited()
    detect_no_op.assert_awaited_once()
    assert persist_and_remove.await_args.kwargs["diff_snapshot"] == []
    assert persist_and_remove.await_args.kwargs["delete_branch"] is True


@pytest.mark.asyncio
async def test_run_investigation_reports_model_resolution_refusal_as_no_model(
    tmp_path: Path,
) -> None:
    attempt_id = uuid.uuid4()
    spawner = MagicMock()
    spawner.trigger = AsyncMock(
        return_value=MagicMock(
            success=False,
            session_id=None,
            error="ModelResolutionError: no_fitting_candidate: required_features=vision",
        )
    )

    with (
        patch(
            "butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock
        ) as update_status,
        patch(
            "butlers.core.qa.dispatch._record_escalation_for_terminal",
            new_callable=AsyncMock,
        ),
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree",
            new_callable=AsyncMock,
        ),
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=tmp_path,
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name="qa/finance/abcdef123456",
            worktree_path=tmp_path,
            finding=_make_finding(),
            config=QaDispatchConfig(repo_whitelist=MagicMock()),
            spawner=spawner,
            gh_token="ghtoken",
        )

    assert update_status.await_args.args[:3] == (
        ANY,
        attempt_id,
        "failed",
    )
    assert update_status.await_args.kwargs["error_detail"].startswith(
        "no_model_available: ModelResolutionError: no_fitting_candidate"
    )


@pytest.mark.asyncio
async def test_persist_notes_and_remove_worktree_skips_diff_fallback_when_notes_persist(
    tmp_path: Path,
):
    diff_snapshot = [{"kind": "+", "text": "fixed = True"}]

    with (
        patch(
            "butlers.core.qa.dispatch._persist_investigation_notes",
            new_callable=AsyncMock,
            return_value="ok",
        ) as persist_notes,
        patch(
            "butlers.core.qa.dispatch._persist_diff_snapshot",
            new_callable=AsyncMock,
        ) as persist_diff,
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
    ):
        await _persist_notes_and_remove_worktree(
            _qa_pool(),
            uuid.uuid4(),
            tmp_path,
            "qa/finance/abcdef123456",
            tmp_path,
            delete_branch=False,
            delete_remote=False,
            diff_snapshot=diff_snapshot,
        )

    persist_notes.assert_awaited_once()
    persist_diff.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_notes_and_remove_worktree_falls_back_when_notes_absent(
    tmp_path: Path,
):
    pool = _qa_pool()
    attempt_id = uuid.uuid4()
    diff_snapshot = [{"kind": "+", "text": "fixed = True"}]

    with (
        patch(
            "butlers.core.qa.dispatch._persist_investigation_notes",
            new_callable=AsyncMock,
            return_value="failed",
        ),
        patch(
            "butlers.core.qa.dispatch._persist_diff_snapshot",
            new_callable=AsyncMock,
        ) as persist_diff,
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
    ):
        await _persist_notes_and_remove_worktree(
            pool,
            attempt_id,
            tmp_path,
            "qa/finance/abcdef123456",
            tmp_path,
            delete_branch=False,
            delete_remote=False,
            diff_snapshot=diff_snapshot,
        )

    persist_diff.assert_awaited_once_with(pool, attempt_id, diff_snapshot)


@pytest.mark.asyncio
async def test_dispatch_novel_findings():
    """Returns all results; empty list → empty; stops at concurrency cap."""
    pool = _qa_pool()

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
    pool = _qa_pool()
    counts = await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token=None)
    assert counts == {"merged": 0, "closed": 0, "errors": 0, "follow_ups_dispatched": 0}
    pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_check_open_pr_statuses_merged():
    """MERGED state → pr_merged transition."""
    attempt_id = uuid.uuid4()
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
    pool = _qa_pool()
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
async def test_dispatch_pr_review_followup_materializes_remote_ref_before_worktree_add():
    """A fetchable PR head is materialized as origin/<branch> before worktree add."""
    pool = _qa_pool()
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
        ) as create_process,
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

    fetch_call, worktree_call = create_process.await_args_list
    assert fetch_call.args[:5] == (
        "git",
        "fetch",
        "--upload-pack=git-upload-pack",
        "origin",
        f"+refs/heads/{branch_name}:refs/remotes/origin/{branch_name}",
    )
    assert worktree_call.args[:5] == ("git", "worktree", "add", "-B", branch_name)
    assert worktree_call.args[-1] == f"origin/{branch_name}"
    assert "FETCH_HEAD" not in worktree_call.args


@pytest.mark.asyncio
async def test_dispatch_pr_review_followup_fails_before_worktree_when_ref_fetch_fails():
    """A failed remote-ref materialization never reaches worktree creation."""
    pool = _qa_pool()
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
        ) as create_process,
        patch("pathlib.Path.mkdir"),
        patch(
            "butlers.core.qa.dispatch._run_review_followup_session",
            new_callable=AsyncMock,
        ) as run_followup,
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
    create_process.assert_awaited_once_with(
        "git",
        "fetch",
        "--upload-pack=git-upload-pack",
        "origin",
        "+refs/heads/qa/general/abcdef:refs/remotes/origin/qa/general/abcdef",
        cwd="/tmp/repo",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=ANY,
    )
    run_followup.assert_not_awaited()
    pool.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_review_followup_persists_publication_unavailable(tmp_path: Path) -> None:
    """A successful follow-up agent run pushes nothing and records the fixed reason."""
    pool = _qa_pool()
    attempt_id = uuid.uuid4()
    session_id = uuid.uuid4()
    spawner = MagicMock()
    spawner.trigger = AsyncMock(
        return_value=MagicMock(success=True, session_id=session_id, error=None)
    )

    with (
        patch("butlers.core.qa.dispatch._prepare_agent_workspace", return_value=tmp_path),
        patch("butlers.core.qa.dispatch.remove_healing_worktree", new_callable=AsyncMock),
    ):
        await _run_review_followup_session(
            pool=pool,
            repo_root=tmp_path,
            attempt_id=attempt_id,
            pr_number=42,
            followup_branch="qa/general/followup",
            worktree_path=tmp_path,
            prompt="review feedback",
            config=QaDispatchConfig(),
            spawner=spawner,
            sandbox_env={"PATH": "/usr/bin", "GH_TOKEN": "not-logged"},
        )

    persisted_error = pool.execute.await_args.args[3]
    assert persisted_error.startswith("publication_unavailable:")


_AGENT_TOKEN = "ghp_unit_test_token_value"


def _assert_no_credential(env: dict[str, str]) -> None:
    assert "GH_TOKEN" not in env
    assert not any(k.startswith(("GIT_ASKPASS", "GIT_CONFIG", "GITHUB_")) for k in env)
    assert _AGENT_TOKEN not in env.values()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retained", "expected_detail"),
    [
        # Sealed and retained: the attempt keeps the publication_unavailable reason.
        (None, "publication_unavailable:"),
        # The store could not keep the bytes (or a re-run met an existing export): fail closed
        # under a reason the breaker does not read as retained success.
        (
            PublicationResult(PublicationStatus.REFUSED, "export_unretained"),
            "publication_unretained:",
        ),
        (PublicationResult(PublicationStatus.REFUSED, "export_changed"), "publication_unretained:"),
        (PublicationResult(PublicationStatus.REFUSED, "export_pruned"), "publication_unretained:"),
        # The validator refused: recorded as the publisher records a refusal.
        (PublicationResult(PublicationStatus.REFUSED, "unbound_export"), "publication_refused:"),
    ],
)
async def test_investigation_agent_has_no_credential_and_publication_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained, expected_detail
):
    """Agent env carries no token; a successful run pushes nothing and records the reason."""
    monkeypatch.setenv("GH_TOKEN", _AGENT_TOKEN)
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))
    with (
        patch(
            "butlers.core.qa.dispatch._detect_no_op_branch",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "butlers.core.qa.dispatch._capture_commit_diff_snapshot",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock) as update,
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree", new_callable=AsyncMock
        ),
        patch(
            "butlers.core.qa.dispatch.retain_unpublished_proposal",
            new_callable=AsyncMock,
            return_value=retained,
        ),
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=tmp_path,
            attempt_id=uuid.uuid4(),
            finding_id=uuid.uuid4(),
            branch_name="qa/finance/abcdef123456",
            worktree_path=tmp_path,
            finding=_make_finding(),
            config=QaDispatchConfig(repo_whitelist=MagicMock()),
            spawner=spawner,
            gh_token=_AGENT_TOKEN,
        )

    _assert_no_credential(spawner.trigger.await_args.kwargs["env_override"])
    assert update.await_args.args[2] == "failed"
    assert update.await_args.kwargs["error_detail"].startswith(expected_detail)


@pytest.mark.asyncio
async def test_followup_agent_has_no_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The review follow-up spawn env is built without the QA token."""
    monkeypatch.setenv("GH_TOKEN", _AGENT_TOKEN)
    captured: dict[str, dict[str, str]] = {}

    async def _fake_session(**kwargs):
        captured["env"] = kwargs["sandbox_env"]

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.returncode = 0
    with (
        patch("butlers.core.qa.dispatch.anonymize", return_value="safe feedback"),
        patch("butlers.core.qa.dispatch.validate_anonymized", return_value=(True, [])),
        patch("butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.is_dir", return_value=True),
        patch("butlers.core.qa.dispatch._run_review_followup_session", side_effect=_fake_session),
    ):
        task_registry: list[asyncio.Task] = []
        result = await _dispatch_pr_review_followup(
            pool=_qa_pool(),
            repo_root=Path("/tmp/repo"),
            attempt_id=uuid.uuid4(),
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name="qa/general/abcdef-1234",
            feedback_summary="reviewer comment",
            config=QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token=_AGENT_TOKEN,
            task_registry=task_registry,
        )
        await asyncio.gather(*task_registry)

    assert result is True
    _assert_no_credential(captured["env"])


@pytest.mark.asyncio
async def test_pr_status_polling_keeps_trusted_token():
    """Dispatcher-only PR polling still runs gh with the trusted token."""
    pool = _qa_pool()
    pool.fetch = AsyncMock(return_value=[_make_pr_row(attempt_id=uuid.uuid4())])
    pr_data = {"state": "MERGED", "reviews": [], "latestReviews": [], "reviewThreads": []}
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(_test_json.dumps(pr_data).encode(), b""))
    mock_proc.returncode = 0
    with (
        patch(
            "butlers.core.qa.dispatch.asyncio.create_subprocess_exec", return_value=mock_proc
        ) as spawn,
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        await check_open_pr_statuses(pool, Path("/tmp/repo"), gh_token=_AGENT_TOKEN)

    assert spawn.call_args.kwargs["env"]["GH_TOKEN"] == _AGENT_TOKEN


# ---------------------------------------------------------------------------
# Trusted publisher (bu-mc01y5.6): synthetic provider and ledger, real git export.
# ---------------------------------------------------------------------------

_QA_BRANCH = "qa/finance/abcdef123456-1"


def _publishing_repo(tmp_path: Path, content: str = "value = 2\n") -> tuple[Path, str]:
    repo = tmp_path / "repo"
    base = init_repo(repo)
    git(repo, "checkout", "-q", "-b", _QA_BRANCH)
    commit_file(repo, "src/app.py", content, "fix: guard value")
    git(repo, "checkout", "-q", "main")
    return repo, base


async def _run_published_investigation(
    repo: Path,
    base: str,
    provider: FakeProvider,
    ledger: FakeLedger,
    attempt_id: uuid.UUID,
    finding: QaFinding | None = None,
) -> dict[str, AsyncMock]:
    whitelist = MagicMock()
    whitelist.ensure_loaded = AsyncMock()
    whitelist.is_allowed = MagicMock(return_value=(True, "allowed"))
    publisher = QaPublisher(provider=provider, ledger=ledger, repo_root=repo)
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))
    worktree = repo.parent / "worktree"
    worktree.mkdir(exist_ok=True)
    with (
        patch("butlers.core.qa.dispatch._detect_no_op_branch", AsyncMock(return_value=False)),
        patch("butlers.core.qa.dispatch._capture_commit_diff_snapshot", AsyncMock(return_value=[])),
        patch("butlers.core.qa.dispatch.update_attempt_status", AsyncMock(return_value=True)) as up,
        patch("butlers.core.qa.dispatch.record_pr_drafted_event", AsyncMock()) as drafted,
        patch("butlers.core.qa.dispatch.record_event", AsyncMock()) as event,
        patch("butlers.core.qa.dispatch.record_escalated_event", AsyncMock()) as escalated,
        patch("butlers.core.qa.dispatch._persist_notes_and_remove_worktree", AsyncMock()) as td,
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=repo,
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name=_QA_BRANCH,
            worktree_path=worktree,
            finding=finding or _make_finding(),
            config=QaDispatchConfig(repo_whitelist=whitelist, publisher=publisher),
            spawner=spawner,
            gh_token=None,
            git_author_name=QA_IDENTITY.name,
            git_author_email=QA_IDENTITY.email,
            base_sha=base,
        )
    _assert_no_credential(spawner.trigger.await_args.kwargs["env_override"])
    return {"update": up, "drafted": drafted, "event": event, "escalated": escalated, "td": td}


@pytest.mark.asyncio
async def test_publication_links_bound_pr_to_attempt_via_ledger(tmp_path: Path) -> None:
    repo, base = _publishing_repo(tmp_path)
    head = git(repo, "rev-parse", _QA_BRANCH)
    provider, ledger, attempt_id = FakeProvider(), FakeLedger(), uuid.uuid4()

    mocks = await _run_published_investigation(repo, base, provider, ledger, attempt_id)

    (pr_number,) = provider.prs
    assert provider.refs == {_QA_BRANCH: head}
    assert provider.prs[pr_number]["labels"] == ("self-healing", "automated")
    created = ledger.row(attempt_id, OperationKind.PR_CREATE)
    assert (created.attempt_id, created.stage, created.new_object_id) == (
        attempt_id,
        "succeeded",
        str(pr_number),
    )
    update = mocks["update"].await_args
    assert update.args[2] == "pr_open" and update.kwargs["pr_number"] == pr_number
    assert mocks["drafted"].await_args.kwargs["pr_number"] == pr_number
    assert mocks["td"].await_args.kwargs["delete_branch"] is False


@pytest.mark.asyncio
async def test_lost_pr_response_is_held_ambiguous_without_retry(tmp_path: Path) -> None:
    repo, base = _publishing_repo(tmp_path)
    provider = FakeProvider(lose_response_on="create_pull_request")
    ledger, attempt_id, finding = FakeLedger(), uuid.uuid4(), _make_finding()

    first = await _run_published_investigation(repo, base, provider, ledger, attempt_id, finding)
    again = await _run_published_investigation(repo, base, provider, ledger, attempt_id, finding)

    assert len(provider.prs) == 1
    assert [name for name, _ in provider.calls] == ["update_ref", "create_pull_request"]
    assert ledger.row(attempt_id, OperationKind.PR_CREATE).stage == "ambiguous"
    # The sealed export outlives worktree teardown (spec: committed proposal not published).
    retained = ledger.exports[attempt_id]
    retained.verify()
    assert (retained.base_sha, retained.head_sha) == (base, git(repo, "rev-parse", _QA_BRANCH))
    for mocks in (first, again):
        assert mocks["update"].await_args.args[2] == "failed"
        assert (
            mocks["update"].await_args.kwargs["error_detail"].startswith("publication_ambiguous:")
        )
        mocks["escalated"].assert_awaited_once()
        mocks["drafted"].assert_not_awaited()


@pytest.mark.asyncio
async def test_leaking_commit_escalates_anonymization_failed_without_egress(
    tmp_path: Path,
) -> None:
    secret = "sk-ant-abcdefghijklmnopqrst"
    repo, base = _publishing_repo(tmp_path, content=f"token = '{secret}'\n")
    provider, ledger, attempt_id = FakeProvider(), FakeLedger(), uuid.uuid4()

    mocks = await _run_published_investigation(repo, base, provider, ledger, attempt_id)

    assert provider.calls == []
    # The export never sealed, so a receipt is the publisher's only trace (bu-mc01y5.28).
    assert ledger.refusals == {attempt_id: ("anonymization_failed", "content_leak")}
    assert mocks["update"].await_args.args[2] == "anonymization_failed"
    escalation = mocks["event"].await_args.kwargs
    assert (escalation["step"], escalation["detail"]) == ("escalated", "content_leak")
    assert secret not in repr(mocks["event"].await_args_list + mocks["update"].await_args_list)
    mocks["drafted"].assert_not_awaited()
    assert mocks["td"].await_args.kwargs["delete_branch"] is True


async def _run_unpublished_investigation(
    repo: Path, base: str, attempt_id: uuid.UUID
) -> dict[str, AsyncMock]:
    """Run a real-git investigation with NO publisher; retention is captured, not stored."""
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))
    worktree = repo.parent / "worktree"
    worktree.mkdir(exist_ok=True)
    with (
        patch("butlers.core.qa.dispatch._detect_no_op_branch", AsyncMock(return_value=False)),
        patch("butlers.core.qa.dispatch._capture_commit_diff_snapshot", AsyncMock(return_value=[])),
        patch("butlers.core.qa.dispatch.update_attempt_status", AsyncMock(return_value=True)) as up,
        patch("butlers.core.qa.dispatch._persist_notes_and_remove_worktree", AsyncMock()) as td,
        patch("butlers.core.qa.dispatch.record_event", AsyncMock()),
        patch("butlers.core.qa.dispatch.record_escalated_event", AsyncMock()),
        patch("butlers.core.qa.publication.proposal.retention.retain_export", AsyncMock()) as keep,
        patch("butlers.core.qa.publication.proposal.retention.record_refusal", AsyncMock()) as rec,
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=repo,
            attempt_id=attempt_id,
            finding_id=uuid.uuid4(),
            branch_name=_QA_BRANCH,
            worktree_path=worktree,
            finding=_make_finding(),
            config=QaDispatchConfig(),
            spawner=spawner,
            gh_token=None,
            git_author_name=QA_IDENTITY.name,
            git_author_email=QA_IDENTITY.email,
            base_sha=base,
        )
    return {"update": up, "td": td, "keep": keep, "refuse": rec}


@pytest.mark.asyncio
async def test_committed_investigation_without_publisher_retains_sealed_proposal(
    tmp_path: Path,
) -> None:
    repo, base = _publishing_repo(tmp_path)
    head = git(repo, "rev-parse", _QA_BRANCH)
    attempt_id = uuid.uuid4()

    mocks = await _run_unpublished_investigation(repo, base, attempt_id)

    mocks["keep"].assert_awaited_once()
    _pool, kept_attempt, export = mocks["keep"].await_args.args
    assert kept_attempt == attempt_id
    export.verify()
    assert (export.base_sha, export.head_sha) == (base, head)
    mocks["refuse"].assert_not_awaited()
    update = mocks["update"].await_args
    assert update.args[2] == "failed"
    assert update.kwargs["error_detail"].startswith("publication_unavailable:")


@pytest.mark.asyncio
async def test_leaking_commit_without_publisher_is_refused_not_retained(tmp_path: Path) -> None:
    repo, base = _publishing_repo(tmp_path, content="token = 'sk-ant-abcdefghijklmnopqrst'\n")

    mocks = await _run_unpublished_investigation(repo, base, uuid.uuid4())

    mocks["keep"].assert_not_awaited()
    assert mocks["refuse"].await_args.kwargs == {
        "status": "anonymization_failed",
        "category": "content_leak",
    }
    assert mocks["update"].await_args.args[2] == "anonymization_failed"


def test_pr_title_keeps_code_identity_after_publisher_sanitizing() -> None:
    """A finding on a real module yields a readable, checkable title (bu-ejow5p.4)."""
    from butlers.core.qa.publication.proposal import compose_pr_text
    from butlers.core.qa.publication.publisher import _sanitize

    finding = _make_finding()
    finding.exception_type = "builtins.ValueError"
    finding.call_site = "src/butlers/core/spawner.py:spawn"
    title, body = compose_pr_text(finding, uuid.uuid4(), {}, None)

    sanitized = _sanitize(title, body, [], Path("/tmp/repo"))

    assert not isinstance(sanitized, str)
    assert "[REDACTED-HOST]" not in sanitized.title
    assert "builtins.ValueError in src/butlers/core/spawner.py:spawn" in sanitized.title


@pytest.mark.asyncio
async def test_gate_10_resolves_the_exact_executor_rung_under_the_launch_intent():
    """bu-ejow5p.5: admission resolves what launch resolves: workhorse, no fallthrough, qa intent."""
    from butlers.core.dispatch_intent import Consequence
    from butlers.core.model_capabilities import ModelFeature
    from butlers.core.model_routing import Complexity

    resolve = AsyncMock(return_value=None)
    spawner = MagicMock()
    spawner._config.name = "qa"
    with (
        patch(
            "butlers.core.qa.dispatch.create_or_join_attempt",
            new_callable=AsyncMock,
            return_value=(uuid.uuid4(), True),
        ),
        patch("butlers.core.qa.dispatch.update_finding_attempt", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.get_publication_hold",
            new_callable=AsyncMock,
            return_value=False,
        ),
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
        patch("butlers.core.qa.dispatch.resolve_model_with_effective_tier", resolve),
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock),
    ):
        result = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=spawner,
            gh_token=None,
        )

    assert result.reason == "no_model"
    args, kwargs = resolve.call_args
    assert args[1] == "qa" and args[2] is Complexity.WORKHORSE
    assert kwargs["allow_tier_fallthrough"] is False
    intent = kwargs["intent"]
    assert intent.required_features == frozenset({ModelFeature.WORKSPACE_AGENT})
    assert intent.consequence is Consequence.REVERSIBLE


@pytest.mark.asyncio
async def test_open_breaker_holds_the_whole_batch_without_a_claim_per_finding():
    """One breaker read, one stamp, one event; no claim, delete or per-finding write."""
    findings = [_make_triaged() for _ in range(3)]
    with (
        patch(
            "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
            new_callable=AsyncMock,
            return_value=True,
        ) as tripped,
        patch("butlers.core.qa.dispatch.hold_findings_for_retry", new_callable=AsyncMock) as hold,
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock) as event,
        patch("butlers.core.qa.dispatch.create_or_join_attempt", new_callable=AsyncMock) as claim,
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation", new_callable=AsyncMock) as run,
    ):
        results = await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    assert tripped.await_count == 1
    hold.assert_awaited_once()
    assert hold.await_args.args[1] == [f.finding_id for f in findings]
    assert hold.await_args.args[2] == "circuit_breaker"
    event.assert_awaited_once()
    claim.assert_not_called()
    run.assert_not_called()
    assert [r.reason for r in results] == ["circuit_breaker"] * 3
    assert not any(r.accepted for r in results)


@pytest.mark.asyncio
async def test_cap_queues_every_leftover_including_the_first_one_over():
    """The finding that first meets the cap is queued like the ones after it."""
    findings = [_make_triaged() for _ in range(4)]
    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            side_effect=[1, 2, 2, 2],
        ),
        patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation",
            new_callable=AsyncMock,
            return_value=QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched"),
        ),
        patch("butlers.core.qa.dispatch.hold_findings_for_retry", new_callable=AsyncMock) as hold,
    ):
        await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(max_concurrent=2),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    hold.assert_awaited_once()
    assert hold.await_args.args[1] == [f.finding_id for f in findings[1:]]
    assert hold.await_args.args[2] == "concurrency_cap"


@pytest.mark.asyncio
async def test_breaker_tripping_mid_patrol_holds_every_remaining_finding():
    """Dispatch k trips the breaker: findings k+1..n are all queued behind one event."""
    findings = [_make_triaged() for _ in range(5)]
    reads = iter([False, False, True])  # open for findings 0 and 1, tripped before finding 2

    async def _tripped(*_args, **_kwargs):
        return next(reads)

    with (
        patch("butlers.core.qa.dispatch._is_circuit_breaker_tripped", side_effect=_tripped),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "butlers.core.qa.dispatch.dispatch_qa_investigation",
            new_callable=AsyncMock,
            return_value=QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched"),
        ) as run,
        patch("butlers.core.qa.dispatch.hold_findings_for_retry", new_callable=AsyncMock) as hold,
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock) as event,
    ):
        results = await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(max_concurrent=10),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    assert run.await_count == 2
    hold.assert_awaited_once()
    assert hold.await_args.args[1] == [f.finding_id for f in findings[2:]]
    assert hold.await_args.args[2] == "circuit_breaker"
    event.assert_awaited_once()
    assert [r.reason for r in results] == ["dispatched"] * 2 + ["circuit_breaker"] * 3


@pytest.mark.asyncio
async def test_a_failed_hold_is_counted_and_not_reported_as_held():
    findings = [_make_triaged() for _ in range(2)]
    metrics = MagicMock()
    with (
        patch(
            "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core.qa.dispatch.hold_findings_for_retry",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock) as event,
    ):
        results = await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            metrics=metrics,
        )

    assert [r.reason for r in results] == ["hold_failed"] * 2
    assert {r.gate for r in results} == {"circuit_breaker"}
    event.assert_not_called()
    decisions = [c.kwargs["decision"] for c in metrics.record_recovery_dispatch_decision.mock_calls]
    assert decisions == ["hold_failed"]


@pytest.mark.asyncio
async def test_a_failed_cap_hold_is_not_reported_as_capped():
    findings = [_make_triaged() for _ in range(3)]
    metrics = MagicMock()
    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=5,
        ),
        patch(
            "butlers.core.qa.dispatch.hold_findings_for_retry",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
    ):
        results = await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(max_concurrent=2, circuit_breaker_threshold=0),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
            metrics=metrics,
        )

    assert [r.reason for r in results] == ["hold_failed"] * 3
    assert {r.gate for r in results} == {"concurrency_cap"}
    assert metrics.record_recovery_dispatch_decision.call_count == 1


@pytest.mark.asyncio
async def test_an_unreadable_breaker_holds_the_patrol_instead_of_raising():
    """A database error on the breaker read counts as tripped: nothing launches, nothing raises."""
    findings = [_make_triaged() for _ in range(3)]
    with (
        patch(
            "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
        patch("butlers.core.qa.dispatch.hold_findings_for_retry", new_callable=AsyncMock) as hold,
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock) as event,
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation", new_callable=AsyncMock) as run,
    ):
        results = await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )

    run.assert_not_called()
    hold.assert_awaited_once()
    assert hold.await_args.args[2] == "circuit_breaker"
    assert [r.reason for r in results] == ["circuit_breaker"] * 3
    # The event says the state was unreadable, never that failures were counted.
    reason = event.await_args.kwargs["reason"]
    assert "unreadable" in reason and "consecutive" not in reason


def _probe_patches(*, due, lock="conn"):
    """Open breaker, probe due per ``due`` (one answer per call), and the probe lock result."""
    conn = MagicMock() if lock == "conn" else None
    return (
        patch(
            "butlers.core.qa.dispatch._is_circuit_breaker_tripped",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core.qa.dispatch._breaker_probe_due",
            new_callable=AsyncMock,
            side_effect=due,
        ),
        patch(
            "butlers.core.qa.dispatch._acquire_probe_lock",
            new_callable=AsyncMock,
            return_value=conn,
        ),
        patch("butlers.core.qa.dispatch._release_probe_lock", new_callable=AsyncMock),
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("butlers.core.qa.dispatch.hold_findings_for_retry", new_callable=AsyncMock),
        patch("butlers.core.qa.dispatch.create_dispatch_event", new_callable=AsyncMock),
    )


async def _dispatch_probe(findings, patches, launch):
    from contextlib import ExitStack

    with ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in patches]
        run = stack.enter_context(
            patch("butlers.core.qa.dispatch.dispatch_qa_investigation", side_effect=launch)
        )
        results = await dispatch_novel_findings(
            pool=_qa_pool(),
            novel_findings=findings,
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(max_concurrent=10),
            repo_root=Path("/tmp/repo"),
            spawner=MagicMock(),
        )
    return results, run, mocks


@pytest.mark.asyncio
async def test_a_due_probe_admits_exactly_one_finding_and_holds_the_rest():
    findings = [_make_triaged() for _ in range(3)]

    async def launch(*_a, **_k):
        return QaDispatchResult(accepted=True, fingerprint="x" * 64, reason="dispatched")

    # due for finding 0; the probe is then in flight, so it is no longer due for 1 and 2
    patches = _probe_patches(due=[True, True, False])
    results, run, mocks = await _dispatch_probe(findings, patches, launch)
    release, hold, event = mocks[3], mocks[5], mocks[6]

    assert run.call_count == 1
    assert run.call_args.kwargs["triaged_finding"] is findings[0]
    release.assert_awaited_once()
    hold.assert_awaited_once()
    assert hold.await_args.args[1] == [f.finding_id for f in findings[1:]]
    decisions = [c.kwargs["decision"] for c in event.await_args_list]
    assert decisions == ["breaker_probe", "circuit_breaker"]
    assert [r.reason for r in results] == ["dispatched", "circuit_breaker", "circuit_breaker"]


@pytest.mark.asyncio
async def test_a_probe_lock_held_elsewhere_holds_the_whole_patrol():
    findings = [_make_triaged() for _ in range(2)]
    patches = _probe_patches(due=[True], lock=None)
    results, run, mocks = await _dispatch_probe(findings, patches, None)

    run.assert_not_called()
    assert [r.reason for r in results] == ["circuit_breaker"] * 2


@pytest.mark.asyncio
async def test_a_probe_that_loses_the_recheck_under_the_lock_holds_the_patrol():
    findings = [_make_triaged() for _ in range(2)]
    patches = _probe_patches(due=[True, False])  # another dispatcher launched it first
    results, run, mocks = await _dispatch_probe(findings, patches, None)

    run.assert_not_called()
    mocks[3].assert_awaited_once()  # the lock is still released
    assert [r.reason for r in results] == ["circuit_breaker"] * 2


@pytest.mark.asyncio
async def test_a_finding_a_gate_rejects_does_not_spend_the_probe():
    findings = [_make_triaged() for _ in range(2)]
    answers = iter(
        [
            QaDispatchResult(accepted=False, fingerprint="a" * 64, reason="cooldown"),
            QaDispatchResult(accepted=True, fingerprint="b" * 64, reason="dispatched"),
        ]
    )

    async def launch(*_a, **_k):
        return next(answers)

    patches = _probe_patches(due=[True, True, True, True])
    results, run, _ = await _dispatch_probe(findings, patches, launch)

    assert run.call_count == 2
    assert [r.reason for r in results] == ["cooldown", "dispatched"]


@pytest.mark.asyncio
async def test_probe_due_needs_an_open_breaker_an_elapsed_backoff_and_nothing_in_flight():
    from datetime import UTC, datetime, timedelta

    from butlers.core.qa.dispatch import _breaker_probe_due

    newest = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    rows = [
        {"status": "failed", "error_detail": None, "closed_at": newest - timedelta(minutes=i)}
        for i in range(5)
    ]

    async def due(now_offset_min, in_flight=0, fetched=rows):
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=fetched)
        pool.fetchval = AsyncMock(return_value=newest + timedelta(minutes=now_offset_min))
        with patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=in_flight,
        ):
            return await _breaker_probe_due(pool, 5)

    assert await due(14) is False  # backoff not elapsed
    assert await due(15) is True
    assert await due(15, in_flight=1) is False  # a probe is already running
    assert await due(60, fetched=rows[:3]) is False  # not open
    bad = MagicMock()
    bad.fetch = AsyncMock(side_effect=RuntimeError("db down"))
    assert await _breaker_probe_due(bad, 5) is False  # unreadable fails closed
