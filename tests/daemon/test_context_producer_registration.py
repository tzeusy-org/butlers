"""Context-bus producer schedule + registry wiring (RFC 0009, bu-hmdqz.15).

Every ``dispatch_mode="job"`` context-producer schedule declared in a butler's
``butler.toml`` must resolve to a callable handler in the deterministic job
registry under that butler, or it silently never runs.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_ROSTER = Path(__file__).resolve().parents[2] / "roster"

# (butler, schedule name, expected job_name, expected cron)
_PRODUCERS = [
    ("general", "context_producer_calendar", "context_producer_calendar", "*/10 * * * *"),
    ("home", "context_producer_home_presence", "context_producer_home_presence", "*/10 * * * *"),
    ("travel", "context_producer_travel", "context_producer_travel", "*/15 * * * *"),
    ("health", "context_producer_sleep_window", "context_producer_sleep_window", "*/15 * * * *"),
]


def _schedules(butler: str) -> list[dict[str, Any]]:
    with (_ROSTER / butler / "butler.toml").open("rb") as fh:
        config = tomllib.load(fh)
    return config.get("butler", {}).get("schedule", [])


@pytest.mark.parametrize("butler,name,job_name,cron", _PRODUCERS)
def test_producer_schedule_declared(butler, name, job_name, cron):
    entry = next(e for e in _schedules(butler) if e["name"] == name)
    assert entry["dispatch_mode"] == "job"
    assert entry["job_name"] == job_name
    assert entry["cron"] == cron


@pytest.mark.parametrize("butler,name,job_name,cron", _PRODUCERS)
def test_producer_job_resolves_to_handler(butler, name, job_name, cron):
    from butlers.daemon import (
        _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
        _resolve_deterministic_schedule_job_name,
    )

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name=butler,
        trigger_source=f"schedule:{name}",
        job_name=job_name,
    )
    assert resolved == job_name
    assert callable(_DETERMINISTIC_SCHEDULE_JOB_REGISTRY[butler].get(job_name))
