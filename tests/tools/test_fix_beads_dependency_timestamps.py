"""Test the fix_beads_dependency_timestamps script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


ZERO_TIMESTAMP = "0001-01-01T00:00:00Z"


def test_fix_script_detects_and_fixes_zero_timestamps(tmp_path: Path) -> None:
    """Test that the fix script correctly identifies and repairs zero timestamps."""
    # Create a test issues.jsonl with zero timestamps
    issues_data = [
        {
            "id": "test-1",
            "title": "Issue with good timestamp",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": "2026-02-14T10:00:00Z",
            "updated_at": "2026-02-14T11:00:00Z",
            "dependencies": [
                {
                    "issue_id": "test-1",
                    "depends_on_id": "test-2",
                    "type": "blocks",
                    "created_at": "2026-02-14T10:30:00Z",
                }
            ],
        },
        {
            "id": "test-2",
            "title": "Issue with zero timestamp dependency",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": "2026-02-14T10:00:00Z",
            "updated_at": "2026-02-14T12:00:00Z",
            "dependencies": [
                {
                    "issue_id": "test-2",
                    "depends_on_id": "test-3",
                    "type": "blocks",
                    "created_at": ZERO_TIMESTAMP,  # Bug: zero timestamp
                }
            ],
        },
        {
            "id": "test-3",
            "title": "Issue with multiple zero timestamps",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": "2026-02-14T10:00:00Z",
            "updated_at": "2026-02-14T13:00:00Z",
            "dependencies": [
                {
                    "issue_id": "test-3",
                    "depends_on_id": "test-1",
                    "type": "blocks",
                    "created_at": ZERO_TIMESTAMP,
                },
                {
                    "issue_id": "test-3",
                    "depends_on_id": "test-2",
                    "type": "blocks",
                    "created_at": ZERO_TIMESTAMP,
                },
            ],
        },
    ]

    # Write test data
    jsonl_path = tmp_path / "issues.jsonl"
    lines = [json.dumps(issue, separators=(",", ":")) for issue in issues_data]
    jsonl_path.write_text("\n".join(lines) + "\n")

    # Run the fix script
    script_path = Path(__file__).parents[2] / "scripts" / "fix_beads_dependency_timestamps.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--jsonl-path", str(jsonl_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, f"Script failed: {result.stderr}"

    # Verify the output mentions the fixes
    assert "test-2" in result.stdout
    assert "test-3" in result.stdout
    assert "fixed 3 dependencies" in result.stdout

    # Load the fixed data and verify
    fixed_issues = []
    for line in jsonl_path.read_text().splitlines():
        if line.strip():
            fixed_issues.append(json.loads(line))

    # test-1 should be unchanged
    test1 = next(i for i in fixed_issues if i["id"] == "test-1")
    assert test1["dependencies"][0]["created_at"] == "2026-02-14T10:30:00Z"

    # test-2 should have its zero timestamp fixed to updated_at
    test2 = next(i for i in fixed_issues if i["id"] == "test-2")
    assert test2["dependencies"][0]["created_at"] == "2026-02-14T12:00:00Z"

    # test-3 should have both zero timestamps fixed to updated_at
    test3 = next(i for i in fixed_issues if i["id"] == "test-3")
    assert test3["dependencies"][0]["created_at"] == "2026-02-14T13:00:00Z"
    assert test3["dependencies"][1]["created_at"] == "2026-02-14T13:00:00Z"


def test_fix_script_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    """Test that --dry-run mode doesn't modify the file."""
    issues_data = [
        {
            "id": "test-1",
            "title": "Issue with zero timestamp",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": "2026-02-14T10:00:00Z",
            "updated_at": "2026-02-14T11:00:00Z",
            "dependencies": [
                {
                    "issue_id": "test-1",
                    "depends_on_id": "test-2",
                    "type": "blocks",
                    "created_at": ZERO_TIMESTAMP,
                }
            ],
        }
    ]

    jsonl_path = tmp_path / "issues.jsonl"
    original_content = (
        "\n".join(json.dumps(issue, separators=(",", ":")) for issue in issues_data) + "\n"
    )
    jsonl_path.write_text(original_content)

    # Run in dry-run mode
    script_path = Path(__file__).parents[2] / "scripts" / "fix_beads_dependency_timestamps.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script_path),
            "--dry-run",
            "--jsonl-path",
            str(jsonl_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert "would fix 1 dependencies" in result.stdout

    # Verify file was not modified
    assert jsonl_path.read_text() == original_content


def test_fix_script_handles_no_zero_timestamps(tmp_path: Path) -> None:
    """Test that the script handles files with no zero timestamps gracefully."""
    issues_data = [
        {
            "id": "test-1",
            "title": "Clean issue",
            "status": "open",
            "priority": 2,
            "issue_type": "task",
            "created_at": "2026-02-14T10:00:00Z",
            "updated_at": "2026-02-14T11:00:00Z",
            "dependencies": [
                {
                    "issue_id": "test-1",
                    "depends_on_id": "test-2",
                    "type": "blocks",
                    "created_at": "2026-02-14T10:30:00Z",
                }
            ],
        }
    ]

    jsonl_path = tmp_path / "issues.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(issue, separators=(",", ":")) for issue in issues_data) + "\n"
    )

    script_path = Path(__file__).parents[2] / "scripts" / "fix_beads_dependency_timestamps.py"
    result = subprocess.run(
        [sys.executable, str(script_path), "--jsonl-path", str(jsonl_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "No zero-timestamp dependencies found" in result.stdout
    assert "fixed 0 dependencies" in result.stdout
