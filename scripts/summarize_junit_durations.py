#!/usr/bin/env python3
"""Write privacy-minimal timing evidence from a pytest JUnit report.

The raw JUnit report can include assertion text and captured output. CI retains
only a normalized report with test identifiers, outcomes, and durations, plus
a short list of the slowest tests. That is enough to target future suite work
without making test payloads durable artifacts.
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _Testcase:
    """One safe-to-publish subset of a JUnit testcase."""

    classname: str
    name: str
    duration_s: float
    outcome: str

    @property
    def label(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name


def _redact_parameter_values(value: str) -> str:
    """Keep a targetable test name without publishing parametrized values."""
    if "[" not in value:
        return value
    return f"{value.split('[', maxsplit=1)[0]}[...]"


def _duration(value: str | None) -> float:
    try:
        return max(float(value or "0"), 0.0)
    except ValueError:
        return 0.0


def _outcome(testcase: ET.Element) -> str:
    if testcase.find("failure") is not None:
        return "failure"
    if testcase.find("error") is not None:
        return "error"
    if testcase.find("skipped") is not None:
        return "skipped"
    return "passed"


def _parse_testcases(raw_report: Path) -> tuple[list[_Testcase], str | None]:
    if not raw_report.is_file():
        return [], "junit-report-unavailable"

    try:
        root = ET.parse(raw_report).getroot()
    except ET.ParseError:
        return [], "junit-report-unparseable"

    cases = [
        _Testcase(
            classname=_redact_parameter_values(testcase.get("classname", "")),
            name=_redact_parameter_values(testcase.get("name", "unknown")),
            duration_s=_duration(testcase.get("time")),
            outcome=_outcome(testcase),
        )
        for testcase in root.findall(".//testcase")
    ]
    return cases, None


def _write_sanitized_junit(
    cases: list[_Testcase], *, error_type: str | None, output_path: Path
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suite = ET.Element(
        "testsuite",
        {
            "name": "pytest duration evidence",
            "tests": str(len(cases)),
            "failures": str(sum(case.outcome == "failure" for case in cases)),
            "errors": str(sum(case.outcome == "error" for case in cases) + bool(error_type)),
            "skipped": str(sum(case.outcome == "skipped" for case in cases)),
            "time": f"{sum(case.duration_s for case in cases):.3f}",
        },
    )
    if error_type is not None:
        ET.SubElement(suite, "error", {"type": error_type})

    for case in cases:
        testcase = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": case.classname,
                "name": case.name,
                "time": f"{case.duration_s:.3f}",
            },
        )
        if case.outcome in {"failure", "error", "skipped"}:
            ET.SubElement(testcase, case.outcome, {"type": case.outcome})

    ET.ElementTree(suite).write(output_path, encoding="utf-8", xml_declaration=True)


def _write_duration_report(cases: list[_Testcase], *, output_path: Path, limit: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cases:
        output_path.write_text(
            "No test timing metadata was produced; inspect the lane's test step.\n",
            encoding="utf-8",
        )
        return

    slowest = sorted(cases, key=lambda case: (-case.duration_s, case.label))[:limit]
    lines = [
        f"Top {len(slowest)} slowest pytest test cases (sanitized JUnit metadata)",
        "duration_s\ttest",
        *(f"{case.duration_s:.3f}\t{case.label}" for case in slowest),
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")


def sanitize_junit_report(
    *, raw_report: Path, sanitized_report: Path, duration_report: Path, limit: int
) -> None:
    """Produce durable timing evidence without raw failure or stdout payloads."""
    cases, error_type = _parse_testcases(raw_report)
    _write_sanitized_junit(cases, error_type=error_type, output_path=sanitized_report)
    _write_duration_report(cases, output_path=duration_report, limit=limit)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Raw pytest JUnit XML path")
    parser.add_argument(
        "--sanitized-junit",
        type=Path,
        required=True,
        help="Output path for the JUnit report without test payloads",
    )
    parser.add_argument(
        "--durations-output",
        type=Path,
        required=True,
        help="Output path for the slowest-test duration table",
    )
    parser.add_argument("--limit", type=int, default=25, help="Number of slowest tests to retain")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def main() -> None:
    args = _parse_args()
    sanitize_junit_report(
        raw_report=args.input,
        sanitized_report=args.sanitized_junit,
        duration_report=args.durations_output,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
