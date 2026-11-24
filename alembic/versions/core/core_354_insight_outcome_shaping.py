"""Owner-only outcome shaping setting and its class state (bu-s11n0s.15 S3-S5).

Revision ID: core_354
Revises: core_353
Create Date: 2026-11-21 00:00:00.000000

openspec change ``insight-outcome-efficacy``, REQ-proactive-insight-engine-037. Two columns on
the single ``public.insight_settings`` row:

- ``outcome_shaping`` (``observe`` or ``shape``, default ``observe``): whether a measured class
  verdict may shape delivery. Nothing in the repository writes it; turning ``shape`` on is an
  owner act, taken directly against the row, and ``observe`` keeps delivery exactly as it was.
- ``shaping_state`` (JSONB object, default ``{}``): per class and priority band, the verdict the
  broker last saw, since when, and when the owner was last told about it. It only exists so a
  class change is spoken once per 30 days and a ``keep`` reply is remembered.

Both are idempotent ``ADD COLUMN IF NOT EXISTS`` statements, so every per-schema core chain may
run them. The downgrade drops both columns; they have no dependents.
"""

from __future__ import annotations

from alembic import op

revision = "core_354"
down_revision = "core_353"
branch_labels = None
depends_on = None

_TABLE = "public.insight_settings"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RETURN;
            END IF;
            ALTER TABLE {_TABLE}
                ADD COLUMN IF NOT EXISTS outcome_shaping TEXT NOT NULL DEFAULT 'observe',
                ADD COLUMN IF NOT EXISTS shaping_state JSONB NOT NULL DEFAULT '{{}}'::jsonb;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_TABLE}'::regclass
                  AND conname = 'chk_insight_settings_outcome_shaping'
            ) THEN
                ALTER TABLE {_TABLE}
                    ADD CONSTRAINT chk_insight_settings_outcome_shaping
                    CHECK (outcome_shaping IN ('observe', 'shape'));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_TABLE}'::regclass
                  AND conname = 'chk_insight_settings_shaping_state'
            ) THEN
                ALTER TABLE {_TABLE}
                    ADD CONSTRAINT chk_insight_settings_shaping_state
                    CHECK (jsonb_typeof(shaping_state) = 'object');
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF to_regclass('{_TABLE}') IS NULL THEN
                RETURN;
            END IF;
            ALTER TABLE {_TABLE}
                DROP CONSTRAINT IF EXISTS chk_insight_settings_outcome_shaping,
                DROP CONSTRAINT IF EXISTS chk_insight_settings_shaping_state,
                DROP COLUMN IF EXISTS outcome_shaping,
                DROP COLUMN IF EXISTS shaping_state;
        END $$;
    """)
