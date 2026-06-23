"""Fail-closed label sanitization gate for QA investigation PRs (#2682).

GitHub labels are an externally-visible field on a public destination. Before
these tests, ``labels`` flowed straight into ``gh pr create --label`` without
passing through the anonymization gate that already guarded the title/body.
These tests lock the gate over labels:

- a label carrying residual sensitive content blocks PR creation fail-closed
  (no ``gh pr create``, remote branch deleted, failure counter incremented);
- clean/sanitized labels reach ``gh pr create`` in scrubbed form.

All fixtures use SYNTHETIC placeholders only — never real private data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.qa import dispatch as dispatch_module
from butlers.core.qa.dispatch import _create_qa_pr
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


class _FakeProc:
    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


@pytest.mark.asyncio
async def test_poisoned_label_blocks_pr_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A label with residual sensitive content blocks the PR before gh pr create."""
    counter = _FakeCounter()
    monkeypatch.setattr(dispatch_module, "_qa_anonymization_failed_total", counter)

    gh_create_calls: list[tuple[str, ...]] = []

    async def fake_exec(*cmd, **_kwargs):
        if cmd[:3] == ("gh", "pr", "create"):
            gh_create_calls.append(cmd)
        return _FakeProc(stdout=b"https://github.com/acme/repo/pull/9")

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
        # Scrub step is a no-op so the synthetic secret reaches the validation
        # backstop unchanged (title/body are clean; only the label is poisoned).
        # Patch BOTH the dispatch-level name (title/body) and the anonymizer-level
        # name (used inside sanitize_labels).
        patch("butlers.core.qa.dispatch.anonymize", side_effect=lambda text, _repo: text),
        patch(
            "butlers.core.healing.anonymizer.anonymize",
            side_effect=lambda text, _repo: text,
        ),
        patch(
            "butlers.core.qa.dispatch._delete_remote_branch_with_gh_auth",
            new_callable=AsyncMock,
            return_value=None,
        ) as delete_branch,
        patch.object(dispatch_module.asyncio, "create_subprocess_exec", side_effect=fake_exec),
    ):
        no_op.return_value = False
        pr_url, pr_number, pr_created_at, error = await _create_qa_pr(
            repo_root=tmp_path,
            branch_name="qa/general/abcdef",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            # Synthetic placeholder label — NOT real PII.
            labels=["automated", "reporter-tester@synthetic.example"],
            gh_token="token",
            whitelist=_AllowAllWhitelist(),
            worktree_path=tmp_path,
        )

    assert (pr_url, pr_number, pr_created_at) == (None, None, None)
    assert error is not None and error.startswith("anonymization_failed")
    assert gh_create_calls == [], "gh pr create must not run when a label is poisoned"
    assert counter.count == 1
    delete_branch.assert_awaited_once()


@pytest.mark.asyncio
async def test_clean_labels_reach_gh_in_sanitized_form(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Labels pass through the scrubber: a synthetic secret is redacted before gh pr create."""
    gh_create_calls: list[tuple[str, ...]] = []

    async def fake_exec(*cmd, **_kwargs):
        if cmd[:3] == ("gh", "pr", "create"):
            gh_create_calls.append(cmd)
            return _FakeProc(stdout=b"https://github.com/acme/repo/pull/11")
        return _FakeProc()

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
        patch.object(dispatch_module.asyncio, "create_subprocess_exec", side_effect=fake_exec),
    ):
        no_op.return_value = False
        pr_url, pr_number, _created_at, error = await _create_qa_pr(
            repo_root=tmp_path,
            branch_name="qa/general/abcdef",
            finding=_make_finding(),
            attempt_id=uuid.uuid4(),
            # Synthetic placeholder embedded in an otherwise-valid label.
            labels=["automated", "owner-tester@synthetic.example"],
            gh_token="token",
            whitelist=_AllowAllWhitelist(),
            worktree_path=tmp_path,
        )

    assert error is None
    assert (pr_url, pr_number) == ("https://github.com/acme/repo/pull/11", 11)
    assert len(gh_create_calls) == 1
    joined = " ".join(gh_create_calls[0])
    assert "automated" in joined
    # The synthetic secret must have been scrubbed before reaching the boundary.
    assert "tester@synthetic.example" not in joined
    assert "REDACTED" in joined
