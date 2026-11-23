"""Tests for the pytest-split CI lanes and their no-drop total (bu-29wkwj.2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

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
    assert "ci_hang_evidence" in command
    # Skip reasons reach the log, so a real-PG node that silently skips is visible.
    assert "-rs" in command
    assert "--ignore=tests/e2e" not in command
    assert not any(part.startswith("--cov") for part in command)
    with pytest.raises(ValueError, match="between 1 and 6"):
        ci_lanes.pytest_command("integration", 7, output=Path("/out"), coverage=False)


def test_only_the_integration_lane_requires_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    # The root conftest fails a "Docker not available" skip under this flag.
    monkeypatch.delenv(ci_lanes.REQUIRE_DOCKER_ENV, raising=False)
    paths = {"output": Path("/out"), "count_path": Path("/out/collected.json")}

    assert ci_lanes.lane_environment("integration", **paths)[ci_lanes.REQUIRE_DOCKER_ENV] == "1"
    assert ci_lanes.REQUIRE_DOCKER_ENV not in ci_lanes.lane_environment("unit", **paths)


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


def test_early_count_writes_lane_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ci_lanes, "fresh_lane_counts", lambda repo_root: {"unit": 50, "integration": 60}
    )
    output = tmp_path / "counts" / "lane-counts.json"

    assert ci_lanes.count(output) == 0
    assert json.loads(output.read_text()) == {"integration": 60, "unit": 50}


def _write_groups(directory: Path, counts: dict[str, dict[int, int]]) -> None:
    for lane, groups in counts.items():
        for group, collected in groups.items():
            path = directory / ci_lanes.evidence_name(lane, group) / ci_lanes.COUNT_FILE
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"lane": lane, "group": group, "collected": collected}))


def test_totals_against_the_early_count_needs_no_fresh_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(repo_root: Path) -> dict[str, int]:
        raise AssertionError("totals --expected must not collect again")

    monkeypatch.setattr(ci_lanes, "fresh_lane_counts", refuse)
    counts = _complete()
    _write_groups(tmp_path / "groups", counts)
    expected = tmp_path / "lane-counts.json"
    expected.write_text(json.dumps(_expected(counts)))

    assert ci_lanes.totals(tmp_path / "groups", expected_path=expected) == 0

    expected.write_text(json.dumps({**_expected(counts), "unit": _expected(counts)["unit"] + 1}))
    assert ci_lanes.totals(tmp_path / "groups", expected_path=expected) == 1


@pytest.mark.parametrize(
    "record",
    [{"unit": 1}, {"unit": 1, "integration": "2"}, {"unit": True, "integration": 2}, [1, 2]],
)
def test_malformed_early_counts_are_refused(tmp_path: Path, record: object) -> None:
    path = tmp_path / "lane-counts.json"
    path.write_text(json.dumps(record))

    with pytest.raises(ValueError, match="malformed lane counts"):
        ci_lanes.read_lane_counts(path)


def test_totals_against_the_early_count_runs_on_the_standard_library_alone(
    tmp_path: Path,
) -> None:
    counts = _complete()
    _write_groups(tmp_path / "groups", counts)
    expected = tmp_path / "lane-counts.json"
    expected.write_text(json.dumps(_expected(counts)))

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(REPO_ROOT / "scripts/ci_lanes.py"),
            "totals",
            "--counts",
            str(tmp_path / "groups"),
            "--expected",
            str(expected),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


_WEDGED_IN_THE_TEST = """
import threading

def test_wedged():
    threading.Event().wait()
"""
_WEDGED_IN_SESSION_TEARDOWN = """
import threading
import pytest

@pytest.fixture(scope="session")
def wedge():
    yield
    threading.Event().wait()

def test_passes(wedge):
    pass
"""


def _assert_ends(pid: int) -> None:
    """A killed orphan stays in /proc as a zombie until init reaps it, so allow for that."""
    stat = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if stat.read_text().rpartition(")")[2].split()[0] == "Z":
                return
        except OSError:
            return
        time.sleep(0.1)
    raise AssertionError(f"process {pid} is still running")


def _nested_pytest(tmp_path: Path, source: str) -> tuple[list[str], dict[str, str], Path]:
    (tmp_path / "test_nested.py").write_text(source, encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    env = {
        **os.environ,
        "CI_HANG_STATE_DIR": str(output / ci_lanes.HANG_STATE_DIR),
        "PYTHONPATH": str(REPO_ROOT / "scripts"),
    }
    env.pop("PYTEST_XDIST_WORKER", None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-c",
        os.devnull,
        "--rootdir",
        str(tmp_path),
        "-p",
        "ci_hang_evidence",
        "-n",
        "2",
        str(tmp_path / "test_nested.py"),
    ]
    return command, env, output


@pytest.mark.parametrize(
    ("source", "frame"),
    [
        pytest.param(_WEDGED_IN_THE_TEST, "test_wedged", id="test_call"),
        pytest.param(_WEDGED_IN_SESSION_TEARDOWN, "wedge", id="session_teardown"),
    ],
)
def test_a_hang_leaves_stacks_from_every_process_and_fails(tmp_path, source, frame):
    command, env, output = _nested_pytest(tmp_path, source)
    result = ci_lanes.run_with_deadline(
        command, cwd=tmp_path, env=env, output=output, deadline_seconds=8, poll_seconds=1
    )
    assert result == ci_lanes.HANG_EXIT
    evidence = output / ci_lanes.HANG_EVIDENCE_DIR
    stacks = list(evidence.glob("stacks-*.txt"))
    assert {path.name.split("-")[1] for path in stacks} == {"controller", "gw0", "gw1"}
    workers = "".join(p.read_text() for p in stacks if "controller" not in p.name)
    assert "test_nested.py" in workers and frame in workers
    assert "deadline" in (evidence / "reason.txt").read_text()
    assert (evidence / "processes.txt").read_text().splitlines()[0].split()[0] == "PID"
    assert "test_nested.py" in "".join(p.read_text() for p in evidence.glob("last-started-*"))
    for path in stacks:  # Nothing is left running to hold the step open.
        _assert_ends(int(path.stem.rpartition("-")[2]))


def test_a_run_that_ends_keeps_its_exit_code_and_no_evidence(tmp_path):
    command, env, output = _nested_pytest(tmp_path, "def test_fails():\n    assert False\n")
    result = ci_lanes.run_with_deadline(
        command, cwd=tmp_path, env=env, output=output, deadline_seconds=120, poll_seconds=1
    )
    assert result == 1
    assert not (output / ci_lanes.HANG_EVIDENCE_DIR).exists()


@pytest.mark.parametrize(
    ("samples", "reason"),
    [
        pytest.param([1, 1], "low memory", id="sustained"),
        pytest.param([1, 10**9], "deadline", id="one_dip"),
    ],
)
def test_low_memory_ends_the_run_only_when_it_lasts(tmp_path, monkeypatch, samples, reason):
    readings = iter(samples * 100)
    monkeypatch.setattr(ci_lanes, "_available_memory_kb", lambda: next(readings))
    command, env, output = _nested_pytest(tmp_path, _WEDGED_IN_THE_TEST)
    result = ci_lanes.run_with_deadline(
        command, cwd=tmp_path, env=env, output=output, deadline_seconds=6, poll_seconds=1
    )
    assert result == ci_lanes.HANG_EXIT
    assert reason in (output / ci_lanes.HANG_EVIDENCE_DIR / "reason.txt").read_text()


def test_each_deadline_leaves_the_job_time_to_upload_the_evidence():
    workflows = REPO_ROOT / ".github" / "workflows"
    ci = yaml.safe_load((workflows / "ci.yml").read_text(encoding="utf-8"))["jobs"]
    nightly = yaml.safe_load((workflows / "nightly.yml").read_text(encoding="utf-8"))["jobs"]
    rows = nightly["coverage-shards"]["strategy"]["matrix"]["include"]
    budgets = [
        ("unit", ci["check-unit"]["timeout-minutes"]),
        ("integration", ci["check-integration"]["timeout-minutes"]),
        *((row["lane"], row["timeout"]) for row in rows),
    ]
    assert {lane for lane, _ in budgets} == {"unit", "integration"}
    for lane, minutes in budgets:
        assert ci_lanes.lane_config(lane).deadline_minutes + 4 <= minutes, lane
