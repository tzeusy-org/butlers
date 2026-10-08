"""Contract tests for the PR affected-test lane's mode decision (bu-v28ho)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ci_test_plan  # noqa: E402

from butlers.testing.scoped_runner import ScopedTestPlan  # noqa: E402

pytestmark = pytest.mark.unit


def _plan(scope: str, test_paths: list[str] | None = None) -> ScopedTestPlan:
    return ScopedTestPlan(scope=scope, test_paths=test_paths or [], reason="fixture")


def test_decide_mode_keeps_a_clean_scoped_plan_scoped() -> None:
    assert ci_test_plan.decide_mode(_plan("scoped", ["tests/api/test_foo.py"])) == "scoped"
    for paths in (
        ["tests/api/"],
        ["roster/relationship/tests/"],
        ["./tests/api/test_foo.py"],
        ["tests/e2e_extra/test_foo.py"],
    ):
        assert ci_test_plan.decide_mode(_plan("scoped", list(paths))) == "scoped"


@pytest.mark.parametrize("scope", ["full", "none"])
def test_decide_mode_fails_closed_to_full_on_escalation_or_empty_plan(scope: str) -> None:
    assert ci_test_plan.decide_mode(_plan(scope)) == "full"
    # Selected scope, including a deleted-file ancestor, is the admission boundary.
    for paths in (
        ["tests/"],
        ["tests"],
        ["roster/"],
        ["tests/e2e/"],
        ["tests/e2e/test_foo.py"],
        [],
        ["unknown/test_foo.py"],
        ["/tests/api/test_foo.py"],
        ["tests/api/../e2e/test_foo.py"],
        ["."],
        ["tests_extra/test_foo.py"],
    ):
        assert ci_test_plan.decide_mode(_plan("scoped", list(paths))) == "full"


def test_ci_fallback_allowlist_widens_the_library_default_with_tests_e2e() -> None:
    assert "tests/e2e/" not in ci_test_plan.FULL_SUITE_FALLBACK_ALLOWLIST
    assert "tests/e2e/" in ci_test_plan.CI_FALLBACK_ALLOWLIST
    assert set(ci_test_plan.FULL_SUITE_FALLBACK_ALLOWLIST) < set(ci_test_plan.CI_FALLBACK_ALLOWLIST)


def test_write_github_output_is_a_noop_without_the_github_output_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)

    ci_test_plan.write_github_output(mode="scoped", test_paths=["tests/api/test_foo.py"])

    assert not output_file.exists()


def test_write_github_output_writes_to_github_output_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    ci_test_plan.write_github_output(mode="scoped", test_paths=["tests/api/test_foo.py"])

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mode=scoped"
    assert json.loads(lines[1].removeprefix("test_paths=")) == ["tests/api/test_foo.py"]


def test_main_writes_full_mode_with_empty_test_paths_on_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output_file = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setattr(
        ci_test_plan,
        "plan_scoped_tests",
        lambda *_args, **_kwargs: _plan("full", ["tests/", "roster/"]),
    )

    assert ci_test_plan.main(["--base", "origin/main"]) == 0

    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mode=full"
    assert json.loads(lines[1].removeprefix("test_paths=")) == []
    assert "[CI DECISION] mode=full" in capsys.readouterr().out
    output_file.unlink()
    monkeypatch.setattr(
        ci_test_plan, "plan_scoped_tests", lambda *_args, **_kwargs: _plan("scoped", ["tests/"])
    )
    assert ci_test_plan.main(["--base", "origin/main"]) == 0
    lines = output_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "mode=full"
    assert json.loads(lines[1].removeprefix("test_paths=")) == []
    assert "[CI DECISION] mode=full" in capsys.readouterr().out


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_IMPORT_RE = re.compile(r"^\s*(?:from|import) tests\.", re.MULTILINE)


def test_scoped_roster_only_run_collects_roster_tests_that_import_tests(tmp_path: Path) -> None:
    """bu-3fdzpf: the check-affected lane runs ``uv run pytest <roster paths>``.

    Roster tests import shared helpers from ``tests.*``. Invoke the installed
    ``pytest`` console script, as ``uv run pytest`` does, so the working
    directory is not put on ``sys.path`` the way ``python -m pytest`` would.
    Without ``pythonpath = ["."]`` in the pytest config this fails collection
    with ``ModuleNotFoundError: No module named 'tests'``.
    """
    roster_files = sorted(
        str(path.relative_to(_REPO_ROOT))
        for path in (_REPO_ROOT / "roster").glob("*/tests/**/test_*.py")
        if _TESTS_IMPORT_RE.search(path.read_text(encoding="utf-8"))
    )
    assert roster_files, "expected roster tests that import tests.* helpers"
    pytest_script = Path(sys.executable).with_name("pytest")
    result = subprocess.run(
        [str(pytest_script), *roster_files, "--co", "-q", "-n", "0", "-p", "no:cacheprovider"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    assert " error" not in result.stdout.splitlines()[-1]
