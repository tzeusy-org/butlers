"""insight_settings.verbosity admits 'auto_off', the ratchet's own state (bu-s11n0s.8 S2).

Revision ID: core_344
Revises: core_343
Create Date: 2026-11-16 00:00:00.000000

The total-disengagement ratchet wrote ``verbosity = 'off'``, the same value as the owner's
explicit opt-out, so nothing could tell "the owner muted me" from "the owner stopped reading".
An owner-declared break-through sender must still reach the owner in the second case and never
in the first. The ratchet now writes ``'auto_off'``: budget 0 like ``'off'`` for all routine
speech, with the break-through exemption applied by the broker.

This widens ``chk_insight_settings_verbosity`` on ``public.insight_settings`` to admit the new
value. Existing rows are untouched; a row already at ``'off'`` stays an explicit opt-out
(the conservative reading, since the two were never distinguished).

The core chain runs once per butler schema but the table is the shared ``public`` one, so a run
that finds the constraint already admitting ``'auto_off'`` is a catalog-only no-op.

The downgrade maps ``'auto_off'`` rows back to ``'off'`` (the nearest old state) before
restoring the narrower check.
"""

from __future__ import annotations

from alembic import op

revision = "core_344"
down_revision = "core_343"
branch_labels = None
depends_on = None

_TABLE = "public.insight_settings"
_CONSTRAINT = "chk_insight_settings_verbosity"


def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{_TABLE}'::regclass
                  AND conname = '{_CONSTRAINT}'
                  AND pg_get_constraintdef(oid) LIKE '%auto_off%'
            ) THEN
                RETURN;
            END IF;
            ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT};
            ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT}
                CHECK (verbosity IN ('off', 'auto_off', 'minimal', 'normal', 'verbose'));
        END $$;
    """)


def downgrade() -> None:
    op.execute(f"""
        UPDATE {_TABLE} SET verbosity = 'off' WHERE verbosity = 'auto_off';
        ALTER TABLE {_TABLE} DROP CONSTRAINT IF EXISTS {_CONSTRAINT};
        ALTER TABLE {_TABLE} ADD CONSTRAINT {_CONSTRAINT}
            CHECK (verbosity IN ('off', 'minimal', 'normal', 'verbose'));
    """)
