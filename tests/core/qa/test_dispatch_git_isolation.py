"""bu-mc01y5.11: agent-writable shared .git never executes beside the dispatcher's token.

QA worktrees are linked worktrees of ``repo_root``, so an investigation agent can write
``.git/hooks`` and ``.git/config``.  These tests drive the real dispatcher git subprocesses
against a repository carrying a planted hook, fsmonitor and credential helper.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.core.git_env import isolated_git_env
from butlers.core.healing import worktree as healing_worktree
from butlers.core.qa import dispatch as qa_dispatch
from butlers.core.qa import repo_clone

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
        assert env["GIT_ASKPASS"] == "/bin/false" and "GH_TOKEN" not in env
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


@pytest.mark.asyncio
async def test_main_refresh_neutralises_planted_commands_and_hides_daemon_secrets(
    tmp_path: Path, monkeypatch
):
    """A core.sshCommand planted in .git/config never executes in the main refresh."""
    repo_root, work, markers = _repository(tmp_path)
    _advance(work, "one")
    monkeypatch.setenv("GH_TOKEN", _TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgres://daemon:secret@db/butlers")

    ssh = tmp_path / "ssh.sh"
    ssh.write_text(
        f'#!/bin/sh\necho "${{DATABASE_URL}}|${{GH_TOKEN}}" >> {tmp_path / "ssh.marker"}\nexit 1\n'
    )
    ssh.chmod(0o755)
    _git(repo_root, "config", "core.sshCommand", str(ssh))

    # Control: the unhardened daemon environment really runs the planted command.
    _git(repo_root, "remote", "add", "remote-ssh", "ssh://git@127.0.0.1:1/x.git")
    subprocess.run(["git", "fetch", "remote-ssh"], cwd=repo_root, capture_output=True)
    assert (tmp_path / "ssh.marker").exists()
    (tmp_path / "ssh.marker").unlink()
    _clear(markers)
    _advance(work, "two")

    commit = await qa_dispatch._refresh_main_commit(repo_root)

    assert commit == _git(work, "rev-parse", "main")
    _git(repo_root, "remote", "set-url", "origin", "ssh://git@127.0.0.1:1/x.git")
    with pytest.raises(qa_dispatch.MainRefreshError):
        await qa_dispatch._refresh_main_commit(repo_root)
    assert not (tmp_path / "ssh.marker").exists()


def test_isolated_git_env_carries_no_secrets_and_no_credential_helper(tmp_path: Path, monkeypatch):
    repo_root, _work, _markers = _repository(tmp_path)
    for name in ("GH_TOKEN", "DATABASE_URL", "ANTHROPIC_API_KEY", "BUTLERS_DB_PASSWORD"):
        monkeypatch.setenv(name, "secret-value")
    env = isolated_git_env()
    assert "secret-value" not in json.dumps(env)
    # A planted local credential.helper is reset by the command-line config.
    subprocess.run(
        ["git", "credential", "fill"],
        cwd=repo_root,
        input="protocol=https\nhost=example.invalid\n\n",
        capture_output=True,
        text=True,
        env={**env, "GIT_ASKPASS": "/bin/false"},
    )
    assert not (tmp_path / "credential.marker").exists()
    subprocess.run(
        ["git", "credential", "fill"],
        cwd=repo_root,
        input="protocol=https\nhost=example.invalid\n\n",
        capture_output=True,
        text=True,
        env={"PATH": env["PATH"], "GIT_TERMINAL_PROMPT": "0", "HOME": str(tmp_path)},
    )
    assert (tmp_path / "credential.marker").exists()  # Control: unhardened git runs the helper.


@pytest.mark.asyncio
async def test_healing_worktree_helpers_run_no_planted_hook_or_secret(tmp_path: Path, monkeypatch):
    """create/remove of QA worktrees run git under the isolated environment."""
    repo_root, _work, markers = _repository(tmp_path)
    monkeypatch.setenv("GH_TOKEN", _TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgres://daemon:secret@db/butlers")
    seen_env: list[dict[str, str]] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen_env.append(dict(kwargs["env"]))
        return await real_exec(*args, **kwargs)

    with patch.object(healing_worktree.asyncio, "create_subprocess_exec", side_effect=spy):
        path, branch = await healing_worktree.create_healing_worktree(
            repo_root, "general", "b" * 64, prefix="qa", base_ref="main"
        )
        await healing_worktree.remove_healing_worktree(repo_root, branch)

    assert not path.exists()
    assert not any(marker.exists() for marker in markers.values())
    assert seen_env
    for env in seen_env:
        assert "GH_TOKEN" not in env and "DATABASE_URL" not in env
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"


@pytest.mark.asyncio
async def test_managed_clone_git_runs_no_planted_hook_or_secret(tmp_path: Path, monkeypatch):
    repo_root, work, markers = _repository(tmp_path)
    _advance(work, "one")
    monkeypatch.setenv("DATABASE_URL", "postgres://daemon:secret@db/butlers")
    upload = tmp_path / "uploadpack.sh"
    upload.write_text(
        f'#!/bin/sh\necho "${{DATABASE_URL}}" >> {tmp_path / "uploadpack.marker"}\n'
        'exec git-upload-pack "$@"\n'
    )
    upload.chmod(0o755)
    _git(repo_root, "config", "remote.origin.uploadpack", str(upload))
    _git(repo_root, "fetch", "-q", "origin")  # Control: the planted command runs unhardened.
    assert (tmp_path / "uploadpack.marker").read_text().startswith("postgres://daemon:secret")
    (tmp_path / "uploadpack.marker").unlink()
    _clear(markers)
    _advance(work, "two")

    seen_env: list[dict[str, str]] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen_env.append(dict(kwargs["env"]))
        return await real_exec(*args, **kwargs)

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"repo_url": str(tmp_path / "origin.git")})
    pool.execute = AsyncMock()
    managed = repo_clone.ManagedRepoClone(pool, clone_dir=repo_root)
    with patch.object(repo_clone.asyncio, "create_subprocess_exec", side_effect=spy):
        await managed.refresh()

    pool.execute.assert_any_await(
        "UPDATE public.qa_repo_config "
        "SET last_synced_at = now(), last_sync_error = NULL, updated_at = now()"
    )
    assert _git(repo_root, "rev-parse", "HEAD") == _git(work, "rev-parse", "main")
    assert not (tmp_path / "uploadpack.marker").exists()
    assert not any(marker.exists() for marker in markers.values())
    for env in seen_env:
        assert "DATABASE_URL" not in env and "GH_TOKEN" not in env


@pytest.mark.asyncio
async def test_head_lookup_is_pinned_to_the_stored_repository_from_a_neutral_cwd(tmp_path: Path):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b'[{"number": 7, "url": "u"}]', b""))
    proc.returncode = 0
    with patch.object(qa_dispatch.asyncio, "create_subprocess_exec", return_value=proc) as spawn:
        found = await qa_dispatch._resolve_pr_by_head(tmp_path, _BRANCH, {}, repo="org/repo")
        unpinned = await qa_dispatch._resolve_pr_by_head(tmp_path, _BRANCH, {})

    assert found == ("u", 7)
    assert unpinned == (None, None)
    assert spawn.call_count == 1
    args = spawn.call_args.args
    assert args[args.index("--repo") + 1] == "org/repo"
    assert Path(spawn.call_args.kwargs["cwd"]) != tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["ext", "gitproxy"])
async def test_main_refresh_never_runs_planted_ext_or_git_proxy_transport(
    tmp_path: Path, transport: str
):
    repo_root, _work, _markers = _repository(tmp_path)
    marker = tmp_path / f"{transport}.marker"
    script = tmp_path / f"{transport}.sh"
    script.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 1\n")
    script.chmod(0o755)
    if transport == "ext":
        _git(repo_root, "remote", "set-url", "origin", f"ext::{script}")
        _git(repo_root, "config", "protocol.ext.allow", "always")  # Off by default; plantable.
    else:
        _git(repo_root, "remote", "set-url", "origin", "git://127.0.0.1:1/x.git")
        _git(repo_root, "config", "core.gitProxy", str(script))

    subprocess.run(["git", "fetch", "origin"], cwd=repo_root, capture_output=True)
    assert marker.exists()  # Control: the unhardened fetch runs the planted command.
    marker.unlink()

    with pytest.raises(qa_dispatch.MainRefreshError):
        await qa_dispatch._refresh_main_commit(repo_root)
    assert not marker.exists()


def _plant_uploadpack(tmp_path: Path, repo_root: Path) -> Path:
    marker = tmp_path / "uploadpack.marker"
    script = tmp_path / "uploadpack.sh"
    script.write_text(
        f'#!/bin/sh\necho "${{DATABASE_URL}}" >> {marker}\nexec git-upload-pack "$@"\n'
    )
    script.chmod(0o755)
    _git(repo_root, "config", "remote.origin.uploadpack", str(script))
    return marker


@pytest.mark.asyncio
async def test_main_refresh_pins_upload_pack_against_a_planted_remote_command(
    tmp_path: Path, monkeypatch
):
    """bu-mc01y5.13: git keeps the first remote.origin.uploadpack, so the fetch pins it."""
    repo_root, work, _markers = _repository(tmp_path)
    _advance(work, "one")
    monkeypatch.setenv("DATABASE_URL", "postgres://daemon:secret@db/butlers")
    marker = _plant_uploadpack(tmp_path, repo_root)
    _git(repo_root, "fetch", "-q", "origin")  # Control: the plant runs for an unpinned fetch.
    assert marker.read_text().startswith("postgres://daemon:secret")
    marker.unlink()
    _advance(work, "two")

    commit = await qa_dispatch._refresh_main_commit(repo_root)

    assert commit == _git(work, "rev-parse", "main")
    assert not marker.exists()


@pytest.mark.asyncio
async def test_followup_fetch_pins_upload_pack_against_a_planted_remote_command(
    tmp_path: Path, monkeypatch
):
    repo_root, work, _markers = _repository(tmp_path)
    _advance(work, "one")
    monkeypatch.setenv("DATABASE_URL", "postgres://daemon:secret@db/butlers")
    marker = _plant_uploadpack(tmp_path, repo_root)
    pool = MagicMock()
    pool.execute = AsyncMock()
    registry: list[asyncio.Task] = []
    with (
        patch.object(qa_dispatch, "anonymize", return_value="feedback"),
        patch.object(qa_dispatch, "validate_anonymized", return_value=(True, [])),
        patch.object(qa_dispatch, "_run_review_followup_session", new_callable=AsyncMock),
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
    assert not marker.exists()


@pytest.mark.asyncio
async def test_commit_diff_snapshot_runs_no_planted_driver_and_sees_no_daemon_secret(
    tmp_path: Path, monkeypatch
):
    """bu-mc01y5.13: diff.external and textconv planted in .git/config never run."""
    repo_root, _work, _markers = _repository(tmp_path)
    monkeypatch.setenv("GH_TOKEN", _TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgres://daemon:secret@db/butlers")
    monkeypatch.setenv("POSTGRES_PASSWORD", "bootstrap-secret")
    external = tmp_path / "external.sh"
    textconv = tmp_path / "textconv.sh"
    external.write_text(
        f'#!/bin/sh\necho "${{DATABASE_URL}}" >> {tmp_path / "external.marker"}\nexit 0\n'
    )
    textconv.write_text(f'#!/bin/sh\necho ran >> {tmp_path / "textconv.marker"}\ncat "$1"\n')
    for script in (external, textconv):
        script.chmod(0o755)
    (repo_root / ".gitattributes").write_text("*.txt diff=probe\n")
    (repo_root / "change.txt").write_text("before\n")
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-qm", "before")
    (repo_root / "change.txt").write_text("after\n")
    _git(repo_root, "commit", "-qam", "after")
    _git(repo_root, "config", "diff.external", str(external))
    _git(repo_root, "config", "diff.probe.textconv", str(textconv))

    # Control: a plain daemon-environment diff really runs the planted drivers.
    subprocess.run(["git", "diff", "HEAD~1..HEAD"], cwd=repo_root, capture_output=True)
    assert (tmp_path / "external.marker").read_text().startswith("postgres://daemon:secret")
    (tmp_path / "external.marker").unlink()
    (tmp_path / "textconv.marker").unlink(missing_ok=True)
    # Control: with the external driver bypassed, the textconv plant fires on its own.
    subprocess.run(
        ["git", "diff", "--no-ext-diff", "HEAD~1..HEAD"], cwd=repo_root, capture_output=True
    )
    assert (tmp_path / "textconv.marker").exists()
    (tmp_path / "textconv.marker").unlink()

    seen_env: list[dict[str, str]] = []
    real_exec = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        seen_env.append(dict(kwargs["env"]))
        return await real_exec(*args, **kwargs)

    with patch.object(qa_dispatch.asyncio, "create_subprocess_exec", side_effect=spy):
        snapshot = await qa_dispatch._capture_commit_diff_snapshot(repo_root)

    assert not (tmp_path / "external.marker").exists()
    assert not (tmp_path / "textconv.marker").exists()
    (env,) = seen_env
    assert not {"GH_TOKEN", "DATABASE_URL", "POSTGRES_PASSWORD"} & set(env)
    assert "bootstrap-secret" not in json.dumps(env)
    assert {"kind": "-", "text": "before"} in snapshot
    assert {"kind": "+", "text": "after"} in snapshot


@pytest.mark.asyncio
async def test_no_op_branch_detection_runs_no_planted_signature_program(tmp_path: Path):
    """bu-mc01y5.13: log.showSignature plus gpg.program never runs during no-op detection."""
    repo_root, _work, _markers = _repository(tmp_path)
    marker = tmp_path / "gpg.marker"
    program = tmp_path / "evil-gpg.sh"
    program.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 1\n")
    program.chmod(0o755)
    tree = _git(repo_root, "rev-parse", "HEAD^{tree}")
    parent = _git(repo_root, "rev-parse", "HEAD")
    commit = (
        f"tree {tree}\nparent {parent}\nauthor t <t@e> 1700000000 +0000\n"
        "committer t <t@e> 1700000000 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n \n fake\n -----END PGP SIGNATURE-----\n\nsigned\n"
    )
    sha = subprocess.run(
        ["git", "hash-object", "-t", "commit", "-w", "--stdin"],
        cwd=repo_root,
        input=commit,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(repo_root, "update-ref", "refs/heads/agent-branch", sha)
    _git(repo_root, "config", "log.showSignature", "true")
    _git(repo_root, "config", "gpg.program", str(program))

    # Control: the plain daemon-style log really runs the planted program.
    subprocess.run(
        ["git", "log", "main..agent-branch", "--oneline"], cwd=repo_root, capture_output=True
    )
    assert marker.exists()
    marker.unlink()

    no_op = await qa_dispatch._detect_no_op_branch(
        repo_root, "agent-branch", {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    )

    assert no_op is False
    assert not marker.exists()
