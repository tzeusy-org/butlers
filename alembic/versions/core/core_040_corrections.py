"""corrections: create per-butler corrections table for error-recovery audit trail.

Revision ID: core_040
Revises: core_039
Create Date: 2026-03-25 00:00:00.000000

Creates the ``corrections`` table in each butler's own schema (accessed via
the active search_path, exactly like ``sessions`` and ``state``).  The table
records every correction attempt — successful or failed — as an append-only
audit trail.

Implements the persistence layer required by ``src/butlers/core/corrections.py``
(the ``create_correction`` function and associated audit helpers).

Schema:

  corrections
  -----------
  id                    UUID PK
  correction_type       TEXT NOT NULL  CHECK (data_correction | memory_deletion |
                                              misroute | action_reversal)
  target_session_id     UUID NOT NULL  FK → sessions(id)
  correcting_session_id UUID NOT NULL  FK → sessions(id)
  description           TEXT NOT NULL
  status                TEXT NOT NULL  CHECK (applied | partially_applied | failed)
  summary               TEXT NOT NULL
  original_data_snapshot JSONB
  correction_details    JSONB
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()

Indexes:
  idx_corrections_target_session_id_created_at     — composite index covering the
                                                      target_session_id equality
                                                      filter + ORDER BY created_at
  idx_corrections_correcting_session_id_created_at — composite index covering the
                                                      correcting_session_id equality
                                                      filter + created_at range
                                                      predicate (rate-limit path)
  idx_corrections_correction_type                  — filter by correction type
                                                     (dashboard, audit exports)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_040"
down_revision = "core_039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create the corrections table (per-butler, via search_path).
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            correction_type TEXT NOT NULL CHECK (correction_type IN (
                'data_correction', 'memory_deletion', 'misroute', 'action_reversal'
            )),
            target_session_id UUID NOT NULL REFERENCES sessions(id),
            correcting_session_id UUID NOT NULL REFERENCES sessions(id),
            description TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'applied', 'partially_applied', 'failed'
            )),
            summary TEXT NOT NULL,
            original_data_snapshot JSONB,
            correction_details JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -------------------------------------------------------------------------
    # 2. Indexes.
    # -------------------------------------------------------------------------

    # Primary lookup: find all corrections targeting a given session.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_corrections_target_session_id
            ON corrections (target_session_id)
    """)

    # Rate-limit query: count corrections made BY a correcting session in the
    # rolling hour window (_check_rate_limit in corrections.py).
    # Composite index covers both the equality filter and the time-range
    # predicate (created_at >= now() - 1h) in a single index scan.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_corrections_correcting_session_id_created_at
            ON corrections (correcting_session_id, created_at)
    """)

    # Audit query: fetch all corrections targeting a session ordered by time
    # (get_corrections_for_session in corrections.py uses ORDER BY created_at).
    # Composite index eliminates the sort step entirely.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_corrections_target_session_id_created_at
            ON corrections (target_session_id, created_at)
    """)

    # Filter by correction type (dashboard, audit exports).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_corrections_correction_type
            ON corrections (correction_type)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_corrections_correction_type")
    op.execute("DROP INDEX IF EXISTS idx_corrections_correcting_session_id_created_at")
    op.execute("DROP INDEX IF EXISTS idx_corrections_target_session_id_created_at")
    op.execute("DROP TABLE IF EXISTS corrections")
