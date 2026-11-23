"""The read-only raw-expiry smoke check's verdicts (bu-s11n0s.75).

Its queries run against Postgres in ``tests/integration/test_location_retention_job_integration.py``.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "location_retention_smoke.py"
_spec = importlib.util.spec_from_file_location("location_retention_smoke", _SCRIPT)
assert _spec is not None and _spec.loader is not None
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

_NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)


def _report(*, enforced: bool, **overrides: Any) -> dict[str, Any]:
    report: dict[str, Any] = {
        "now": _NOW,
        "policy": {"days": 30, "version": 2, "enforced": enforced, "updated_at": _NOW},
        "run": {
            "last_outcome": "succeeded",
            "last_started_at": _NOW - timedelta(minutes=20),
            "last_success_at": _NOW - timedelta(minutes=20),
            "consecutive_failures": 0,
            "last_error_class": None,
        },
        "grants": {"reconciled": {"grants": 3, "items": 40}},
        "grants_with_rows_since_switch": 0,
        "overdue_raw_fixes": 0,
        "open_conditions": {},
    }
    report.update(overrides)
    return report


@pytest.mark.parametrize("enforced", [True, False])
def test_a_healthy_state_has_no_problems(enforced: bool) -> None:
    assert smoke.problems(_report(enforced=enforced)) == []


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        pytest.param(_report(enforced=True, policy=None), ["policy_missing"], id="no-policy"),
        pytest.param(
            _report(
                enforced=True,
                run={
                    "last_started_at": _NOW,
                    "consecutive_failures": 2,
                    "last_error_class": "StaleRevisions",
                },
            ),
            ["run_failing:StaleRevisions"],
            id="failing",
        ),
        pytest.param(_report(enforced=True, run=None), ["run_stale"], id="never-ran"),
        pytest.param(
            _report(
                enforced=True,
                run={"last_started_at": _NOW - timedelta(hours=3), "consecutive_failures": 0},
            ),
            ["run_stale"],
            id="stale",
        ),
        pytest.param(
            _report(enforced=False, grants_with_rows_since_switch=1),
            ["granted_while_off"],
            id="granted-while-off",
        ),
        pytest.param(
            _report(enforced=True, grants={"failed": {"grants": 1, "items": 2}}),
            ["grant_failed"],
            id="grant-failed",
        ),
    ],
)
def test_each_problem_is_named(report: dict[str, Any], expected: list[str]) -> None:
    assert smoke.problems(report) == expected


def test_off_a_stale_run_is_not_a_problem() -> None:
    assert smoke.problems(_report(enforced=False, run=None)) == []


def test_the_rendered_report_is_json_with_its_problems() -> None:
    rendered = json.loads(smoke.render(_report(enforced=True, run=None)))
    assert rendered["problems"] == ["run_stale"]
    assert rendered["policy"]["updated_at"] == _NOW.isoformat()


def test_the_dsn_comes_only_from_stdin(monkeypatch, capsys) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr("sys.argv", ["smoke", "postgres://ignored"])
    assert smoke.main() == 2
    assert "stdin" in capsys.readouterr().err
