"""Contract tests for the PR affected-test lane's mode decision (bu-v28ho)."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("scope", ["full", "none"])
def test_decide_mode_fails_closed_to_full_on_escalation_or_empty_plan(scope: str) -> None:
    assert ci_test_plan.decide_mode(_plan(scope)) == "full"


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
