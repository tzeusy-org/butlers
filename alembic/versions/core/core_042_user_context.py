"""user_context: create shared.user_context table for situational context bus

Revision ID: core_042
Revises: core_041
Create Date: 2026-03-28 00:00:00.000000

Creates ``shared.user_context`` as a shared situational-awareness store used
by all butlers to publish and query presence-like signals (traveling, sleeping,
meeting, etc.).

Table design:

  shared.user_context
  -------------------
  id               UUID PK                  — surrogate key
  signal_type      TEXT NOT NULL            — vocabulary-validated signal name
  value            TEXT                     — optional signal value (e.g. destination)
  set_by_butler    TEXT NOT NULL            — butler that wrote the signal
  set_at           TIMESTAMPTZ NOT NULL     — write time (always refreshed on upsert)
  expires_at       TIMESTAMPTZ NOT NULL     — hard expiry; no indefinite signals
  confidence       REAL NOT NULL DEFAULT 1.0 — 0.0–1.0 range enforced by CHECK
  metadata         JSONB                   — optional extra payload
  superseded_at    TIMESTAMPTZ              — set when explicitly cleared early

Constraints:
  UNIQUE (signal_type, set_by_butler)
    — each butler maintains at most one active row per signal type;
      upserts use ON CONFLICT DO UPDATE.
  CHECK (confidence >= 0.0 AND confidence <= 1.0)
    — rejects out-of-range confidence at the DB level.

Index:
  idx_user_context_active_signals  (signal_type) WHERE superseded_at IS NULL AND expires_at > now()
    — partial index optimising the common hot-path: queries for currently active
      signals of a given type.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_042"
down_revision = "core_041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Ensure the shared schema exists (idempotent guard).
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS shared")

    # -------------------------------------------------------------------------
    # 2. Create shared.user_context.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.user_context (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            signal_type   TEXT NOT NULL,
            value         TEXT,
            set_by_butler TEXT NOT NULL,
            set_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ NOT NULL,
            confidence    REAL NOT NULL DEFAULT 1.0
                          CHECK (confidence >= 0.0 AND confidence <= 1.0),
            metadata      JSONB,
            superseded_at TIMESTAMPTZ,

            CONSTRAINT uq_user_context_signal_butler
                UNIQUE (signal_type, set_by_butler)
        )
    """)

    # -------------------------------------------------------------------------
    # 3. Partial index on signal_type for active-signal queries.
    #    The WHERE clause mirrors the application-level "is_active" definition:
    #    superseded_at IS NULL AND expires_at > now().
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_context_active_signals
            ON shared.user_context (signal_type)
            WHERE superseded_at IS NULL AND expires_at > now()
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shared.idx_user_context_active_signals")
    op.execute("DROP TABLE IF EXISTS shared.user_context")
