"""routines

Revision ID: chronicler_018
Revises: chronicler_017
Create Date: 2026-07-05 00:00:00.000000

Add ``chronicler.routines`` (bu-whhll.9): owner-reviewable weekly routine
rows produced by the deterministic routine miner (no LLM — pure statistics
over ``episodes``/``point_events``).

Schema
------
- ``dow_mask`` — bitmask over ISO weekday, bit 0 = Monday ... bit 6 = Sunday
  (``1 << date.weekday()``). ``CHECK`` restricts it to the non-empty range
  ``[1, 127]``.
- ``window_start_local`` / ``window_end_local`` — local wall-clock ``TIME``
  bounds (e.g. 09:30-19:30). ``CHECK`` enforces a same-day window (no
  midnight-spanning routines in v1).
- ``timezone`` — IANA zone the window is expressed in (owner is
  Asia/Singapore today; stored per-row so a future multi-timezone owner is
  not a schema change).
- ``label`` — human-readable summary, e.g. "Mon-Fri 09:30-19:30".
- ``support_count`` / ``confidence`` — mining statistics: how many observed
  weekday instances matched the pattern, and the resulting ratio.
- ``evidence_summary`` — JSONB free-form mining evidence (weeks analyzed,
  signal categories, day counts).
- ``origin`` — ``mined`` (written by the weekly job) or ``declared`` (owner
  bootstrap, bu-whhll.11). Only ``mined`` rows are subject to the
  idempotency index below; ``declared`` rows are managed by a later bead.
- ``enabled`` — owner review flag (PATCH /api/chronicler/routines/{id});
  re-mining must never silently re-enable a routine the owner disabled.

Idempotent re-mining
--------------------
``routines_mined_dow_mask_idx`` is a **partial unique index** on
``dow_mask`` WHERE ``origin = 'mined'``: at most one mined routine per exact
day-of-week combination. The miner's upsert targets this index and refreshes
only the mining-derived columns (window bounds, support_count, confidence,
evidence_summary) — it never touches ``label`` or ``enabled`` once a row
exists, so an owner rename/disable survives every subsequent re-mine.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_018"
down_revision = "chronicler_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS routines (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            dow_mask SMALLINT NOT NULL CHECK (dow_mask BETWEEN 1 AND 127),
            window_start_local TIME NOT NULL,
            window_end_local TIME NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Singapore',
            label TEXT NOT NULL,
            support_count INTEGER NOT NULL DEFAULT 0 CHECK (support_count >= 0),
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0
                CHECK (confidence >= 0 AND confidence <= 1),
            evidence_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            origin TEXT NOT NULL DEFAULT 'mined'
                CHECK (origin IN ('mined', 'declared')),
            enabled BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (window_end_local > window_start_local)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS routines_mined_dow_mask_idx
        ON routines (dow_mask)
        WHERE origin = 'mined'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS routines_mined_dow_mask_idx")
    op.execute("DROP TABLE IF EXISTS routines")
