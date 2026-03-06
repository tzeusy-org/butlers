"""Regression test for beads dependency timestamp handling."""

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


def _drop_dolt_db(db_name: str) -> None:
    """Best-effort drop of the Dolt database created during the test."""
    try:
        subprocess.run(
            [
                "mysql",
                "-h",
                "127.0.0.1",
                "-P",
                "3307",
                "-u",
                "root",
                "-e",
                f"DROP DATABASE `{db_name}`;",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        pass


@pytest.mark.skipif(
    shutil.which("bd") is None or shutil.which("git") is None or shutil.which("mysql") is None,
    reason="requires bd, git, and mysql in PATH",
)
def test_dep_add_sets_real_timestamp(tmp_path: Path) -> None:
    """Verify that bd dep add sets a real created_at timestamp.

    Previously this test verified worktree-specific behavior using the removed
    'bd sync --import' command.  The underlying bug (zero timestamps on
    dependency records) is now tested against the Dolt backend directly.
    """
    env = os.environ.copy()
    env["BEADS_NO_DAEMON"] = "1"

    repo = tmp_path / "repo"
    repo.mkdir()

    # Use a prefix without "test" to avoid Dolt server rejection
    db_name = f"bdts_{os.getpid()}"

    try:
        # Initialize git repo
        git_init = _run(["git", "init"], cwd=repo, env=env)
        assert git_init.returncode == 0, git_init.stderr
        _run(["git", "config", "user.email", "beads-test@example.com"], cwd=repo, env=env)
        _run(["git", "config", "user.name", "Beads Test"], cwd=repo, env=env)

        # Initialize beads with a safe prefix
        beads_init = _run(["bd", "init", "--prefix", db_name, "--json"], cwd=repo, env=env)
        assert beads_init.returncode == 0, f"bd init failed:\n{beads_init.stderr}"

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

        # Add dependency
        dep_add = _run(["bd", "dep", "add", issue1_id, issue2_id], cwd=repo, env=env)
        assert dep_add.returncode == 0, dep_add.stderr

        # Export to JSONL and check the dependency timestamp
        export_path = tmp_path / "export.jsonl"
        export_result = _run(["bd", "export", "-o", str(export_path)], cwd=repo, env=env)
        assert export_result.returncode == 0, export_result.stderr

        issue_data = _get_issue_from_jsonl(export_path, issue1_id)

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

        assert created_at != zero_timestamp, "Dependency created_at should not be zero timestamp"
        assert created_at != "", "Dependency created_at should not be empty"

        # Verify it's a valid parseable timestamp (not a sentinel/zero value)
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            assert parsed.year >= 2020, f"Timestamp {created_at} has implausible year {parsed.year}"
        except (ValueError, AttributeError) as e:
            pytest.fail(f"Invalid timestamp format {created_at!r}: {e}")

    finally:
        _drop_dolt_db(db_name)
