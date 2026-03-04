"""Tests for switchboard schedule execution modes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_switchboard_deterministic_schedules_use_job_mode() -> None:
    """Deterministic switchboard schedules should use dispatch_mode='job'."""
    import tomllib

    toml_path = Path(__file__).resolve().parents[2] / "roster" / "switchboard" / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)

    schedules = config.get("butler", {}).get("schedule", [])
    by_name = {entry["name"]: entry for entry in schedules}
    expected_jobs = {
        "eligibility-sweep": "eligibility_sweep",
        "memory-consolidation": "memory_consolidation",
        "memory-episode-cleanup": "memory_episode_cleanup",
    }

    # Verify rollup jobs were removed (replaced by OTel/Prometheus pipeline in butlers-ufzc)
    removed_jobs = {
        "connector-stats-hourly-rollup",
        "connector-stats-daily-rollup",
        "connector-stats-pruning",
    }
    still_present = removed_jobs & set(by_name)
    assert not still_present, (
        f"Rollup schedule entries should have been removed (butlers-ufzc): {sorted(still_present)}"
    )

    missing = set(expected_jobs) - set(by_name)
    assert not missing, f"Missing switchboard deterministic schedules: {sorted(missing)}"

    for schedule_name in sorted(expected_jobs):
        entry = by_name[schedule_name]
        assert entry.get("dispatch_mode") == "job", (
            "Schedule "
            f"{schedule_name!r} must declare dispatch_mode='job', "
            f"got {entry.get('dispatch_mode')!r}"
        )
        assert entry.get("job_name") == expected_jobs[schedule_name]
