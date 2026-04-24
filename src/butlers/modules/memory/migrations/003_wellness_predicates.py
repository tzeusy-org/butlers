"""Seed predicate_registry with nine Google-Health wellness predicates.

Revision ID: mem_003
Revises: mem_002
Create Date: 2026-04-25 00:00:00.000000

Adds the predicate registry entries required by the google-health-connector
wellness ingestion path (bu-k5l35 epic).  All nine predicates live under
scope='health'.

Two predicate types:
- Temporal events (is_temporal=True): sleep_session, sleep_stage_summary
- Daily measurements (is_temporal=True, one-per-day): measurement_resting_hr,
  measurement_hrv, measurement_spo2, measurement_breathing_rate,
  measurement_steps, measurement_active_minutes, measurement_vo2_max

Note: measurement_resting_hr is a daily *derived* resting-HR summary.  It is
distinct from the pre-existing measurement_heart_rate (point-in-time manual
reading).

Uses INSERT ... ON CONFLICT (name) DO NOTHING for idempotency.
downgrade() removes exactly these nine rows; it targets them by name so it
does not affect predicates inserted by other migrations.
"""

from __future__ import annotations

import json

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_003"
down_revision = "mem_002"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Example JSON payloads
# ---------------------------------------------------------------------------

_EXAMPLES: dict[str, dict] = {
    "sleep_session": {
        "content": "Sleep 2026-04-24: 7h 32m, efficiency 91%",
        "metadata": {
            "session_id": "abc123",
            "end_time": "2026-04-24T07:12:00Z",
            "duration_ms": 27120000,
            "efficiency": 91,
            "minutes_asleep": 452,
            "minutes_awake": 40,
            "stages": {"deep": 95, "light": 220, "rem": 137, "wake": 40},
        },
    },
    "sleep_stage_summary": {
        "content": "Sleep stages 2026-04-24: deep 95m light 220m rem 137m wake 40m",
        "metadata": {
            "session_id": "abc123",
            "stages": {"deep": 95, "light": 220, "rem": 137, "wake": 40},
        },
    },
    "measurement_resting_hr": {
        "content": "Resting HR: 58 bpm",
        "metadata": {
            "value": 58,
            "heart_rate_zones": {
                "out_of_range": 1050,
                "fat_burn": 180,
                "cardio": 30,
                "peak": 0,
            },
        },
    },
    "measurement_hrv": {
        "content": "HRV: daily RMSSD 42ms",
        "metadata": {"daily_rmssd": 42.1, "deep_rmssd": 51.3, "coverage": 0.87},
    },
    "measurement_spo2": {
        "content": "SpO2: avg 97%, min 94%, max 99%",
        "metadata": {"avg": 97.0, "min": 94.0, "max": 99.0},
    },
    "measurement_breathing_rate": {
        "content": "Breathing rate: 14.2 breaths/min",
        "metadata": {"value": 14.2},
    },
    "measurement_steps": {
        "content": "Steps: 9341, distance 6.8 km",
        "metadata": {"value": 9341, "distance_km": 6.8, "floors": 12},
    },
    "measurement_active_minutes": {
        "content": "Active minutes: very=22 fairly=35 lightly=90 sedentary=780",
        "metadata": {
            "very_active": 22,
            "fairly_active": 35,
            "lightly_active": 90,
            "sedentary": 780,
        },
    },
    "measurement_vo2_max": {
        "content": "VO2 Max: 44–49 mL/kg/min (midpoint 46.5)",
        "metadata": {"range_low": 44.0, "range_high": 49.0, "midpoint": 46.5},
    },
}


def _ej(obj: dict) -> str:
    """Return a SQL-safe JSON literal from a Python dict."""
    return json.dumps(obj).replace("'", "''")


def _example_clause(name: str) -> str:
    """Return the example_json SQL value for a predicate, or 'NULL' if none."""
    if name in _EXAMPLES:
        return f"'{_ej(_EXAMPLES[name])}'::jsonb"
    return "NULL"


# ---------------------------------------------------------------------------
# Predicate definitions
# ---------------------------------------------------------------------------

# Each tuple: (name, expected_subject_type, is_temporal, description)
# All predicates: is_edge=False, expected_object_type=None, scope='health',
#                 status='active', superseded_by=None.
_WELLNESS_PREDICATES: list[tuple[str, str, bool, str]] = [
    # --- Temporal events (many-per-day) ---
    (
        "sleep_session",
        "person",
        True,
        "Sleep session event. valid_at = session start. "
        "Metadata: {session_id, end_time, duration_ms, efficiency, "
        "minutes_asleep, minutes_awake, stages: {deep, light, rem, wake}}.",
    ),
    (
        "sleep_stage_summary",
        "person",
        True,
        "Sleep stage summary for a session. valid_at = session start. "
        "Metadata: {session_id, stages: {deep, light, rem, wake}}.",
    ),
    # --- Daily measurements (one-per-day, is_temporal=True, valid_at = date 00:00 local) ---
    (
        "measurement_resting_hr",
        "person",
        True,
        "Daily resting heart rate derived from continuous monitoring. "
        "Distinct from measurement_heart_rate (point-in-time manual reading). "
        "Metadata: {value (bpm), heart_rate_zones: {out_of_range, fat_burn, cardio, peak}}.",
    ),
    (
        "measurement_hrv",
        "person",
        True,
        "Daily heart-rate variability (HRV). Metadata: {daily_rmssd, deep_rmssd, coverage}.",
    ),
    (
        "measurement_spo2",
        "person",
        True,
        "Daily blood oxygen saturation (SpO2). Metadata: {avg, min, max} (percentage).",
    ),
    (
        "measurement_breathing_rate",
        "person",
        True,
        "Daily breathing rate. Metadata: {value} (breaths per minute).",
    ),
    (
        "measurement_steps",
        "person",
        True,
        "Daily step count and activity distance. Metadata: {value, distance_km, floors}.",
    ),
    (
        "measurement_active_minutes",
        "person",
        True,
        "Daily active-minutes breakdown. "
        "Metadata: {very_active, fairly_active, lightly_active, sedentary} (minutes).",
    ),
    (
        "measurement_vo2_max",
        "person",
        True,
        "Estimated VO2 Max fitness score. Metadata: {range_low, range_high, midpoint} (mL/kg/min).",
    ),
]

# Ordered list of names — used by downgrade() to remove exactly these rows.
WELLNESS_PREDICATE_NAMES: list[str] = [name for name, *_ in _WELLNESS_PREDICATES]


def _insert_predicate(
    name: str,
    expected_subject_type: str | None,
    is_temporal: bool,
    description: str,
) -> None:
    """Insert a single wellness predicate with ON CONFLICT DO NOTHING."""
    subj = f"'{expected_subject_type}'" if expected_subject_type else "NULL"
    desc_escaped = description.replace("'", "''")
    example_sql = _example_clause(name)

    op.execute(
        f"INSERT INTO predicate_registry"
        f" (name, expected_subject_type, expected_object_type, is_edge, is_temporal,"
        f"  description, scope, status, superseded_by, deprecated_at,"
        f"  inverse_of, is_symmetric, example_json)"
        f" VALUES"
        f" ('{name}', {subj}, NULL, false, {is_temporal},"
        f"  '{desc_escaped}', 'health', 'active', NULL, NULL,"
        f"  NULL, false, {example_sql})"
        f" ON CONFLICT (name) DO NOTHING"
    )


def upgrade() -> None:
    """Upsert nine wellness predicates into predicate_registry.

    Idempotent: ON CONFLICT (name) DO NOTHING means a second run is a no-op.
    """
    for name, subject_type, is_temporal, description in _WELLNESS_PREDICATES:
        _insert_predicate(name, subject_type, is_temporal, description)


def downgrade() -> None:
    """Remove exactly the nine wellness predicates added by this migration.

    Targeted DELETE by name so predicates from other migrations with
    coincidental matching names (should they ever exist) are unaffected.
    """
    names_sql = ", ".join(f"'{n}'" for n in WELLNESS_PREDICATE_NAMES)
    op.execute(f"DELETE FROM predicate_registry WHERE name IN ({names_sql})")
