"""sessions: add healing_fingerprint column for self-healing dispatch tracking

Revision ID: core_036
Revises: core_035
Create Date: 2026-03-17 00:00:00.000000

Adds a nullable TEXT column ``healing_fingerprint`` to the per-butler sessions
table.  This column is populated by the self-healing dispatcher after a session
fails: it stores the 64-character hex SHA-256 fingerprint used to deduplicate
error investigations.

The column is NULL for:
  - Successful sessions (no error to fingerprint).
  - Healing sessions themselves (trigger_source = 'healing' — no recursion).
  - Sessions that fail but do not trigger healing (below severity threshold,
    cooldown active, circuit breaker tripped, etc.).

This migration runs once per butler schema context; the unqualified 'sessions'
table resolves to the schema-specific table via the active search_path.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_036"
down_revision = "core_035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("healing_fingerprint", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "healing_fingerprint")
