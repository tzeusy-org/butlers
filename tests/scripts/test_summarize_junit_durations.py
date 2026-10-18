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


def test_failed_nodes_lists_sorted_failures_and_errors_without_payloads(tmp_path: Path) -> None:
    raw_report = tmp_path / "raw-junit.xml"
    failed_nodes = tmp_path / "failed-nodes.txt"
    raw_report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="tests.zeta.TestZ" name="test_b[x-1]" time="1">
    <failure message="secret assertion text">secret body</failure>
  </testcase>
  <testcase classname="tests.alpha" name="test_a" time="1"><error message="boom" /></testcase>
  <testcase classname="tests.alpha" name="test_skipped" time="1"><skipped /></testcase>
  <testcase classname="tests.alpha" name="test_ok" time="1" />
</testsuite></testsuites>
""",
        encoding="utf-8",
    )

    summary.sanitize_junit_report(
        raw_report=raw_report,
        sanitized_report=tmp_path / "junit.xml",
        duration_report=tmp_path / "top.txt",
        limit=5,
        failed_nodes_report=failed_nodes,
    )

    assert failed_nodes.read_text(encoding="utf-8") == (
        "tests/alpha.py::test_a\ntests/zeta.py::TestZ::test_b[x-1]\n"
    )


def test_failed_nodes_is_empty_when_green_and_flag_is_opt_in(tmp_path: Path) -> None:
    raw_report = tmp_path / "raw-junit.xml"
    raw_report.write_text(
        '<testsuites><testsuite><testcase classname="tests.a" name="test_ok" time="1" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    failed_nodes = tmp_path / "nested" / "failed-nodes.txt"
    with_flag = {"sanitized_report": tmp_path / "j1.xml", "duration_report": tmp_path / "d1.txt"}
    without_flag = {"sanitized_report": tmp_path / "j2.xml", "duration_report": tmp_path / "d2.txt"}

    summary.sanitize_junit_report(
        raw_report=raw_report, limit=5, failed_nodes_report=failed_nodes, **with_flag
    )
    summary.sanitize_junit_report(raw_report=raw_report, limit=5, **without_flag)

    assert failed_nodes.read_text(encoding="utf-8") == ""
    assert (
        with_flag["sanitized_report"].read_bytes() == without_flag["sanitized_report"].read_bytes()
    )
    assert with_flag["duration_report"].read_bytes() == without_flag["duration_report"].read_bytes()
    assert not (tmp_path / "failed-nodes.txt").exists()


def test_failed_nodes_rebuilds_a_rerunnable_path_for_a_collection_failure(tmp_path: Path) -> None:
    raw_report = tmp_path / "raw-junit.xml"
    failed_nodes = tmp_path / "failed-nodes.txt"
    # pytest's shape for an import-broken module: empty classname, dotted module as name.
    raw_report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest">
  <testcase classname="" name="tests.unit.test_broken" time="0">
    <error message="collection failure">ImportError body</error>
  </testcase>
  <testcase classname="tests.unit.test_ok" name="test_fails" time="1"><failure /></testcase>
</testsuite></testsuites>
""",
        encoding="utf-8",
    )

    summary.sanitize_junit_report(
        raw_report=raw_report,
        sanitized_report=tmp_path / "junit.xml",
        duration_report=tmp_path / "top.txt",
        limit=5,
        failed_nodes_report=failed_nodes,
    )

    assert failed_nodes.read_text(encoding="utf-8") == (
        "tests/unit/test_broken.py\ntests/unit/test_ok.py::test_fails\n"
    )
