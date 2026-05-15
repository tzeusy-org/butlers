"""Tests for QA PR anonymization failure observability."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa import dispatch as dispatch_module
from butlers.core.qa.dispatch import QaDispatchConfig, _create_qa_pr, _run_investigation_session
from butlers.core.qa.models import QaFinding


class _FakeCounter:
    def __init__(self) -> None:
        self.count = 0

    def inc(self) -> None:
        self.count += 1


class _AllowAllWhitelist:
    async def ensure_loaded(self) -> None:
        return None

    def is_allowed(self, owner_repo: str) -> tuple[bool, str | None]:
        return True, None


def _make_finding() -> QaFinding:
    now = datetime.now(UTC)
    return QaFinding(
        fingerprint=uuid.uuid4().hex * 2,
        source_type="log_scanner",
        source_butler="finance",
        severity=1,
        exception_type="ValueError",
        event_summary="Test event",
        call_site="module:1",
        occurrence_count=3,
        first_seen=now,
        last_seen=now,
        timestamp=now,
    )


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.execute = AsyncMock()
    return pool


def test_anonymization_failure_counter_registered_without_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakePromCounter:
        def __init__(self, name, documentation, labelnames=None):
            captured["name"] = name
            captured["documentation"] = documentation
            captured["labelnames"] = labelnames

    monkeypatch.setattr("prometheus_client.Counter", FakePromCounter)

    counter = dispatch_module._get_qa_anonymization_failed_total()

    assert isinstance(counter, FakePromCounter)
    assert captured["name"] == "qa_anonymization_failed_total"
    assert captured["labelnames"] is None


@pytest.mark.asyncio
async def test_validation_failure_increments_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = _FakeCounter()
    monkeypatch.setattr(dispatch_module, "_qa_anonymization_failed_total", counter)

    with (
        patch("butlers.core.qa.dispatch._detect_no_op_branch", new_callable=AsyncMock) as no_op,
        patch(
            "butlers.core.qa.dispatch._get_remote_owner_repo",
            new_callable=AsyncMock,
            return_value="acme/repo",
        ),
        patch(
            "butlers.core.qa.dispatch._push_branch_with_gh_auth",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("butlers.core.qa.dispatch.anonymize", side_effect=lambda text, _repo: text),
        patch(
            "butlers.core.qa.dispatch.validate_anonymized",
            side_effect=[
                (True, []),
                (
                    False,
                    [
                        "email pattern detected at offset 10-26 (len=16); "
                        "context: 'raw evidence line with tze@example.com'"
                    ],
                ),
            ],
        ),
        patch(
            "butlers.core.qa.dispatch._delete_remote_branch_with_gh_auth",
            new_callable=AsyncMock,
        ),
    ):
        no_op.return_value = False
        pr_url, pr_number, pr_created_at, error = await _create_qa_pr(
            repo_root=tmp_path,
            branch_name="qa/general/abcdef",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            labels=[],
            gh_token="token",
            whitelist=_AllowAllWhitelist(),
            worktree_path=tmp_path,
        )

    assert (pr_url, pr_number, pr_created_at) == (None, None, None)
    assert error is not None and error.startswith("anonymization_failed")
    assert counter.count == 1


@pytest.mark.asyncio
async def test_validation_failure_emits_escalated_journal_event(tmp_path: Path) -> None:
    attempt_id = uuid.uuid4()
    branch_name = "qa/general/abcdef"
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))
    detail = "email pattern detected at offset 10-26 (len=16)"

    with (
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=(None, None, None, f"anonymization_failed: {detail}"),
        ),
        patch(
            "butlers.core.qa.dispatch._capture_commit_diff_snapshot",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree",
            new_callable=AsyncMock,
        ),
        patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record,
    ):
        await _run_investigation_session(
            pool=_make_pool(),
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

    record.assert_awaited_once()
    assert record.await_args.kwargs["attempt_id"] == attempt_id
    assert record.await_args.kwargs["step"] == "escalated"
    assert record.await_args.kwargs["text"] == "anonymization validator rejected PR payload"
    assert record.await_args.kwargs["detail"] == detail


@pytest.mark.asyncio
async def test_validation_failure_detail_anonymized(tmp_path: Path) -> None:
    attempt_id = uuid.uuid4()
    branch_name = "qa/general/abcdef"
    raw_evidence = "raw evidence line with tze@example.com"
    spawner = MagicMock()
    spawner.trigger = AsyncMock(return_value=MagicMock(success=True, session_id=None))

    with (
        patch(
            "butlers.core.qa.dispatch._create_qa_pr",
            new_callable=AsyncMock,
            return_value=(
                None,
                None,
                None,
                "anonymization_failed: "
                f"email pattern detected at offset 10-26 (len=16); context: '{raw_evidence}'",
            ),
        ),
        patch(
            "butlers.core.qa.dispatch._capture_commit_diff_snapshot",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "butlers.core.qa.dispatch.update_attempt_status",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree",
            new_callable=AsyncMock,
        ),
        patch("butlers.core.qa.dispatch.record_event", new_callable=AsyncMock) as record,
    ):
        await _run_investigation_session(
            pool=_make_pool(),
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

    detail = record.await_args.kwargs["detail"]
    assert raw_evidence not in detail
    assert "tze@example.com" not in detail
    assert detail == "email pattern detected at offset 10-26 (len=16)"
