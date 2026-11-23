"""bu-ejow5p.11: daemon-side isolation plumbing and the dispatcher's isolated attempt path."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa import investigator_job as ij
from butlers.core.qa.dispatch import QaDispatchConfig, _run_investigation_session
from butlers.core.qa.job_isolation import (
    JobIsolation,
    cleanup_attempt,
    import_artifacts,
    prepare_attempt_clone,
    read_untrusted,
)
from butlers.core.runtimes.base import JobOutcome
from tests.core.qa.test_dispatch import _make_finding, _qa_pool

pytestmark = pytest.mark.unit

_IMAGE = "registry.invalid/butlers-app@sha256:" + "a" * 64


class _Runner:
    async def run(self, manifest, *, timeout_s):
        return JobOutcome("succeeded", 0)


def _git(*args: str, cwd: Path) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t.invalid",
        "PATH": os.environ["PATH"],
        "HOME": str(cwd),
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def trusted(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "trusted"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    (repo / "a.txt").write_text("hello\n")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-q", "-m", "base", cwd=repo)
    return repo, _git("rev-parse", "HEAD", cwd=repo)


def _iso(tmp_path: Path) -> JobIsolation:
    root = tmp_path / "exchange"
    root.mkdir()
    return JobIsolation(
        namespace="butlers-dev",
        release="butlers",
        image=_IMAGE,
        exchange_claim="butlers-qa-exchange",
        exchange_root=root,
        runner=_Runner(),
        model_proxy_url="http://proxy.invalid:3110",
    )


async def test_the_clone_is_independent_of_the_trusted_repository(tmp_path, trusted) -> None:
    repo, base = trusted
    iso, attempt = _iso(tmp_path), uuid.uuid4()
    clone = await prepare_attempt_clone(
        iso,
        attempt_id=attempt,
        trusted_repo=repo,
        base_sha=base,
        branch_name="qa/x/abc",
        author=("QA", "qa@example.invalid"),
    )
    assert clone == iso.attempt_dir(attempt) / "repo"
    assert _git("rev-parse", "HEAD", cwd=clone) == base
    assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=clone) == "qa/x/abc"
    assert _git("remote", cwd=clone) == ""
    # No object file is a hard link into the trusted repository.
    trusted_inodes = {
        p.stat().st_ino for p in (repo / ".git" / "objects").rglob("*") if p.is_file()
    }
    clone_inodes = {p.stat().st_ino for p in (clone / ".git" / "objects").rglob("*") if p.is_file()}
    assert not trusted_inodes & clone_inodes
    assert (clone / ".git").is_dir() and not (clone / ".git").is_symlink()


def test_launch_runs_the_manifest_in_the_clone_with_the_pinned_template(tmp_path) -> None:
    iso, attempt = _iso(tmp_path), uuid.uuid4()
    launch = iso.launch(attempt, deadline_s=900)
    manifest = launch.build_manifest(["/bin/true"])
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["workingDir"] == "/workspace/repo"
    assert manifest["metadata"]["name"] == ij.job_name(attempt)
    assert launch.staging_dir == iso.attempt_dir(attempt) and launch.deadline_s == 900


def test_read_untrusted_refuses_links_fifos_and_oversize(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "d" / "ok.txt").write_text("fine")
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET")
    (root / "d" / "link.txt").symlink_to(outside)
    (root / "dirlink").symlink_to(tmp_path)
    os.mkfifo(root / "d" / "pipe")
    (root / "d" / "big.txt").write_bytes(b"x" * 100)
    assert read_untrusted(root, ["d", "ok.txt"]) == b"fine"
    assert read_untrusted(root, ["d", "link.txt"]) is None
    assert read_untrusted(root, ["dirlink", "outside.txt"]) is None
    assert read_untrusted(root, ["d", "pipe"]) is None
    assert read_untrusted(root, ["d", "missing"]) is None
    assert read_untrusted(root, ["d", "big.txt"], cap=10) == b"x" * 10


async def test_import_copies_only_regular_files_into_daemon_scratch(tmp_path, trusted) -> None:
    repo, base = trusted
    iso, attempt = _iso(tmp_path), uuid.uuid4()
    clone = await prepare_attempt_clone(
        iso,
        attempt_id=attempt,
        trusted_repo=repo,
        base_sha=base,
        branch_name="qa/x/abc",
        author=None,
    )
    (clone / ".qa").mkdir()
    (clone / ".qa" / "investigation_notes.json").write_text("{}")
    secret = tmp_path / "daemon-secret"
    secret.write_text("DAEMON")
    (clone / ".qa" / "unfixable").symlink_to(secret)
    scratch = import_artifacts(
        iso, attempt, [Path(".qa/investigation_notes.json"), Path(".qa/unfixable")]
    )
    assert scratch == iso.scratch_dir(attempt)
    assert (scratch / ".qa" / "investigation_notes.json").read_text() == "{}"
    assert not (scratch / ".qa" / "unfixable").exists()
    assert scratch.parent != iso.attempt_dir(attempt).parent or scratch != iso.attempt_dir(attempt)


async def test_cleanup_removes_the_attempt_without_following_links(tmp_path, trusted) -> None:
    repo, base = trusted
    iso, attempt = _iso(tmp_path), uuid.uuid4()
    clone = await prepare_attempt_clone(
        iso,
        attempt_id=attempt,
        trusted_repo=repo,
        base_sha=base,
        branch_name="qa/x/abc",
        author=None,
    )
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "f").write_text("stay")
    (clone / "trap").symlink_to(keep)
    cleanup_attempt(iso, attempt)
    assert not iso.attempt_dir(attempt).exists()
    assert (keep / "f").read_text() == "stay"


async def _session(tmp_path, trusted, *, pod):
    repo, base = trusted
    iso = _iso(tmp_path)
    attempt = uuid.uuid4()
    branch = "qa/finance/abcdef123456"
    clone = await prepare_attempt_clone(
        iso, attempt_id=attempt, trusted_repo=repo, base_sha=base, branch_name=branch, author=None
    )
    seen: dict = {}

    async def trigger(**kwargs):
        seen.update(kwargs)
        pod(clone)
        return MagicMock(success=True, session_id=None)

    spawner = MagicMock()
    spawner.trigger = AsyncMock(side_effect=trigger)
    with (
        patch("butlers.core.qa.dispatch._detect_no_op_branch", new_callable=AsyncMock) as git_noop,
        patch(
            "butlers.core.qa.dispatch._capture_commit_diff_snapshot", new_callable=AsyncMock
        ) as diff,
        patch("butlers.core.qa.dispatch.update_attempt_status", new_callable=AsyncMock) as status,
        patch(
            "butlers.core.qa.dispatch.retain_unpublished_proposal",
            new_callable=AsyncMock,
            return_value=None,
        ) as retain,
        patch(
            "butlers.core.qa.dispatch._persist_notes_and_remove_worktree", new_callable=AsyncMock
        ) as persist,
    ):
        await _run_investigation_session(
            pool=_qa_pool(),
            repo_root=repo,
            attempt_id=attempt,
            finding_id=uuid.uuid4(),
            branch_name=branch,
            worktree_path=clone,
            finding=_make_finding(),
            config=QaDispatchConfig(repo_whitelist=MagicMock(), job_isolation=iso),
            spawner=spawner,
            gh_token=None,
            base_sha=base,
        )
    return iso, attempt, clone, seen, git_noop, diff, status, retain, persist


async def test_isolated_no_op_attempt_is_unfixable_with_no_git_in_the_clone(tmp_path, trusted):
    iso, attempt, _clone, seen, git_noop, diff, status, _retain, persist = await _session(
        tmp_path, trusted, pod=lambda clone: None
    )
    assert seen["qa_job_launch"].attempt_id == str(attempt) and seen["cwd"] is None
    git_noop.assert_not_awaited()
    diff.assert_not_awaited()
    assert status.await_args.args[2] == "unfixable"
    assert persist.await_args.kwargs["diff_snapshot"] == []
    assert not iso.attempt_dir(attempt).exists()


async def test_isolated_commit_is_sealed_from_the_clone_and_notes_come_from_scratch(
    tmp_path, trusted
):
    def pod(clone: Path) -> None:
        (clone / "a.txt").write_text("fixed\n")
        _git("commit", "-q", "-am", "fix", cwd=clone)
        (clone / ".qa").mkdir()
        (clone / ".qa" / "investigation_notes.json").write_text("{}")

    iso, attempt, clone, _seen, git_noop, diff, _status, retain, persist = await _session(
        tmp_path, trusted, pod=pod
    )
    git_noop.assert_not_awaited()
    diff.assert_not_awaited()
    assert retain.await_args.kwargs["repo_root"] == clone
    scratch_arg = persist.await_args.args[4]
    assert scratch_arg == iso.scratch_dir(attempt)
    assert clone not in (scratch_arg, *scratch_arg.parents)


async def test_a_planted_notes_symlink_never_reaches_the_daemon_scratch(tmp_path, trusted):
    secret = tmp_path / "daemon-secret"
    secret.write_text("DAEMON")

    def pod(clone: Path) -> None:
        (clone / ".qa").mkdir()
        (clone / ".qa" / "investigation_notes.json").symlink_to(secret)
        (clone / "a.txt").write_text("fixed\n")
        _git("commit", "-q", "-am", "fix", cwd=clone)

    iso, attempt, *_ = await _session(tmp_path, trusted, pod=pod)
    assert not (iso.scratch_dir(attempt) / ".qa" / "investigation_notes.json").exists()
