"""pending_actions: add why and evidence columns.

Revision ID: core_097
Revises: core_096
Create Date: 2026-05-16 00:00:00.000000

Phase 6 of the settings-redesign epic (bu-5xiu9).

Adds two columns to ``pending_actions`` in every butler schema so that the
Dispatch dossier UI can render a human-readable rationale (why) and supporting
evidence (evidence) alongside each pending approval.

Columns
-------
why       TEXT        — single serif paragraph (≤2000 chars) explaining why
                        human input is required; NULL for legacy rows.
evidence  JSONB       — ordered list of mono evidence strings (≤50 items,
                        ≤500 chars each); defaults to empty array.

NULL tolerance
--------------
Both columns are nullable so that rows created before this migration are not
broken.  ``evidence`` has a server-side default of ``'[]'::jsonb`` so new rows
always have an array rather than NULL unless the caller explicitly passes NULL.

The migration is applied to every butler schema that already has the
``pending_actions`` table.  It uses ``ALTER TABLE … ADD COLUMN IF NOT EXISTS``
so it is idempotent and can be re-run safely.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "core_097"
down_revision = "core_096"
branch_labels = None
depends_on = None


def _butler_schemas() -> list[str]:
    """Return butler schema names from pg_namespace that have pending_actions."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT DISTINCT n.nspname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = 'pending_actions'
              AND c.relkind = 'r'
              AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'public')
            ORDER BY n.nspname
            """
        )
    ).fetchall()
    return [row[0] for row in rows]


def upgrade() -> None:
    for schema in _butler_schemas():
        op.execute(
            f"""
            ALTER TABLE {schema}.pending_actions
                ADD COLUMN IF NOT EXISTS why TEXT,
                ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb
            """
        )


def downgrade() -> None:
    for schema in _butler_schemas():
        op.execute(
            f"""
            ALTER TABLE {schema}.pending_actions
                DROP COLUMN IF EXISTS why,
                DROP COLUMN IF EXISTS evidence
            """
        )
