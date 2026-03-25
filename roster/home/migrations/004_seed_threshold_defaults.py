"""seed_threshold_defaults

Revision ID: home_thresholds_001
Revises: home_maintenance_001
Create Date: 2026-03-25 00:00:00.000000

Seeds default monitoring threshold values into the Home butler's state store.
Each threshold key follows the pattern ``home:thresholds:<name>`` and is
inserted with ``ON CONFLICT (key) DO NOTHING`` so the migration is safe to
re-run and will never overwrite operator-customised values.

Threshold keys seeded:
  - home:thresholds:battery          — battery level severity cutoffs (%)
  - home:thresholds:offline_hours    — entity offline duration cutoffs (hours)
  - home:thresholds:comfort_defaults — default healthy comfort ranges
  - home:thresholds:comfort_deviation — comfort deviation severity thresholds
  - home:thresholds:energy           — energy anomaly detection thresholds
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_thresholds_001"
down_revision = "home_maintenance_001"
branch_labels = None
depends_on = None

# ---------------------------------------------------------------------------
# Default threshold values — kept as module-level constants so tests can
# import and verify them without loading the migration via Alembic.
# ---------------------------------------------------------------------------

BATTERY_THRESHOLDS: dict = {
    "critical": 10,
    "warning": 20,
    "info": 30,
}

OFFLINE_HOURS_THRESHOLDS: dict = {
    "critical": 24,
    "warning": 1,
}

COMFORT_DEFAULTS: dict = {
    "temp_min_f": 68,
    "temp_max_f": 76,
    "humidity_min": 30,
    "humidity_max": 60,
    "co2_max_ppm": 1000,
}

COMFORT_DEVIATION_THRESHOLDS: dict = {
    "minor_temp_f": 2,
    "moderate_temp_f": 5,
    "minor_humidity": 10,
    "moderate_humidity": 20,
    "critical_temp_low_f": 60,
    "critical_temp_high_f": 85,
    "critical_co2_ppm": 1500,
    "critical_humidity_low": 15,
    "critical_humidity_high": 80,
}

ENERGY_THRESHOLDS: dict = {
    "anomaly_pct": 20,
    "high_severity_pct": 100,
}

_THRESHOLD_SEEDS: list[tuple[str, dict]] = [
    ("home:thresholds:battery", BATTERY_THRESHOLDS),
    ("home:thresholds:offline_hours", OFFLINE_HOURS_THRESHOLDS),
    ("home:thresholds:comfort_defaults", COMFORT_DEFAULTS),
    ("home:thresholds:comfort_deviation", COMFORT_DEVIATION_THRESHOLDS),
    ("home:thresholds:energy", ENERGY_THRESHOLDS),
]


def upgrade() -> None:
    # Seed each threshold key into the state store.
    # ON CONFLICT DO NOTHING ensures operator-customised values are preserved
    # if the migration is re-run (e.g. after a schema reset).
    conn = op.get_bind()
    for key, value in _THRESHOLD_SEEDS:
        conn.execute(
            sa.text(
                """
                INSERT INTO state (key, value, updated_at, version)
                VALUES (
                    :key,
                    CAST(:value AS jsonb),
                    now(),
                    1
                )
                ON CONFLICT (key) DO NOTHING
                """
            ),
            {"key": key, "value": json.dumps(value)},
        )


def downgrade() -> None:
    # Remove only rows that still match the exact defaults seeded by this
    # migration. Keys that were customised by the operator (i.e. whose
    # stored value no longer equals the seeded default) are left alone
    # because downgrade can only safely remove what upgrade inserted.
    conn = op.get_bind()
    for key, value in _THRESHOLD_SEEDS:
        conn.execute(
            sa.text(
                """
                DELETE FROM state
                WHERE key = :key
                  AND value = CAST(:value AS jsonb)
                """
            ),
            {"key": key, "value": json.dumps(value)},
        )
