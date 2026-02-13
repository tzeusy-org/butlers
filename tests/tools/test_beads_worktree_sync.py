"""Regression test for Beads worktree path resolution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_BEADS_CONFIG = REPO_ROOT / ".beads" / "config.yaml"


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
def test_worktree_close_sync_export_import_use_active_worktree_jsonl(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["BEADS_NO_DAEMON"] = "1"
    env["BD_NO_DAEMON"] = "1"

    repo = tmp_path / "repo"
    repo.mkdir()

    _run(["git", "init"], cwd=repo, env=env)
    _run(["git", "config", "user.email", "beads-test@example.com"], cwd=repo, env=env)
    _run(["git", "config", "user.name", "Beads Test"], cwd=repo, env=env)
    _run(["bd", "init", "--json"], cwd=repo, env=env)
    (repo / ".beads" / "config.yaml").write_text(REPO_BEADS_CONFIG.read_text())

    create_out = _run(
        ["bd", "create", "Worktree sync regression", "-t", "bug", "-p", "2", "--json"],
        cwd=repo,
        env=env,
    )
    created = _decode_first_json(create_out.stdout)
    issue_id = created["id"] if isinstance(created, dict) else created[0]["id"]

    _run(["git", "add", "."], cwd=repo, env=env)
    _run(["git", "commit", "-m", "seed"], cwd=repo, env=env)

    worktree = repo / ".worktrees" / "worker"
    worktree.parent.mkdir()
    _run(["git", "worktree", "add", "-b", "worker", str(worktree)], cwd=repo, env=env)

    _run(["bd", "close", issue_id], cwd=worktree, env=env)
    _run(["bd", "sync"], cwd=worktree, env=env)

    show_out = _run(["bd", "show", issue_id, "--json"], cwd=worktree, env=env)
    shown = _decode_first_json(show_out.stdout)
    shown_issue = shown[0] if isinstance(shown, list) else shown
    assert shown_issue["status"] == "closed"

    worktree_issue = _issue_from_jsonl(worktree / ".beads" / "issues.jsonl", issue_id)
    repo_issue = _issue_from_jsonl(repo / ".beads" / "issues.jsonl", issue_id)
    assert worktree_issue["status"] == "closed"
    assert repo_issue["status"] == "open"

    export_path = worktree / "export.jsonl"
    _run(["bd", "export", "-o", str(export_path)], cwd=worktree, env=env)
    exported_issue = _issue_from_jsonl(export_path, issue_id)
    assert exported_issue["status"] == "closed"

    imported_title = "Worktree import applied"
    rewritten_lines: list[str] = []
    for line in export_path.read_text().splitlines():
        payload = json.loads(line)
        if payload.get("id") == issue_id:
            payload["title"] = imported_title
            # Import resolution is timestamp-aware; bump updated_at to ensure update wins.
            payload["updated_at"] = "2099-01-01T00:00:00Z"
        rewritten_lines.append(json.dumps(payload, separators=(",", ":")))
    export_path.write_text("\n".join(rewritten_lines) + "\n")

    _run(["bd", "import", "-i", str(export_path)], cwd=worktree, env=env)
    post_import_worktree = _issue_from_jsonl(worktree / ".beads" / "issues.jsonl", issue_id)
    assert post_import_worktree["title"] == imported_title
