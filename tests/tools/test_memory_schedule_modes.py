"""Tests for memory maintenance schedule execution modes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_memory_maintenance_schedules_use_job_mode_for_memory_enabled_butlers() -> None:
    """Memory maintenance schedules should run as deterministic jobs, not prompts."""
    import tomllib

    expected = {
        "general": {
            "memory-consolidation": "memory_consolidation",
            "memory-episode-cleanup": "memory_episode_cleanup",
        },
        "health": {
            "memory-consolidation": "memory_consolidation",
            "memory-episode-cleanup": "memory_episode_cleanup",
        },
        "relationship": {
            "memory-consolidation": "memory_consolidation",
            "memory-episode-cleanup": "memory_episode_cleanup",
        },
        "switchboard": {
            "memory-consolidation": "memory_consolidation",
            "memory-episode-cleanup": "memory_episode_cleanup",
        },
    }

    repo_root = Path(__file__).resolve().parents[2]
    for butler_name, schedules in expected.items():
        toml_path = repo_root / "roster" / butler_name / "butler.toml"
        with toml_path.open("rb") as fh:
            config = tomllib.load(fh)

        entries = config.get("butler", {}).get("schedule", [])
        by_name = {entry["name"]: entry for entry in entries}

        missing = set(schedules) - set(by_name)
        assert not missing, (
            f"Missing expected memory schedules for {butler_name!r}: {sorted(missing)}"
        )

        for schedule_name, job_name in schedules.items():
            entry = by_name[schedule_name]
            assert entry.get("dispatch_mode") == "job", (
                f"{butler_name!r} schedule {schedule_name!r} must set dispatch_mode='job'"
            )
            assert entry.get("job_name") == job_name, (
                f"{butler_name!r} schedule {schedule_name!r} must set job_name={job_name!r}"
            )
            assert "prompt" not in entry, (
                f"{butler_name!r} schedule {schedule_name!r} should not declare prompt mode"
            )
