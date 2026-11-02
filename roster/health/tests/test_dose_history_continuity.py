"""Dose history survives medication edits on every read surface (bu-2eaep2).

``medication_log_dose`` stamps each dose with the medication fact id current at log
time, and ``medication_update`` supersedes that fact (new id) on any edit. Reads that
matched only the *current* id forgot every dose logged before the last edit. Doses now
belong to every fact of the medication's stable subject, across all validities; dose
records are never rewritten. The refill scan got the same fix in bu-uc6wh2.

These run the real write tools against Postgres (CI-only).
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def health_pool(db):
    """Core and memory (``public.facts``), the health chain, and switchboard for the
    insight tables the scan submits into (mirrors ``test_jobs.health_pool``)."""
    return await db(
        "core",
        "memory",
        "health",
        "switchboard",
        schemas={"health": "health", "switchboard": "switchboard"},
        schema="health",
    )


async def _log_doses_days_ago(pool, medication_id: str, days: range) -> None:
    from butlers.tools.health import medication_log_dose

    now = datetime.now(UTC)
    for day in days:
        await medication_log_dose(pool, medication_id, taken_at=now - timedelta(days=day))


async def _log_flare(pool) -> None:
    from butlers.tools.health import symptom_log

    for _ in range(2):
        await symptom_log(pool, "nausea", 4)


async def _adherence_candidates(pool) -> list[str]:
    from butlers.jobs._roster.health_jobs import run_insight_scan

    await run_insight_scan(pool)
    rows = await pool.fetch(
        "SELECT dedup_key FROM insight_candidates WHERE category = 'correlation-adherence'"
    )
    return [r["dedup_key"] for r in rows]


async def test_adherence_dip_sees_history_logged_before_an_edit(health_pool):
    from butlers.tools.health import medication_add, medication_update

    med = await medication_add(health_pool, "Sertraline", "50mg", "daily")
    # Normal logging in the prior week, nothing in the dip week, then an edit.
    await _log_doses_days_ago(health_pool, str(med["id"]), range(15, 22))
    edited = await medication_update(health_pool, str(med["id"]), dosage="100mg")
    await _log_flare(health_pool)

    # Matching the current id alone found no prior history and skipped the dip.
    (dedup_key,) = await _adherence_candidates(health_pool)
    assert dedup_key.startswith(f"health:correlation-adherence:{edited['id']}:")


async def test_medication_history_keeps_doses_across_edits_and_excludes_others(health_pool):
    from butlers.tools.health import medication_add, medication_history, medication_update

    med = await medication_add(health_pool, "Metformin", "500mg", "daily")
    other = await medication_add(health_pool, "Aspirin", "100mg", "daily")
    await _log_doses_days_ago(health_pool, str(med["id"]), range(3, 6))
    await _log_doses_days_ago(health_pool, str(other["id"]), range(1, 5))
    edited = await medication_update(health_pool, str(med["id"]), dosage="850mg")
    await medication_update(health_pool, str(other["id"]), dosage="75mg")
    await _log_doses_days_ago(health_pool, str(edited["id"]), range(1, 3))

    history = await medication_history(health_pool, str(edited["id"]))

    assert len(history["doses"]) == 5
    assert history["medication"]["dosage"] == "850mg"


async def test_doses_endpoint_lists_doses_logged_before_an_edit(health_pool):
    from butlers.api._roster.health.router import list_medication_doses
    from butlers.tools.health import medication_add, medication_update

    med = await medication_add(health_pool, "Metformin", "500mg", "daily")
    await _log_doses_days_ago(health_pool, str(med["id"]), range(2, 5))
    edited = await medication_update(health_pool, str(med["id"]), dosage="850mg")
    await _log_doses_days_ago(health_pool, str(edited["id"]), range(0, 1))
    db = MagicMock()
    db.pool.return_value = health_pool

    doses = await list_medication_doses(str(edited["id"]), since=None, until=None, db=db)

    # Each dose keeps the id it was logged under; none is rewritten.
    assert sorted(d.medication_id for d in doses) == sorted(
        [str(med["id"])] * 3 + [str(edited["id"])]
    )


async def test_trend_report_adherence_counts_doses_logged_before_an_edit(health_pool):
    from butlers.tools.health import medication_add, medication_update, trend_report

    med = await medication_add(health_pool, "Metformin", "500mg", "daily")
    await _log_doses_days_ago(health_pool, str(med["id"]), range(1, 4))
    edited = await medication_update(health_pool, str(med["id"]), dosage="850mg")
    await _log_doses_days_ago(health_pool, str(edited["id"]), range(0, 1))

    report = await trend_report(health_pool, "week")

    (row,) = report["medication_adherence"]
    assert row["taken_doses"] == 4
