"""seed_ha_state_predicate

Revision ID: home_assistant_002
Revises: home_assistant_001
Create Date: 2026-03-08 00:00:00.000000

Seeds the ``ha_state`` predicate into the shared predicate registry so that
Home Assistant entity state snapshots stored as SPO facts use a consistent,
registered predicate name.

The predicate is registered with ``expected_subject_type = 'other'`` because
HA entities are devices/sensors, not people, organizations, or places.

The INSERT uses ``ON CONFLICT (name) DO NOTHING`` so the migration is safe to
re-run and will not overwrite any manually-adjusted registry entry.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "home_assistant_002"
down_revision = "home_assistant_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Seed ha_state into the predicate registry if that table already exists
    # (it is created by mem_005 on the memory branch; the home branch may run
    # before or after the memory branch depending on the butler schema).
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


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('predicate_registry') IS NOT NULL THEN
                DELETE FROM predicate_registry WHERE name = 'ha_state';
            END IF;
        END
        $$;
    """)
