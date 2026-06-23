"""Fail-closed label sanitization gate for self-healing PRs (#2682).

The self-healing dispatch path (``_create_pr``) is the sibling of the QA path:
both push a branch and open a GitHub PR on a public destination. Labels are an
externally-visible field that previously bypassed the anonymization gate that
already guarded the title/body. This locks the gate over labels for the healing
path too.

All fixtures use SYNTHETIC placeholders only — never real private data.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from butlers.core.healing import dispatch as dispatch_module
from butlers.core.healing.dispatch import _create_pr
from butlers.core.healing.fingerprint import FingerprintResult


def _make_fp() -> FingerprintResult:
    return FingerprintResult(
        fingerprint="a" * 64,
        severity=2,
        exception_type="builtins.ValueError",
        call_site="src/butlers/modules/email/tools.py:send_email",
        sanitized_message="connection failed",
    )


class _FakeProc:
    def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""


@pytest.mark.asyncio
async def test_poisoned_label_blocks_healing_pr_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A label with residual sensitive content blocks the PR before gh pr create."""
    gh_create_calls: list[tuple[str, ...]] = []

    async def fake_exec(*cmd, **_kwargs):
        if cmd[:3] == ("gh", "pr", "create"):
            gh_create_calls.append(cmd)
        # git push, branch delete, and (hypothetically) gh all succeed.
        return _FakeProc(stdout=b"https://github.com/acme/repo/pull/9")

    with (
        # Scrub step is a no-op so the synthetic secret reaches the validation
        # backstop unchanged; title/body are clean, only the label is poisoned.
        # Patch BOTH the dispatch-level name (title/body) and the anonymizer-level
        # name (used inside sanitize_labels).
        patch("butlers.core.healing.dispatch.anonymize", side_effect=lambda text, _repo: text),
        patch(
            "butlers.core.healing.anonymizer.anonymize",
            side_effect=lambda text, _repo: text,
        ),
        patch.object(dispatch_module.asyncio, "create_subprocess_exec", side_effect=fake_exec),
    ):
        pr_url, pr_number, error = await _create_pr(
            repo_root=tmp_path,
            branch_name="healing/abcdef",
            fp=_make_fp(),
            butler_name="email",
            attempt_id=uuid.uuid4(),
            agent_context=None,
            # Synthetic placeholder label — NOT real PII.
            labels=["automated", "reporter-tester@synthetic.example"],
            gh_token="token",
        )

    assert (pr_url, pr_number) == (None, None)
    assert error == "anonymization_failed"
    assert gh_create_calls == [], "gh pr create must not run when a label is poisoned"


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

    with patch.object(dispatch_module.asyncio, "create_subprocess_exec", side_effect=fake_exec):
        pr_url, pr_number, error = await _create_pr(
            repo_root=tmp_path,
            branch_name="healing/abcdef",
            fp=_make_fp(),
            butler_name="email",
            attempt_id=uuid.uuid4(),
            agent_context=None,
            labels=["automated", "owner-tester@synthetic.example"],
            gh_token="token",
        )

    assert error is None
    assert (pr_url, pr_number) == ("https://github.com/acme/repo/pull/11", 11)
    assert len(gh_create_calls) == 1
    joined = " ".join(gh_create_calls[0])
    assert "automated" in joined
    assert "tester@synthetic.example" not in joined
    assert "REDACTED" in joined
