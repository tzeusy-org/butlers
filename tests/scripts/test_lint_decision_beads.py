"""Tests for scripts/lint_decision_beads.py (bu-ckkpz.1, epic bu-ckkpz "Owner
Decision Desk").

Covers the convention check itself (label + metadata.decision.options/default
+ due_at), the CLI's JSON-file offline input path, and the exit-code
contract. No live `bd`/Dolt server required -- `lint_issue`/`lint_issues`
are pure functions over issue dicts, and the CLI's file-input mode
(`--issues-json-file`) is exercised via subprocess against a fixture file
so the `bd`-unavailable path is never hit in CI.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import lint_decision_beads as ldb  # noqa: E402

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "lint_decision_beads.py"


def _decision_issue(**overrides):
    issue = {
        "id": "bu-abc",
        "title": "DECISION REQUIRED (owner): pick a lane",
        "status": "open",
        "labels": ["decision"],
        "metadata": {"decision": {"options": ["A: keep", "B: change"], "default": "A: keep"}},
        "due_at": "2026-07-25T00:00:00Z",
    }
    issue.update(overrides)
    return issue


# ---------------------------------------------------------------------------
# lint_issue -- positive case
# ---------------------------------------------------------------------------


def test_well_formed_decision_bead_passes():
    result = ldb.lint_issue(_decision_issue())
    assert result.ok
    assert result.violations == []


# ---------------------------------------------------------------------------
# lint_issue -- each violation independently
# ---------------------------------------------------------------------------


def test_missing_label_flagged():
    result = ldb.lint_issue(_decision_issue(labels=[]))
    assert not result.ok
    assert any("missing 'decision' label" in v for v in result.violations)


def test_missing_labels_field_entirely_flagged():
    issue = _decision_issue()
    del issue["labels"]
    result = ldb.lint_issue(issue)
    assert any("missing 'decision' label" in v for v in result.violations)


def test_missing_metadata_decision_flagged():
    result = ldb.lint_issue(_decision_issue(metadata={}))
    assert any("missing metadata.decision" in v for v in result.violations)


def test_metadata_not_a_dict_flagged():
    result = ldb.lint_issue(_decision_issue(metadata="not a dict"))
    assert any("missing metadata.decision" in v for v in result.violations)


def test_empty_options_list_flagged():
    result = ldb.lint_issue(_decision_issue(metadata={"decision": {"options": [], "default": "A"}}))
    assert any("options must be a non-empty list" in v for v in result.violations)


def test_options_not_a_list_flagged():
    result = ldb.lint_issue(
        _decision_issue(metadata={"decision": {"options": "A, B", "default": "A"}})
    )
    assert any("options must be a non-empty list" in v for v in result.violations)


def test_blank_option_entries_flagged():
    result = ldb.lint_issue(
        _decision_issue(
            metadata={"decision": {"options": ["A: keep", "  ", ""], "default": "A: keep"}}
        )
    )
    assert any("non-blank strings" in v for v in result.violations)


def test_duplicate_options_flagged():
    result = ldb.lint_issue(
        _decision_issue(
            metadata={"decision": {"options": ["A: keep", "A: keep"], "default": "A: keep"}}
        )
    )
    assert any("must not contain duplicates" in v for v in result.violations)


def test_missing_default_flagged():
    result = ldb.lint_issue(
        _decision_issue(metadata={"decision": {"options": ["A: keep", "B: change"]}})
    )
    assert any("default must be a non-blank string" in v for v in result.violations)


def test_blank_default_flagged():
    result = ldb.lint_issue(
        _decision_issue(
            metadata={"decision": {"options": ["A: keep", "B: change"], "default": "   "}}
        )
    )
    assert any("default must be a non-blank string" in v for v in result.violations)


def test_default_not_in_options_flagged():
    result = ldb.lint_issue(
        _decision_issue(
            metadata={
                "decision": {"options": ["A: keep", "B: change"], "default": "C: not an option"}
            }
        )
    )
    assert any("must exactly match one entry" in v for v in result.violations)


def test_missing_due_at_flagged():
    issue = _decision_issue()
    del issue["due_at"]
    result = ldb.lint_issue(issue)
    assert any("due_at (deadline) must be set" in v for v in result.violations)


def test_blank_due_at_flagged():
    result = ldb.lint_issue(_decision_issue(due_at=""))
    assert any("due_at (deadline) must be set" in v for v in result.violations)


def test_multiple_violations_all_reported():
    result = ldb.lint_issue({"id": "bu-bare", "title": "bare task"})
    assert not result.ok
    assert len(result.violations) == 5  # label, metadata, options, default, due_at


def test_non_dict_issue_flagged_not_raised():
    result = ldb.lint_issue(["not", "a", "dict"])
    assert not result.ok
    assert result.issue_id == "<unknown>"
    assert any("invalid issue data format" in v for v in result.violations)


# ---------------------------------------------------------------------------
# lint_issues / format_results
# ---------------------------------------------------------------------------


def test_lint_issues_checks_each_independently():
    results = ldb.lint_issues([_decision_issue(id="bu-1"), _decision_issue(id="bu-2", labels=[])])
    assert results[0].ok
    assert not results[1].ok


def test_format_results_clean():
    text = ldb.format_results([ldb.lint_issue(_decision_issue())])
    assert "clean" in text


def test_format_results_reports_each_failing_issue():
    bad = ldb.lint_issue(_decision_issue(labels=[]))
    text = ldb.format_results([bad])
    assert "bu-abc" in text
    assert "missing 'decision' label" in text


# ---------------------------------------------------------------------------
# CLI -- offline file input (no live bd/Dolt dependency)
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args], capture_output=True, text=True, check=False
    )


def test_cli_exits_zero_on_clean_fixture(tmp_path):
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps([_decision_issue()]))
    proc = _run_cli("--issues-json-file", str(fixture))
    assert proc.returncode == 0
    assert "clean" in proc.stdout


def test_cli_exits_one_on_violation(tmp_path):
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps([_decision_issue(labels=[])]))
    proc = _run_cli("--issues-json-file", str(fixture))
    assert proc.returncode == 1
    assert "bu-abc" in proc.stdout


def test_cli_json_output_shape(tmp_path):
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps([_decision_issue(labels=[])]))
    proc = _run_cli("--issues-json-file", str(fixture), "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload[0]["id"] == "bu-abc"
    assert payload[0]["ok"] is False
    assert any("label" in v for v in payload[0]["violations"])


def test_cli_exits_two_when_bd_unavailable(tmp_path, monkeypatch):
    # No --issues-json-file and no real `bd` on PATH -> exit 2, not a false pass.
    monkeypatch.setenv("PATH", str(tmp_path))  # empty dir, no `bd` binary
    proc = _run_cli()
    assert proc.returncode == 2
    assert "could not obtain issue data" in proc.stderr


def test_cli_single_issue_json_object_is_wrapped(tmp_path):
    # bd show with exactly one ID returns a single JSON object, not a list.
    fixture = tmp_path / "issue.json"
    fixture.write_text(json.dumps(_decision_issue()))
    proc = _run_cli("--issues-json-file", str(fixture))
    assert proc.returncode == 0


def test_cli_exits_two_on_missing_file(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    proc = _run_cli("--issues-json-file", str(missing))
    assert proc.returncode == 2
    assert "could not obtain issue data" in proc.stderr


def test_cli_exits_two_on_invalid_json(tmp_path):
    fixture = tmp_path / "issues.json"
    fixture.write_text("{not valid json")
    proc = _run_cli("--issues-json-file", str(fixture))
    assert proc.returncode == 2
    assert "could not obtain issue data" in proc.stderr


def test_load_issues_from_file_missing_raises_bd_unavailable(tmp_path):
    with pytest.raises(ldb.BdUnavailableError):
        ldb.load_issues_from_file(tmp_path / "nope.json")


def test_load_issues_from_file_invalid_json_raises_bd_unavailable(tmp_path):
    fixture = tmp_path / "bad.json"
    fixture.write_text("not json")
    with pytest.raises(ldb.BdUnavailableError):
        ldb.load_issues_from_file(fixture)
