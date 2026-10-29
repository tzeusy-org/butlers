"""The two backend CI test lanes, split into duration-balanced pytest-split groups.

Each lane is one marker selection over the whole suite. A CI child runs group
``k`` of ``N`` with pytest-split over the committed ``.test_durations``, and
records how many tests its group collected. ``totals`` then sums the groups of
each lane and compares the sum with one fresh ``--collect-only`` count of the
same selection, so a group that silently drops (or duplicates) tests fails the
run. This replaced the computed inventory, partition and receipt tooling
(bu-29wkwj.2).

Usage:
  uv run python scripts/ci_lanes.py run --lane unit --group 3 --output DIR
  uv run python scripts/ci_lanes.py totals --counts DIR
  python3 scripts/ci_lanes.py durations --evidence DIR [--output .test_durations]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DURATIONS_PATH = ".test_durations"
COUNT_FILE = "collected.json"
DURATIONS_FILE = "durations.json"


@dataclass(frozen=True)
class Lane:
    """The CI-owned pytest selection and execution contract for one lane."""

    marker: str
    groups: int
    maxfail: int
    ignore_e2e: bool
    workers: str


LANES: dict[str, Lane] = {
    "unit": Lane(
        marker="not integration and not e2e and not nightly and not bench and not perf",
        groups=5,
        maxfail=1,
        ignore_e2e=True,
        workers="3",
    ),
    "integration": Lane(
        marker="integration and not nightly and not bench and not perf",
        groups=6,
        maxfail=5,
        ignore_e2e=False,
        workers="auto",
    ),
}


def lane_config(lane: str) -> Lane:
    try:
        return LANES[lane]
    except KeyError as exc:
        raise ValueError(f"Unknown CI test lane {lane!r}; expected one of {sorted(LANES)}") from exc


def collect_lane_node_ids(lane: str, *, repo_root: Path = REPO_ROOT) -> set[str]:
    """Collect every node id the lane's marker selection picks up today."""
    config = lane_config(lane)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-n",
        "0",
        "-m",
        config.marker,
        "-p",
        "no:cacheprovider",
    ]
    if config.ignore_e2e:
        command.append("--ignore=tests/e2e")
    result = subprocess.run(  # noqa: S603
        command, cwd=repo_root, check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pytest --collect-only failed for the {lane} lane (exit={result.returncode}). "
            "Run the selector locally for full pytest diagnostics."
        )
    return {
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line and not line.startswith(("=", "-", " "))
    }


def coverage_enabled() -> bool:
    """CI supplies exact 0/1; standalone calls retain coverage by default."""
    value = os.environ.get("CI_COVERAGE", "1")
    if value not in {"0", "1"}:
        raise ValueError("CI_COVERAGE must be exactly 0 or 1")
    return value == "1"


def pytest_command(lane: str, group: int, *, output: Path, coverage: bool) -> list[str]:
    """The exact pytest argv one lane group runs."""
    config = lane_config(lane)
    if group not in range(1, config.groups + 1):
        raise ValueError(f"{lane}: group must be between 1 and {config.groups}, got {group}")
    command = [sys.executable, "-m", "pytest", "-q", f"--maxfail={config.maxfail}", "--tb=short"]
    if config.ignore_e2e:
        command.append("--ignore=tests/e2e")
    command += [
        "-m",
        config.marker,
        "-n",
        config.workers,
        "--dist",
        "loadfile",
        "--splits",
        str(config.groups),
        "--group",
        str(group),
        # Contiguous chunks in collection order keep most files whole within one
        # group, which preserves module-scoped fixture lifetime under loadfile.
        "--splitting-algorithm",
        "duration_based_chunks",
        "--durations-path",
        DURATIONS_PATH,
        "-p",
        "ci_group_count",
        f"--junitxml={output / 'raw-junit.xml'}",
    ]
    if coverage:
        command += [
            "--cov=src/butlers",
            f"--cov-report=json:{output / 'coverage.json'}",
            "--cov-report=term-missing",
        ]
    return command


def run(lane: str, group: int, *, output: Path, repo_root: Path = REPO_ROOT) -> int:
    """Run one lane group, then publish its collected count."""
    output.mkdir(parents=True, exist_ok=True)
    count_path = output / COUNT_FILE
    count_path.unlink(missing_ok=True)
    coverage = coverage_enabled()
    environment = {
        **os.environ,
        "CI_GROUP_COUNT_FILE": str(count_path),
        "CI_GROUP_DURATIONS_FILE": str(output / DURATIONS_FILE),
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).resolve().parent), os.environ.get("PYTHONPATH", "")]
        ),
    }
    if coverage and not environment.get("COVERAGE_FILE"):
        raise ValueError("COVERAGE_FILE is required when CI_COVERAGE is enabled")
    if coverage:
        Path(environment["COVERAGE_FILE"]).parent.mkdir(parents=True, exist_ok=True)
    else:
        environment.pop("COVERAGE_FILE", None)
    command = pytest_command(lane, group, output=output, coverage=coverage)
    result = subprocess.run(command, cwd=repo_root, check=False, env=environment).returncode  # noqa: S603
    if coverage:
        from check_ci_coverage import write_group_metadata

        coverage_file = Path(environment["COVERAGE_FILE"])
        write_group_metadata(
            coverage_file=coverage_file, repo_root=repo_root, lane=lane, group=group
        )
    if not count_path.is_file():
        print(f"::error::{lane} group {group} recorded no collected count", file=sys.stderr)
        return result or 1
    collected = json.loads(count_path.read_text(encoding="utf-8"))["collected"]
    count_path.write_text(
        json.dumps({"lane": lane, "group": group, "collected": collected}) + "\n",
        encoding="utf-8",
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"collected={collected}\n")
    print(f"{lane} group {group}: collected={collected}")
    return result


def read_group_counts(counts_dir: Path) -> dict[str, dict[int, int]]:
    """Read every group's count file; a malformed or duplicated group fails closed."""
    counts: dict[str, dict[int, int]] = {lane: {} for lane in LANES}
    for path in sorted(counts_dir.rglob(COUNT_FILE)):
        record = json.loads(path.read_text(encoding="utf-8"))
        lane, group, collected = record.get("lane"), record.get("group"), record.get("collected")
        if (
            lane not in LANES
            or not isinstance(group, int)
            or isinstance(group, bool)
            or not isinstance(collected, int)
            or isinstance(collected, bool)
            or collected < 0
        ):
            raise ValueError(f"{path}: malformed group count {record!r}")
        if group in counts[lane]:
            raise ValueError(f"{lane} group {group} reported twice")
        counts[lane][group] = collected
    return counts


def compare_totals(group_counts: dict[str, dict[int, int]], expected: dict[str, int]) -> list[str]:
    """Return one ``::error::`` line per lane whose groups do not add up."""
    errors = []
    for lane, config in sorted(LANES.items()):
        groups = group_counts.get(lane, {})
        missing = sorted(set(range(1, config.groups + 1)) - set(groups))
        extra = sorted(set(groups) - set(range(1, config.groups + 1)))
        if missing or extra:
            errors.append(
                f"::error::{lane}: expected groups 1..{config.groups}, "
                f"missing {missing}, unexpected {extra}"
            )
            continue
        total = sum(groups.values())
        if total != expected[lane]:
            errors.append(
                f"::error::{lane}: groups collected {total} tests, but the lane selects "
                f"{expected[lane]}; a group dropped or duplicated tests"
            )
    return errors


def totals(counts_dir: Path, *, repo_root: Path = REPO_ROOT) -> int:
    """Fail unless every lane's groups sum to its fresh --collect-only count.

    The same fresh counts feed the per-lane test budget, so one collection
    serves both checks.
    """
    from check_test_budget import BASELINE_PATH, compare, load_baseline

    group_counts = read_group_counts(counts_dir)
    expected = {lane: len(collect_lane_node_ids(lane, repo_root=repo_root)) for lane in LANES}
    errors = compare_totals(group_counts, expected) + compare(
        expected, load_baseline(BASELINE_PATH)
    )
    for lane in sorted(LANES):
        print(f"{lane}: groups={sum(group_counts[lane].values())} collect-only={expected[lane]}")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


def evidence_name(lane: str, group: int) -> str:
    """The artifact (and download directory) name of one lane group's evidence."""
    return f"ci-{lane}-{group}-test-evidence"


def merge_durations(evidence_dir: Path, output: Path) -> None:
    """Merge every lane group's measured durations into pytest-split's file.

    Each group's directory carries its evidence artifact's name,
    ``ci-<lane>-<group>-test-evidence``. A missing group refuses the merge, so a
    refresh never silently drops a slice of the suite's timings.
    """
    merged: dict[str, float] = {}
    for lane, config in sorted(LANES.items()):
        missing = []
        for group in range(1, config.groups + 1):
            path = evidence_dir / evidence_name(lane, group) / DURATIONS_FILE
            if not path.is_file():
                missing.append(group)
                continue
            merged.update(json.loads(path.read_text(encoding="utf-8")))
        if missing:
            raise ValueError(f"{lane}: no durations for groups {missing}")
    output.write_text(json.dumps(merged, sort_keys=True, indent=4) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="run one lane group")
    run_parser.add_argument("--lane", choices=sorted(LANES), required=True)
    run_parser.add_argument("--group", type=int, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    totals_parser = commands.add_parser("totals", help="check group counts add up")
    totals_parser.add_argument("--counts", type=Path, required=True)
    durations_parser = commands.add_parser("durations", help="merge group durations")
    durations_parser.add_argument("--evidence", type=Path, required=True)
    durations_parser.add_argument("--output", type=Path, default=REPO_ROOT / DURATIONS_PATH)
    args = parser.parse_args(argv)
    if args.command == "run":
        return run(args.lane, args.group, output=args.output)
    if args.command == "durations":
        merge_durations(args.evidence, args.output)
        return 0
    return totals(args.counts)


if __name__ == "__main__":
    raise SystemExit(main())
