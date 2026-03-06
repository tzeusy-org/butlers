"""Tests for diff-based changed-file detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from butlers.testing.changed_files import ChangedFiles, get_changed_files


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit on main."""
    run = lambda *args: subprocess.run(  # noqa: E731
        args, cwd=tmp_path, capture_output=True, text=True, check=True
    )
    run("git", "init", "-b", "main")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    # Initial commit on main
    (tmp_path / "existing.py").write_text("# existing\n")
    run("git", "add", ".")
    run("git", "commit", "-m", "initial")
    return tmp_path


def _run(repo: Path, *args: str) -> None:
    subprocess.run(args, cwd=repo, capture_output=True, text=True, check=True)


class TestGetChangedFiles:
    def test_added_file(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/add")
        (git_repo / "new.py").write_text("# new\n")
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "add new file")

        result = get_changed_files("feature/add", base="main", repo_dir=git_repo)
        assert result.files == ["new.py"]
        assert result.base_ref == "main"
        assert result.head_ref == "feature/add"

    def test_modified_file(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/mod")
        (git_repo / "existing.py").write_text("# modified\n")
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "modify existing")

        result = get_changed_files("feature/mod", base="main", repo_dir=git_repo)
        assert result.files == ["existing.py"]

    def test_deleted_file(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/del")
        (git_repo / "existing.py").unlink()
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "delete file")

        result = get_changed_files("feature/del", base="main", repo_dir=git_repo)
        assert result.files == ["existing.py"]

    def test_renamed_file_shows_both_paths(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/rename")
        _run(git_repo, "git", "mv", "existing.py", "renamed.py")
        _run(git_repo, "git", "commit", "-m", "rename file")

        result = get_changed_files("feature/rename", base="main", repo_dir=git_repo)
        # --no-renames decomposes rename into delete + add
        assert "existing.py" in result.files
        assert "renamed.py" in result.files

    def test_multiple_changes_sorted_deduped(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/multi")
        (git_repo / "b.py").write_text("# b\n")
        (git_repo / "a.py").write_text("# a\n")
        (git_repo / "existing.py").write_text("# changed\n")
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "multiple changes")

        result = get_changed_files("feature/multi", base="main", repo_dir=git_repo)
        assert result.files == ["a.py", "b.py", "existing.py"]

    def test_merge_commit_on_branch(self, git_repo: Path) -> None:
        """Merge commits on the branch should not inflate the file list."""
        # Create a second commit on main
        (git_repo / "main_only.py").write_text("# main\n")
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "main commit 2")

        # Branch from the first commit, add a file, merge main in
        _run(git_repo, "git", "checkout", "-b", "feature/merge", "HEAD~1")
        (git_repo / "branch.py").write_text("# branch\n")
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "branch work")
        _run(git_repo, "git", "merge", "main", "--no-edit")

        result = get_changed_files("feature/merge", base="main", repo_dir=git_repo)
        # Only branch.py was changed by the branch; main_only.py is on main
        assert result.files == ["branch.py"]

    def test_no_changes(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/noop")

        result = get_changed_files("feature/noop", base="main", repo_dir=git_repo)
        assert result.files == []

    def test_invalid_branch_raises(self, git_repo: Path) -> None:
        with pytest.raises(RuntimeError, match="git diff failed"):
            get_changed_files("nonexistent/branch", base="main", repo_dir=git_repo)

    def test_subdirectory_paths(self, git_repo: Path) -> None:
        _run(git_repo, "git", "checkout", "-b", "feature/subdir")
        (git_repo / "src").mkdir()
        (git_repo / "src" / "mod.py").write_text("# mod\n")
        _run(git_repo, "git", "add", ".")
        _run(git_repo, "git", "commit", "-m", "add subdir file")

        result = get_changed_files("feature/subdir", base="main", repo_dir=git_repo)
        assert result.files == ["src/mod.py"]

    def test_dataclass_is_frozen(self) -> None:
        cf = ChangedFiles(files=["a.py"], base_ref="main", head_ref="feat")
        with pytest.raises(AttributeError):
            cf.files = []  # type: ignore[misc]
