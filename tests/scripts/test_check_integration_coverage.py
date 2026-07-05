"""Tests for scripts/check_integration_coverage.py.

Regression guard for bu-m8cmk: the CI "Integration tests (testcontainers)"
step used to pass an enumerated path list that silently missed
`pytest.mark.integration` tests living outside those directories (32 files /
193 tests at the time this was caught). These tests cover the guard script's
own logic — the parsing of the step's `run:` command, and the main()
orchestration that decides pass/fail — using fast, deterministic inputs
rather than real subprocess pytest collection (that end-to-end behavior is
exercised by actually running `make check-integration-coverage` in CI/locally).
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import check_integration_coverage as cic  # noqa: E402

pytestmark = pytest.mark.unit


def test_parse_pytest_paths_and_marker_extracts_paths_and_marker() -> None:
    run_command = (
        "uv run pytest roster/ tests/integration/ tests/config/ -q --maxfail=5 "
        '--tb=short -m "integration and not nightly" -n auto --dist loadfile'
    )
    paths, marker = cic._parse_pytest_paths_and_marker(run_command)
    assert paths == ["roster/", "tests/integration/", "tests/config/"]
    assert marker == "integration and not nightly"


def test_parse_pytest_paths_and_marker_handles_single_path_and_marker() -> None:
    run_command = 'uv run pytest tests/ -q -m "integration"'
    paths, marker = cic._parse_pytest_paths_and_marker(run_command)
    assert paths == ["tests/"]
    assert marker == "integration"


def test_parse_pytest_paths_and_marker_raises_when_no_pytest_token() -> None:
    with pytest.raises(ValueError, match="No 'pytest' invocation"):
        cic._parse_pytest_paths_and_marker("uv run ruff check src/")


def test_parse_pytest_paths_and_marker_raises_when_no_paths() -> None:
    with pytest.raises(ValueError, match="No path arguments"):
        cic._parse_pytest_paths_and_marker('uv run pytest -m "integration"')


def test_parse_pytest_paths_and_marker_raises_when_no_marker() -> None:
    with pytest.raises(ValueError, match="No '-m <marker>'"):
        cic._parse_pytest_paths_and_marker("uv run pytest tests/ -q --maxfail=1")


def test_load_step_run_command_finds_step_in_real_ci_workflow() -> None:
    # Uses the actual checked-in workflow file — no subprocess, just a YAML
    # parse — so this both documents and enforces that the step name this
    # script depends on ("Integration tests (testcontainers)") still exists.
    run_command = cic._load_step_run_command()
    assert "pytest" in run_command
    assert "-m" in run_command


def test_load_step_run_command_raises_for_missing_step(tmp_path: Path) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        textwrap.dedent(
            """
            jobs:
              check:
                steps:
                  - name: Some other step
                    run: echo hello
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Could not find a step named"):
        cic._load_step_run_command(workflow_path=workflow)


def test_load_step_run_command_raises_when_run_is_not_a_scalar(tmp_path: Path) -> None:
    workflow = tmp_path / "ci.yml"
    workflow.write_text(
        textwrap.dedent(
            f"""
            jobs:
              check:
                steps:
                  - name: {cic.STEP_NAME}
                    uses: some/action@v1
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no scalar 'run:' command"):
        cic._load_step_run_command(workflow_path=workflow)


def test_main_passes_when_job_collection_matches_full_collection(monkeypatch) -> None:
    monkeypatch.setattr(
        cic, "_load_step_run_command", lambda: 'uv run pytest tests/ roster/ -m "integration"'
    )
    calls = []

    def fake_collect(*, paths, marker):
        calls.append((tuple(paths), marker))
        return {"tests/a.py::test_1", "tests/b.py::test_2"}

    monkeypatch.setattr(cic, "_collect_node_ids", fake_collect)
    assert cic.main() == 0
    # Job-scoped collection, then full-repo (paths=[]) ground-truth collection.
    assert calls == [
        (("tests/", "roster/"), "integration"),
        ((), "integration"),
    ]


def test_main_fails_when_job_collection_misses_tests(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cic,
        "_load_step_run_command",
        lambda: 'uv run pytest tests/integration/ -m "integration"',
    )

    def fake_collect(*, paths, marker):
        if paths == ["tests/integration/"]:
            return {"tests/integration/test_a.py::test_1"}
        return {
            "tests/integration/test_a.py::test_1",
            "tests/modules/test_b.py::test_2",
        }

    monkeypatch.setattr(cic, "_collect_node_ids", fake_collect)
    assert cic.main() == 1
    captured = capsys.readouterr()
    assert "tests/modules/test_b.py" in captured.err


def test_main_returns_2_when_step_cannot_be_parsed(monkeypatch) -> None:
    def raise_value_error():
        raise ValueError("boom")

    monkeypatch.setattr(cic, "_load_step_run_command", raise_value_error)
    assert cic.main() == 2
