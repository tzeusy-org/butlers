"""bu-mc01y5.11: agent-writable shared .git never executes beside the dispatcher's token.

QA worktrees are linked worktrees of ``repo_root``, so an investigation agent can write
``.git/hooks`` and ``.git/config``.  These tests drive the real dispatcher git subprocesses
against a repository carrying a planted hook, fsmonitor and credential helper.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.qa import dispatch as qa_dispatch

pytestmark = pytest.mark.unit

_TOKEN = "ghp_isolation_test_token_value"
_BRANCH = "qa/general/abcdef-1234"
_IDENTITY = ["-c", "user.name=Proof", "-c", "user.email=proof@example.invalid"]


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *_IDENTITY, *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _advance(origin_work: Path, name: str) -> None:
    (origin_work / f"{name}.txt").write_text(name)
    _git(origin_work, "add", ".")
    _git(origin_work, "commit", "-qm", name)
    _git(origin_work, "push", "-q", "origin", "main", _BRANCH)


def _repository(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    """Return (repo_root clone, origin work tree, planted markers)."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    (work / "seed.txt").write_text("seed")
    _git(work, "add", ".")
    _git(work, "commit", "-qm", "seed")
    _git(work, "branch", _BRANCH)
    _git(work, "push", "-q", "origin", "main", _BRANCH)
    repo_root = tmp_path / "repo_root"
    _git(tmp_path, "clone", "-q", str(bare), str(repo_root))

    markers = {
        name: tmp_path / f"{name}.marker"
        for name in ("reference-transaction", "post-checkout", "fsmonitor")
    }
    for name in ("reference-transaction", "post-checkout"):
        hook = repo_root / ".git/hooks" / name
        hook.write_text(f'#!/bin/sh\necho "${{GH_TOKEN}}|${{PATH}}" >> {markers[name]}\n')
        hook.chmod(0o755)
    monitor = tmp_path / "fsmonitor.sh"
    monitor.write_text(f"#!/bin/sh\necho ran >> {markers['fsmonitor']}\n")
    monitor.chmod(0o755)
    _git(repo_root, "config", "core.fsmonitor", str(monitor))
    _git(repo_root, "config", "credential.helper", f"!echo ran >> {tmp_path}/credential.marker")
    return repo_root, work, markers


def _clear(markers: dict[str, Path]) -> None:
    for marker in markers.values():
        marker.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_followup_prep_runs_no_planted_hook_and_never_holds_the_token(tmp_path: Path):
    """Follow-up fetch/worktree prep neither runs agent-written hooks nor sees the token."""
    repo_root, work, markers = _repository(tmp_path)
    _advance(work, "one")
    # Control: the planted hook really fires for an unhardened git in this repository.
    control = tmp_path / "control-worktree"
    _git(repo_root, "worktree", "add", "-q", "-b", "control", str(control), "HEAD")
    assert markers["post-checkout"].exists()
    _clear(markers)

    seen_env: list[dict[str, str]] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen_env.append(dict(kwargs["env"]))
        return await real_exec(*args, **kwargs)

    pool = MagicMock()
    pool.execute = AsyncMock()
    registry: list[asyncio.Task] = []
    with (
        patch.object(qa_dispatch, "anonymize", return_value="feedback"),
        patch.object(qa_dispatch, "validate_anonymized", return_value=(True, [])),
        patch.object(qa_dispatch, "_run_review_followup_session", new_callable=AsyncMock),
        patch.object(qa_dispatch.asyncio, "create_subprocess_exec", side_effect=spy),
    ):
        dispatched = await qa_dispatch._dispatch_pr_review_followup(
            pool=pool,
            repo_root=repo_root,
            attempt_id=uuid.uuid4(),
            pr_number=42,
            pr_url="https://github.com/org/repo/pull/42",
            fingerprint="a" * 64,
            butler_name="general",
            pr_branch_name=_BRANCH,
            feedback_summary="comment",
            config=qa_dispatch.QaDispatchConfig(),
            spawner=MagicMock(),
            gh_token=_TOKEN,
            task_registry=registry,
        )
        await asyncio.gather(*registry)

    assert dispatched is True
    assert not any(marker.exists() for marker in markers.values())
    assert not (tmp_path / "credential.marker").exists()
    assert seen_env
    for env in seen_env:
        assert _TOKEN not in json.dumps(env)
        assert "GIT_ASKPASS" not in env and "GH_TOKEN" not in env
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.asyncio
async def test_main_refresh_runs_no_planted_hook(tmp_path: Path, monkeypatch):
    """The trusted main refresh keeps ambient auth but never runs agent-written hooks."""
    repo_root, work, markers = _repository(tmp_path)
    _advance(work, "one")
    _git(repo_root, "fetch", "-q", "origin")  # Control: unhardened fetch fires the hook.
    assert markers["reference-transaction"].exists()
    _clear(markers)
    _advance(work, "two")
    monkeypatch.setenv("GH_TOKEN", _TOKEN)

    commit = await qa_dispatch._refresh_main_commit(repo_root)

    assert commit == _git(work, "rev-parse", "main")
    assert not markers["reference-transaction"].exists()


@pytest.mark.asyncio
async def test_pr_polling_keeps_the_token_but_not_the_shared_git_directory(tmp_path: Path):
    """gh addresses the PR by URL from a neutral cwd under hardened git config."""
    repo_root = tmp_path / "repo_root"
    repo_root.mkdir()
    pool = MagicMock()
    pool.fetch = AsyncMock(
        return_value=[
            {
                "id": uuid.uuid4(),
                "pr_url": "https://github.com/org/repo/pull/42",
                "pr_number": 42,
                "fingerprint": "a" * 64,
                "butler_name": "general",
                "follow_up_count": 0,
                "branch_name": _BRANCH,
                "last_follow_up_at": None,
            }
        ]
    )
    pool.execute = AsyncMock()
    proc = MagicMock()
    proc.communicate = AsyncMock(
        return_value=(
            json.dumps(
                {"state": "MERGED", "reviews": [], "latestReviews": [], "reviewThreads": []}
            ).encode(),
            b"",
        )
    )
    proc.returncode = 0
    with (
        patch.object(qa_dispatch.asyncio, "create_subprocess_exec", return_value=proc) as spawn,
        patch.object(qa_dispatch, "update_attempt_status", new_callable=AsyncMock),
    ):
        await qa_dispatch.check_open_pr_statuses(pool, repo_root, gh_token=_TOKEN)

    assert spawn.call_args.args[:4] == ("gh", "pr", "view", "https://github.com/org/repo/pull/42")
    assert Path(spawn.call_args.kwargs["cwd"]) != repo_root
    env = spawn.call_args.kwargs["env"]
    assert env["GH_TOKEN"] == _TOKEN
    assert env["GIT_CONFIG_KEY_0"] == "core.hooksPath" and env["GIT_CONFIG_VALUE_0"] == "/dev/null"
