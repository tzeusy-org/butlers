"""home_tables — collapsed from home_assistant_001 through home_thresholds_001.

Revision ID: home_001
Revises:
Create Date: 2026-02-28 00:00:00.000000

All home domain tables: ha_entity_snapshot, ha_command_log, maintenance_items.
Includes the ha_state predicate seed (INSERT into predicate_registry) and
threshold seed data (INSERT into state table).
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_001"
down_revision = None
branch_labels = ("home",)
depends_on = None

# ---------------------------------------------------------------------------
# Default threshold values (from home_thresholds_001)
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
    # --- ha_entity_snapshot (from home_assistant_001) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS ha_entity_snapshot (
            entity_id    TEXT        PRIMARY KEY,
            state        TEXT,
            attributes   JSONB,
            last_updated TIMESTAMPTZ,
            captured_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # --- ha_command_log (from home_assistant_001) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS ha_command_log (
            id         BIGSERIAL   PRIMARY KEY,
            domain     TEXT        NOT NULL,
            service    TEXT        NOT NULL,
            target     JSONB,
            data       JSONB,
            result     JSONB,
            context_id TEXT,
            issued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ha_command_log_issued_at
            ON ha_command_log (issued_at)
    """)

    # --- maintenance_items (from home_maintenance_001) ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_items (
            id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            name               TEXT        NOT NULL UNIQUE,
            category           TEXT        NOT NULL
                               CHECK (category IN (
                                   'filter', 'hvac', 'appliance',
                                   'plumbing', 'electrical', 'general'
                               )),
            interval_days      INTEGER     NOT NULL CHECK (interval_days > 0),
            last_completed_at  TIMESTAMPTZ,
            next_due_at        TIMESTAMPTZ,
            notes              TEXT,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_maintenance_items_next_due_at
            ON maintenance_items (next_due_at)
    """)

    # --- ha_state predicate seed (from home_assistant_002) ---
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('predicate_registry') IS NOT NULL THEN
                INSERT INTO predicate_registry
                    (name, expected_subject_type, expected_object_type,
                     is_edge, description)
                VALUES
                    ('ha_state', 'other', NULL, false,
                     'Current state of a Home Assistant entity (device/sensor). '
                     'Content = state value (e.g. ''on'', ''22.5''). '
                     'Metadata contains {attributes: JSONB, entity_id_ha: text}.')
                ON CONFLICT (name) DO NOTHING;
            END IF;
        END
        $$;
    """)

    # --- threshold seed data (from home_thresholds_001) ---
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
    # Remove threshold seeds (only if still at default values)
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

    # Remove ha_state predicate
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('predicate_registry') IS NOT NULL THEN
                DELETE FROM predicate_registry WHERE name = 'ha_state';
            END IF;
        END
        $$;
    """)

    op.execute("DROP INDEX IF EXISTS ix_maintenance_items_next_due_at")
    op.execute("DROP TABLE IF EXISTS maintenance_items")
    op.execute("DROP INDEX IF EXISTS ix_ha_command_log_issued_at")
    op.execute("DROP TABLE IF EXISTS ha_command_log")
    op.execute("DROP TABLE IF EXISTS ha_entity_snapshot")
