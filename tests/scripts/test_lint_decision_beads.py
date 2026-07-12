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


def test_load_issues_from_file_accepts_jsonl_bd_export_format(tmp_path):
    # bu-hmdqz.6: `bd export -o issues.export.jsonl` writes newline-delimited
    # JSON (one issue object per line), not a single JSON array -- the format
    # the weekly decision_review job feeds straight into --issues-json-file.
    fixture = tmp_path / "issues.export.jsonl"
    fixture.write_text(
        json.dumps(_decision_issue(id="bu-1"))
        + "\n"
        + json.dumps(_decision_issue(id="bu-2", labels=[]))
        + "\n"
    )
    issues = ldb.load_issues_from_file(fixture)
    assert [i["id"] for i in issues] == ["bu-1", "bu-2"]


def test_load_issues_from_file_jsonl_skips_blank_lines(tmp_path):
    fixture = tmp_path / "issues.export.jsonl"
    fixture.write_text(json.dumps(_decision_issue(id="bu-1")) + "\n\n\n")
    issues = ldb.load_issues_from_file(fixture)
    assert [i["id"] for i in issues] == ["bu-1"]


def test_load_issues_from_file_jsonl_with_bad_line_raises_bd_unavailable(tmp_path):
    fixture = tmp_path / "issues.export.jsonl"
    fixture.write_text(json.dumps(_decision_issue(id="bu-1")) + "\n{not valid json\n")
    with pytest.raises(ldb.BdUnavailableError):
        ldb.load_issues_from_file(fixture)


# ---------------------------------------------------------------------------
# is_unlabeled_marker_match / select_issues_to_check (bu-hmdqz.6,
# --check-unlabeled-markers non-vacuous mode)
# ---------------------------------------------------------------------------


def _marker_issue(**overrides):
    issue = {
        "id": "bu-marker",
        "title": "DECISION REQUIRED (owner): pick a lane",
        "status": "open",
        "issue_type": "task",
        "labels": [],
    }
    issue.update(overrides)
    return issue


def test_unlabeled_marker_match_true_for_open_nonepic_unlabeled_marker_title():
    assert ldb.is_unlabeled_marker_match(_marker_issue()) is True


def test_unlabeled_marker_match_false_when_already_labeled():
    assert ldb.is_unlabeled_marker_match(_marker_issue(labels=["decision"])) is False


def test_unlabeled_marker_match_false_for_epic_even_if_title_matches():
    # Regression: an epic named e.g. "Owner Decision Desk..." must not match --
    # a container epic is not itself a single decision (mirrors
    # decision_review.py::_is_decision_bead's epic exclusion).
    assert (
        ldb.is_unlabeled_marker_match(
            _marker_issue(title="Owner Decision Desk: decision beads...", issue_type="epic")
        )
        is False
    )


def test_unlabeled_marker_match_false_for_closed_bead():
    assert ldb.is_unlabeled_marker_match(_marker_issue(status="closed")) is False


def test_unlabeled_marker_match_false_when_title_has_no_marker():
    assert ldb.is_unlabeled_marker_match(_marker_issue(title="fix the flaky test")) is False


@pytest.mark.parametrize(
    "title",
    [
        "ARCHITECTURAL DECISION (owner): memory-module episodes table collides",
        "OWNER: decide auto-apply vs owner-confirmed for skip/metadata_only",
        "[OWNER-GATED] restart the drift job",
        "OWNER DECISION needed on the api-haiku lane",
    ],
)
def test_unlabeled_marker_match_covers_all_seed_queue_title_shapes(title):
    assert ldb.is_unlabeled_marker_match(_marker_issue(title=title)) is True


def test_select_issues_to_check_noop_without_flag():
    issues = [_marker_issue(), _decision_issue()]
    assert ldb.select_issues_to_check(issues, check_unlabeled_markers=False) == issues


def test_select_issues_to_check_keeps_labeled_and_marker_matched_drops_rest():
    labeled = _decision_issue(id="bu-labeled")
    marker_unlabeled = _marker_issue(id="bu-unlabeled")
    unrelated = {"id": "bu-unrelated", "title": "fix a typo", "status": "open", "labels": []}
    epic = _marker_issue(id="bu-epic", issue_type="epic", title="Owner Decision Desk: ...")

    selected = ldb.select_issues_to_check(
        [labeled, marker_unlabeled, unrelated, epic], check_unlabeled_markers=True
    )

    assert {i["id"] for i in selected} == {"bu-labeled", "bu-unlabeled"}


def test_select_issues_to_check_drops_non_dict_entries():
    selected = ldb.select_issues_to_check(
        ["not a dict", _marker_issue()], check_unlabeled_markers=True
    )
    assert len(selected) == 1


# ---------------------------------------------------------------------------
# CLI -- --check-unlabeled-markers end to end
# ---------------------------------------------------------------------------


def test_cli_check_unlabeled_markers_fails_unlabeled_marker_bead(tmp_path):
    fixture = tmp_path / "issues.json"
    fixture.write_text(json.dumps([_marker_issue()]))
    proc = _run_cli("--issues-json-file", str(fixture), "--check-unlabeled-markers", "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert len(payload) == 1
    assert payload[0]["id"] == "bu-marker"
    assert payload[0]["ok"] is False
    assert any("label" in v for v in payload[0]["violations"])


def test_cli_check_unlabeled_markers_ignores_unrelated_beads_in_the_same_file(tmp_path):
    fixture = tmp_path / "issues.json"
    unrelated = {"id": "bu-unrelated", "title": "fix a typo", "status": "open", "labels": []}
    fixture.write_text(json.dumps([unrelated]))
    proc = _run_cli("--issues-json-file", str(fixture), "--check-unlabeled-markers", "--json")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload == []


def test_cli_check_unlabeled_markers_excludes_epic_false_positive(tmp_path):
    fixture = tmp_path / "issues.json"
    epic = _marker_issue(
        id="bu-ckkpz", issue_type="epic", title="Owner Decision Desk: decision beads..."
    )
    fixture.write_text(json.dumps([epic]))
    proc = _run_cli("--issues-json-file", str(fixture), "--check-unlabeled-markers", "--json")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == []


def test_cli_without_flag_file_mode_stays_unfiltered(tmp_path):
    # Back-compat: WITHOUT --check-unlabeled-markers, --issues-json-file
    # keeps its original "check everything in the file, as given" contract
    # -- an unrelated, non-decision issue in the file is still linted (and
    # fails, since it has none of the four convention fields), not silently
    # dropped by the new selection logic.
    fixture = tmp_path / "issues.json"
    unrelated = {"id": "bu-unrelated", "title": "fix a typo", "status": "open", "labels": []}
    fixture.write_text(json.dumps([unrelated]))
    proc = _run_cli("--issues-json-file", str(fixture), "--json")
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload[0]["id"] == "bu-unrelated"


def test_cli_check_unlabeled_markers_explicit_ids_unaffected(tmp_path):
    # Explicit issue IDs bypass discovery/select_issues_to_check entirely --
    # the flag must not change that an explicitly-named ID is always checked.
    fixture = tmp_path / "issue.json"
    fixture.write_text(json.dumps(_marker_issue()))
    proc_without_flag = _run_cli("--issues-json-file", str(fixture), "--json")
    proc_with_flag = _run_cli(
        "--issues-json-file", str(fixture), "--check-unlabeled-markers", "--json"
    )
    # Both check the single issue in the file as given (file mode has no
    # concept of "explicit IDs" -- this asserts the file-mode result is
    # identical with/without the flag when there's only one candidate and no
    # unrelated noise to filter out).
    assert json.loads(proc_without_flag.stdout) == json.loads(proc_with_flag.stdout)
