"""Refill supply survives medication edits (bu-uc6wh2).

``medication_log_dose`` stamps each dose with the medication fact id current at log
time, and ``medication_update`` supersedes that fact (new id) on any edit. The refill
scan used to count only doses carrying the *current* id, so editing the dosage,
schedule or notes forgot every dose since the last fill and overstated supply. Doses
now count by the medication's stable subject; a quantity change (a refill) still
resets the count through ``quantity_updated_at``.

These run the real write tools and ``run_insight_scan`` against Postgres (CI-only).
"""

from __future__ import annotations

import shutil

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


async def _log_doses(pool, medication_id: str, count: int) -> None:
    from butlers.tools.health import medication_log_dose

    for _ in range(count):
        await medication_log_dose(pool, medication_id)


async def _refill_messages(pool) -> list[str]:
    from butlers.jobs._roster.health_jobs import run_insight_scan

    await run_insight_scan(pool)
    rows = await pool.fetch(
        "SELECT message FROM insight_candidates WHERE category = 'medication-refill'"
    )
    return [r["message"] for r in rows]


async def test_a_dosage_edit_between_doses_keeps_the_dose_count(health_pool):
    from butlers.tools.health import medication_add, medication_update

    med = await medication_add(health_pool, "Metformin", "500mg", "daily", quantity=10)
    await _log_doses(health_pool, str(med["id"]), 3)
    edited = await medication_update(health_pool, str(med["id"]), dosage="850mg")
    await medication_update(health_pool, str(edited["id"]), notes="with dinner")
    await _log_doses(health_pool, str(edited["id"]), 3)

    # 10 pills - 6 doses = 4 days left; forgetting the first 3 would say 7.
    (message,) = await _refill_messages(health_pool)
    assert "Metformin supply estimated to run out in 4 day(s)" in message


async def test_a_quantity_change_resets_the_dose_count(health_pool):
    from butlers.tools.health import medication_add, medication_update

    med = await medication_add(health_pool, "Lisinopril", "10mg", "daily", quantity=10)
    await _log_doses(health_pool, str(med["id"]), 6)
    refilled = await medication_update(health_pool, str(med["id"]), quantity=20)
    await _log_doses(health_pool, str(refilled["id"]), 8)

    # 20 pills - 8 doses since the refill = 12 days; the 6 earlier doses drew the old supply.
    (message,) = await _refill_messages(health_pool)
    assert "Lisinopril supply estimated to run out in 12 day(s)" in message


async def test_another_medications_doses_are_not_counted(health_pool):
    from butlers.tools.health import medication_add, medication_update

    metformin = await medication_add(health_pool, "Metformin", "500mg", "daily", quantity=10)
    other = await medication_add(health_pool, "Aspirin", "100mg", "daily", quantity=100)
    await medication_update(health_pool, str(other["id"]), dosage="75mg")
    await _log_doses(health_pool, str(other["id"]), 5)
    await _log_doses(health_pool, str(metformin["id"]), 2)

    # Aspirin (100 - 5) stays outside the window; Metformin counts only its own 2 doses.
    (message,) = await _refill_messages(health_pool)
    assert "Metformin supply estimated to run out in 8 day(s)" in message
