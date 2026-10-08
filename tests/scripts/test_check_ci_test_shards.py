"""Contract tests for the CI file-shard selector and no-gap guard."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_ci_test_shards as shards  # noqa: E402

pytestmark = pytest.mark.unit


UNIT_MARKER = "not integration and not e2e and not nightly and not bench and not perf"


def _write_test_file(repo_root: Path, relative_path: str) -> None:
    path = repo_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def test_example(): pass\n", encoding="utf-8")


def _write_manifest(repo_root: Path, lane: str, shard: int, contents: str) -> Path:
    path = repo_root / ".github" / "ci-test-shards" / f"{lane}-{shard}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def test_read_manifest_requires_repo_relative_python_test_files(tmp_path: Path) -> None:
    _write_test_file(tmp_path, "tests/test_a.py")
    manifest = _write_manifest(tmp_path, "unit", 1, "tests/test_a.py\n")

    assert shards._read_manifest(manifest=manifest, repo_root=tmp_path) == ["tests/test_a.py"]

    manifest.write_text("tests/test_a.py::test_example\n", encoding="utf-8")
    with pytest.raises(ValueError, match="test files"):
        shards._read_manifest(manifest=manifest, repo_root=tmp_path)


def test_read_manifest_rejects_unsorted_files(tmp_path: Path) -> None:
    _write_test_file(tmp_path, "tests/test_a.py")
    _write_test_file(tmp_path, "tests/test_b.py")
    manifest = _write_manifest(tmp_path, "unit", 1, "tests/test_b.py\ntests/test_a.py\n")

    with pytest.raises(ValueError, match="must be sorted"):
        shards._read_manifest(manifest=manifest, repo_root=tmp_path)


def test_no_subcommand_defaults_to_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["check_ci_test_shards.py"])
    assert shards._parse_args().command is None


def test_validate_lane_rejects_missing_and_duplicate_selected_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(shards.LANES, "unit", shards.LaneConfig(UNIT_MARKER, 2, 1, True, "3"))
    for name in ("tests/test_a.py", "tests/test_b.py"):
        _write_test_file(tmp_path, name)
    manifest_one = _write_manifest(tmp_path, "unit", 1, "tests/test_a.py\n")
    manifest_two = _write_manifest(tmp_path, "unit", 2, "tests/test_a.py\n")
    shard_one = shards.ShardSpec("unit", 1, manifest_one.relative_to(tmp_path))
    shard_two = shards.ShardSpec("unit", 2, manifest_two.relative_to(tmp_path))

    def collect(*, paths: list[str], marker: str, ignore_e2e: bool, repo_root: Path) -> set[str]:
        assert marker == UNIT_MARKER
        if not paths:
            return {"tests/test_a.py::test_example", "tests/test_b.py::test_example"}
        return {"tests/test_a.py::test_example"}

    monkeypatch.setattr(shards, "_collect_node_ids", collect)

    with pytest.raises(ValueError, match="listed more than once"):
        shards._validate_lane(lane="unit", shard_specs=[shard_one, shard_two], repo_root=tmp_path)

    manifest_two.write_text("tests/test_b.py\n", encoding="utf-8")
    monkeypatch.setitem(shards.LANES, "unit", shards.LaneConfig(UNIT_MARKER, 1, 1, True, "3"))
    with pytest.raises(ValueError, match="Missing selected test files"):
        shards._validate_lane(lane="unit", shard_specs=[shard_one], repo_root=tmp_path)


def test_validate_lane_rejects_overlapping_selected_node_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(shards.LANES, "unit", shards.LaneConfig(UNIT_MARKER, 2, 1, True, "3"))
    for name in ("tests/test_a.py", "tests/test_b.py"):
        _write_test_file(tmp_path, name)
    manifest_one = _write_manifest(tmp_path, "unit", 1, "tests/test_a.py\n")
    manifest_two = _write_manifest(tmp_path, "unit", 2, "tests/test_b.py\n")
    shard_one = shards.ShardSpec("unit", 1, manifest_one.relative_to(tmp_path))
    shard_two = shards.ShardSpec("unit", 2, manifest_two.relative_to(tmp_path))

    def collect(*, paths: list[str], marker: str, ignore_e2e: bool, repo_root: Path) -> set[str]:
        assert marker == UNIT_MARKER
        if not paths:
            return {"tests/test_a.py::test_example", "tests/test_b.py::test_example"}
        return {"tests/test_a.py::test_example", "tests/test_b.py::test_example"}

    monkeypatch.setattr(shards, "_collect_node_ids", collect)

    with pytest.raises(ValueError, match="selected by more than one shard"):
        shards._validate_lane(lane="unit", shard_specs=[shard_one, shard_two], repo_root=tmp_path)


def test_validate_lane_accepts_exactly_once_file_and_node_coverage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(shards.LANES, "unit", shards.LaneConfig(UNIT_MARKER, 2, 1, True, "3"))
    for name in ("tests/test_a.py", "tests/test_b.py"):
        _write_test_file(tmp_path, name)
    manifest_one = _write_manifest(tmp_path, "unit", 1, "tests/test_a.py\n")
    manifest_two = _write_manifest(tmp_path, "unit", 2, "tests/test_b.py\n")
    shard_one = shards.ShardSpec("unit", 1, manifest_one.relative_to(tmp_path))
    shard_two = shards.ShardSpec("unit", 2, manifest_two.relative_to(tmp_path))

    def collect(*, paths: list[str], marker: str, ignore_e2e: bool, repo_root: Path) -> set[str]:
        assert marker == UNIT_MARKER
        if not paths:
            return {"tests/test_a.py::test_example", "tests/test_b.py::test_example"}
        return {f"{paths[0]}::test_example"}

    monkeypatch.setattr(shards, "_collect_node_ids", collect)

    assert shards._validate_lane(
        lane="unit", shard_specs=[shard_one, shard_two], repo_root=tmp_path
    ) == (2, 2)


def test_lane_local_validation_allows_a_mixed_marker_file_in_both_lanes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_test_file(tmp_path, "tests/test_mixed.py")
    unit_manifest = _write_manifest(tmp_path, "unit", 1, "tests/test_mixed.py\n")
    integration_manifest = _write_manifest(tmp_path, "integration", 1, "tests/test_mixed.py\n")
    monkeypatch.setitem(shards.LANES, "unit", shards.LaneConfig(UNIT_MARKER, 1, 1, True, "3"))
    monkeypatch.setitem(
        shards.LANES,
        "integration",
        shards.LaneConfig("integration", 1, 5, False, "auto"),
    )

    def collect(*, paths: list[str], marker: str, ignore_e2e: bool, repo_root: Path) -> set[str]:
        if marker == UNIT_MARKER:
            return {"tests/test_mixed.py::test_unit"}
        return {"tests/test_mixed.py::test_integration"}

    monkeypatch.setattr(shards, "_collect_node_ids", collect)
    assert shards._validate_lane(
        lane="unit",
        shard_specs=[shards.ShardSpec("unit", 1, unit_manifest.relative_to(tmp_path))],
        repo_root=tmp_path,
    ) == (1, 1)
    assert shards._validate_lane(
        lane="integration",
        shard_specs=[
            shards.ShardSpec("integration", 1, integration_manifest.relative_to(tmp_path))
        ],
        repo_root=tmp_path,
    ) == (1, 1)


def test_run_shard_keeps_the_lane_marker_file_boundary_and_loadfile_distribution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CI_COVERAGE", raising=False)
    subprocess_run = subprocess.run
    _write_test_file(tmp_path, "tests/test_a.py")
    _write_manifest(tmp_path, "unit", 1, "tests/test_a.py\n")
    coverage_file = tmp_path / "coverage-unit-1.data"
    evidence_dir = tmp_path / "evidence"
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(shards.subprocess, "run", fake_run)

    assert (
        shards.run_shard(
            lane="unit",
            shard=1,
            repo_root=tmp_path,
            coverage_file=coverage_file,
            evidence_dir=evidence_dir,
        )
        == 0
    )

    command = captured["command"]
    assert command[:3] == [sys.executable, "-m", "pytest"]
    assert "tests/test_a.py" in command
    assert command[command.index("--") + 1 :] == ["tests/test_a.py"]
    assert command[command.index("-m", 3) + 1] == UNIT_MARKER
    assert "--ignore=tests/e2e" in command
    assert command[command.index("-n") + 1] == "3"
    assert command[command.index("--dist") + 1] == "loadfile"
    # REQ-testing-046: actual argv ordering reaches the inherited loadfile scheduler.
    assert "--no-loadscope-reorder" in command
    assert "--cov=src/butlers" in command
    assert f"--junitxml={evidence_dir / 'raw-junit.xml'}" in command
    assert captured["kwargs"]["env"]["COVERAGE_FILE"] == str(coverage_file)

    # Both exact opt-ins keep the entire lane command outside instrumentation.
    original = command[:]
    for flag in ("0", "1"):
        monkeypatch.setenv("CI_COVERAGE", flag)
        assert (
            shards.run_shard(
                lane="unit",
                shard=1,
                repo_root=tmp_path,
                coverage_file=coverage_file if flag == "1" else None,
                evidence_dir=evidence_dir,
            )
            == 0
        )
        selected = captured["command"]
        assert [arg for arg in selected if not arg.startswith("--cov")] == [
            arg for arg in original if not arg.startswith("--cov")
        ]
        assert any(arg.startswith("--cov") for arg in selected) == (flag == "1")
        assert ("COVERAGE_FILE" in captured["kwargs"]["env"]) == (flag == "1")
    for bad in ("", "true", "false", " 1", "1 ", "2"):
        monkeypatch.setenv("CI_COVERAGE", bad)
        with pytest.raises(ValueError, match="CI_COVERAGE"):
            shards.run_shard(
                lane="unit",
                shard=1,
                repo_root=tmp_path,
                coverage_file=None,
                evidence_dir=evidence_dir,
            )
    monkeypatch.setenv("CI_COVERAGE", "1")
    with pytest.raises(ValueError, match="COVERAGE_FILE"):
        shards.run_shard(
            lane="unit",
            shard=1,
            repo_root=tmp_path,
            coverage_file=None,
            evidence_dir=evidence_dir,
        )

    # Execute real pytest/pytest-cov with the same selector/three workers, rather
    # than infer output files from a mocked command. No project or Docker fixture.
    with monkeypatch.context() as real:
        for name in ("GITHUB_SHA", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_EVENT_NAME"):
            real.delenv(name, raising=False)
        real.setattr(shards.subprocess, "run", subprocess_run)
        source = tmp_path / "src/butlers/example.py"
        source.parent.mkdir(parents=True)
        source.write_text("value = 7\n")
        (tmp_path / "tests/test_a.py").write_text(
            "import runpy\ndef test_example():\n"
            "    assert runpy.run_path('src/butlers/example.py')['value'] == 7\n"
        )
        receipts = []
        for flag in ("0", "1"):
            real.setenv("CI_COVERAGE", flag)
            data = tmp_path / f"actual-{flag}/coverage.data"
            evidence = tmp_path / f"actual-{flag}/evidence"
            assert (
                shards.run_shard(
                    lane="unit",
                    shard=1,
                    repo_root=tmp_path,
                    coverage_file=data if flag == "1" else None,
                    evidence_dir=evidence,
                )
                == 0
            )
            assert (evidence / "raw-junit.xml").is_file()
            assert data.exists() == (flag == "1")
            assert (evidence / "coverage.json").exists() == (flag == "1")
            assert data.with_suffix(".data.metadata.json").exists() == (flag == "1")
            receipts.append(
                {
                    "flag": flag,
                    "pytest_exit": 0,
                    "junit": True,
                    "coverage_data": data.exists(),
                    "coverage_json": (evidence / "coverage.json").exists(),
                }
            )
        # REQ-testing-049: same real corpus independently varies workers and tracer. These are
        # conformance runs, not full-shard A/B measurements or a gain claim.
        import ci_shard_comparison as comparison

        snapshots = []
        coverages = []
        (tmp_path / "conftest.py").write_text(
            "def pytest_xdist_auto_num_workers(config): return 3\n"
        )
        for workers in ("3", "4", "auto"):
            for core in ("ctrace", "sysmon"):
                real.setenv("CI_UNIT_WORKERS", workers)
                real.setenv("CI_COVERAGE_CORE", core)
                real.setenv("CI_COVERAGE", "1")
                evidence = tmp_path / f"experiment-{workers}-{core}"
                assert (
                    shards.run_shard(
                        lane="unit",
                        shard=1,
                        repo_root=tmp_path,
                        coverage_file=evidence / "coverage.data",
                        evidence_dir=evidence,
                    )
                    == 0
                )
                snapshot = json.loads((evidence / "shard-observation.json").read_text())
                assert snapshot["complete"] is True
                assert snapshot["selected_count"] == 1
                assert snapshot["actual_tracers"] == [
                    "SysMonitor" if core == "sysmon" else "CTracer"
                ]
                assert snapshot["worker_resources"]
                assert snapshot["first_result_s"] is not None
                assert snapshot["last_five_percent_s"] is not None
                snapshots.append(snapshot)
                coverages.append(json.loads((evidence / "coverage.json").read_text()))
        assert all(comparison.compare(snapshots[0], value)["eligible"] for value in snapshots)
        assert all(comparison.compare_coverage(coverages[0], value) for value in coverages)
        # Complete flags cannot replace actual provenance/population/phase evidence.
        assert comparison.compare({"complete": True}, {"complete": True})["eligible"] is False
        for invalid in (
            {},
            {"nodes": {}},
            {"source": None},
            {"collected_at": "2000-01-01T00:00:00+00:00"},
            {"selected_count": 0},
            {"manifest_digest": None},
            {"effective_workers": []},
            {"actual_tracers": ["malformed"]},
            {"nodes": {next(iter(snapshots[0]["nodes"])): {}}},
        ):
            value = {} if not invalid else {**snapshots[0], **invalid}
            assert comparison.compare(value, value)["eligible"] is False
        changed = copy.deepcopy(snapshots[0])
        changed["nodes"][next(iter(changed["nodes"]))]["call"]["outcome"] = "failed"
        assert comparison.compare(snapshots[0], changed)["eligible"] is False
        changed = copy.deepcopy(coverages[0])
        changed["files"]["src/butlers/planted.py"] = {}
        assert not comparison.compare_coverage(coverages[0], changed)

        for value in ("", "2", "5", " AUTO"):
            real.setenv("CI_UNIT_WORKERS", value)
            with pytest.raises(ValueError, match="CI_UNIT_WORKERS"):
                shards.unit_workers("unit")
        real.setenv("CI_UNIT_WORKERS", "3")
        real.setenv("CI_COVERAGE_CORE", "unknown")
        with pytest.raises(ValueError, match="CI_COVERAGE_CORE"):
            shards.coverage_core()
        real.setenv("CI_COVERAGE_CORE", "sysmon")
        import coverage.collector

        with real.context() as forced:
            forced.setattr(coverage.collector.Collector, "tracer_name", lambda self: "CTracer")
            with pytest.raises(ValueError, match="not installed"):
                shards.coverage_core()
        real.setenv("CI_COVERAGE_CORE", "ctrace")

        # A real one-worker loadfile control isolates scheduler reorder from
        # execution races. Default reorder visits the larger module first;
        # disabling it executes the supplied duration order without splitting.
        (tmp_path / "tests/test_a.py").write_text(
            "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n"
        )
        (tmp_path / "tests/test_b.py").write_text("def test_example(): pass\n")
        _write_manifest(tmp_path, "unit", 1, "tests/test_a.py\ntests/test_b.py\n")
        context = shards.timing_context(
            files=["tests/test_a.py", "tests/test_b.py"], repo_root=tmp_path, lane="unit", shard=1
        )
        selected = shards._collect_node_ids(
            paths=["tests/test_a.py", "tests/test_b.py"],
            marker=UNIT_MARKER,
            ignore_e2e=True,
            repo_root=tmp_path,
        )
        receipt = tmp_path / "input-timings.json"
        record = {
            **context,
            "source": "a" * 40,
            "run": "123",
            "attempt": "1",
            "complete": True,
            "collected_at": datetime.now(UTC).isoformat(),
            "file_durations_s": {"tests/test_a.py": 1, "tests/test_b.py": 10},
            "selected_count": len(selected),
            "selected_node_digest": shards._digest(sorted(shards._digest(n) for n in selected)),
            "pytest_exit": 0,
            "node_files": {shards._digest(n): n.split("::", 1)[0] for n in selected},
            "nodes": {
                shards._digest(n): {
                    "setup": {"outcome": "passed", "duration_s": 0, "completed_s": 0},
                    "call": {
                        "outcome": "passed",
                        "duration_s": 10 if "test_b.py" in n else 1 / 3,
                        "completed_s": 1,
                    },
                    "teardown": {"outcome": "passed", "duration_s": 0, "completed_s": 2},
                }
                for n in selected
            },
        }
        receipt.write_text(json.dumps(record))
        files, status = shards.duration_order(
            ["tests/test_a.py", "tests/test_b.py"],
            context=context,
            receipt=receipt,
            repo_root=tmp_path,
        )
        assert status == "compatible" and files == ["tests/test_b.py", "tests/test_a.py"]
        for mutation in (
            {"complete": False},
            {"selected_count": 0},
            {"lane": "integration"},
            {"collected_at": "2000-01-01T00:00:00+00:00"},
            {"nodes": {}},
            {"pytest_exit": 1},
            {"source": None},
            {"file_durations_s": {"tests/test_a.py": float("nan"), "tests/test_b.py": 10}},
            {"file_durations_s": {"tests/test_a.py": -1, "tests/test_b.py": 10}},
        ):
            receipt.write_text(json.dumps({**record, **mutation}))
            assert shards.duration_order(
                ["tests/test_a.py", "tests/test_b.py"],
                context=context,
                receipt=receipt,
                repo_root=tmp_path,
            ) == (["tests/test_a.py", "tests/test_b.py"], "unknown:invalid")
        for disable in (False, True):
            output = tmp_path / f"scheduler-{disable}.xml"
            args = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-n",
                "1",
                "--dist",
                "loadfile",
                f"--junitxml={output}",
            ]
            if disable:
                args += ["--no-loadscope-reorder"]
            result = subprocess_run(
                args + files, cwd=tmp_path, env=os.environ.copy(), capture_output=True, timeout=60
            )
            assert result.returncode == 0
            cases = ET.parse(output).getroot().findall(".//testcase")
            assert len(cases) == 4
            assert cases[0].attrib["classname"].endswith("test_b" if disable else "test_a")

        (tmp_path / "coverage-mode-controls.json").write_text(json.dumps(receipts, indent=2))
        print(f"coverage mode receipt: {tmp_path / 'coverage-mode-controls.json'}")


def test_run_shard_retains_auto_workers_for_integration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_test_file(tmp_path, "tests/test_a.py")
    _write_manifest(tmp_path, "integration", 1, "tests/test_a.py\n")
    monkeypatch.setitem(
        shards.LANES,
        "integration",
        shards.LaneConfig("integration", 1, 5, False, "auto"),
    )
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(shards.subprocess, "run", fake_run)
    assert (
        shards.run_shard(
            lane="integration",
            shard=1,
            repo_root=tmp_path,
            coverage_file=tmp_path / "coverage.data",
            evidence_dir=tmp_path / "evidence",
        )
        == 0
    )
    assert captured["command"][captured["command"].index("-n") + 1] == "auto"
