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
        "Meta: document `DECISION REQUIRED (owner)` title-marker text",
        "Developer note: explain (OWNER-GATED) as an advisory example",
    ],
)
def test_unlabeled_marker_match_excludes_quoted_marker_examples(title):
    assert ldb.is_unlabeled_marker_match(_marker_issue(title=title)) is False


@pytest.mark.parametrize(
    "title",
    [
        "DECISION REQUIRED (owner): pick a lane",
        "[OWNER-GATED] restart the drift job",
    ],
)
def test_unlabeled_marker_match_retains_marker_title_prefixes(title):
    assert ldb.is_unlabeled_marker_match(_marker_issue(title=title)) is True


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


def test_select_issues_to_check_keeps_closed_records_without_strict_flag():
    # A normal `--status all` audit reaches this no-op path, so its historical
    # input remains available for explicit forensic inspection.
    issues = [_marker_issue(), _decision_issue(), _decision_issue(id="bu-closed", status="closed")]
    assert ldb.select_issues_to_check(issues, check_unlabeled_markers=False) == issues


@pytest.mark.parametrize("status", ("open", "in_progress", "blocked"))
def test_select_issues_to_check_keeps_each_live_labeled_and_marker_candidate(status):
    labeled = _decision_issue(id="bu-labeled", status=status)
    closed_labeled = _decision_issue(id="bu-closed-labeled", status="closed")
    marker_unlabeled = _marker_issue(id="bu-unlabeled", status=status)
    closed_marker_unlabeled = _marker_issue(id="bu-closed-unlabeled", status="closed")
    unrelated = {"id": "bu-unrelated", "title": "fix a typo", "status": "open", "labels": []}
    epic = _marker_issue(id="bu-epic", issue_type="epic", title="Owner Decision Desk: ...")

    selected = ldb.select_issues_to_check(
        [
            labeled,
            closed_labeled,
            marker_unlabeled,
            closed_marker_unlabeled,
            unrelated,
            epic,
        ],
        check_unlabeled_markers=True,
    )

    assert {i["id"] for i in selected} == {"bu-labeled", "bu-unlabeled"}


def test_select_issues_to_check_drops_non_dict_entries():
    selected = ldb.select_issues_to_check(
        ["not a dict", _marker_issue()], check_unlabeled_markers=True
    )
    assert len(selected) == 1


def test_load_issues_from_bd_explicit_id_bypasses_strict_discovery(monkeypatch):
    closed = _marker_issue(id="bu-closed-explicit", status="closed")
    calls = []

    def fake_run_bd_json(args):
        calls.append(args)
        return closed

    monkeypatch.setattr(ldb, "_run_bd_json", fake_run_bd_json)

    assert ldb.load_issues_from_bd(
        ["bu-closed-explicit"], status="all", check_unlabeled_markers=True
    ) == [closed]
    assert calls == [["show", "bu-closed-explicit"]]


def test_load_issues_from_bd_all_status_forensic_mode_keeps_closed_history(monkeypatch):
    closed = _decision_issue(id="bu-closed-forensic", status="closed")
    calls = []

    def fake_run_bd_json(args):
        calls.append(args)
        return [closed]

    monkeypatch.setattr(ldb, "_run_bd_json", fake_run_bd_json)

    issues = ldb.load_issues_from_bd([], status="all", check_unlabeled_markers=False)

    assert ldb.select_issues_to_check(issues, check_unlabeled_markers=False) == [closed]
    assert calls == [["list", "--label", "decision", "--status", "all"]]


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
    fixture.write_text(json.dumps(_marker_issue(id="bu-closed-marker", status="closed")))
    proc = _run_cli(
        "bu-closed-marker",
        "--issues-json-file",
        str(fixture),
        "--check-unlabeled-markers",
        "--json",
    )
    assert proc.returncode == 1
    assert [result["id"] for result in json.loads(proc.stdout)] == ["bu-closed-marker"]


@pytest.mark.parametrize("violation", [False, True], ids=["warning_only", "warning_and_violation"])
def test_cli_renderability_warnings_preserve_exit_contract(tmp_path, violation):
    option = "private-option-" + "x" * 513
    issue = _decision_issue(
        labels=[] if violation else ["decision"],
        metadata={"decision": {"options": [option, "Hold"], "default": "Hold"}},
    )
    fixture = tmp_path / "oversized.json"
    fixture.write_text(json.dumps([issue]))
    proc = _run_cli("--issues-json-file", str(fixture), "--json")
    assert proc.returncode == int(violation)
    result = json.loads(proc.stdout)[0]
    assert result["ok"] is (not violation)
    assert bool(result["violations"]) is violation
    assert any("option 1" in warning and "512" in warning for warning in result["warnings"])
    assert all(option not in warning for warning in result["warnings"])
    human = _run_cli("--issues-json-file", str(fixture))
    assert human.returncode == int(violation)
    assert "Telegram renderability (advisory)" in human.stdout
    assert option not in human.stdout


def _renderability_context():
    from datetime import UTC, datetime

    return datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "case",
    ["option_512", "option_513", "message_4096", "message_4097", "both_lengths", "malformed_shape"],
)
def test_renderability_warning_boundaries(case):
    now = _renderability_context()
    issue = _decision_issue(created_at="2026-10-15T10:00:00Z")
    if case.startswith("option_"):
        issue["metadata"] = {
            "decision": {"options": ["x" * int(case[-3:]), "Hold"], "default": "Hold"}
        }
    elif case == "both_lengths":
        issue["metadata"] = {"decision": {"options": ["x" * 4100, "Hold"], "default": "Hold"}}
    elif case == "malformed_shape":
        issue["metadata"] = {"decision": {"options": ["x" * 513, None, ""], "default": "missing"}}
    else:
        wanted = int(case[-4:])
        issue["title"] = ""
        # Empty titles use the bead id, so start with one literal character.
        issue["title"] = "t"
        initial, lower_bound = ldb._estimated_prompt_length(issue, now=now)
        assert lower_bound is False
        issue["title"] += "t" * (wanted - initial)
        assert ldb._estimated_prompt_length(issue, now=now)[0] == wanted
    result = ldb.lint_issue(issue, now=now)
    assert result.ok is (case != "malformed_shape")
    assert any("option" in w for w in result.warnings) is (
        case in {"option_513", "both_lengths", "malformed_shape"}
    )
    assert any("prompt" in w for w in result.warnings) is (case in {"message_4097", "both_lengths"})
    assert all("x" * 100 not in w for w in result.warnings)
    if case == "malformed_shape":
        assert any("non-blank strings" in v for v in result.violations)
        assert ldb._estimated_prompt_length(issue, now=now) is None
        assert ldb.lint_issue(None).warnings == []
        assert ldb.lint_issue(_decision_issue(metadata=[])).warnings == []
    first = ldb.LintResult("one", "one")
    second = ldb.LintResult("two", "two")
    first.warnings.append("independent")
    assert second.warnings == []


@pytest.mark.parametrize(
    "case",
    [
        "hours",
        "days_default_repeat",
        "future_creation",
        "offset_due",
        "naive_due",
        "missing_due",
        "unicode",
        "newlines_two_digit_numbering",
        "incomplete_dates_lower_bound",
    ],
)
def test_estimated_prompt_length_follows_current_template(case):
    from datetime import timedelta

    now = _renderability_context()
    issue = _decision_issue(created_at=(now - timedelta(hours=3)).isoformat())
    if case == "days_default_repeat":
        issue["created_at"] = (now - timedelta(days=14)).isoformat()
        issue["metadata"] = {"decision": {"options": ["d" * 500, "Hold"], "default": "d" * 500}}
    elif case == "future_creation":
        issue["created_at"] = (now + timedelta(days=1)).isoformat()
    elif case == "offset_due":
        issue["due_at"] = "2026-10-16T00:30:00+08:00"
    elif case == "naive_due":
        issue["due_at"] = "2026-10-16T00:30:00"
    elif case == "missing_due":
        issue.pop("due_at")
    elif case == "unicode":
        issue["title"] = "🙂e\u0301 title"
        issue["metadata"] = {"decision": {"options": ["🙂" * 512, "e\u0301"], "default": "e\u0301"}}
    elif case == "newlines_two_digit_numbering":
        issue["metadata"] = {
            "decision": {"options": [f"{i}\n next " for i in range(12)], "default": "10\n next "}
        }
    elif case == "incomplete_dates_lower_bound":
        issue["created_at"] = "not-a-date"
        issue["due_at"] = "present-but-invalid"
        issue["title"] = "t" * 4097
    # Fixed receipts from the independent formatter before product retirement.
    expected = {
        "hours": (181, False),
        "days_default_repeat": (1163, False),
        "future_creation": (181, False),
        "offset_due": (181, False),
        "naive_due": (181, False),
        "missing_due": (165, True),
        "unicode": (645, False),
        "newlines_two_digit_numbering": (308, False),
        "incomplete_dates_lower_bound": (4224, True),
    }
    assert ldb._estimated_prompt_length(issue, now=now) == expected[case]
    result = ldb.lint_issue(issue, now=now)
    if case == "incomplete_dates_lower_bound":
        assert result.ok  # Existing linter checks due presence, not timestamp validity.
        assert any("lower bound" in w for w in result.warnings)
    if case == "missing_due":
        assert any("deadline" in v for v in result.violations)
    if case == "unicode":
        assert not any("option" in w for w in result.warnings)


def test_warning_cli_runs_with_only_stdlib(tmp_path):
    fixture = tmp_path / "isolated.json"
    fixture.write_text(json.dumps([_decision_issue(title="t" * 4097)]))
    proc = subprocess.run(
        [sys.executable, "-I", "-S", str(_SCRIPT), "--issues-json-file", str(fixture), "--json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout)[0]["warnings"]
