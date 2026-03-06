"""Regression test for Beads worktree close and export operations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        cmd = " ".join(args)
        raise AssertionError(
            f"Command failed ({cmd})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _decode_first_json(text: str) -> object:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return parsed
    raise AssertionError(f"No JSON payload found in output:\n{text}")


def _issue_from_jsonl(path: Path, issue_id: str) -> dict[str, object]:
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("id") == issue_id:
            return payload
    raise AssertionError(f"Issue {issue_id!r} not found in {path}")


@pytest.mark.skipif(
    shutil.which("bd") is None or shutil.which("git") is None,
    reason="requires bd and git in PATH",
)
def test_worktree_close_and_export(tmp_path: Path) -> None:
    """Verify that closing an issue in a worktree is reflected in bd show and bd export."""
    env = os.environ.copy()
    env["BEADS_NO_DAEMON"] = "1"
    env["BD_NO_DAEMON"] = "1"

    repo = tmp_path / "repo"
    repo.mkdir()

    _run(["git", "init"], cwd=repo, env=env)
    _run(["git", "config", "user.email", "beads-test@example.com"], cwd=repo, env=env)
    _run(["git", "config", "user.name", "Beads Test"], cwd=repo, env=env)
    _run(["bd", "init", "--json"], cwd=repo, env=env)

    create_out = _run(
        ["bd", "create", "Worktree close regression", "-t", "bug", "-p", "2", "--json"],
        cwd=repo,
        env=env,
    )
    created = _decode_first_json(create_out.stdout)
    issue_id = created["id"] if isinstance(created, dict) else created[0]["id"]

    # bd init may already commit .beads/ files; ensure HEAD exists for worktree add.
    subprocess.run(
        ["git", "add", "."],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed", "--allow-empty"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    worktree = repo / ".worktrees" / "worker"
    worktree.parent.mkdir()
    _run(["git", "worktree", "add", "-b", "worker", str(worktree)], cwd=repo, env=env)

    # Close the issue from the worktree (shared Dolt backend)
    _run(["bd", "close", issue_id], cwd=worktree, env=env)

    # Verify the close is visible via bd show in the worktree
    show_out = _run(["bd", "show", issue_id, "--json"], cwd=worktree, env=env)
    shown = _decode_first_json(show_out.stdout)
    shown_issue = shown[0] if isinstance(shown, list) else shown
    assert shown_issue["status"] == "closed"

    # Verify export from the worktree reflects the closed status
    export_path = tmp_path / "export.jsonl"
    _run(["bd", "export", "-o", str(export_path)], cwd=worktree, env=env)
    exported_issue = _issue_from_jsonl(export_path, issue_id)
    assert exported_issue["status"] == "closed"

    # Verify the close is also visible from the main repo
    show_from_repo = _run(["bd", "show", issue_id, "--json"], cwd=repo, env=env)
    shown_from_repo = _decode_first_json(show_from_repo.stdout)
    repo_issue = shown_from_repo[0] if isinstance(shown_from_repo, list) else shown_from_repo
    assert repo_issue["status"] == "closed"
