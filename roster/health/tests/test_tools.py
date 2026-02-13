"""Tests for butlers.tools.health — health tracking tools aligned with spec."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with health tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create health tables matching spec schema
        await p.execute("""
            CREATE TABLE IF NOT EXISTS measurements (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                type TEXT NOT NULL,
                value JSONB NOT NULL,
                measured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS medications (
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
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS medication_doses (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                medication_id UUID NOT NULL REFERENCES medications(id),
                taken_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                skipped BOOLEAN NOT NULL DEFAULT false,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS conditions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                diagnosed_at TIMESTAMPTZ,
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS meals (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                nutrition JSONB,
                eaten_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS symptoms (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                severity INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 10),
                condition_id UUID REFERENCES conditions(id),
                occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                notes TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS research (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags JSONB NOT NULL DEFAULT '[]',
                source_url TEXT,
                condition_id UUID REFERENCES conditions(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        # Indexes
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_measurements_type_measured_at
                ON measurements (type, measured_at)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_medication_doses_med_taken
                ON medication_doses (medication_id, taken_at)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_symptoms_name_occurred
                ON symptoms (name, occurred_at)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_meals_eaten_at
                ON meals (eaten_at)
        """)

        yield p


# ------------------------------------------------------------------
# Measurements
# ------------------------------------------------------------------


async def test_measurement_log_weight(pool):
    """measurement_log inserts a weight measurement and returns it."""
    from butlers.tools.health import measurement_log

    result = await measurement_log(pool, "weight", {"kg": 75.5})
    assert result["type"] == "weight"
    assert result["value"] == {"kg": 75.5}
    assert result["id"] is not None


async def test_measurement_log_blood_pressure(pool):
    """measurement_log supports compound JSONB values like blood pressure."""
    from butlers.tools.health import measurement_log

    bp = {"systolic": 120, "diastolic": 80}
    result = await measurement_log(pool, "blood_pressure", bp)
    assert result["value"] == bp
    assert result["type"] == "blood_pressure"


async def test_measurement_log_with_timestamp(pool):
    """measurement_log accepts a custom measured_at timestamp."""
    from butlers.tools.health import measurement_log

    ts = _utcnow() - timedelta(hours=2)
    result = await measurement_log(pool, "heart_rate", 72, measured_at=ts)
    assert result["measured_at"] is not None


async def test_measurement_log_with_notes(pool):
    """measurement_log accepts optional notes."""
    from butlers.tools.health import measurement_log

    result = await measurement_log(pool, "weight", {"kg": 80}, notes="After workout")
    assert result["notes"] == "After workout"


async def test_measurement_log_rejects_invalid_type(pool):
    """measurement_log rejects unrecognized measurement types."""
    from butlers.tools.health import measurement_log

    with pytest.raises(ValueError, match="Unrecognized measurement type"):
        await measurement_log(pool, "cholesterol", 200)


async def test_measurement_log_valid_types(pool):
    """measurement_log accepts all five valid measurement types."""
    from butlers.tools.health import measurement_log

    for mtype in ("weight", "blood_pressure", "heart_rate", "blood_sugar", "temperature"):
        result = await measurement_log(pool, mtype, 42)
        assert result["type"] == mtype


async def test_measurement_history(pool):
    """measurement_history returns measurements filtered by type."""
    from butlers.tools.health import measurement_history, measurement_log

    now = _utcnow()
    await measurement_log(pool, "weight", {"kg": 70}, measured_at=now - timedelta(hours=3))
    await measurement_log(pool, "weight", {"kg": 71}, measured_at=now - timedelta(hours=1))
    await measurement_log(pool, "heart_rate", 72, measured_at=now)

    history = await measurement_history(pool, "weight")
    assert len(history) >= 2
    # Most recent first
    types = [m["type"] for m in history]
    assert all(t == "weight" for t in types)


async def test_measurement_history_with_date_filters(pool):
    """measurement_history respects start_date and end_date filters."""
    from butlers.tools.health import measurement_history, measurement_log

    now = _utcnow()
    await measurement_log(pool, "temperature", 98.0, measured_at=now - timedelta(days=5))
    await measurement_log(pool, "temperature", 99.0, measured_at=now - timedelta(days=2))
    await measurement_log(pool, "temperature", 97.5, measured_at=now)

    history = await measurement_history(
        pool,
        "temperature",
        start_date=now - timedelta(days=3),
        end_date=now - timedelta(days=1),
    )
    assert len(history) == 1
    assert history[0]["value"] == 99.0


async def test_measurement_history_empty(pool):
    """measurement_history returns empty list when no measurements exist."""
    from butlers.tools.health import measurement_history

    result = await measurement_history(pool, "nonexistent_type_hist")
    assert result == []


async def test_measurement_latest(pool):
    """measurement_latest returns the most recent measurement of a type."""
    from butlers.tools.health import measurement_latest, measurement_log

    now = _utcnow()
    await measurement_log(pool, "blood_sugar", 95, measured_at=now - timedelta(hours=2))
    await measurement_log(pool, "blood_sugar", 110, measured_at=now)

    latest = await measurement_latest(pool, "blood_sugar")
    assert latest is not None
    assert latest["value"] == 110


async def test_measurement_latest_no_data(pool):
    """measurement_latest returns None when no measurements exist."""
    from butlers.tools.health import measurement_latest

    result = await measurement_latest(pool, "nonexistent_type_latest")
    assert result is None


# ------------------------------------------------------------------
# Medications
# ------------------------------------------------------------------


async def test_medication_add(pool):
    """medication_add creates a medication with required fields."""
    from butlers.tools.health import medication_add

    med = await medication_add(pool, "Metformin", "500mg", "twice daily")
    assert med["name"] == "Metformin"
    assert med["dosage"] == "500mg"
    assert med["frequency"] == "twice daily"
    assert med["active"] is True
    assert med["schedule"] == []


async def test_medication_add_with_schedule(pool):
    """medication_add supports schedule and notes."""
    from butlers.tools.health import medication_add

    med = await medication_add(
        pool,
        "Ibuprofen",
        "200mg",
        "twice daily",
        schedule=["08:00", "20:00"],
        notes="Take with food",
    )
    assert med["schedule"] == ["08:00", "20:00"]
    assert med["notes"] == "Take with food"


async def test_medication_list_active_only(pool):
    """medication_list returns only active medications by default."""
    from butlers.tools.health import medication_add, medication_list

    await medication_add(pool, "ActiveMed_A2", "10mg", "daily")
    med_b = await medication_add(pool, "InactiveMed_B2", "20mg", "daily")
    await pool.execute("UPDATE medications SET active = false WHERE id = $1", med_b["id"])

    active = await medication_list(pool, active_only=True)
    names = [m["name"] for m in active]
    assert "ActiveMed_A2" in names
    assert "InactiveMed_B2" not in names


async def test_medication_list_all(pool):
    """medication_list with active_only=False returns all medications."""
    from butlers.tools.health import medication_add, medication_list

    await medication_add(pool, "AllMed_X2", "5mg", "daily")
    med_y = await medication_add(pool, "AllMed_Y2", "10mg", "daily")
    await pool.execute("UPDATE medications SET active = false WHERE id = $1", med_y["id"])

    all_meds = await medication_list(pool, active_only=False)
    names = [m["name"] for m in all_meds]
    assert "AllMed_X2" in names
    assert "AllMed_Y2" in names


async def test_medication_list_empty(pool):
    """medication_list returns empty list when no medications exist (filtered)."""
    from butlers.tools.health import medication_list

    # This just checks it doesn't error; there may be meds from other tests
    result = await medication_list(pool)
    assert isinstance(result, list)


async def test_medication_log_dose(pool):
    """medication_log_dose records a dose for a medication."""
    from butlers.tools.health import medication_add, medication_log_dose

    med = await medication_add(pool, "DoseMed2", "100mg", "daily")
    dose = await medication_log_dose(pool, str(med["id"]), notes="Taken with food")
    assert dose["medication_id"] == med["id"]
    assert dose["notes"] == "Taken with food"
    assert dose["skipped"] is False


async def test_medication_log_dose_skipped(pool):
    """medication_log_dose supports skipped=True for missed doses."""
    from butlers.tools.health import medication_add, medication_log_dose

    med = await medication_add(pool, "SkipMed", "50mg", "daily")
    dose = await medication_log_dose(pool, str(med["id"]), skipped=True, notes="Forgot")
    assert dose["skipped"] is True


async def test_medication_log_dose_invalid_id(pool):
    """medication_log_dose raises ValueError for non-existent medication."""
    from butlers.tools.health import medication_log_dose

    with pytest.raises(ValueError, match="not found"):
        await medication_log_dose(pool, str(uuid.uuid4()))


async def test_medication_history_with_adherence(pool):
    """medication_history returns doses and adherence rate based on skipped flag."""
    from butlers.tools.health import medication_add, medication_history, medication_log_dose

    now = _utcnow()
    med = await medication_add(pool, "AdherenceMed2", "50mg", "daily")

    # Log 10 doses: 8 taken, 2 skipped
    for i in range(8):
        await medication_log_dose(pool, str(med["id"]), taken_at=now - timedelta(days=10 - i))
    for i in range(2):
        await medication_log_dose(
            pool, str(med["id"]), taken_at=now - timedelta(hours=i + 1), skipped=True
        )

    result = await medication_history(
        pool,
        str(med["id"]),
        start_date=now - timedelta(days=11),
        end_date=now + timedelta(hours=1),
    )
    assert "medication" in result
    assert "doses" in result
    assert len(result["doses"]) == 10
    # 8 taken / 10 total = 80%
    assert result["adherence_rate"] == 80.0


async def test_medication_history_no_doses(pool):
    """medication_history returns empty doses and null adherence when no doses exist."""
    from butlers.tools.health import medication_add, medication_history

    med = await medication_add(pool, "NoDoseMed", "10mg", "daily")
    result = await medication_history(pool, str(med["id"]))
    assert result["doses"] == []
    assert result["adherence_rate"] is None


async def test_medication_history_not_found(pool):
    """medication_history raises ValueError for non-existent medication."""
    from butlers.tools.health import medication_history

    with pytest.raises(ValueError, match="not found"):
        await medication_history(pool, str(uuid.uuid4()))


async def test_medication_history_date_range(pool):
    """medication_history respects start_date and end_date."""
    from butlers.tools.health import medication_add, medication_history, medication_log_dose

    now = _utcnow()
    med = await medication_add(pool, "DateRangeMed", "25mg", "daily")
    await medication_log_dose(pool, str(med["id"]), taken_at=now - timedelta(days=5))
    await medication_log_dose(pool, str(med["id"]), taken_at=now - timedelta(days=2))
    await medication_log_dose(pool, str(med["id"]), taken_at=now)

    result = await medication_history(
        pool,
        str(med["id"]),
        start_date=now - timedelta(days=3),
        end_date=now - timedelta(days=1),
    )
    assert len(result["doses"]) == 1


# ------------------------------------------------------------------
# Conditions
# ------------------------------------------------------------------


async def test_condition_add(pool):
    """condition_add creates a condition with default active status."""
    from butlers.tools.health import condition_add

    cond = await condition_add(pool, "Asthma2", notes="Mild case")
    assert cond["name"] == "Asthma2"
    assert cond["status"] == "active"
    assert cond["notes"] == "Mild case"


async def test_condition_add_with_status(pool):
    """condition_add supports custom valid status."""
    from butlers.tools.health import condition_add

    cond = await condition_add(pool, "Flu2", status="resolved")
    assert cond["status"] == "resolved"


async def test_condition_add_invalid_status(pool):
    """condition_add rejects invalid status values."""
    from butlers.tools.health import condition_add

    with pytest.raises(ValueError, match="Invalid condition status"):
        await condition_add(pool, "BadStatus", status="unknown")


async def test_condition_add_all_valid_statuses(pool):
    """condition_add accepts all valid statuses: active, managed, resolved."""
    from butlers.tools.health import condition_add

    for status in ("active", "managed", "resolved"):
        cond = await condition_add(pool, f"Status_{status}", status=status)
        assert cond["status"] == status


async def test_condition_list_all(pool):
    """condition_list without filter returns all conditions ordered by created_at desc."""
    from butlers.tools.health import condition_add, condition_list

    await condition_add(pool, "CondAll_A2", status="active")
    await condition_add(pool, "CondAll_B2", status="resolved")

    all_conds = await condition_list(pool)
    names = [c["name"] for c in all_conds]
    assert "CondAll_A2" in names
    assert "CondAll_B2" in names


async def test_condition_list_filtered(pool):
    """condition_list filters by status when provided."""
    from butlers.tools.health import condition_add, condition_list

    await condition_add(pool, "FilterActive2", status="active")
    await condition_add(pool, "FilterResolved2", status="resolved")

    active = await condition_list(pool, status="active")
    names = [c["name"] for c in active]
    assert "FilterActive2" in names
    assert "FilterResolved2" not in names


async def test_condition_update(pool):
    """condition_update modifies allowed fields and updates updated_at."""
    from butlers.tools.health import condition_add, condition_update

    cond = await condition_add(pool, "UpdateCond2", status="active")
    updated = await condition_update(pool, str(cond["id"]), status="managed", notes="Under control")
    assert updated["status"] == "managed"
    assert updated["notes"] == "Under control"
    assert updated["name"] == "UpdateCond2"


async def test_condition_update_invalid_status(pool):
    """condition_update rejects invalid status values."""
    from butlers.tools.health import condition_add, condition_update

    cond = await condition_add(pool, "UpdateBadStatus")
    with pytest.raises(ValueError, match="Invalid condition status"):
        await condition_update(pool, str(cond["id"]), status="cured")


async def test_condition_update_not_found(pool):
    """condition_update raises ValueError for non-existent condition."""
    from butlers.tools.health import condition_update

    with pytest.raises(ValueError, match="not found"):
        await condition_update(pool, str(uuid.uuid4()), status="resolved")


async def test_condition_update_no_valid_fields(pool):
    """condition_update raises ValueError when no valid fields are given."""
    from butlers.tools.health import condition_add, condition_update

    cond = await condition_add(pool, "NoFieldCond2")
    with pytest.raises(ValueError, match="No valid fields"):
        await condition_update(pool, str(cond["id"]), bogus_field="nope")


# ------------------------------------------------------------------
# Symptoms
# ------------------------------------------------------------------


async def test_symptom_log(pool):
    """symptom_log inserts a symptom with severity."""
    from butlers.tools.health import symptom_log

    symptom = await symptom_log(pool, "Headache2", 7, notes="After exercise")
    assert symptom["name"] == "Headache2"
    assert symptom["severity"] == 7
    assert symptom["notes"] == "After exercise"


async def test_symptom_log_with_condition(pool):
    """symptom_log links a symptom to a condition."""
    from butlers.tools.health import condition_add, symptom_log

    cond = await condition_add(pool, "Migraine")
    symptom = await symptom_log(pool, "Headache_linked", 6, condition_id=str(cond["id"]))
    assert symptom["condition_id"] == cond["id"]


async def test_symptom_log_invalid_condition(pool):
    """symptom_log rejects invalid condition_id."""
    from butlers.tools.health import symptom_log

    with pytest.raises(ValueError, match="Condition.*not found"):
        await symptom_log(pool, "BadLink", 5, condition_id=str(uuid.uuid4()))


async def test_symptom_log_invalid_severity_low(pool):
    """symptom_log rejects severity below 1."""
    from butlers.tools.health import symptom_log

    with pytest.raises(ValueError, match="Severity must be between 1 and 10"):
        await symptom_log(pool, "LowSev", 0)


async def test_symptom_log_invalid_severity_high(pool):
    """symptom_log rejects severity above 10."""
    from butlers.tools.health import symptom_log

    with pytest.raises(ValueError, match="Severity must be between 1 and 10"):
        await symptom_log(pool, "HighSev", 11)


async def test_symptom_log_with_timestamp(pool):
    """symptom_log accepts a custom occurred_at timestamp."""
    from butlers.tools.health import symptom_log

    ts = _utcnow() - timedelta(hours=3)
    symptom = await symptom_log(pool, "Nausea2", 4, occurred_at=ts)
    assert symptom["occurred_at"] is not None


async def test_symptom_history_all(pool):
    """symptom_history returns all symptoms when no filters provided."""
    from butlers.tools.health import symptom_history, symptom_log

    await symptom_log(pool, "HistAll_A2", 3)
    await symptom_log(pool, "HistAll_B2", 5)

    history = await symptom_history(pool)
    names = [s["name"] for s in history]
    assert "HistAll_A2" in names
    assert "HistAll_B2" in names


async def test_symptom_history_with_date_filters(pool):
    """symptom_history respects start_date and end_date filters."""
    from butlers.tools.health import symptom_history, symptom_log

    now = _utcnow()
    await symptom_log(pool, "DateSym2", 3, occurred_at=now - timedelta(days=5))
    await symptom_log(pool, "DateSym2", 5, occurred_at=now - timedelta(days=2))
    await symptom_log(pool, "DateSym2", 7, occurred_at=now)

    history = await symptom_history(
        pool,
        start_date=now - timedelta(days=3),
        end_date=now - timedelta(days=1),
    )
    # Should pick up the one from 2 days ago
    date_syms = [s for s in history if s["name"] == "DateSym2"]
    assert len(date_syms) == 1
    assert date_syms[0]["severity"] == 5


async def test_symptom_search_by_name(pool):
    """symptom_search finds symptoms by name (case-insensitive)."""
    from butlers.tools.health import symptom_log, symptom_search

    await symptom_log(pool, "SearchHeadache", 8)
    await symptom_log(pool, "BackPain_search", 5)

    results = await symptom_search(pool, name="SearchHeadache")
    assert all(s["name"] == "SearchHeadache" for s in results)
    assert len(results) >= 1


async def test_symptom_search_by_severity_range(pool):
    """symptom_search filters by min and max severity."""
    from butlers.tools.health import symptom_log, symptom_search

    await symptom_log(pool, "SevSearch_low", 2)
    await symptom_log(pool, "SevSearch_mid", 6)
    await symptom_log(pool, "SevSearch_high", 9)

    results = await symptom_search(pool, min_severity=7, max_severity=10)
    severities = [s["severity"] for s in results]
    assert all(7 <= s <= 10 for s in severities)


async def test_symptom_search_combined(pool):
    """symptom_search combines name, severity, and date filters with AND logic."""
    from butlers.tools.health import symptom_log, symptom_search

    now = _utcnow()
    await symptom_log(pool, "CombinedSearch", 8, occurred_at=now - timedelta(days=1))
    await symptom_log(pool, "CombinedSearch", 3, occurred_at=now - timedelta(days=1))
    await symptom_log(pool, "OtherSymptom", 8, occurred_at=now - timedelta(days=1))

    results = await symptom_search(
        pool,
        name="CombinedSearch",
        min_severity=5,
        start_date=now - timedelta(days=2),
        end_date=now,
    )
    assert len(results) >= 1
    assert all(s["name"] == "CombinedSearch" and s["severity"] >= 5 for s in results)


async def test_symptom_search_no_matches(pool):
    """symptom_search returns empty list when no symptoms match."""
    from butlers.tools.health import symptom_search

    results = await symptom_search(pool, name="ZZZ_NoMatch_ZZZ")
    assert results == []


# ------------------------------------------------------------------
# Diet and Nutrition
# ------------------------------------------------------------------


async def test_meal_log(pool):
    """meal_log inserts a meal with type, description, and nutrition."""
    from butlers.tools.health import meal_log

    meal = await meal_log(
        pool,
        "lunch",
        "Grilled chicken salad",
        nutrition={"calories": 450, "protein_g": 30},
    )
    assert meal["type"] == "lunch"
    assert meal["description"] == "Grilled chicken salad"
    assert meal["nutrition"]["calories"] == 450


async def test_meal_log_without_nutrition(pool):
    """meal_log works without nutrition data (defaults to null)."""
    from butlers.tools.health import meal_log

    meal = await meal_log(pool, "snack", "Apple")
    assert meal["description"] == "Apple"
    assert meal["nutrition"] is None


async def test_meal_log_with_notes(pool):
    """meal_log accepts optional notes."""
    from butlers.tools.health import meal_log

    meal = await meal_log(pool, "dinner", "Pasta", notes="Homemade")
    assert meal["notes"] == "Homemade"


async def test_meal_log_rejects_invalid_type(pool):
    """meal_log rejects invalid meal types."""
    from butlers.tools.health import meal_log

    with pytest.raises(ValueError, match="Invalid meal type"):
        await meal_log(pool, "brunch", "Eggs Benedict")


async def test_meal_log_valid_types(pool):
    """meal_log accepts all valid meal types."""
    from butlers.tools.health import meal_log

    for mtype in ("breakfast", "lunch", "dinner", "snack"):
        meal = await meal_log(pool, mtype, f"Test {mtype}")
        assert meal["type"] == mtype


async def test_meal_history_all(pool):
    """meal_history returns all meals in reverse chronological order."""
    from butlers.tools.health import meal_history, meal_log

    now = _utcnow()
    await meal_log(pool, "breakfast", "BH_first", eaten_at=now - timedelta(hours=8))
    await meal_log(pool, "lunch", "BH_second", eaten_at=now - timedelta(hours=4))
    await meal_log(pool, "dinner", "BH_third", eaten_at=now)

    history = await meal_history(pool, start_date=now - timedelta(hours=9), end_date=now)
    descriptions = [m["description"] for m in history]
    assert "BH_third" in descriptions
    assert "BH_first" in descriptions
    # Most recent first
    assert descriptions.index("BH_third") < descriptions.index("BH_first")


async def test_meal_history_by_type(pool):
    """meal_history filters by meal type."""
    from butlers.tools.health import meal_history, meal_log

    now = _utcnow()
    await meal_log(pool, "breakfast", "TypeFilter_B", eaten_at=now - timedelta(hours=2))
    await meal_log(pool, "dinner", "TypeFilter_D", eaten_at=now)

    history = await meal_history(
        pool, type="dinner", start_date=now - timedelta(hours=3), end_date=now
    )
    descriptions = [m["description"] for m in history]
    assert "TypeFilter_D" in descriptions
    assert "TypeFilter_B" not in descriptions


async def test_meal_history_with_date_range(pool):
    """meal_history respects start_date and end_date."""
    from butlers.tools.health import meal_history, meal_log

    now = _utcnow()
    await meal_log(pool, "lunch", "DateRange_old", eaten_at=now - timedelta(days=5))
    await meal_log(pool, "lunch", "DateRange_mid", eaten_at=now - timedelta(days=2))
    await meal_log(pool, "lunch", "DateRange_new", eaten_at=now)

    history = await meal_history(
        pool, start_date=now - timedelta(days=3), end_date=now - timedelta(days=1)
    )
    descriptions = [m["description"] for m in history]
    assert "DateRange_mid" in descriptions
    assert "DateRange_old" not in descriptions
    assert "DateRange_new" not in descriptions


async def test_nutrition_summary(pool):
    """nutrition_summary aggregates totals and daily averages from nutrition JSONB."""
    from butlers.tools.health import meal_log, nutrition_summary

    now = _utcnow()
    await meal_log(
        pool,
        "breakfast",
        "NutrSum_1",
        nutrition={"calories": 400, "protein_g": 30, "carbs_g": 50, "fat_g": 10},
        eaten_at=now - timedelta(days=3),
    )
    await meal_log(
        pool,
        "lunch",
        "NutrSum_2",
        nutrition={"calories": 600, "protein_g": 25, "carbs_g": 70, "fat_g": 20},
        eaten_at=now - timedelta(days=1),
    )

    summary = await nutrition_summary(pool, start_date=now - timedelta(days=7), end_date=now)
    assert summary["total_calories"] == 1000.0
    assert summary["total_protein_g"] == 55.0
    assert summary["total_carbs_g"] == 120.0
    assert summary["total_fat_g"] == 30.0
    assert summary["meal_count"] == 2
    # Daily averages over 7 days
    assert summary["daily_avg_calories"] == round(1000.0 / 7, 1)


async def test_nutrition_summary_empty(pool):
    """nutrition_summary returns zeros when no meals with nutrition exist in range."""
    from butlers.tools.health import nutrition_summary

    now = _utcnow()
    summary = await nutrition_summary(
        pool,
        start_date=now - timedelta(hours=1),
        end_date=now - timedelta(minutes=30),
    )
    assert summary["total_calories"] == 0.0
    assert summary["total_protein_g"] == 0.0
    assert summary["meal_count"] == 0


async def test_nutrition_summary_excludes_null_nutrition(pool):
    """nutrition_summary excludes meals with null nutrition data."""
    from butlers.tools.health import meal_log, nutrition_summary

    now = _utcnow()
    await meal_log(pool, "snack", "NutrNull_1", eaten_at=now - timedelta(hours=2))  # null nutrition
    await meal_log(
        pool,
        "lunch",
        "NutrNull_2",
        nutrition={"calories": 300},
        eaten_at=now - timedelta(hours=1),
    )

    summary = await nutrition_summary(pool, start_date=now - timedelta(hours=3), end_date=now)
    assert summary["meal_count"] == 1
    assert summary["total_calories"] == 300.0


# ------------------------------------------------------------------
# Research
# ------------------------------------------------------------------


async def test_research_save(pool):
    """research_save creates a research entry with title, content, tags."""
    from butlers.tools.health import research_save

    entry = await research_save(
        pool,
        "Vitamin D Benefits",
        "Important for bone health.",
        tags=["vitamins", "bones"],
        source_url="https://example.com/vitd",
    )
    assert entry["title"] == "Vitamin D Benefits"
    assert entry["content"] == "Important for bone health."
    assert entry["tags"] == ["vitamins", "bones"]
    assert entry["source_url"] == "https://example.com/vitd"


async def test_research_save_minimal(pool):
    """research_save works with only title and content."""
    from butlers.tools.health import research_save

    entry = await research_save(pool, "Sleep", "8 hours is recommended.")
    assert entry["tags"] == []
    assert entry["source_url"] is None
    assert entry["condition_id"] is None


async def test_research_save_with_condition(pool):
    """research_save links to a condition when condition_id provided."""
    from butlers.tools.health import condition_add, research_save

    cond = await condition_add(pool, "Diabetes_research")
    entry = await research_save(
        pool,
        "Metformin Study",
        "Longevity benefits.",
        condition_id=str(cond["id"]),
    )
    assert entry["condition_id"] == cond["id"]


async def test_research_save_invalid_condition(pool):
    """research_save rejects invalid condition_id."""
    from butlers.tools.health import research_save

    with pytest.raises(ValueError, match="Condition.*not found"):
        await research_save(pool, "Bad research", "Content", condition_id=str(uuid.uuid4()))


async def test_research_search_by_query(pool):
    """research_search finds entries by text query in title and content."""
    from butlers.tools.health import research_save, research_search

    await research_save(pool, "Omega-3 RSearch", "Good for heart health.")
    await research_save(pool, "Exercise RSearch", "Improves omega-3 absorption.")

    results = await research_search(pool, query="omega")
    titles = [r["title"] for r in results]
    assert any("Omega-3" in t for t in titles)


async def test_research_search_by_tags(pool):
    """research_search filters by tags (any match)."""
    from butlers.tools.health import research_save, research_search

    await research_save(pool, "TagSearch1", "Content", tags=["longevity", "diabetes"])
    await research_save(pool, "TagSearch2", "Content", tags=["fitness"])

    results = await research_search(pool, tags=["longevity"])
    titles = [r["title"] for r in results]
    assert "TagSearch1" in titles
    assert "TagSearch2" not in titles


async def test_research_search_by_condition(pool):
    """research_search filters by condition_id."""
    from butlers.tools.health import condition_add, research_save, research_search

    cond = await condition_add(pool, "CondSearch_research")
    await research_save(pool, "CondLinked", "Content", condition_id=str(cond["id"]))
    await research_save(pool, "CondUnlinked", "Content")

    results = await research_search(pool, condition_id=str(cond["id"]))
    titles = [r["title"] for r in results]
    assert "CondLinked" in titles
    assert "CondUnlinked" not in titles


async def test_research_search_no_matches(pool):
    """research_search returns empty list when no entries match."""
    from butlers.tools.health import research_search

    results = await research_search(pool, query="ZZZ_no_match_ZZZ")
    assert results == []


async def test_research_summarize_all(pool):
    """research_summarize returns count, tags, titles for all entries."""
    from butlers.tools.health import research_save, research_summarize

    await research_save(pool, "SumAll_1", "Content1", tags=["tag_a"])
    await research_save(pool, "SumAll_2", "Content2", tags=["tag_b", "tag_a"])

    summary = await research_summarize(pool)
    assert summary["count"] >= 2
    assert "SumAll_1" in summary["titles"]
    assert "SumAll_2" in summary["titles"]
    assert "tag_a" in summary["tags"]
    assert "tag_b" in summary["tags"]


async def test_research_summarize_by_condition(pool):
    """research_summarize scoped by condition."""
    from butlers.tools.health import condition_add, research_save, research_summarize

    cond = await condition_add(pool, "SumCond_research")
    await research_save(
        pool, "SumCond_linked", "Content", tags=["cond_tag"], condition_id=str(cond["id"])
    )
    await research_save(pool, "SumCond_unlinked", "Content", tags=["other_tag"])

    summary = await research_summarize(pool, condition_id=str(cond["id"]))
    assert summary["count"] >= 1
    assert "SumCond_linked" in summary["titles"]
    assert "SumCond_unlinked" not in summary["titles"]


async def test_research_summarize_by_tags(pool):
    """research_summarize scoped by tags."""
    from butlers.tools.health import research_save, research_summarize

    await research_save(pool, "SumTag_match", "Content", tags=["rare_tag_sum"])
    await research_save(pool, "SumTag_no", "Content", tags=["other_rare_tag_sum"])

    summary = await research_summarize(pool, tags=["rare_tag_sum"])
    assert summary["count"] >= 1
    assert "SumTag_match" in summary["titles"]


# ------------------------------------------------------------------
# Reports
# ------------------------------------------------------------------


async def test_health_summary(pool):
    """health_summary returns measurements, medications, and conditions."""
    from butlers.tools.health import (
        condition_add,
        health_summary,
        measurement_log,
        medication_add,
    )

    await measurement_log(pool, "weight", {"kg": 80})
    await medication_add(pool, "SummaryMed2", "10mg", "daily")
    await condition_add(pool, "SummaryCond2", status="active")

    summary = await health_summary(pool)
    assert "recent_measurements" in summary
    assert "active_medications" in summary
    assert "active_conditions" in summary
    # Spec says no recent_symptoms in health_summary
    assert "recent_symptoms" not in summary

    meas_types = [m["type"] for m in summary["recent_measurements"]]
    assert "weight" in meas_types

    med_names = [m["name"] for m in summary["active_medications"]]
    assert "SummaryMed2" in med_names

    cond_names = [c["name"] for c in summary["active_conditions"]]
    assert "SummaryCond2" in cond_names


async def test_health_summary_sparse(pool):
    """health_summary works with minimal data."""
    from butlers.tools.health import health_summary

    # Just check it doesn't error
    summary = await health_summary(pool)
    assert isinstance(summary["recent_measurements"], list)
    assert isinstance(summary["active_medications"], list)
    assert isinstance(summary["active_conditions"], list)


async def test_trend_report_week(pool):
    """trend_report returns weekly trend data with measurements, adherence, symptoms."""
    from butlers.tools.health import (
        measurement_log,
        medication_add,
        medication_log_dose,
        symptom_log,
        trend_report,
    )

    now = _utcnow()
    # Add data within the last 7 days
    await measurement_log(pool, "weight", {"kg": 75}, measured_at=now - timedelta(days=5))
    await measurement_log(pool, "weight", {"kg": 74}, measured_at=now - timedelta(days=1))

    med = await medication_add(pool, "TrendMed", "10mg", "daily")
    await medication_log_dose(pool, str(med["id"]), taken_at=now - timedelta(days=2))
    await medication_log_dose(pool, str(med["id"]), taken_at=now - timedelta(days=1), skipped=True)

    await symptom_log(pool, "TrendHeadache", 6, occurred_at=now - timedelta(days=3))
    await symptom_log(pool, "TrendHeadache", 4, occurred_at=now - timedelta(days=1))

    report = await trend_report(pool, period="week")
    assert report["period"] == "week"
    assert report["days"] == 7

    # Measurement trends
    assert "weight" in report["measurement_trends"]
    wt = report["measurement_trends"]["weight"]
    assert len(wt["measurements"]) >= 2
    assert wt["first"] is not None
    assert wt["last"] is not None

    # Medication adherence
    med_adh = [m for m in report["medication_adherence"] if m["name"] == "TrendMed"]
    assert len(med_adh) == 1
    assert med_adh[0]["total_doses"] == 2
    assert med_adh[0]["taken_doses"] == 1
    assert med_adh[0]["adherence_rate"] == 50.0

    # Symptom data
    assert "TrendHeadache" in report["symptom_frequency"]
    assert report["symptom_frequency"]["TrendHeadache"] >= 2
    assert "TrendHeadache" in report["symptom_severity_avg"]


async def test_trend_report_month(pool):
    """trend_report with period=month covers 30 days."""
    from butlers.tools.health import trend_report

    report = await trend_report(pool, period="month")
    assert report["period"] == "month"
    assert report["days"] == 30


async def test_trend_report_empty(pool):
    """trend_report returns empty data when no health data exists in period."""
    from butlers.tools.health import trend_report

    # This is OK even if other tests added data, we just check structure
    report = await trend_report(pool, period="week")
    assert isinstance(report["measurement_trends"], dict)
    assert isinstance(report["medication_adherence"], list)
    assert isinstance(report["symptom_frequency"], dict)
    assert isinstance(report["symptom_severity_avg"], dict)


async def test_trend_report_invalid_period(pool):
    """trend_report rejects invalid period values."""
    from butlers.tools.health import trend_report

    with pytest.raises(ValueError, match="Invalid period"):
        await trend_report(pool, period="year")
