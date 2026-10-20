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
    monkeypatch.setattr(guards, "inventory_stale", lambda: False)
    calls = _record(monkeypatch, guards, failing={"lint:query-coercion"})
    assert guards.run("origin/main") == 1
    assert [c[-1] for c in calls] == [script for _, script in guards.STEPS]
    assert "FAILED: lint:query-coercion" in capsys.readouterr().err


@pytest.mark.unit
def test_stale_inventory_fails_and_skip_flag_bypasses_it(monkeypatch, guards, tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(guards, "changed_paths", lambda base: ["frontend/src/a.tsx"])
    monkeypatch.setattr(guards, "inventory_stale", lambda: True)
    _record(monkeypatch, guards)
    assert guards.run("origin/main") == 1
    assert guards.run("origin/main", skip_inventory=True) == 0


@pytest.mark.unit
def test_assume_changed_skips_the_diff(monkeypatch, guards, tmp_path) -> None:
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(guards, "changed_paths", lambda base: pytest.fail("diffed"))
    monkeypatch.setattr(guards, "inventory_stale", lambda: False)
    calls = _record(monkeypatch, guards)
    assert guards.run("origin/main", assume_changed=True) == 0
    assert len(calls) == len(guards.STEPS)


@pytest.mark.unit
def test_inventory_check_never_writes_the_file() -> None:
    spec = importlib.util.spec_from_file_location(
        "frontend_guards", ROOT / "scripts/frontend_guards.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = ROOT / "frontend/COPY_INVENTORY.md"
    before = path.stat().st_mtime_ns
    assert module.inventory_stale() is False
    assert path.stat().st_mtime_ns == before


@pytest.mark.unit
def test_pre_push_plan_includes_the_runner_only_for_frontend_paths() -> None:
    spec = importlib.util.spec_from_file_location("pre_push", ROOT / "scripts/pre_push.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    with_fe = dict(driver.guard_plan(["frontend/src/a.tsx"]))
    without = dict(driver.guard_plan(["tests/test_x.py"]))
    assert "frontend-guards" in with_fe and "frontend-guards" not in without


@pytest.mark.unit
def test_changed_paths_sees_committed_modified_and_untracked_files(
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
    git("add", ".")
    git("commit", "-q", "-m", "base")
    (repo / "a.txt").write_text("b")
    (repo / "frontend").mkdir()
    (repo / "frontend/new.tsx").write_text("x")
    monkeypatch.setattr(guards, "REPO_ROOT", repo)
    assert sorted(guards.changed_paths("HEAD")) == ["a.txt", "frontend/new.tsx"]
