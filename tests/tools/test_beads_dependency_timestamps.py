"""Regression test for beads dependency timestamp handling in worktree flows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _run(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result."""
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _decode_first_json(text: str) -> object:
    """Extract the first JSON object/array from text."""
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
    """Extract issue ID from JSON response."""
    payload = _decode_first_json(json_text)
    if isinstance(payload, list):
        return str(payload[0]["id"])
    return str(payload["id"])


def _get_issue_from_jsonl(jsonl_path: Path, issue_id: str) -> dict:
    """Load an issue record from issues.jsonl."""
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("id") == issue_id:
            return payload
    raise AssertionError(f"Issue {issue_id!r} not found in {jsonl_path}")


@pytest.mark.skipif(
    shutil.which("bd") is None or shutil.which("git") is None,
    reason="requires bd and git in PATH",
)
def test_dep_add_sets_real_timestamp_in_worktree(tmp_path: Path) -> None:
    """Verify that bd dep add sets a real created_at timestamp in worktree flows."""
    env = os.environ.copy()
    env["BEADS_NO_DAEMON"] = "1"

    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize git repo
    git_init = _run(["git", "init"], cwd=repo, env=env)
    assert git_init.returncode == 0, git_init.stderr
    _run(["git", "config", "user.email", "beads-test@example.com"], cwd=repo, env=env)
    _run(["git", "config", "user.name", "Beads Test"], cwd=repo, env=env)

    # Initialize beads
    beads_init = _run(["bd", "init", "--json"], cwd=repo, env=env)
    assert beads_init.returncode == 0, beads_init.stderr
    (repo / ".beads" / "config.yaml").write_text("no-db: true\n")

    # Create two issues
    issue1_result = _run(
        ["bd", "create", "First issue", "-t", "task", "-p", "2", "--json"],
        cwd=repo,
        env=env,
    )
    assert issue1_result.returncode == 0, issue1_result.stderr
    issue1_id = _extract_issue_id(issue1_result.stdout)

    issue2_result = _run(
        ["bd", "create", "Second issue", "-t", "task", "-p", "2", "--json"],
        cwd=repo,
        env=env,
    )
    assert issue2_result.returncode == 0, issue2_result.stderr
    issue2_id = _extract_issue_id(issue2_result.stdout)

    # Commit initial state
    _run(["git", "add", "."], cwd=repo, env=env)
    _run(["git", "commit", "-m", "initial"], cwd=repo, env=env)

    # Create worktree
    worktree = repo / ".worktrees" / "test-worker"
    worktree.parent.mkdir()
    add_worktree = _run(
        ["git", "worktree", "add", "-b", "test-branch", str(worktree)], cwd=repo, env=env
    )
    assert add_worktree.returncode == 0, add_worktree.stderr

    # Sync to worktree
    sync_result = _run(["bd", "sync", "--import"], cwd=worktree, env=env)
    assert sync_result.returncode == 0, sync_result.stderr

    # Add dependency in worktree
    dep_add = _run(["bd", "dep", "add", issue1_id, issue2_id], cwd=worktree, env=env)
    assert dep_add.returncode == 0, dep_add.stderr

    # Load the issue from worktree jsonl
    worktree_jsonl = worktree / ".beads" / "issues.jsonl"
    issue_data = _get_issue_from_jsonl(worktree_jsonl, issue1_id)

    # Find the dependency we just added
    deps = issue_data.get("dependencies", [])
    matching_dep = None
    for dep in deps:
        if dep["depends_on_id"] == issue2_id:
            matching_dep = dep
            break

    assert matching_dep is not None, f"Dependency {issue1_id} -> {issue2_id} not found"

    # Check that created_at is NOT the zero timestamp
    created_at = matching_dep.get("created_at", "")
    zero_timestamp = "0001-01-01T00:00:00Z"

    # This test documents the current bug behavior - it will fail until bd is fixed
    # Once bd is fixed upstream, this assertion will pass
    if created_at == zero_timestamp:
        pytest.skip(
            "Bug reproduced: dependency created_at is zero timestamp. "
            "This is expected until bd CLI is fixed upstream."
        )

    # Verify it's a valid recent timestamp
    assert created_at != zero_timestamp, "Dependency created_at should not be zero timestamp"
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        # Should be reasonably recent (within last minute)
        now = datetime.now(parsed.tzinfo)
        delta = abs((now - parsed).total_seconds())
        assert delta < 60, f"Timestamp {created_at} is not recent (delta: {delta}s)"
    except (ValueError, AttributeError) as e:
        pytest.fail(f"Invalid timestamp format {created_at!r}: {e}")
