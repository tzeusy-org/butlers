"""Regression coverage for the worktree cache setup helper."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_SCRIPT = _REPO_ROOT / "scripts" / "setup_worktree.sh"


def test_setup_worktree_rejects_the_main_checkout_without_touching_its_cache(
    tmp_path: Path,
) -> None:
    """A main-checkout invocation must not replace its cache with a self-link."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    script = repo / "scripts" / "setup_worktree.sh"
    script.parent.mkdir()
    shutil.copy2(_SOURCE_SCRIPT, script)

    cache_dir = repo / "frontend" / "node_modules"
    cache_dir.mkdir(parents=True)
    sentinel = cache_dir / "keep"
    sentinel.write_text("must survive\n", encoding="utf-8")

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "linked worktree" in completed.stderr
    assert cache_dir.is_dir()
    assert not cache_dir.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "must survive\n"


def test_setup_worktree_keeps_linked_worktree_cache_setup_available(tmp_path: Path) -> None:
    """The main-checkout guard must not block the helper's intended use."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)

    script = repo / "scripts" / "setup_worktree.sh"
    script.parent.mkdir()
    shutil.copy2(_SOURCE_SCRIPT, script)
    (repo / ".gitignore").write_text("frontend/node_modules\n", encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")

    cache_dir = repo / "frontend" / "node_modules"
    cache_dir.mkdir(parents=True)
    sentinel = cache_dir / "keep"
    sentinel.write_text("main cache\n", encoding="utf-8")

    subprocess.run(
        ["git", "add", ".gitignore", "README.md", "scripts/setup_worktree.sh"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)

    worktree = tmp_path / "worker"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "worker", str(worktree)],
        cwd=repo,
        check=True,
    )

    completed = subprocess.run(
        ["bash", str(worktree / "scripts" / "setup_worktree.sh")],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )

    worker_cache = worktree / "frontend" / "node_modules"
    assert completed.returncode == 0, completed.stderr
    assert worker_cache.is_symlink()
    assert worker_cache.resolve() == cache_dir
    assert (worker_cache / "keep").read_text(encoding="utf-8") == "main cache\n"
