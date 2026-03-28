"""event_chains_status: align event_chains.status CHECK with spec.

Revision ID: core_044
Revises: core_043
Create Date: 2026-03-28 00:00:00.000000

The spec (openspec/specs/event-chains/spec.md) defines the status enum as:
  active | paused | fired | failed

The original core_013 migration only allowed: active | fired | disabled

This migration drops the old check constraint and replaces it with one that
includes the spec-mandated values.  ``disabled`` is retained for backward
compatibility with any rows written by the old code.

New allowed values: active | paused | fired | failed | disabled
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_044"
down_revision = "core_043"
branch_labels = None
depends_on = None

_BUTLER_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)

_OLD_VALUES = "('active', 'fired', 'disabled')"
_NEW_VALUES = "('active', 'paused', 'fired', 'failed', 'disabled')"
_CONSTRAINT_NAME = "chk_event_chains_status"


def upgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        # Drop old constraint, add new one — guarded with existence checks so
        # the migration is safe to re-run against a schema that was never
        # migrated from core_013 (e.g., test DBs created fresh from core_044).
        op.execute(f"""
            DO $$
            BEGIN
                -- Only alter the table if it exists in this schema
                IF to_regclass('{schema}.event_chains') IS NOT NULL THEN
                    -- Drop old constraint if it exists
                    IF EXISTS (
                        SELECT 1
                        FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE n.nspname = '{schema}'
                          AND t.relname = 'event_chains'
                          AND c.conname = '{_CONSTRAINT_NAME}'
                    ) THEN
                        ALTER TABLE {schema}.event_chains
                            DROP CONSTRAINT {_CONSTRAINT_NAME};
                    END IF;

                    -- Add new constraint
                    ALTER TABLE {schema}.event_chains
                        ADD CONSTRAINT {_CONSTRAINT_NAME}
                        CHECK (status IN {_NEW_VALUES});
                END IF;
            END
            $$;
        """)


def downgrade() -> None:
    for schema in _BUTLER_SCHEMAS:
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('{schema}.event_chains') IS NOT NULL THEN
                    IF EXISTS (
                        SELECT 1
                        FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE n.nspname = '{schema}'
                          AND t.relname = 'event_chains'
                          AND c.conname = '{_CONSTRAINT_NAME}'
                    ) THEN
                        ALTER TABLE {schema}.event_chains
                            DROP CONSTRAINT {_CONSTRAINT_NAME};
                    END IF;

                    ALTER TABLE {schema}.event_chains
                        ADD CONSTRAINT {_CONSTRAINT_NAME}
                        CHECK (status IN {_OLD_VALUES});
                END IF;
            END
            $$;
        """)
