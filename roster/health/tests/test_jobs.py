"""Tests for Health butler scheduled job handlers."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _today() -> date:
    return datetime.now(UTC).date()


# ---------------------------------------------------------------------------
# Schema setup helpers (health schema tables + public.facts from memory schema)
# ---------------------------------------------------------------------------

CREATE_HEALTH_SCHEMA = "CREATE SCHEMA IF NOT EXISTS health"
SET_HEALTH_SEARCH_PATH = "SET search_path TO health, public"

# public.facts stores health measurements via predicate = "measurement_{type}".
# This is the canonical write path; the old health.measurements table is no longer used.
CREATE_FACTS_SQL = """
CREATE TABLE IF NOT EXISTS public.facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT,
    search_vector TSVECTOR,
    importance FLOAT NOT NULL DEFAULT 5.0,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    decay_rate FLOAT NOT NULL DEFAULT 0.008,
    permanence TEXT NOT NULL DEFAULT 'standard',
    source_butler TEXT,
    source_episode_id UUID,
    supersedes_id UUID,
    validity TEXT NOT NULL DEFAULT 'active',
    scope TEXT NOT NULL DEFAULT 'global',
    reference_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ,
    tags JSONB DEFAULT '[]'::jsonb,
    metadata JSONB DEFAULT '{}'::jsonb,
    entity_id UUID,
    object_entity_id UUID,
    valid_at TIMESTAMPTZ DEFAULT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'owner',
    request_id TEXT,
    retention_class TEXT NOT NULL DEFAULT 'operational',
    sensitivity TEXT NOT NULL DEFAULT 'normal',
    idempotency_key TEXT,
    observed_at TIMESTAMPTZ DEFAULT now(),
    invalid_at TIMESTAMPTZ,
    embedding_model_version TEXT DEFAULT 'unknown'
)
"""

CREATE_MEDICATIONS_SQL = """
CREATE TABLE IF NOT EXISTS health.medications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    dosage TEXT NOT NULL,
    frequency TEXT NOT NULL,
    schedule JSONB NOT NULL DEFAULT '[]',
    active BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_MEDICATION_DOSES_SQL = """
CREATE TABLE IF NOT EXISTS health.medication_doses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    medication_id UUID NOT NULL REFERENCES health.medications(id),
    taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    skipped BOOLEAN NOT NULL DEFAULT false,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

CREATE_SYMPTOMS_SQL = """
CREATE TABLE IF NOT EXISTS health.symptoms (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 10),
    condition_id UUID,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


async def _setup_health_schema(pool) -> None:
    """Create the health schema and all required tables."""
    await pool.execute(CREATE_HEALTH_SCHEMA)
    await pool.execute(CREATE_FACTS_SQL)
    await pool.execute(CREATE_MEDICATIONS_SQL)
    await pool.execute(CREATE_MEDICATION_DOSES_SQL)
    await pool.execute(CREATE_SYMPTOMS_SQL)


async def _setup_insight_tables(pool) -> None:
    """Create insight_candidates and related tables for insight scan tests."""
    from butlers.tools.switchboard.insight.broker import create_insight_tables

    await create_insight_tables(pool)


# ---------------------------------------------------------------------------
# Helper insert functions
# ---------------------------------------------------------------------------


async def _insert_measurement(
    pool,
    *,
    mtype: str = "weight",
    value: dict | None = None,
    measured_at: datetime | None = None,
) -> str:
    """Insert a measurement fact into public.facts and return its UUID string.

    Measurements are stored as temporal facts with predicate = "measurement_{type}",
    scope = "health", validity = "active", valid_at = measured_at, and the value
    under metadata.value (scalar or compound dict) — the same surface
    ``measurement_log`` writes and the drift correlation reads.
    """
    if value is None:
        value = {"value": 70.0}
    if measured_at is None:
        measured_at = _utcnow()
    mid = str(uuid.uuid4())
    predicate = f"measurement_{mtype}"
    metadata = {"value": value.get("value", value) if isinstance(value, dict) else value}

    # Pass the dict so the pool's JSONB codec serializes it once (no double-encode);
    # otherwise metadata->>'value' would be NULL.
    await pool.execute(
        """
        INSERT INTO public.facts
            (id, subject, predicate, content, validity, scope, valid_at, metadata)
        VALUES ($1::uuid, 'owner', $2, $3, 'active', 'health', $4, $5)
        """,
        mid,
        predicate,
        f"{mtype}: {metadata['value']}",
        measured_at,
        metadata,
    )
    return mid


async def _insert_medication(
    pool,
    *,
    name: str = "Aspirin",
    dosage: str = "100mg",
    frequency: str = "daily",
    active: bool = True,
) -> str:
    """Insert a medication FACT into public.facts and return its UUID string.

    Medications are property facts (predicate='medication', scope='health',
    validity='active') with name/dosage/frequency/active in metadata — the same
    surface ``medication_add`` writes and ``run_insight_scan`` now reads.
    """
    med_id = str(uuid.uuid4())
    metadata = {
        "name": name,
        "dosage": dosage,
        "frequency": frequency,
        "schedule": [],
        "active": active,
    }
    # The pool registers a JSONB codec that serializes Python objects directly,
    # so pass the dict (not a pre-serialized string) to avoid double-encoding.
    await pool.execute(
        """
        INSERT INTO public.facts
            (id, subject, predicate, content, validity, scope, valid_at, metadata)
        VALUES ($1::uuid, $2, 'medication', $3, 'active', 'health', NULL, $4)
        """,
        med_id,
        f"medication:{name}",
        f"{name} {dosage} {frequency}",
        metadata,
    )
    return med_id


async def _insert_dose(
    pool,
    *,
    medication_id: str,
    taken_at: datetime | None = None,
    skipped: bool = False,
) -> str:
    """Insert a dose FACT into public.facts and return its UUID string.

    Doses are temporal facts (predicate='took_dose', scope='health',
    validity='active') with valid_at=taken_at and medication_id/skipped in
    metadata — the same surface ``medication_log_dose`` writes and
    ``run_insight_scan`` now reads.
    """
    if taken_at is None:
        taken_at = _utcnow()
    dose_id = str(uuid.uuid4())
    metadata = {"medication_id": str(medication_id), "skipped": skipped}
    # Pass the dict so the pool's JSONB codec serializes it once (no double-encode).
    await pool.execute(
        """
        INSERT INTO public.facts
            (id, subject, predicate, content, validity, scope, valid_at, metadata)
        VALUES ($1::uuid, 'owner', 'took_dose', $2, 'active', 'health', $3, $4)
        """,
        dose_id,
        "dose",
        taken_at,
        metadata,
    )
    return dose_id


async def _insert_symptom(
    pool,
    *,
    name: str = "headache",
    severity: int = 4,
    occurred_at: datetime | None = None,
) -> str:
    """Insert a symptom FACT into public.facts and return its UUID string.

    Symptoms are temporal facts (predicate='symptom', scope='health',
    validity='active') with content=name, valid_at=occurred_at, and severity in
    metadata — the same surface ``symptom_log`` writes and ``run_insight_scan``
    now reads.
    """
    if occurred_at is None:
        occurred_at = _utcnow()
    sym_id = str(uuid.uuid4())
    metadata = {"severity": severity}
    # Pass the dict so the pool's JSONB codec serializes it once (no double-encode).
    await pool.execute(
        """
        INSERT INTO public.facts
            (id, subject, predicate, content, validity, scope, valid_at, metadata)
        VALUES ($1::uuid, 'owner', 'symptom', $2, 'active', 'health', $3, $4)
        """,
        sym_id,
        name,
        occurred_at,
        metadata,
    )
    return sym_id


async def _insert_sleep_session(
    pool,
    *,
    duration_hours: float,
    occurred_at: datetime | None = None,
) -> str:
    """Insert a sleep_session FACT into public.facts and return its UUID string.

    Sleep sessions are temporal facts (predicate='sleep_session', scope='health',
    validity='active') with valid_at=session start and duration_ms in metadata —
    the same surface the google_health wellness ingest writes and the environment
    correlation reads.
    """
    if occurred_at is None:
        occurred_at = _utcnow()
    sleep_id = str(uuid.uuid4())
    metadata = {"duration_ms": int(duration_hours * 3_600_000)}
    await pool.execute(
        """
        INSERT INTO public.facts
            (id, subject, predicate, content, validity, scope, valid_at, metadata)
        VALUES ($1::uuid, 'owner', 'sleep_session', $2, 'active', 'health', $3, $4)
        """,
        sleep_id,
        "sleep session",
        occurred_at,
        metadata,
    )
    return sleep_id


# ---------------------------------------------------------------------------
# Tests: run_insight_scan — no data / no-op paths
# ---------------------------------------------------------------------------


async def test_insight_scan_no_data(provisioned_postgres_pool):
    """No-op: returns zeros when no health data exists."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        assert result["candidates_proposed"] == 0
        assert result["candidates_accepted"] == 0
        assert result["candidates_filtered"] == 0
        assert result["candidates_errored"] == 0
        assert result["early_exit"] is False


async def test_insight_scan_result_keys_present(provisioned_postgres_pool):
    """Result dict always contains all expected keys."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        assert "candidates_proposed" in result
        assert "candidates_accepted" in result
        assert "candidates_filtered" in result
        assert "candidates_errored" in result
        assert "early_exit" in result


# ---------------------------------------------------------------------------
# Tests: measurement gap insights
# ---------------------------------------------------------------------------


async def test_measurement_gap_no_gap_does_not_generate_candidate(provisioned_postgres_pool):
    """Recent measurements within normal cadence produce no gap insight."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # 10 daily measurements with the latest being today — no gap
        for i in range(10):
            await _insert_measurement(pool, mtype="weight", measured_at=now - timedelta(days=i))

        result = await run_insight_scan(pool)

        gap_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'measurement-gap'"
        )
        assert len(gap_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_measurement_gap_insufficient_history_excluded(provisioned_postgres_pool):
    """Types with fewer than 3 historical entries are excluded from gap detection."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # Only 2 measurements — below minimum of 3
        await _insert_measurement(pool, mtype="glucose", measured_at=now - timedelta(days=60))
        await _insert_measurement(pool, mtype="glucose", measured_at=now - timedelta(days=120))

        result = await run_insight_scan(pool)

        gap_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'measurement-gap'"
        )
        assert len(gap_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_measurement_gap_2x_cadence_generates_warning_candidate(provisioned_postgres_pool):
    """Gap exceeding 2x typical cadence generates a warning (priority 55) candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # 5 weekly measurements, last one 16 days ago (2.28x cadence of 7 days)
        for i in range(5):
            offset_days = 16 + (i * 7)
            await _insert_measurement(
                pool, mtype="blood_pressure", measured_at=now - timedelta(days=offset_days)
            )

        result = await run_insight_scan(pool)

        gap_rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE category = 'measurement-gap'"
        )
        assert len(gap_rows) == 1
        row = gap_rows[0]
        assert row["priority"] == 55
        assert "health:measurement-gap:blood_pressure" == row["dedup_key"]
        assert "blood_pressure" in row["message"]
        assert result["candidates_accepted"] == 1


async def test_measurement_gap_3x_cadence_generates_critical_candidate(provisioned_postgres_pool):
    """Gap exceeding 3x typical cadence generates a critical (priority 75) candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # 5 weekly measurements, last one 25 days ago (3.57x cadence of 7 days)
        for i in range(5):
            offset_days = 25 + (i * 7)
            await _insert_measurement(
                pool, mtype="weight", measured_at=now - timedelta(days=offset_days)
            )

        result = await run_insight_scan(pool)

        gap_rows = await pool.fetch(
            "SELECT priority FROM insight_candidates WHERE category = 'measurement-gap'"
        )
        assert len(gap_rows) == 1
        assert gap_rows[0]["priority"] == 75
        assert result["candidates_accepted"] == 1


async def test_measurement_gap_dedup_key_format(provisioned_postgres_pool):
    """Measurement gap dedup_key follows health:measurement-gap:{type} format."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(5):
            await _insert_measurement(
                pool, mtype="temperature", measured_at=now - timedelta(days=25 + i * 7)
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'measurement-gap'"
        )
        assert len(rows) == 1
        assert rows[0]["dedup_key"] == "health:measurement-gap:temperature"


async def test_measurement_gap_expires_3_days_from_now(provisioned_postgres_pool):
    """Measurement gap candidate expires_at is 3 days from generation."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(5):
            await _insert_measurement(
                pool, mtype="weight", measured_at=now - timedelta(days=25 + i * 7)
            )

        before = _utcnow()
        await run_insight_scan(pool)
        after = _utcnow()

        rows = await pool.fetch(
            "SELECT expires_at FROM insight_candidates WHERE category = 'measurement-gap'"
        )
        assert len(rows) == 1
        expires_at = rows[0]["expires_at"]
        # Should be approximately 3 days from now
        expected_min = before + timedelta(days=2, hours=23)
        expected_max = after + timedelta(days=3, hours=1)
        assert expected_min <= expires_at <= expected_max


# ---------------------------------------------------------------------------
# Tests: medication refill insights
# ---------------------------------------------------------------------------


async def test_medication_refill_inactive_medication_excluded(provisioned_postgres_pool):
    """Inactive medications are excluded from refill insights."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        med_id = await _insert_medication(pool, name="OldMed", active=False)
        now = _utcnow()
        for i in range(30):
            await _insert_dose(pool, medication_id=med_id, taken_at=now - timedelta(days=i))

        result = await run_insight_scan(pool)

        refill_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'medication-refill'"
        )
        assert len(refill_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_medication_refill_no_doses_excluded(provisioned_postgres_pool):
    """Active medications with no dose history in 30 days are excluded."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        await _insert_medication(pool, name="UnloggedMed", active=True)

        result = await run_insight_scan(pool)

        refill_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'medication-refill'"
        )
        assert len(refill_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_medication_refill_within_14_days_generates_candidate(provisioned_postgres_pool):
    """Active medication running out within 14 days generates a refill candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        med_id = await _insert_medication(pool, name="Metformin", frequency="daily", active=True)
        now = _utcnow()
        # Log 28 doses over the last 28 days (daily), depleting a 30-day supply
        for i in range(28):
            await _insert_dose(pool, medication_id=med_id, taken_at=now - timedelta(days=i))

        result = await run_insight_scan(pool)

        refill_rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE category = 'medication-refill'"
        )
        assert len(refill_rows) == 1
        row = refill_rows[0]
        assert "Metformin" in row["message"]
        assert "refill" in row["message"].lower()
        assert result["candidates_accepted"] == 1


async def test_medication_refill_critical_priority_within_3_days(provisioned_postgres_pool):
    """Medication depleting within 3 days gets priority 90 (time-critical)."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        med_id = await _insert_medication(pool, name="Insulin", frequency="daily", active=True)
        now = _utcnow()
        # Log 29 doses over the last 29 days — only ~1 day remaining
        for i in range(29):
            await _insert_dose(pool, medication_id=med_id, taken_at=now - timedelta(days=i))

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority FROM insight_candidates WHERE category = 'medication-refill'"
        )
        assert len(rows) == 1
        assert rows[0]["priority"] == 90


async def test_medication_refill_dedup_key_format(provisioned_postgres_pool):
    """Medication refill dedup_key follows health:medication-refill:{id} format."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        med_id = await _insert_medication(pool, name="Lisinopril", active=True)
        now = _utcnow()
        for i in range(28):
            await _insert_dose(pool, medication_id=med_id, taken_at=now - timedelta(days=i))

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'medication-refill'"
        )
        assert len(rows) == 1
        assert rows[0]["dedup_key"] == f"health:medication-refill:{med_id}"


async def test_medication_refill_skipped_doses_excluded_from_count(provisioned_postgres_pool):
    """Skipped doses are excluded when computing supply consumption."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        med_id = await _insert_medication(pool, name="TestMed", active=True)
        now = _utcnow()
        # Log 10 real doses and 20 skipped — should only count 10 consumed
        for i in range(10):
            await _insert_dose(
                pool, medication_id=med_id, taken_at=now - timedelta(days=i), skipped=False
            )
        for i in range(20):
            await _insert_dose(
                pool, medication_id=med_id, taken_at=now - timedelta(days=i + 10), skipped=True
            )

        result = await run_insight_scan(pool)

        # With only 10 doses consumed out of a 30-day supply, there are 20 days remaining
        # which is > 14, so no refill candidate should be generated
        refill_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'medication-refill'"
        )
        assert len(refill_rows) == 0
        assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: symptom trend insights
# ---------------------------------------------------------------------------


async def test_symptom_trend_below_threshold_no_candidate(provisioned_postgres_pool):
    """Symptom logged only twice in 7 days does not trigger a trend alert."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        await _insert_symptom(pool, name="nausea", severity=4, occurred_at=now - timedelta(days=1))
        await _insert_symptom(pool, name="nausea", severity=5, occurred_at=now - timedelta(days=3))

        result = await run_insight_scan(pool)

        trend_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'symptom-trend'"
        )
        assert len(trend_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_symptom_trend_low_severity_excluded(provisioned_postgres_pool):
    """Symptoms with severity below threshold are excluded even if frequent."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # 5 occurrences but severity 2 (below threshold of 3)
        for i in range(5):
            await _insert_symptom(
                pool, name="mild_discomfort", severity=2, occurred_at=now - timedelta(days=i)
            )

        result = await run_insight_scan(pool)

        trend_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'symptom-trend'"
        )
        assert len(trend_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_symptom_trend_3x_in_7_days_generates_candidate(provisioned_postgres_pool):
    """Symptom logged 3+ times in 7 days with severity >= 3 generates a trend alert."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(3):
            await _insert_symptom(
                pool, name="headache", severity=5, occurred_at=now - timedelta(days=i)
            )

        result = await run_insight_scan(pool)

        trend_rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE category = 'symptom-trend'"
        )
        assert len(trend_rows) == 1
        row = trend_rows[0]
        assert row["priority"] == 70
        assert "headache" in row["message"]
        assert "3" in row["message"]
        assert result["candidates_accepted"] == 1


async def test_symptom_trend_dedup_key_includes_year_week(provisioned_postgres_pool):
    """Symptom trend dedup_key includes health:symptom-trend:{name}:{year-week}."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(3):
            await _insert_symptom(
                pool, name="fatigue", severity=4, occurred_at=now - timedelta(days=i)
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'symptom-trend'"
        )
        assert len(rows) == 1
        dedup_key = rows[0]["dedup_key"]
        assert dedup_key.startswith("health:symptom-trend:fatigue:")
        # year-week part should be present
        year_week = now.strftime("%Y-W%W")
        assert dedup_key == f"health:symptom-trend:fatigue:{year_week}"


async def test_symptom_trend_excludes_symptoms_outside_7_days(provisioned_postgres_pool):
    """Symptoms older than 7 days are excluded from trend detection."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # 2 recent + 2 old (outside 7-day window) — total < 3 in window
        await _insert_symptom(
            pool, name="backache", severity=4, occurred_at=now - timedelta(days=1)
        )
        await _insert_symptom(
            pool, name="backache", severity=3, occurred_at=now - timedelta(days=2)
        )
        await _insert_symptom(
            pool, name="backache", severity=4, occurred_at=now - timedelta(days=8)
        )
        await _insert_symptom(
            pool, name="backache", severity=5, occurred_at=now - timedelta(days=10)
        )

        result = await run_insight_scan(pool)

        trend_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'symptom-trend'"
        )
        assert len(trend_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_symptom_trend_message_includes_count_and_severity(provisioned_postgres_pool):
    """Symptom trend message includes occurrence count and average severity."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        severities = [4, 5, 6]
        for i, sev in enumerate(severities):
            await _insert_symptom(
                pool, name="migraine", severity=sev, occurred_at=now - timedelta(days=i)
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT message FROM insight_candidates WHERE category = 'symptom-trend'"
        )
        assert len(rows) == 1
        message = rows[0]["message"]
        assert "3" in message  # count
        assert "migraine" in message


async def test_symptom_trend_expires_3_days_from_now(provisioned_postgres_pool):
    """Symptom trend candidate expires_at is 3 days from generation."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(3):
            await _insert_symptom(
                pool, name="chest_pain", severity=6, occurred_at=now - timedelta(days=i)
            )

        before = _utcnow()
        await run_insight_scan(pool)
        after = _utcnow()

        rows = await pool.fetch(
            "SELECT expires_at FROM insight_candidates WHERE category = 'symptom-trend'"
        )
        assert len(rows) == 1
        expires_at = rows[0]["expires_at"]
        expected_min = before + timedelta(days=2, hours=23)
        expected_max = after + timedelta(days=3, hours=1)
        assert expected_min <= expires_at <= expected_max


# ---------------------------------------------------------------------------
# Tests: health streak insights
# ---------------------------------------------------------------------------


async def test_streak_no_measurements_no_candidate(provisioned_postgres_pool):
    """No measurements → no streak candidate generated."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        result = await run_insight_scan(pool)

        streak_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'health-streak'"
        )
        assert len(streak_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_streak_5_consecutive_days_no_milestone(provisioned_postgres_pool):
    """5 consecutive days does not hit any milestone threshold."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        for i in range(5):
            # Use noon UTC to avoid date boundary issues
            day_dt = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
            await _insert_measurement(pool, mtype="weight", measured_at=day_dt - timedelta(days=i))

        result = await run_insight_scan(pool)

        streak_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'health-streak'"
        )
        assert len(streak_rows) == 0
        assert result["candidates_proposed"] == 0


async def test_streak_7_consecutive_days_generates_candidate(provisioned_postgres_pool):
    """7 consecutive days of measurements triggers the first streak milestone."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        for i in range(7):
            day_dt = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
            await _insert_measurement(pool, mtype="weight", measured_at=day_dt - timedelta(days=i))

        result = await run_insight_scan(pool)

        streak_rows = await pool.fetch(
            "SELECT priority, dedup_key, message, cooldown_days FROM insight_candidates"
            " WHERE category = 'health-streak'"
        )
        assert len(streak_rows) == 1
        row = streak_rows[0]
        assert row["priority"] == 25
        assert row["dedup_key"] == "health:streak:weight:7"
        assert "7" in row["message"]
        assert "weight" in row["message"]
        assert row["cooldown_days"] == 30
        assert result["candidates_accepted"] == 1


async def test_streak_30_day_milestone(provisioned_postgres_pool):
    """30 consecutive days of measurements triggers the 30-day milestone."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        for i in range(30):
            day_dt = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
            await _insert_measurement(
                pool, mtype="blood_pressure", measured_at=day_dt - timedelta(days=i)
            )

        result = await run_insight_scan(pool)

        streak_rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'health-streak'"
        )
        assert len(streak_rows) == 1
        assert streak_rows[0]["dedup_key"] == "health:streak:blood_pressure:30"
        assert result["candidates_accepted"] == 1


async def test_streak_only_one_milestone_per_type_per_run(provisioned_postgres_pool):
    """Only one milestone candidate is generated per measurement type per run."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        # Exactly 30 days — should only get the 30-day milestone, not 7
        for i in range(30):
            day_dt = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
            await _insert_measurement(pool, mtype="glucose", measured_at=day_dt - timedelta(days=i))

        await run_insight_scan(pool)

        streak_rows = await pool.fetch(
            "SELECT dedup_key FROM insight_candidates WHERE category = 'health-streak'"
        )
        assert len(streak_rows) == 1
        assert streak_rows[0]["dedup_key"] == "health:streak:glucose:30"


async def test_streak_expires_7_days_from_now(provisioned_postgres_pool):
    """Streak candidate expires_at is 7 days from generation."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        for i in range(7):
            day_dt = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
            await _insert_measurement(pool, mtype="weight", measured_at=day_dt - timedelta(days=i))

        before = _utcnow()
        await run_insight_scan(pool)
        after = _utcnow()

        rows = await pool.fetch(
            "SELECT expires_at FROM insight_candidates WHERE category = 'health-streak'"
        )
        assert len(rows) == 1
        expires_at = rows[0]["expires_at"]
        expected_min = before + timedelta(days=6, hours=23)
        expected_max = after + timedelta(days=7, hours=1)
        assert expected_min <= expires_at <= expected_max


async def test_streak_non_consecutive_days_no_milestone(provisioned_postgres_pool):
    """Non-consecutive measurement days reset the streak and miss milestones."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        today = _today()
        # Log today, skip yesterday, log 2 days ago — streak of 1
        for i in [0, 2, 3, 4, 5, 6, 7]:
            day_dt = datetime(today.year, today.month, today.day, 12, 0, tzinfo=UTC)
            await _insert_measurement(pool, mtype="weight", measured_at=day_dt - timedelta(days=i))

        result = await run_insight_scan(pool)

        streak_rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'health-streak'"
        )
        assert len(streak_rows) == 0
        assert result["candidates_proposed"] == 0


# ---------------------------------------------------------------------------
# Tests: verbosity=off early exit
# ---------------------------------------------------------------------------


async def test_insight_scan_verbosity_off_exits_early_on_measurement_gap(
    provisioned_postgres_pool,
):
    """When verbosity=off, first measurement gap candidate causes early exit."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'off') "
            "ON CONFLICT (id) DO UPDATE SET verbosity = 'off'"
        )

        now = _utcnow()
        for i in range(5):
            await _insert_measurement(
                pool, mtype="weight", measured_at=now - timedelta(days=25 + i * 7)
            )

        result = await run_insight_scan(pool)

        assert result["early_exit"] is True
        assert result["candidates_proposed"] >= 1
        assert result["candidates_accepted"] == 0


async def test_insight_scan_verbosity_off_exits_early_on_symptom_trend(
    provisioned_postgres_pool,
):
    """When verbosity=off, first symptom trend candidate causes early exit."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        await pool.execute(
            "INSERT INTO insight_settings (id, verbosity) VALUES (1, 'off') "
            "ON CONFLICT (id) DO UPDATE SET verbosity = 'off'"
        )

        now = _utcnow()
        for i in range(3):
            await _insert_symptom(
                pool, name="headache", severity=5, occurred_at=now - timedelta(days=i)
            )

        result = await run_insight_scan(pool)

        assert result["early_exit"] is True
        assert result["candidates_proposed"] >= 1
        assert result["candidates_accepted"] == 0


# ---------------------------------------------------------------------------
# Tests: origin_butler attribute
# ---------------------------------------------------------------------------


async def test_insight_scan_origin_butler_is_health(provisioned_postgres_pool):
    """All candidates submitted by the health insight scan have origin_butler='health'."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(3):
            await _insert_symptom(
                pool, name="backpain", severity=4, occurred_at=now - timedelta(days=i)
            )

        await run_insight_scan(pool)

        rows = await pool.fetch("SELECT origin_butler FROM insight_candidates")
        assert len(rows) >= 1
        for row in rows:
            assert row["origin_butler"] == "health"


# ---------------------------------------------------------------------------
# Tests: cross-signal correlation — adherence dip preceding a symptom flare
# ---------------------------------------------------------------------------


async def test_correlation_adherence_dip_then_symptom_flare(provisioned_postgres_pool):
    """A medication-adherence dip followed by a symptom flare generates a candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        med_id = await _insert_medication(pool, name="Sertraline", frequency="daily", active=True)
        # Prior week (days 15-21): the user normally logs the medication daily.
        for day in range(15, 22):
            await _insert_dose(pool, medication_id=med_id, taken_at=now - timedelta(days=day))
        # Dip week (days 7-14): no doses logged → adherence collapses.
        # Flare week (days 0-7): two higher-severity symptom entries.
        for day in (1, 3):
            await _insert_symptom(
                pool, name="nausea", severity=4, occurred_at=now - timedelta(days=day)
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE category = 'correlation-adherence'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["priority"] == 60
        assert row["dedup_key"].startswith(f"health:correlation-adherence:{med_id}:")
        assert "Sertraline" in row["message"]


async def test_correlation_adherence_no_flare_no_candidate(provisioned_postgres_pool):
    """An adherence dip with no following symptom flare does not generate a candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        med_id = await _insert_medication(pool, name="Sertraline", frequency="daily", active=True)
        for day in range(15, 22):
            await _insert_dose(pool, medication_id=med_id, taken_at=now - timedelta(days=day))
        # Only one symptom in the flare window — below the flare threshold.
        await _insert_symptom(pool, name="nausea", severity=4, occurred_at=now - timedelta(days=1))

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'correlation-adherence'"
        )
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Tests: cross-signal correlation — slow measurement drift
# ---------------------------------------------------------------------------


async def test_correlation_measurement_drift_generates_candidate(provisioned_postgres_pool):
    """A gradual upward median shift across recent readings generates a drift candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        # Daily weight readings drifting 70 → 80 over 9 days (newest = highest).
        values = [80.0, 80.0, 80.0, 75.0, 75.0, 75.0, 70.0, 70.0, 70.0]
        for i, value in enumerate(values):
            await _insert_measurement(
                pool,
                mtype="weight",
                value={"value": value},
                measured_at=now - timedelta(days=i),
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE category = 'correlation-drift'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["priority"] == 50
        assert row["dedup_key"].startswith("health:correlation-drift:weight:")
        assert "drift" in row["message"]


async def test_correlation_measurement_drift_stable_no_candidate(provisioned_postgres_pool):
    """Stable readings (no meaningful drift) do not generate a drift candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        for i in range(9):
            await _insert_measurement(
                pool,
                mtype="weight",
                value={"value": 70.0},
                measured_at=now - timedelta(days=i),
            )

        await run_insight_scan(pool)

        rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'correlation-drift'"
        )
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Tests: cross-signal correlation — HA environment vs sleep / symptoms
# ---------------------------------------------------------------------------


async def test_correlation_environment_with_reader_generates_candidate(provisioned_postgres_pool):
    """Adverse HA readings co-occurring with short sleep generate an environment candidate."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        day1 = now - timedelta(days=2)
        day2 = now - timedelta(days=3)
        # 5h sleep on two distinct nights.
        await _insert_sleep_session(pool, duration_hours=5.0, occurred_at=day1)
        await _insert_sleep_session(pool, duration_hours=5.0, occurred_at=day2)

        async def reader():
            return [
                {"captured_at": day1, "metric": "temperature", "adverse": True},
                {"captured_at": day2, "metric": "temperature", "adverse": True},
            ]

        await run_insight_scan(pool, reader)

        rows = await pool.fetch(
            "SELECT priority, dedup_key, message FROM insight_candidates"
            " WHERE category = 'correlation-environment'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["priority"] == 50
        assert row["dedup_key"].startswith("health:correlation-env:temperature:")
        assert "bedroom temperature" in row["message"]


async def test_correlation_environment_no_reader_skipped(provisioned_postgres_pool):
    """Without an HA environment reader, no environment candidate is generated."""
    from butlers.jobs._roster.health_jobs import run_insight_scan

    async with provisioned_postgres_pool() as pool:
        await _setup_health_schema(pool)
        await _setup_insight_tables(pool)

        now = _utcnow()
        await _insert_sleep_session(pool, duration_hours=5.0, occurred_at=now - timedelta(days=2))

        await run_insight_scan(pool)  # no reader

        rows = await pool.fetch(
            "SELECT id FROM insight_candidates WHERE category = 'correlation-environment'"
        )
        assert len(rows) == 0
