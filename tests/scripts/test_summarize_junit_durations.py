"""Regression coverage for privacy-minimal CI duration evidence."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import summarize_junit_durations as summary  # noqa: E402

pytestmark = pytest.mark.unit


def test_sanitize_junit_report_sorts_durations_and_removes_failure_content(tmp_path: Path) -> None:
    raw_report = tmp_path / "raw-junit.xml"
    sanitized_report = tmp_path / "sanitized-junit.xml"
    duration_report = tmp_path / "top-durations.txt"
    raw_report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2">
    <testcase classname="tests.slow" name="test_slow[owner-data]" time="12.5">
      <failure message="owner data must never become an artifact">sensitive assertion body</failure>
      <system-out>captured sensitive stdout</system-out>
    </testcase>
    <testcase classname="tests.fast" name="test_fast" time="0.5" />
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    summary.sanitize_junit_report(
        raw_report=raw_report,
        sanitized_report=sanitized_report,
        duration_report=duration_report,
        limit=1,
    )

    duration_text = duration_report.read_text(encoding="utf-8")
    assert "12.500" in duration_text
    assert "tests.slow::test_slow[...]" in duration_text
    assert "test_fast" not in duration_text
    assert "owner-data" not in duration_text

    sanitized_text = sanitized_report.read_text(encoding="utf-8")
    assert "sensitive assertion body" not in sanitized_text
    assert "captured sensitive stdout" not in sanitized_text
    assert "owner data must never become an artifact" not in sanitized_text

    root = ET.parse(sanitized_report).getroot()
    cases = root.findall(".//testcase")
    assert [(case.attrib["classname"], case.attrib["name"]) for case in cases] == [
        ("tests.slow", "test_slow[...]"),
        ("tests.fast", "test_fast"),
    ]
    assert cases[0].find("failure") is not None
    assert cases[0].find("system-out") is None


def test_sanitize_junit_report_writes_safe_empty_evidence_when_report_is_missing(
    tmp_path: Path,
) -> None:
    sanitized_report = tmp_path / "sanitized-junit.xml"
    duration_report = tmp_path / "top-durations.txt"

    summary.sanitize_junit_report(
        raw_report=tmp_path / "missing.xml",
        sanitized_report=sanitized_report,
        duration_report=duration_report,
        limit=25,
    )

    assert "No test timing metadata was produced" in duration_report.read_text(encoding="utf-8")
    root = ET.parse(sanitized_report).getroot()
    assert root.find(".//error") is not None
