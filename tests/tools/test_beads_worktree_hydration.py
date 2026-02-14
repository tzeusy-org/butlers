"""Regression coverage for Beads no-db worktree hydration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _decode_first_json(text: str) -> object:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return payload
    raise AssertionError(f"No JSON payload found in output:\n{text}")


def _extract_issue_id(json_text: str) -> str:
    payload = _decode_first_json(json_text)
    if isinstance(payload, list):
        return str(payload[0]["id"])
    return str(payload["id"])


@pytest.mark.skipif(
    shutil.which("bd") is None or shutil.which("git") is None,
    reason="requires bd and git in PATH",
)
def test_stale_worktree_issue_lookup_hydrates_via_sync_import(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["BEADS_NO_DAEMON"] = "1"

    repo = tmp_path / "repo"
    repo.mkdir()

    git_init = _run(["git", "init"], cwd=repo, env=env)
    assert git_init.returncode == 0, git_init.stderr
    git_config_email = _run(
        ["git", "config", "user.email", "beads-test@example.com"], cwd=repo, env=env
    )
    assert git_config_email.returncode == 0, git_config_email.stderr
    git_config_name = _run(["git", "config", "user.name", "Beads Test"], cwd=repo, env=env)
    assert git_config_name.returncode == 0, git_config_name.stderr
    beads_init = _run(["bd", "init", "--json"], cwd=repo, env=env)
    assert beads_init.returncode == 0, beads_init.stderr
    (repo / ".beads" / "config.yaml").write_text("no-db: true\n")

    seed_issue = _run(
        [
            "bd",
            "create",
            "Seed issue",
            "--description",
            "seed issue for worktree setup",
            "-t",
            "bug",
            "-p",
            "2",
            "--json",
        ],
        cwd=repo,
        env=env,
    )
    assert seed_issue.returncode == 0, seed_issue.stderr

    git_add = _run(["git", "add", "."], cwd=repo, env=env)
    assert git_add.returncode == 0, git_add.stderr
    git_commit = _run(["git", "commit", "-m", "seed"], cwd=repo, env=env)
    assert git_commit.returncode == 0, git_commit.stderr

    worktree = repo / ".worktrees" / "worker"
    worktree.parent.mkdir()
    add_worktree = _run(
        ["git", "worktree", "add", "-b", "worker", str(worktree)], cwd=repo, env=env
    )
    assert add_worktree.returncode == 0, add_worktree.stderr

    created = _run(
        [
            "bd",
            "create",
            "Freshly created issue",
            "--description",
            "created after worker worktree exists",
            "-t",
            "bug",
            "-p",
            "2",
            "--json",
        ],
        cwd=repo,
        env=env,
    )
    assert created.returncode == 0, created.stderr
    created_issue_id = _extract_issue_id(created.stdout)

    stale_show = _run(["bd", "show", created_issue_id, "--json"], cwd=worktree, env=env)
    assert stale_show.returncode == 0, stale_show.stderr
    assert stale_show.stdout.strip() == ""
    assert f'no issue found matching "{created_issue_id}"' in stale_show.stderr

    imported = _run(["bd", "sync", "--import"], cwd=worktree, env=env)
    assert imported.returncode == 0, imported.stderr

    hydrated_show = _run(["bd", "show", created_issue_id, "--json"], cwd=worktree, env=env)
    assert hydrated_show.returncode == 0, hydrated_show.stderr
    hydrated_payload = _decode_first_json(hydrated_show.stdout)
    issue = hydrated_payload[0] if isinstance(hydrated_payload, list) else hydrated_payload
    assert issue["id"] == created_issue_id
    assert issue["title"] == "Freshly created issue"
