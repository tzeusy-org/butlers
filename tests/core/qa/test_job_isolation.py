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
    os.link(outside, root / "d" / "hard.txt")
    assert read_untrusted(root, ["d", "hard.txt"]) is None
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


async def test_pr_review_followup_is_refused_when_isolation_is_configured(tmp_path) -> None:
    """The follow-up path runs the agent in the daemon, so it must never run when isolated."""
    from butlers.core.qa.dispatch import _dispatch_pr_review_followup

    spawner = MagicMock()
    spawner.trigger = AsyncMock()
    with patch("butlers.core.qa.dispatch.anonymize") as anonymize:
        dispatched = await _dispatch_pr_review_followup(
            pool=MagicMock(),
            repo_root=tmp_path,
            attempt_id=uuid.uuid4(),
            pr_number=1,
            pr_url="https://example.invalid/pr/1",
            fingerprint="f" * 64,
            butler_name="finance",
            pr_branch_name="qa/finance/abc",
            feedback_summary="please fix",
            config=QaDispatchConfig(job_isolation=_iso(tmp_path)),
            spawner=spawner,
            gh_token="t",
        )
    assert dispatched is False
    spawner.trigger.assert_not_awaited()
    anonymize.assert_not_called()


async def test_containment_is_labelled_job_or_none_and_a_failed_write_is_harmless() -> None:
    from butlers.core.qa.dispatch import _label_containment

    pool = MagicMock()
    pool.execute = AsyncMock()
    attempt = uuid.uuid4()
    await _label_containment(pool, attempt, "job")
    assert pool.execute.await_args.args[1:] == (attempt, "job")
    pool.execute = AsyncMock(side_effect=RuntimeError("db down"))
    await _label_containment(pool, attempt, "none")


async def test_retention_flags_a_daemon_run_export_and_clears_it_only_for_a_job() -> None:
    from butlers.core.qa.publication import retention

    seen = []

    class _Pool:
        async def fetchrow(self, sql, *args):
            seen.append(args[-1])
            return {"digest": "d", "pruned": False}

    export = MagicMock()
    export.digest = "d"
    export.commits = ()
    export.objects = ()
    with patch.object(retention, "encode_objects", return_value=b""):
        await retention.retain_export(_Pool(), uuid.uuid4(), export)
        await retention.retain_export(_Pool(), uuid.uuid4(), export, sealed_before_isolation=False)
    assert seen == [True, False]


class _Fleet(_Runner):
    def __init__(self, jobs, *, fail_delete=()):
        self.jobs = jobs
        self.deleted: list[str] = []
        self.fail_delete = set(fail_delete)

    async def list_attempt_jobs(self, release):
        self.releases = getattr(self, "releases", []) + [release]
        if self.jobs is None:
            raise RuntimeError("api down")
        return self.jobs

    async def delete_job(self, name):
        if name in self.fail_delete:
            return False
        self.deleted.append(name)
        return True


def _job(name, attempt, terminal=False):
    from butlers.core.qa.k8s_job_process import AttemptJob

    return AttemptJob(name=name, attempt_id=attempt, terminal=terminal)


def _fleet_iso(tmp_path, fleet) -> JobIsolation:
    from dataclasses import replace

    return replace(_iso(tmp_path), runner=fleet)


async def test_the_sweep_removes_only_what_no_live_attempt_owns(tmp_path) -> None:
    from butlers.core.qa.job_isolation import sweep_orphans

    fleet = _Fleet(
        [_job("qa-live", "live"), _job("qa-dead", "dead"), _job("qa-stuck", "stuck")],
        fail_delete={"qa-stuck"},
    )
    iso = _fleet_iso(tmp_path, fleet)
    for kind in ("attempts", "scratch"):
        for name in ("live", "dead"):
            (iso.exchange_root / kind / name).mkdir(parents=True)
    report = await sweep_orphans(iso, live={"live"})
    assert fleet.deleted == ["qa-dead"]
    assert (report.jobs_deleted, report.jobs_undeleted, report.directories_removed) == (1, 1, 2)
    assert (iso.exchange_root / "attempts" / "live").is_dir()
    assert not (iso.exchange_root / "attempts" / "dead").exists()


async def test_the_sweep_unlinks_a_planted_symlink_without_following_it(tmp_path) -> None:
    from butlers.core.qa.job_isolation import sweep_orphans

    iso = _fleet_iso(tmp_path, _Fleet([]))
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    (iso.exchange_root / "attempts").mkdir()
    os.symlink(victim, iso.exchange_root / "attempts" / "evil")
    await sweep_orphans(iso, live=set())
    assert (victim / "keep").exists()
    assert not (iso.exchange_root / "attempts" / "evil").is_symlink()


async def test_an_unreadable_fleet_reads_as_at_cap_not_empty(tmp_path) -> None:
    from butlers.core.qa.job_isolation import active_job_count

    (tmp_path / "b").mkdir()
    assert await active_job_count(_fleet_iso(tmp_path / "b", _Fleet(None))) is None
    live = _Fleet([_job("a", "1"), _job("b", "2", terminal=True), _job("c", "3")])
    assert await active_job_count(_fleet_iso(tmp_path, live)) == 2


async def test_the_sweep_removes_a_pod_chmodded_000_tree(tmp_path) -> None:
    from butlers.core.qa.job_isolation import sweep_orphans

    iso = _fleet_iso(tmp_path, _Fleet([]))
    locked = iso.exchange_root / "attempts" / "dead" / "sub"
    locked.mkdir(parents=True)
    (locked / "f").write_text("x")
    os.chmod(locked, 0o000)
    os.chmod(locked.parent, 0o500)
    report = await sweep_orphans(iso, live=set())
    assert report.directories_removed == 1
    assert not (iso.exchange_root / "attempts" / "dead").exists()


async def test_the_sweep_lists_only_this_releases_jobs(tmp_path) -> None:
    from butlers.core.qa.job_isolation import sweep_orphans

    fleet = _Fleet([])
    await sweep_orphans(_fleet_iso(tmp_path, fleet), live=set())
    assert fleet.releases == ["butlers"]


async def test_jobs_count_toward_the_batch_concurrency_cap(tmp_path) -> None:
    """Rows say 0 active, but two non-terminal Jobs exist: nothing is dispatched."""
    from butlers.core.qa.dispatch import dispatch_novel_findings
    from tests.core.qa.test_dispatch import _make_triaged

    fleet = _Fleet([_job("a", "1"), _job("b", "2")])
    config = QaDispatchConfig(max_concurrent=2, job_isolation=_fleet_iso(tmp_path, fleet))
    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation") as dispatch,
    ):
        results = await dispatch_novel_findings(
            pool=MagicMock(),
            novel_findings=[_make_triaged(), _make_triaged()],
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=tmp_path,
            spawner=MagicMock(),
        )
    assert [r.reason for r in results] == ["concurrency_cap", "concurrency_cap"]
    dispatch.assert_not_called()


async def test_an_unreadable_fleet_stops_the_batch(tmp_path) -> None:
    from butlers.core.qa.dispatch import dispatch_novel_findings
    from tests.core.qa.test_dispatch import _make_triaged

    config = QaDispatchConfig(max_concurrent=2, job_isolation=_fleet_iso(tmp_path, _Fleet(None)))
    with (
        patch(
            "butlers.core.qa.dispatch.count_active_attempts",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch("butlers.core.qa.dispatch.dispatch_qa_investigation") as dispatch,
    ):
        results = await dispatch_novel_findings(
            pool=MagicMock(),
            novel_findings=[_make_triaged()],
            patrol_id=uuid.uuid4(),
            config=config,
            repo_root=tmp_path,
            spawner=MagicMock(),
        )
    assert [r.reason for r in results] == ["concurrency_cap"]
    dispatch.assert_not_called()


async def test_a_failed_clone_cleans_the_attempt_directory_and_releases_ownership(tmp_path) -> None:
    from butlers.core.qa.dispatch import dispatch_qa_investigation
    from butlers.core.qa.job_isolation import LIVE_ATTEMPTS
    from tests.core.qa.test_dispatch import _make_triaged

    iso = _iso(tmp_path)
    attempt_id = uuid.uuid4()
    attempt_dir = iso.attempt_dir(attempt_id)

    async def failing_clone(*args, **kwargs):
        attempt_dir.mkdir(parents=True)  # the half-built tree a failed clone leaves behind
        (attempt_dir / "repo").mkdir()
        raise OSError("clone failed")

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(("c" * 40).encode(), b""))
    mock_proc.returncode = 0
    p = "butlers.core.qa.dispatch."
    with (
        patch(
            p + "create_or_join_attempt", new_callable=AsyncMock, return_value=(attempt_id, True)
        ),
        patch(p + "update_finding_attempt", new_callable=AsyncMock),
        patch(p + "get_publication_hold", new_callable=AsyncMock, return_value=False),
        patch(p + "get_recent_attempt", new_callable=AsyncMock, return_value=None),
        patch(p + "count_active_attempts", new_callable=AsyncMock, return_value=0),
        patch(p + "active_job_count", new_callable=AsyncMock, return_value=0),
        patch(p + "_is_circuit_breaker_tripped", new_callable=AsyncMock, return_value=False),
        patch(p + "resolve_model_with_effective_tier", new_callable=AsyncMock),
        patch(p + "update_attempt_status", new_callable=AsyncMock),
        patch(p + "_refresh_main_commit", new_callable=AsyncMock, return_value="c" * 40),
        patch(p + "prepare_attempt_clone", side_effect=failing_clone),
    ):
        result = await dispatch_qa_investigation(
            pool=_qa_pool(),
            triaged_finding=_make_triaged(_make_finding(severity=1)),
            patrol_id=uuid.uuid4(),
            config=QaDispatchConfig(job_isolation=iso),
            repo_root=tmp_path,
            spawner=MagicMock(),
            gh_token=None,
        )
    assert result.reason == "worktree_creation_failed"
    assert not attempt_dir.exists()
    assert str(attempt_id) not in LIVE_ATTEMPTS


async def test_reopening_never_chmods_through_a_symlink(tmp_path) -> None:
    from butlers.core.qa.job_isolation import remove_tree

    victim = tmp_path / "victim"
    victim.mkdir()
    os.chmod(victim, 0o500)
    root = tmp_path / "attempt"
    locked = root / "sub"
    locked.mkdir(parents=True)
    os.chmod(locked, 0o000)  # forces the reopen path
    os.symlink(victim, root / "link")
    try:
        remove_tree(root)
        victim_mode = victim.stat().st_mode & 0o777
    finally:
        os.chmod(victim, 0o700)
    assert not root.exists()
    assert victim_mode == 0o500
