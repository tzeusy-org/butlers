"""Tests for the pytest-split CI lanes and their no-drop total (bu-29wkwj.2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ci_lanes  # noqa: E402


def _complete(**overrides: dict[int, int]) -> dict[str, dict[int, int]]:
    counts = {
        lane: {group: 10 for group in range(1, config.groups + 1)}
        for lane, config in ci_lanes.LANES.items()
    }
    counts.update(overrides)
    return counts


def _expected(counts: dict[str, dict[int, int]]) -> dict[str, int]:
    return {lane: sum(groups.values()) for lane, groups in counts.items()}


def test_groups_that_add_up_pass() -> None:
    counts = _complete()

    assert ci_lanes.compare_totals(counts, _expected(counts)) == []


def test_a_group_that_drops_tests_fails_the_total() -> None:
    counts = _complete()
    expected = _expected(counts)
    counts["unit"][3] -= 1

    errors = ci_lanes.compare_totals(counts, expected)

    assert len(errors) == 1
    assert "unit: groups collected 49 tests, but the lane selects 50" in errors[0]


def test_a_group_that_duplicates_tests_fails_the_total() -> None:
    counts = _complete()
    expected = _expected(counts)
    counts["integration"][1] += 2

    assert ci_lanes.compare_totals(counts, expected) == [
        "::error::integration: groups collected 62 tests, but the lane selects 60; "
        "a group dropped or duplicated tests"
    ]


def test_a_missing_group_fails_even_when_the_rest_add_up() -> None:
    counts = _complete(unit={1: 10, 2: 10, 3: 10, 4: 20})

    errors = ci_lanes.compare_totals(counts, _expected(counts))

    assert errors == ["::error::unit: expected groups 1..5, missing [5], unexpected []"]


def test_group_count_files_are_read_and_duplicates_refused(tmp_path: Path) -> None:
    for name, record in (
        ("a", {"lane": "unit", "group": 1, "collected": 7}),
        ("b", {"lane": "integration", "group": 2, "collected": 3}),
    ):
        (tmp_path / name).mkdir()
        (tmp_path / name / ci_lanes.COUNT_FILE).write_text(json.dumps(record))

    counts = ci_lanes.read_group_counts(tmp_path)
    assert counts["unit"] == {1: 7}
    assert counts["integration"] == {2: 3}

    (tmp_path / "c").mkdir()
    (tmp_path / "c" / ci_lanes.COUNT_FILE).write_text(
        json.dumps({"lane": "unit", "group": 1, "collected": 7})
    )
    with pytest.raises(ValueError, match="reported twice"):
        ci_lanes.read_group_counts(tmp_path)


@pytest.mark.parametrize("collected", [-1, True, "7", None])
def test_malformed_group_counts_are_refused(tmp_path: Path, collected: object) -> None:
    (tmp_path / ci_lanes.COUNT_FILE).write_text(
        json.dumps({"lane": "unit", "group": 1, "collected": collected})
    )

    with pytest.raises(ValueError, match="malformed group count"):
        ci_lanes.read_group_counts(tmp_path)


def test_group_command_selects_its_lane_group_over_the_committed_durations() -> None:
    command = ci_lanes.pytest_command("integration", 4, output=Path("/out"), coverage=False)

    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert command[command.index("-m", 3) + 1] == ci_lanes.LANES["integration"].marker
    assert command[command.index("--splits") + 1] == "6"
    assert command[command.index("--group") + 1] == "4"
    assert command[command.index("--splitting-algorithm") + 1] == "duration_based_chunks"
    assert command[command.index("--durations-path") + 1] == ".test_durations"
    assert command[command.index("-p") + 1] == "ci_group_count"
    assert "--ignore=tests/e2e" not in command
    assert not any(part.startswith("--cov") for part in command)
    with pytest.raises(ValueError, match="between 1 and 6"):
        ci_lanes.pytest_command("integration", 7, output=Path("/out"), coverage=False)


def test_the_count_plugin_records_each_real_pytest_split_group(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text("[pytest]\nmarkers =\n    slow: slow\n")
    for index in range(4):
        (project / f"test_m{index}.py").write_text(
            "import pytest\n"
            "@pytest.mark.parametrize('n', range(3))\n"
            "def test_a(n): pass\n"
            "@pytest.mark.slow\n"
            "def test_slow(): pass\n"
        )
    (project / ".test_durations").write_text(json.dumps({"test_m0.py::test_slow": 5.0}))
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "scripts"),
    }
    totals = []
    timed: dict[str, float] = {}
    for group in (1, 2, 3):
        count_file = tmp_path / f"count-{group}.json"
        durations_file = tmp_path / f"durations-{group}.json"
        environment["CI_GROUP_COUNT_FILE"] = str(count_file)
        environment["CI_GROUP_DURATIONS_FILE"] = str(durations_file)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-p",
                "ci_group_count",
                "-m",
                "not slow",
                "--splits",
                "3",
                "--group",
                str(group),
                "--splitting-algorithm",
                "duration_based_chunks",
                "--durations-path",
                ".test_durations",
            ],
            cwd=project,
            env=environment,
            check=True,
            capture_output=True,
        )
        totals.append(json.loads(count_file.read_text())["collected"])
        group_durations = json.loads(durations_file.read_text())
        assert len(group_durations) == totals[-1]
        assert not set(group_durations) & set(timed)
        timed.update(group_durations)

    assert all(totals)
    assert sum(totals) == 12
    assert all(isinstance(seconds, float) and seconds >= 0 for seconds in timed.values())
    assert "test_m0.py::test_slow" not in timed


def _durations(directory: Path, lane: str, group: int, values: dict[str, float]) -> None:
    target = directory / ci_lanes.evidence_name(lane, group)
    target.mkdir(parents=True)
    (target / ci_lanes.DURATIONS_FILE).write_text(json.dumps(values))


def test_merged_durations_cover_every_group_and_refuse_a_missing_one(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    for lane, config in ci_lanes.LANES.items():
        for group in range(1, config.groups + 1):
            _durations(evidence, lane, group, {f"tests/{lane}_{group}.py::test": group / 10})
    output = tmp_path / ".test_durations"

    ci_lanes.merge_durations(evidence, output)

    merged = json.loads(output.read_text())
    assert merged["tests/unit_5.py::test"] == 0.5
    assert len(merged) == sum(config.groups for config in ci_lanes.LANES.values())
    assert output.read_text() == json.dumps(merged, sort_keys=True, indent=4) + "\n"

    (evidence / "ci-integration-6-test-evidence" / ci_lanes.DURATIONS_FILE).unlink()
    with pytest.raises(ValueError, match=r"integration: no durations for groups \[6\]"):
        ci_lanes.merge_durations(evidence, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
