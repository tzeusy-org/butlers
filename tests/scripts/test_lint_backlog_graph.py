"""Behavioral contract for the read-only backlog graph lint (bu-1mbyoa.1)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import lint_backlog_graph as lint  # noqa: E402

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 21, tzinfo=UTC)
SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lint_backlog_graph.py"


def issue(id, **changes):
    return {
        "_type": "issue",
        "id": id,
        "title": f"Work {id}",
        "status": "open",
        "issue_type": "task",
        "dependencies": [],
        **changes,
    }


def edge(target, kind="blocks"):
    return {"type": kind, "depends_on_id": target}


def findings(issues, rule):
    return lint.lint_issues(issues, now=NOW, rules={rule})


def cli(path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--issues-json-file", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_parent_blocking_own_live_child():
    parent = issue("p", issue_type="epic", dependencies=[edge("c")])
    child = issue("c", dependencies=[edge("p", "parent-child")])
    assert [r["id"] for r in findings([parent, child], "G1")] == ["p"]
    child["status"] = "closed"
    assert findings([parent, child], "G1") == []


@pytest.mark.parametrize("indirect", [False, True])
def test_leaf_waiting_on_parent_or_ancestor(indirect):
    root = issue("root", issue_type="epic")
    parent = issue("p", issue_type="epic", dependencies=[edge("root", "parent-child")])
    leaf = issue(
        "leaf", dependencies=[edge("p", "parent-child"), edge("root" if indirect else "p")]
    )
    assert [r["id"] for r in findings([root, parent, leaf], "G2")] == ["leaf"]
    leaf["dependencies"][-1] = edge("unrelated")
    assert findings([root, parent, leaf, issue("unrelated")], "G2") == []


def test_blocked_with_all_closed_blockers_and_unknown_blocker():
    blocked = issue("b", status="blocked", dependencies=[edge("d")])
    done = issue("d", status="closed")
    assert len(findings([blocked, done], "G3")) == 1
    assert findings([blocked, issue("d")], "G3") == []
    assert findings([blocked], "G3") == []  # Missing evidence is not a closed blocker.
    blocked["dependencies"] = []
    assert findings([blocked, done], "G3") == []


def test_unexplained_block_exempts_assigned_and_review_owner_external_labels():
    blocked = issue("b", status="blocked")
    assert len(findings([blocked], "G4")) == 1
    assert findings([{**blocked, "assignee": "human-owner"}], "G4") == []
    for label in ["pr-review", "pr-review-task", "owner-gated", "external-gate"]:
        assert findings([{**blocked, "labels": [label]}], "G4") == []
    assert findings([{**blocked, "dependencies": [edge("missing")]}], "G4") == []


def test_claimable_non_epic_parent_with_live_child():
    parent = issue("p")
    child = issue("c", dependencies=[edge("p", "parent-child")])
    assert [r["id"] for r in findings([parent, child], "G5")] == ["p"]
    assert findings([{**parent, "issue_type": "epic"}, child], "G5") == []
    assert findings([parent, {**child, "status": "closed"}], "G5") == []


def test_owner_label_requires_open_decision_or_owner_blocker():
    task = issue("task", labels=["owner-gated"], dependencies=[edge("gate")])
    assert len(findings([task, issue("gate")], "G6")) == 1
    for gate in [issue("gate", labels=["decision"]), issue("gate", assignee="tze")]:
        assert findings([task, gate], "G6") == []
        for status in ("in_progress", "blocked", "deferred"):
            gate["status"] = status
            assert findings([task, gate], "G6") == []
        gate["status"] = "closed"
        assert len(findings([task, gate], "G6")) == 1
        gate["status"] = "unknown"
        assert len(findings([task, gate], "G6")) == 1
        for status in (None, "", []):
            with pytest.raises(lint.InputUnavailableError):
                findings([task, {**gate, "status": status}], "G6")
    assert len(findings([task], "G6")) == 1
    assert len(findings([{**task, "dependencies": []}], "G6")) == 1
    for status in ("open", "in_progress", "blocked", "deferred"):
        assert len(findings([task, issue("gate", status=status)], "G6")) == 1
    assert findings([{**task, "assignee": "tze"}], "G6") == []
    assert findings([{**task, "status": "blocked"}], "G6") == []


@pytest.mark.parametrize("actor", ["coordinator:example", "agent:example"])
def test_stale_claim_uses_latest_heartbeat_and_update_boundary(actor):
    stale = issue("claim", assignee=actor, updated_at=(NOW - timedelta(hours=25)).isoformat())
    assert len(findings([stale], "G7")) == 1
    stale["notes"] = (
        "[beads-heartbeat]\nlast_heartbeat_at=2026-10-18T00:00:00Z\n[/beads-heartbeat]\n"
        "[beads-heartbeat]\nlast_heartbeat_at=2026-10-20T23:00:00Z\n[/beads-heartbeat]"
    )
    assert findings([stale], "G7") == []
    stale["notes"] = ""
    stale["updated_at"] = (NOW - timedelta(hours=24)).isoformat()
    assert len(findings([stale], "G7")) == 1
    stale["updated_at"] = (NOW - timedelta(hours=24) + timedelta(seconds=1)).isoformat()
    assert findings([stale], "G7") == []
    assert findings([{**stale, "status": "closed"}], "G7") == []
    assert findings([{**stale, "assignee": "human-owner"}], "G7") == []


def test_jsonl_array_cli_shape_rule_filter_and_report_only(tmp_path):
    records = [
        issue("p", dependencies=[edge("c")]),
        issue("c", dependencies=[edge("p", "parent-child")]),
    ]
    array = tmp_path / "array.json"
    array.write_text(json.dumps(records))
    lines = tmp_path / "export.jsonl"
    lines.write_text("\n".join(json.dumps(r) for r in records))
    a, b = cli(array, "--json"), cli(lines, "--json")
    assert a.returncode == b.returncode == 1
    results = json.loads(a.stdout)
    assert results == json.loads(b.stdout)
    assert results
    for result in results:
        assert set(result) == {"id", "title", "ok", "violations"}
        assert isinstance(result["id"], str) and isinstance(result["title"], str)
        assert isinstance(result["ok"], bool)
        assert isinstance(result["violations"], list)
        assert all(isinstance(v, str) and "Fix:" in v for v in result["violations"])
        assert result["ok"] == (not result["violations"])
    filtered = cli(array, "--json", "--rules", "G1")
    assert [r["id"] for r in json.loads(filtered.stdout)] == ["p"]
    report = cli(array, "--json", "--report-only")
    assert report.returncode == 0 and json.loads(report.stdout) == results
    assert cli(array, "--rules", "G3").returncode == 0


def test_warning_only_cli_is_success_but_reports_actionable_finding(tmp_path):
    path = tmp_path / "issues.json"
    path.write_text(
        json.dumps([issue("old", assignee="coordinator:old", updated_at="2000-01-01T00:00:00Z")])
    )
    result = cli(path, "--json")
    assert result.returncode == 0
    assert json.loads(result.stdout)[0]["violations"][0].startswith("G7 stale-claim:")
    text = cli(path)
    assert text.returncode == 0 and "warning" in text.stdout.lower() and "Fix:" in text.stdout


@pytest.mark.parametrize(
    ("out_of_range", "boundary", "boundary_is_stale"),
    [
        ("0001-01-01T00:00:00+01:00", "0001-01-01T01:00:00+01:00", True),
        ("9999-12-31T23:59:59-01:00", "9999-12-31T22:59:59-01:00", False),
    ],
)
def test_cli_unrepresentable_utc_timestamps_preserve_findings(
    tmp_path, out_of_range, boundary, boundary_is_stale
):
    def heartbeat(timestamp):
        return f"[beads-heartbeat]\nlast_heartbeat_at={timestamp}\n[/beads-heartbeat]"

    path = tmp_path / "issues.json"
    path.write_text(
        json.dumps(
            [
                issue("bad-update", assignee="agent:example", updated_at=out_of_range),
                issue(
                    "bad-heartbeat",
                    assignee="agent:example",
                    updated_at="2000-01-01T00:00:00Z",
                    notes=heartbeat(out_of_range),
                ),
                issue("boundary", assignee="agent:example", updated_at=boundary),
                issue(
                    "fresh-heartbeat",
                    assignee="agent:example",
                    updated_at=out_of_range,
                    notes=heartbeat("9999-12-31T23:59:59Z"),
                ),
                issue("parent", dependencies=[edge("child")]),
                issue("child", dependencies=[edge("parent", "parent-child")]),
            ]
        )
    )
    expected = {"bad-update", "bad-heartbeat"}
    if boundary_is_stale:
        expected.add("boundary")
    warning_only = cli(path, "--json", "--rules", "G7")
    assert warning_only.returncode == 0 and not warning_only.stderr
    assert {result["id"] for result in json.loads(warning_only.stdout)} == expected
    complete = cli(path, "--json")
    assert complete.returncode == 1 and not complete.stderr
    results = json.loads(complete.stdout)
    assert {result["id"] for result in results} == expected | {"parent"}
    assert any(result["violations"][0].startswith("G1 ") for result in results)


@pytest.mark.parametrize("content", [None, "{broken", '[{"id":"x"}]', "[null]"])
def test_unavailable_or_malformed_input_is_exit_two(tmp_path, content):
    path = tmp_path / "input.json"
    if content is not None:
        path.write_text(content)
    result = cli(path, "--json", "--report-only")
    assert result.returncode == 2 and not result.stdout and result.stderr


def test_live_discovery_is_complete_read_only_and_refuses_failed_read(monkeypatch, capsys):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps([issue("healthy")]), stderr="")

    monkeypatch.setattr(lint.subprocess, "run", run)
    assert lint.main(["--json"]) == 0
    assert calls == [["bd", "list", "--all", "--limit", "0", "--json"]]
    assert json.loads(capsys.readouterr().out) == []
    monkeypatch.setattr(
        lint.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="private sentinel", stderr="private sentinel"
        ),
    )
    assert lint.main(["--json"]) == 2
    captured = capsys.readouterr()
    assert not captured.out and "private sentinel" not in captured.err


def test_unknown_rules_and_duplicate_identity_refuse(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps([issue("same"), issue("same")]))
    assert cli(path, "--json").returncode == 2
    path.write_text("[]")
    assert cli(path, "--rules", "G0").returncode == 2
