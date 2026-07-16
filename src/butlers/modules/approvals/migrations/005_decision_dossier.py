"""RFC 0021 structured decision dossier.

Revision ID: approvals_005
Revises: approvals_002
Create Date: 2026-07-17 00:00:00.000000

``approvals_004`` remains reserved by the pre-collapse approvals history; do
not reuse it. This migration follows the active ``approvals_003``
fingerprint-v2 revision so both upgrades run in one linear chain.

Adds nullable risk labels to ``pending_actions`` and upgrades legacy
plain-string evidence entries to the typed evidence-reference shape required by
RFC 0021.  The data rewrite is deliberately one-way: preserving typed
references is safer than discarding their type or note during a downgrade.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_005"
down_revision = "approvals_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pending_actions
            ADD COLUMN IF NOT EXISTS blast_radius TEXT,
            ADD COLUMN IF NOT EXISTS reversibility TEXT
    """)

    # Rewrite only legacy string entries. Existing typed entries retain their
    # complete shape and array order; malformed historical non-array JSONB is
    # left untouched rather than silently coerced at migration time.
    op.execute("""
        UPDATE pending_actions
        SET evidence = (
            SELECT jsonb_agg(
                CASE
                    WHEN jsonb_typeof(entry.value) = 'string' THEN
                        jsonb_build_object('type', 'text', 'ref', entry.value #>> '{}', 'note', '')
                    ELSE entry.value
                END
                ORDER BY entry.ordinality
            )
            FROM jsonb_array_elements(evidence) WITH ORDINALITY AS entry(value, ordinality)
        )
        WHERE jsonb_typeof(evidence) = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(evidence) AS entry(value)
              WHERE jsonb_typeof(entry.value) = 'string'
          )
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'pending_actions_blast_radius_check'
                  AND conrelid = 'pending_actions'::regclass
            ) THEN
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_blast_radius_check
                    CHECK (blast_radius IS NULL OR blast_radius IN (
                        'none', 'self', 'contact', 'external'
                    ));
            END IF;
        END $$;
    """)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'pending_actions_reversibility_check'
                  AND conrelid = 'pending_actions'::regclass
            ) THEN
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_reversibility_check
                    CHECK (reversibility IS NULL OR reversibility IN (
                        'reversible', 'compensable', 'irreversible'
                    ));
            END IF;
        END $$;
    """)


def downgrade() -> None:
    """Remove the additive columns; retain upgraded evidence without data loss."""
    op.execute(
        "ALTER TABLE pending_actions DROP CONSTRAINT IF EXISTS pending_actions_blast_radius_check"
    )
    op.execute(
        "ALTER TABLE pending_actions DROP CONSTRAINT IF EXISTS pending_actions_reversibility_check"
    )
    op.execute("ALTER TABLE pending_actions DROP COLUMN IF EXISTS blast_radius")
    op.execute("ALTER TABLE pending_actions DROP COLUMN IF EXISTS reversibility")
