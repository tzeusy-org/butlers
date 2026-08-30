"""Contract tests for dirty-worktree file discovery."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from butlers.testing.changed_files import get_changed_files, get_worktree_changed_files

pytestmark = pytest.mark.unit


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative_path: str, content: str = "x = 1\n") -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _initialised_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Butlers test")
    _write(repo, ".gitignore", "ignored.py\n")
    _write(repo, "src/original.py")
    _write(repo, "tests/api/test_deleted.py", "def test_deleted():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_worktree_detection_unions_branch_index_worktree_and_untracked(tmp_path: Path) -> None:
    repo, base = _initialised_repo(tmp_path)
    _write(repo, "src/committed.py")
    _git(repo, "add", "src/committed.py")
    _git(repo, "commit", "-qm", "branch change")

    _write(repo, "src/staged.py")
    _git(repo, "add", "src/staged.py")
    _write(repo, "src/original.py", "x = 2\n")
    _write(repo, "src/untracked.py")
    _write(repo, "src/with space.py")
    _write(repo, "ignored.py")

    changed = get_worktree_changed_files(base, repo_dir=repo)

    assert changed.files == [
        "src/committed.py",
        "src/original.py",
        "src/staged.py",
        "src/untracked.py",
        "src/with space.py",
    ]
    assert changed.sources == ("branch", "staged", "unstaged", "untracked")


def test_worktree_detection_reports_both_sides_of_a_rename(tmp_path: Path) -> None:
    repo, base = _initialised_repo(tmp_path)
    _git(repo, "mv", "src/original.py", "src/renamed.py")

    changed = get_worktree_changed_files(base, repo_dir=repo)

    assert changed.files == ["src/original.py", "src/renamed.py"]
    assert changed.sources == ("staged",)


def test_branch_detection_keeps_refinery_api_and_space_safe_paths(tmp_path: Path) -> None:
    repo, base = _initialised_repo(tmp_path)
    _write(repo, "src/with space.py")
    _git(repo, "add", "src/with space.py")
    _git(repo, "commit", "-qm", "space path")

    changed = get_changed_files("HEAD", base, repo_dir=repo)

    assert changed.files == ["src/with space.py"]
    assert changed.sources == ("branch",)
