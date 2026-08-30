"""Contract tests for plan-only agent worktree test selection."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from butlers.testing import scoped_runner
from butlers.testing.scoped_runner import ScopedTestPlan, plan_worktree_tests
from butlers.testing.source_test_map import FULL_SUITE

pytestmark = pytest.mark.unit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, relative_path: str, content: str = "x = 1\n") -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "planner-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Butlers test")
    _write(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\ntestpaths = ["tests", "roster"]\n',
    )
    _write(repo, "tests/api/test_existing.py", "def test_existing():\n    assert True\n")
    _write(repo, "roster/health/tests/test_existing.py", "def test_existing():\n    assert True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, base


def test_untracked_test_file_produces_plan_only_exact_scope(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "tests/api/test_new.py", "def test_new():\n    assert True\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/test_new.py"]
    report = plan.report()
    assert "[PLAN ONLY] pytest was not executed" in report
    assert "[SCOPED]" in report
    assert "Running:" not in report


def test_deleted_test_file_widens_to_existing_parent_for_collection(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    (repo / "tests/api/test_existing.py").unlink()

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/"]
    assert "Deleted test path" in plan.reason


def test_renamed_test_deduplicates_the_parent_scope_and_collects(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _git(repo, "mv", "tests/api/test_existing.py", "tests/api/test_renamed.py")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "scoped"
    assert plan.test_paths == ["tests/api/"]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *plan.test_paths, "--collect-only", "-q"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_makefile_change_escalates_instead_of_claiming_no_tests(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(repo, "Makefile", "all:\n\t@true\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "full"
    assert plan.test_paths == FULL_SUITE
    assert "Escalate" in plan.reason


def test_unavailable_base_fails_closed_to_escalation(tmp_path: Path) -> None:
    repo, _ = _repo(tmp_path)

    plan = plan_worktree_tests("does-not-exist", repo_dir=repo)

    assert plan.scope == "full"
    assert plan.test_paths == FULL_SUITE
    assert "unable to compute worktree diff" in plan.reason


def test_full_scope_uses_the_requested_worktree_testpaths(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    _write(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\ntestpaths = ["custom_tests"]\n',
    )
    _write(repo, "custom_tests/test_custom.py", "def test_custom():\n    assert True\n")
    _write(repo, "Makefile", "all:\n\t@true\n")

    plan = plan_worktree_tests(base, repo_dir=repo)

    assert plan.scope == "full"
    assert plan.test_paths == ["custom_tests/"]


def test_cli_main_is_plan_only_and_never_calls_legacy_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = ScopedTestPlan(
        scope="scoped",
        test_paths=["tests/testing/test_scoped_runner.py"],
        changed_files=["tests/testing/test_scoped_runner.py"],
        reason="fixture plan",
    )
    monkeypatch.setattr(scoped_runner, "plan_worktree_tests", lambda **_: plan)

    def _unexpected_run(*_args: object, **_kwargs: object) -> None:
        pytest.fail("plan-only CLI must not execute the legacy runner")

    monkeypatch.setattr(scoped_runner, "run_scoped_tests", _unexpected_run)

    assert scoped_runner.main(["--base", "origin/main"]) == 0
    output = capsys.readouterr().out
    assert "[PLAN ONLY] pytest was not executed" in output
    assert "[SCOPED] fixture plan" in output
