"""Tests for switchboard schedule execution modes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_switchboard_deterministic_schedules_use_job_mode() -> None:
    """Deterministic switchboard schedules should be configured for native job dispatch."""
    import tomllib

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    by_name = {entry["name"]: entry for entry in schedules}
    deterministic_names = {
        "connector-stats-hourly-rollup",
        "connector-stats-daily-rollup",
        "connector-stats-pruning",
        "eligibility-sweep",
    }

    missing = deterministic_names - set(by_name)
    assert not missing, f"Missing switchboard deterministic schedules: {sorted(missing)}"

    for schedule_name in sorted(deterministic_names):
        entry = by_name[schedule_name]
        assert entry.get("mode") == "job", (
            f"Schedule {schedule_name!r} must declare mode='job', got {entry.get('mode')!r}"
        )
