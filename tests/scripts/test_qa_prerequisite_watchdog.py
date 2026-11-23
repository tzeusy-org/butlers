"""Dry-run proofs for scripts/qa_prerequisite_watchdog.py (bu-ejow5p.8, REQ-qa-remediation-liveness-004)."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "qa_prerequisite_watchdog.py"
_spec = importlib.util.spec_from_file_location("qa_prerequisite_watchdog", _SCRIPT)
assert _spec and _spec.loader
wd = importlib.util.module_from_spec(_spec)
sys.modules["qa_prerequisite_watchdog"] = wd
_spec.loader.exec_module(wd)

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def _row(ident: str, status: str, assignee: str, updated: str) -> dict[str, str]:
    return {
        "_type": "issue",
        "id": ident,
        "status": status,
        "assignee": assignee,
        "updated_at": updated,
    }


def _export(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "export.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def _states(findings: list) -> dict[str, str]:
    return {f.item: f.state for f in findings}


def test_blocked_prerequisite_is_stalled_after_the_window(tmp_path: Path) -> None:
    path = _export(
        tmp_path,
        [
            _row("bu-ejow5p.11", "blocked", "lane-1", "2026-11-10T00:00:00Z"),
            _row("bu-6a9xxw.1", "closed", "", "2026-11-01T00:00:00Z"),
            _row("bu-gjmx8w", "in_progress", "lane-2", "2026-11-01T00:00:00Z"),
            _row("bu-ratgnb", "open", "", "2026-11-22T00:00:00Z"),
        ],
    )
    findings = wd.evaluate(wd.load_issues(path), NOW, days=7)
    states = _states(findings)
    assert states["bu-ejow5p.11"] == "stalled"
    assert states["bu-6a9xxw.1"] == "ok"
    assert states["bu-gjmx8w"] == "ok"  # owned and not blocked
    assert states["bu-ratgnb"] == "ok"  # unowned but only one day old
    assert wd.exit_code(findings) == 1


def test_unowned_open_item_stalls_and_family_prefix_matches_children(tmp_path: Path) -> None:
    path = _export(
        tmp_path,
        [
            _row("bu-ejow5p.11", "closed", "", "2026-11-01T00:00:00Z"),
            _row("bu-6a9xxw.1", "closed", "", "2026-11-01T00:00:00Z"),
            _row("bu-6a9xxw.2", "open", "", "2026-11-01T00:00:00Z"),
            _row("bu-gjmx8w", "closed", "", "2026-11-01T00:00:00Z"),
            _row("bu-ratgnb", "closed", "", "2026-11-01T00:00:00Z"),
        ],
    )
    findings = wd.evaluate(wd.load_issues(path), NOW)
    assert {f.item: f.state for f in findings if f.state == "stalled"} == {"bu-6a9xxw.2": "stalled"}


def test_missing_rows_and_unreadable_exports_claim_nothing(tmp_path: Path) -> None:
    only_one = _export(tmp_path, [_row("bu-gjmx8w", "closed", "", "2026-11-01T00:00:00Z")])
    findings = wd.evaluate(wd.load_issues(only_one), NOW)
    states = _states(findings)
    assert states["bu-ejow5p.11"] == "unknown"
    assert states["bu-6a9xxw.*"] == "unknown"
    assert wd.exit_code(findings) == 2

    missing = wd.evaluate(wd.load_issues(tmp_path / "absent.jsonl"), NOW)
    assert {f.state for f in missing} == {"unknown"}
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json\n")
    assert wd.load_issues(bad) is None


def test_cli_prints_report_and_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _export(tmp_path, [_row("bu-ejow5p.11", "blocked", "x", "2026-11-01T00:00:00Z")])
    code = wd.main([str(path), "--now", "2026-11-23T00:00:00Z"])
    out = capsys.readouterr().out
    assert code == 1
    assert "stalled  bu-ejow5p.11: blocked for 22d" in out
