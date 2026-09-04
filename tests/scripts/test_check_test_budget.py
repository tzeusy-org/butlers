"""Contract tests for the per-lane collected-test budget ratchet."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_test_budget as budget  # noqa: E402

pytestmark = pytest.mark.unit


def _write_baseline(path: Path, budgets: dict[str, int]) -> Path:
    payload: dict[str, object] = {"note": "test"}
    payload.update({lane: {"budget": value} for lane, value in budgets.items()})
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stub_counts(monkeypatch: pytest.MonkeyPatch, counts: dict[str, int]) -> None:
    monkeypatch.setattr(budget, "collect_counts", lambda *, repo_root: dict(counts))


def test_compare_reports_lane_count_budget_and_remedy() -> None:
    errors = budget.compare({"unit": 105, "integration": 40}, {"unit": 100, "integration": 50})

    assert len(errors) == 1
    assert errors[0].startswith("::error::")
    for fragment in ("'unit'", "105", "budget is 100", "+5", "--update-baseline", "PR body"):
        assert fragment in errors[0]


def test_compare_fails_closed_on_missing_or_stale_lanes() -> None:
    errors = budget.compare({"unit": 1, "perf": 1}, {"unit": 5, "integration": 5})

    assert any("'perf' has no budget" in line for line in errors)
    assert any("'integration' is not a CI lane" in line for line in errors)


def test_propose_budgets_adds_five_percent_rounded_up() -> None:
    assert budget.propose_budgets({"unit": 100, "integration": 33}) == {
        "unit": 105,
        "integration": 35,
    }


@pytest.mark.parametrize(
    "payload",
    ["[]", '{"note": "x"}', '{"unit": {"budget": "9"}}', '{"unit": {"budget": -1}}', "{nope"],
)
def test_load_baseline_rejects_malformed_files(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(budget.BudgetError):
        budget.load_baseline(path)


def test_main_exit_codes_follow_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write_baseline(tmp_path / "baseline.json", {"unit": 100, "integration": 50})
    _stub_counts(monkeypatch, {"unit": 100, "integration": 50})
    assert budget.main(["--baseline", str(baseline)]) == 0

    _stub_counts(monkeypatch, {"unit": 101, "integration": 50})
    assert budget.main(["--baseline", str(baseline)]) == 1
    assert "::error::test budget: lane 'unit'" in capsys.readouterr().out

    assert budget.main(["--baseline", str(baseline), "--print"]) == 0
    assert "::error::" not in capsys.readouterr().out

    assert budget.main(["--baseline", str(tmp_path / "missing.json")]) == 2


def test_update_baseline_writes_headroom_and_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    baseline = tmp_path / "baseline.json"
    _stub_counts(monkeypatch, {"unit": 200, "integration": 10})

    assert budget.main(["--baseline", str(baseline), "--update-baseline"]) == 0

    payload = json.loads(baseline.read_text(encoding="utf-8"))
    assert payload["unit"] == {"budget": 210, "collected_at_update": 200}
    assert payload["integration"] == {"budget": 11, "collected_at_update": 10}
    assert "update-baseline" in payload["note"]
    assert budget.load_baseline(baseline) == {"unit": 210, "integration": 11}
