"""Clinical records never decay out of the active readers (bu-bvzjhw).

Doses and symptoms used to be written ``standard`` (decay 0.008/day) and medications,
conditions and measurements ``stable`` (0.002/day).  ``run_decay_sweep`` fades a fact
once ``confidence * exp(-decay_rate * days)`` falls under the class threshold, and the
adherence report and briefing read only ``validity = 'active'``, so records vanished
from them after about 201 days.  They are now written ``permanent`` and ``health_003``
corrects the rows already written.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "003_clinical_records_never_decay.py"
)
_OLD = "now() - interval '400 days'"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    from butlers.db import register_jsonb_codec

    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.memory_links, public.facts CASCADE")
    yield p
    await p.close()


async def _age(pool, predicate_like: str, *, validity: str | None = None) -> None:
    """Make every matching fact look 400 days old, optionally forcing a validity."""
    await pool.execute(
        f"UPDATE facts SET created_at = {_OLD}, last_confirmed_at = {_OLD} WHERE predicate LIKE $1",
        predicate_like,
    )
    if validity is not None:
        await pool.execute(
            "UPDATE facts SET validity = $2 WHERE predicate LIKE $1", predicate_like, validity
        )


async def _write_every_clinical_record(pool) -> None:
    from butlers.tools.health import (
        condition_add,
        condition_update,
        measurement_log,
        medication_add,
        medication_log_dose,
        medication_update,
        symptom_log,
    )

    med = await medication_add(pool, "RetentionMed", "10mg", "daily")
    await medication_update(pool, str(med["id"]), dosage="20mg")
    await medication_log_dose(pool, str(med["id"]))
    cond = await condition_add(pool, "RetentionCondition")
    await condition_update(pool, str(cond["id"]), status="managed")
    await symptom_log(pool, "RetentionSymptom", 4)
    await measurement_log(pool, "weight", {"kg": 70.0})


async def test_every_clinical_write_site_is_permanent(pool) -> None:
    await _write_every_clinical_record(pool)

    rows = await pool.fetch(
        "SELECT predicate, permanence, decay_rate FROM facts WHERE scope = 'health'"
    )

    assert {r["predicate"] for r in rows} >= {
        "medication",
        "took_dose",
        "condition",
        "symptom",
        "measurement_weight",
    }
    assert {(r["permanence"], r["decay_rate"]) for r in rows} == {("permanent", 0.0)}


async def test_decay_sweep_never_fades_clinical_records_but_still_fades_others(pool) -> None:
    from butlers.modules.memory.storage import run_decay_sweep
    from butlers.tools.health import medication_add, medication_log_dose, trend_report

    med = await medication_add(pool, "SweepMed", "10mg", "daily")
    await medication_log_dose(pool, str(med["id"]))
    # A control that must still decay proves the sweep ran over aged data.
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler) "
        "VALUES ('control', 'control_note', 'x', 'health', 'standard', 0.008, 'active', "
        "1.0, 5.0, 'health')"
    )
    await _age(pool, "%")

    stats = await run_decay_sweep(pool)

    assert stats["facts_checked"] == 1  # only the control still decays
    assert stats["facts_fading"] + stats["facts_expired"] == 1
    validity = {
        r["predicate"]: r["validity"]
        for r in await pool.fetch("SELECT predicate, validity FROM facts")
    }
    assert validity["control_note"] != "active"
    assert validity["took_dose"] == "active"
    assert validity["medication"] == "active"
    # Readers filter validity='active'; the dose and medication must still be there.
    report = await trend_report(pool, "week")
    assert [m["taken_doses"] for m in report["medication_adherence"]] == [1]


def _run_migration_sql() -> list[str]:
    spec = importlib.util.spec_from_file_location("health_003", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert (mod.revision, mod.down_revision) == ("health_003", "health_002")
    sqls: list[str] = []
    mock_op = MagicMock()
    mock_op.execute.side_effect = sqls.append
    with patch.object(mod, "op", mock_op):
        mod.upgrade()
    return sqls


async def test_health_003_restores_decayed_clinical_rows_and_touches_nothing_else(pool) -> None:
    await _write_every_clinical_record(pool)
    # Re-create the pre-fix state: decaying classes, faded and expired rows.
    # Historical write classes: doses and symptoms 'standard'; the rest 'stable'.
    await pool.execute(
        "UPDATE facts SET permanence = 'standard', decay_rate = 0.008 "
        "WHERE predicate IN ('took_dose', 'symptom')"
    )
    await pool.execute(
        "UPDATE facts SET permanence = 'stable', decay_rate = 0.002 "
        "WHERE predicate IN ('medication', 'condition') OR predicate = 'measurement_weight'"
    )
    await _age(pool, "took_dose", validity="fading")
    await _age(pool, "symptom", validity="expired")
    await pool.execute(
        "UPDATE facts SET metadata = metadata || "
        '\'{"archived_at": "2026-01-01", "archived_content": true}\'::jsonb '
        "WHERE predicate = 'symptom'"
    )
    await _age(pool, "measurement\\_%", validity="fading")
    # Rows that must stay as they are.
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler) VALUES "
        "('c1', 'control_note', 'x', 'health', 'standard', 0.008, 'fading', 1.0, 5.0, 'health'), "
        "('c2', 'took_dose', 'x', 'general', 'standard', 0.008, 'fading', 1.0, 5.0, 'general'), "
        "('c3', 'took_dose', 'x', 'health', 'standard', 0.008, 'superseded', 1.0, 5.0, 'health'), "
        # Wellness ingest shares the measurement_* names but writes 'standard'.
        "('w1', 'measurement_resting_hr', 'x', 'health', 'standard', 0.008, 'fading', 1.0, 5.0, "
        "'health')"
    )

    for sql in _run_migration_sql():
        await pool.execute(sql)

    clinical = await pool.fetch(
        "SELECT predicate, permanence, decay_rate, validity, metadata FROM facts "
        "WHERE subject NOT IN ('c1', 'c2', 'c3', 'w1')"
    )
    assert {r["predicate"] for r in clinical} >= {
        "medication",
        "took_dose",
        "condition",
        "symptom",
        "measurement_weight",
    }
    current = [r for r in clinical if r["validity"] != "superseded"]
    assert all(r["permanence"] == "permanent" and r["decay_rate"] == 0.0 for r in current)
    # Only superseded history may stay out of 'active'.
    assert {r["validity"] for r in clinical} <= {"active", "superseded"}
    for predicate in ("took_dose", "symptom", "measurement_weight"):
        assert {r["validity"] for r in clinical if r["predicate"] == predicate} == {"active"}
    for predicate in ("medication", "condition"):
        live = [r for r in clinical if r["predicate"] == predicate and r["validity"] == "active"]
        assert len(live) == 1
    symptom = next(r for r in clinical if r["predicate"] == "symptom")
    assert "archived_at" not in symptom["metadata"]
    untouched = {
        r["subject"]: (r["permanence"], r["validity"])
        for r in await pool.fetch(
            "SELECT subject, permanence, validity FROM facts "
            "WHERE subject IN ('c1', 'c2', 'c3', 'w1')"
        )
    }
    assert untouched == {
        "c1": ("standard", "fading"),
        "c2": ("standard", "fading"),
        "c3": ("standard", "superseded"),
        "w1": ("standard", "fading"),
    }


async def test_health_003_does_not_revive_an_expired_record_with_a_newer_successor(pool) -> None:
    await pool.execute(
        "INSERT INTO facts (subject, predicate, content, scope, permanence, decay_rate, "
        "validity, confidence, importance, source_butler, created_at) VALUES "
        # Deleted by the owner after it expired: v2 is a newer retracted row for the same key.
        "('gone', 'medication', 'v1', 'health', 'stable', 0.002, 'expired', 1.0, 5.0, 'health', "
        "now() - interval '300 days'), "
        "('gone', 'medication', 'v2', 'health', 'stable', 0.002, 'retracted', 1.0, 5.0, 'health', "
        "now() - interval '200 days'), "
        # An expired record with no successor returns.
        "('kept', 'medication', 'v1', 'health', 'stable', 0.002, 'expired', 1.0, 5.0, 'health', "
        "now() - interval '300 days')"
    )

    for sql in _run_migration_sql():
        await pool.execute(sql)

    state = {
        (r["subject"], r["content"]): r["validity"]
        for r in await pool.fetch("SELECT subject, content, validity FROM facts")
    }
    assert state == {
        ("gone", "v1"): "expired",
        ("gone", "v2"): "retracted",
        ("kept", "v1"): "active",
    }
