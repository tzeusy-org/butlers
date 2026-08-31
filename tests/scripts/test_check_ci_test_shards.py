"""Contract tests for the CI file-shard selector and no-gap guard."""

from __future__ import annotations

import subprocess
import sys
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
    assert "--cov=src/butlers" in command
    assert f"--junitxml={evidence_dir / 'raw-junit.xml'}" in command
    assert captured["kwargs"]["env"]["COVERAGE_FILE"] == str(coverage_file)


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
