"""scripts/frontend_guards.py runs only for frontend changes and never passes silently."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def guards(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "frontend_guards", ROOT / "scripts/frontend_guards.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "FRONTEND", tmp_path)
    return module


def _record(monkeypatch, guards, failing=()):
    calls: list[list[str]] = []

    def fake_run(command, cwd=None, **_):
        calls.append(command)
        return SimpleNamespace(returncode=1 if command[-1] in failing else 0)

    monkeypatch.setattr(guards.subprocess, "run", fake_run)
    return calls


@pytest.mark.unit
def test_no_frontend_change_runs_nothing(monkeypatch, guards) -> None:
    monkeypatch.setattr(guards, "changed_paths", lambda base: ["src/x.py", "docs/a.md"])
    calls = _record(monkeypatch, guards)
    assert guards.run("origin/main") == 0
    assert calls == []


@pytest.mark.unit
def test_frontend_change_without_node_modules_fails_loudly(monkeypatch, guards, capsys) -> None:
    monkeypatch.setattr(guards, "changed_paths", lambda base: ["frontend/src/a.tsx"])
    calls = _record(monkeypatch, guards)
    assert guards.run("origin/main") == 1
    err = capsys.readouterr().err
    assert "frontend guard steps not run:" in err and "lint:query-coercion" in err
    assert calls == []


@pytest.mark.unit
def test_every_step_runs_and_a_failure_does_not_hide_the_others(
    monkeypatch, guards, tmp_path, capsys
) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(guards, "changed_paths", lambda base: ["frontend/src/a.tsx"])
    calls = _record(monkeypatch, guards, failing={"lint:query-coercion"})
    assert guards.run("origin/main") == 1
    assert [c[-1] for c in calls] == [script for _, script in guards.STEPS]
    assert "FAILED: lint:query-coercion" in capsys.readouterr().err


@pytest.mark.unit
def test_frontend_guards_ignore_absent_or_stale_inventory(monkeypatch, guards, tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(guards, "changed_paths", lambda base: ["frontend/src/a.tsx"])
    inventory = tmp_path / "COPY_INVENTORY.md"
    for contents in (None, "stale\n"):
        if contents is not None:
            inventory.write_text(contents)
        calls = _record(monkeypatch, guards)
        assert guards.run("origin/main") == 0
        assert [c[-1] for c in calls] == [script for _, script in guards.STEPS]
        if contents is None:
            assert not inventory.exists()
        else:
            assert inventory.read_text() == contents


@pytest.mark.unit
def test_assume_changed_skips_the_diff(monkeypatch, guards, tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(guards, "changed_paths", lambda base: pytest.fail("diffed"))
    calls = _record(monkeypatch, guards)
    assert guards.run("origin/main", assume_changed=True) == 0
    assert len(calls) == len(guards.STEPS)


@pytest.mark.unit
def test_changed_paths_sees_branch_commits_edits_and_untracked_not_base_moves(
    monkeypatch, guards, tmp_path
) -> None:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a")
    (repo / "frontend").mkdir()
    (repo / "frontend/main.tsx").write_text("m")
    git("add", ".")
    git("commit", "-q", "-m", "fork point")
    git("checkout", "-q", "-b", "feature")
    (repo / "branch.py").write_text("# Python-only branch\n")
    git("add", ".")
    git("commit", "-q", "-m", "Python-only branch commit")
    git("checkout", "-q", "main")
    (repo / "frontend/main.tsx").write_text("main moved")
    git("commit", "-q", "-am", "main moves frontend")
    git("checkout", "-q", "feature")
    monkeypatch.setattr(guards, "REPO_ROOT", repo)
    assert guards.changed_paths("main") == ["branch.py"]
    # No tools exist in FRONTEND: inheriting main's edit would fail this real run.
    assert guards.run("main") == 0
    (repo / "frontend/committed.tsx").write_text("c")
    git("add", ".")
    git("commit", "-q", "-m", "frontend branch commit")
    (repo / "frontend/staged.tsx").write_text("s")
    git("add", "frontend/staged.tsx")
    (repo / "a.txt").write_text("b")
    (repo / "frontend/new.tsx").write_text("x")
    assert sorted(guards.changed_paths("main")) == [
        "a.txt",
        "branch.py",
        "frontend/committed.tsx",
        "frontend/new.tsx",
        "frontend/staged.tsx",
    ]


@pytest.mark.unit
def test_a_file_moved_out_of_frontend_still_counts_as_a_frontend_change(
    monkeypatch, guards, tmp_path
) -> None:
    import subprocess

    repo = tmp_path / "repo"
    (repo / "frontend").mkdir(parents=True)

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (repo / "frontend/widget.tsx").write_text("w" * 200)
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "feature")
    git("mv", "frontend/widget.tsx", "widget.tsx")
    git("commit", "-q", "-m", "move out")
    monkeypatch.setattr(guards, "REPO_ROOT", repo)
    assert guards.frontend_changed(guards.changed_paths("main")) is True


@pytest.mark.unit
def test_pre_push_plan_runs_the_runner_only_for_frontend_paths() -> None:
    # The ledger refuses an extension of the retained test_pre_push.py plan case, so this
    # lives here (bu-5n9pfa).
    spec = importlib.util.spec_from_file_location("pre_push", ROOT / "scripts/pre_push.py")
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    names = [name for name, _ in driver.guard_plan(["tests/test_x.py"])]
    assert "frontend-guards" not in names
    command = dict(driver.guard_plan(["frontend/src/a.tsx"]))["frontend-guards"]
    assert command[1:] == ["scripts/frontend_guards.py", "--assume-changed"]


@pytest.mark.unit
def test_pre_push_branch_paths_ignore_base_moves_and_keep_moves_out_of_frontend(
    tmp_path,
) -> None:
    import subprocess

    spec = importlib.util.spec_from_file_location("pre_push", ROOT / "scripts/pre_push.py")
    assert spec is not None and spec.loader is not None
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    repo = tmp_path / "repo"
    (repo / "frontend").mkdir(parents=True)

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q", "-b", "main")
    (repo / "frontend/widget.tsx").write_text("w" * 200)
    (repo / "frontend/main.tsx").write_text("m")
    git("add", ".")
    git("commit", "-q", "-m", "fork point")
    git("checkout", "-q", "-b", "feature")
    (repo / "branch.py").write_text("# Python-only branch\n")
    git("add", ".")
    git("commit", "-q", "-m", "Python branch commit")
    git("checkout", "-q", "main")
    (repo / "frontend/main.tsx").write_text("main moved")
    git("commit", "-q", "-am", "main moves frontend")
    git("checkout", "-q", "feature")
    main_oid = driver.git(repo, "rev-parse", "main").decode().strip()
    head = driver.git(repo, "rev-parse", "HEAD").decode().strip()
    paths = driver.branch_paths(repo, main_oid, head)
    assert paths == ["branch.py"]
    assert "frontend-guards" not in dict(driver.guard_plan(paths))
    git("mv", "frontend/widget.tsx", "widget.tsx")
    git("commit", "-q", "-m", "move out")
    head = driver.git(repo, "rev-parse", "HEAD").decode().strip()
    assert sorted(driver.branch_paths(repo, main_oid, head)) == [
        "branch.py",
        "frontend/widget.tsx",
        "widget.tsx",
    ]
