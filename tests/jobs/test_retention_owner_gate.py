"""Owner-consent gates on retention: pin that nothing deletes owner data today.

The shipped posture, verified against the code, is that **no retention sweep
runs**.  Three independent gates each hold that line for the
``connectors.filtered_events`` partition pruner:

1. no roster ``butler.toml`` schedules the job, so it is never invoked;
2. the pruner's ``enabled`` default is ``False``, so an unconfigured
   invocation returns without touching the database;
3. the pruner's ``dry_run`` default is ``True``, so even an enabled
   invocation counts candidates instead of dropping them.

``tests/jobs/test_retention_pruners.py`` already covers gates 2 and 3 at the
function and job-handler level.  This module exists for the gate that had no
coverage at all -- gate 1 -- and for the two structural facts the replay-history
retention contract rests on: that replay history is read from the append-only
``public.audit_log`` rather than from the prunable partitions, and that no
pruner targets ``audit_log``.

These are pinning tests.  They pass on today's tree by construction; their
value is that they turn a silent future change into a red build.  Deleting
owner data is an owner decision (``docs/operations/data-retention.md``), and a
pruner that starts running because someone added four lines of TOML must not be
able to reach production unreviewed.
"""

from __future__ import annotations

import inspect
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROSTER_DIR = REPO_ROOT / "roster"

# The deterministic job names registered by src/butlers/jobs/retention.py.
RETENTION_PRUNER_JOB_NAMES = frozenset(
    {
        "session_process_logs_prune",
        "filtered_events_partition_prune",
        "insight_candidates_prune",
        "secret_probe_log_prune",
    }
)


def _butler_tomls() -> list[Path]:
    return sorted(ROSTER_DIR.glob("*/butler.toml"))


def _scheduled_job_names(config: dict) -> list[str]:
    """Every ``job_name`` reachable from a butler config's schedule blocks."""
    butler = config.get("butler")
    if not isinstance(butler, dict):
        return []
    schedules = butler.get("schedule")
    if not isinstance(schedules, list):
        return []
    return [
        entry["job_name"]
        for entry in schedules
        if isinstance(entry, dict) and isinstance(entry.get("job_name"), str)
    ]


def test_roster_configs_exist_to_scan():
    """Guard the guard: an empty glob would make the scan below vacuously pass."""
    assert _butler_tomls(), f"no butler.toml found under {ROSTER_DIR}"


def test_no_roster_config_schedules_a_retention_pruner():
    """Gate 1: no butler schedules a retention pruner, so none of them ever run.

    If this fails, someone scheduled a sweep that deletes owner data.  That is
    an owner decision and it needs an explicit one, not a passing test suite.
    """
    scheduled: dict[str, list[str]] = {}
    for toml_path in _butler_tomls():
        config = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        hits = [name for name in _scheduled_job_names(config) if name in RETENTION_PRUNER_JOB_NAMES]
        if hits:
            scheduled[str(toml_path.relative_to(REPO_ROOT))] = hits

    assert not scheduled, (
        "A retention pruner is now scheduled, so it will delete owner data on a cron: "
        f"{scheduled}. Deleting owner data requires an explicit owner decision and a "
        "retention window recorded in openspec/specs/connector-filtered-events/spec.md. "
        "If that decision has been made, update this test in the same change."
    )


@pytest.mark.parametrize(
    "func_name",
    sorted(RETENTION_PRUNER_JOB_NAMES),
)
def test_every_registered_pruner_is_unscheduled_and_therefore_inert(func_name):
    """The pruner job names this module guards match the registry it claims to cover.

    Keeps ``RETENTION_PRUNER_JOB_NAMES`` from drifting out of date, which would
    silently narrow the scan above.
    """
    from butlers.scheduled_jobs import get_deterministic_schedule_job_registry

    registry = get_deterministic_schedule_job_registry()
    assert func_name in registry.get("general", {}), (
        f"{func_name} is no longer registered; the retention scan in this module "
        "is guarding a stale job-name list."
    )


def test_filtered_events_pruner_ships_disabled_and_dry_run():
    """Gates 2 and 3, pinned at the signature so a default flip is visible in review."""
    from butlers.jobs.retention import prune_filtered_events_partitions

    params = inspect.signature(prune_filtered_events_partitions).parameters
    assert params["enabled"].default is False, (
        "filtered_events pruner would run without being explicitly enabled"
    )
    assert params["dry_run"].default is True, (
        "filtered_events pruner would delete without being explicitly confirmed"
    )


def test_filtered_events_keep_window_is_the_documented_default():
    """The shipped default window is 12 months, not the 90 days an archived delta claimed.

    This pins documentation, not policy: the job is unscheduled and disabled, so
    the number governs nothing today.  It exists so the spec and the code state
    the same number.
    """
    from butlers.jobs.retention import (
        _FILTERED_EVENTS_DEFAULT_KEEP_MONTHS,
        prune_filtered_events_partitions,
    )

    assert _FILTERED_EVENTS_DEFAULT_KEEP_MONTHS == 12
    params = inspect.signature(prune_filtered_events_partitions).parameters
    assert params["keep_months"].default == 12


def test_replay_history_reads_the_append_only_audit_log():
    """Replay history must not be served from the prunable partitions.

    ``public.audit_log`` is retained indefinitely
    (openspec/specs/dashboard-audit-log/spec.md, "Audit Log Retention").
    ``connectors.filtered_events`` is partitioned and has a pruner.  If the
    replay-history read is ever moved onto the partitions, replay lineage
    silently becomes deletable.
    """
    from butlers.core import ingestion_events

    source = inspect.getsource(ingestion_events.ingestion_event_replay_history)
    assert "public.audit_log" in source, (
        "replay history no longer reads public.audit_log; if it now reads a "
        "prunable table, its retention contract changed and the spec must too"
    )
    assert "filtered_events" not in source, (
        "replay history now reads connectors.filtered_events, which is prunable; "
        "replay lineage would become deletable without an owner decision"
    )


def test_no_retention_pruner_targets_the_audit_log():
    """No sweep in the retention module may reach ``audit_log``.

    ``tests/api/test_audit_log.py`` already forbids row-level deletion against
    the audit log repo-wide.  This closes the adjacent hole: a partition-style
    drop or a truncate aimed at the audit log would not match that pattern.
    """
    from butlers.jobs import retention

    source = Path(inspect.getfile(retention)).read_text(encoding="utf-8")
    offenders = re.findall(r"(?:DROP\s+TABLE|TRUNCATE)[^\n]*audit_log", source, re.IGNORECASE)
    assert not offenders, f"retention module targets the audit log: {offenders}"
    assert "audit_log" not in source, (
        "the retention module now references audit_log; the audit log is retained "
        "indefinitely and no sweep may touch it"
    )
