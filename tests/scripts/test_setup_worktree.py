"""Regression coverage for the worktree cache setup helper."""

from __future__ import annotations

import os
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


def _init_repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Seed a git repo plus a linked worktree, both carrying the setup script."""
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
    (repo / "frontend").mkdir()
    (repo / "frontend" / "package.json").write_text("{}\n", encoding="utf-8")

    subprocess.run(
        [
            "git",
            "add",
            ".gitignore",
            "README.md",
            "scripts/setup_worktree.sh",
            "frontend/package.json",
        ],
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
    return repo, worktree


def _fake_npm_bin(tmp_path: Path) -> Path:
    """A stand-in `npm` that simulates `npm install` without touching the network."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    fake_npm = bin_dir / "npm"
    fake_npm.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "install" ]; then\n'
        "  mkdir -p node_modules\n"
        "  echo installed > node_modules/.fake-installed\n"
        "  exit 0\n"
        "fi\n"
        'echo "unexpected npm invocation: $*" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)
    return bin_dir


def test_setup_worktree_falls_back_to_local_install_when_main_cache_is_empty(
    tmp_path: Path,
) -> None:
    """An empty (e.g. broken root-owned) main cache must not become a dead symlink (bu-87osw)."""
    repo, worktree = _init_repo_with_worktree(tmp_path)

    empty_cache = repo / "frontend" / "node_modules"
    empty_cache.mkdir(parents=True)

    fake_bin = _fake_npm_bin(tmp_path)
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}

    completed = subprocess.run(
        ["bash", str(worktree / "scripts" / "setup_worktree.sh")],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    worker_cache = worktree / "frontend" / "node_modules"
    assert completed.returncode == 0, completed.stderr
    assert "empty" in completed.stderr
    assert "Falling back" in completed.stderr
    assert worker_cache.is_dir()
    assert not worker_cache.is_symlink()
    assert (worker_cache / ".fake-installed").exists()


def test_setup_worktree_fails_loudly_when_no_fallback_is_available(tmp_path: Path) -> None:
    """With an empty main cache and no npm on PATH, fail loudly instead of linking to nothing."""
    repo, worktree = _init_repo_with_worktree(tmp_path)

    empty_cache = repo / "frontend" / "node_modules"
    empty_cache.mkdir(parents=True)

    stripped_path = os.pathsep.join(
        part for part in os.environ["PATH"].split(os.pathsep) if not (Path(part) / "npm").exists()
    )
    env = {**os.environ, "PATH": stripped_path}

    completed = subprocess.run(
        ["bash", str(worktree / "scripts" / "setup_worktree.sh")],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    worker_cache = worktree / "frontend" / "node_modules"
    assert completed.returncode != 0
    assert "cannot fall back" in completed.stderr
    assert not worker_cache.exists()
