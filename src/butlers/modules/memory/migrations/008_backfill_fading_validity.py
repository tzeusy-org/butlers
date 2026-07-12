"""Backfill facts.validity='fading' for legacy metadata.status-only rows.

Revision ID: mem_008
Revises: mem_007
Create Date: 2026-07-12 00:00:00.000000

bu-5ud8p.1 (discovered-from bu-5ud8p) — ``run_decay_sweep``
(``src/butlers/modules/memory/storage.py``) has, since it was first written,
only ever recorded a fading fact by setting ``metadata.status = 'fading'``
(a JSONB key). Every reader that reports the fading count — the dashboard
API (``src/butlers/api/routers/memory.py`` ``GET /api/memory/stats``,
``GET /api/memory/facts?validity=fading``) and the ``memory_stats`` MCP tool
(``src/butlers/modules/memory/tools/management.py``) — queries the
``validity`` COLUMN, e.g. ``WHERE validity = 'fading'``. Because the sweep
never wrote that column, the fading count has been structurally zero on
every butler schema since the feature shipped: a dead data contract on the
dashboard page titled "What the house believes".

The same PR fixes ``run_decay_sweep`` to write ``validity = 'fading'`` going
forward (and re-select already-fading facts so they can recover to 'active'
or progress to 'expired'). This migration is the one-time correction for
facts that a *prior* sweep run already marked via the legacy
``metadata.status`` key but left at ``validity = 'active'``.

Guards (mirrors the core_166 self-guarding backfill pattern):
  - Runs unconditionally: on a fresh/pre-memory-module schema (no ``facts``
    table yet) it is a no-op via ``to_regclass``.
  - Only touches rows that are unambiguously legacy-fading: ``validity =
    'active' AND metadata->>'status' = 'fading'``. Rows already correctly
    marked (``validity = 'fading'``) or genuinely healthy rows are untouched.
  - Snapshots the matching count, updates, and RAISEs on any parity mismatch
    between the snapshot and the rows actually updated, so a surprise (e.g.
    a concurrent writer) aborts the migration rather than silently under-
    or over-correcting.
  - The legacy ``metadata.status`` key is removed as part of the same UPDATE
    — it is superseded by the ``validity`` column, not dual-written (see the
    matching cleanup in ``run_decay_sweep`` itself).

Applied per butler schema like every other migration in this module chain
(``src/butlers/migrations.py`` runs the ``memory`` chain once per schema for
every butler with ``[modules.memory]`` enabled) — unqualified ``facts``
resolves via each schema's own search_path, so one migration file backfills
every butler's data, not just one.

No corresponding downgrade: this is a data correction, not a schema change —
the pre-backfill state (validity='active' with a stale metadata.status key)
was itself the bug, so there is nothing worth restoring.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_008"
down_revision = "mem_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        DECLARE
            v_snapshot BIGINT;
            v_updated  BIGINT;
        BEGIN
            IF to_regclass('facts') IS NULL THEN
                RETURN;
            END IF;

            SELECT count(*) INTO v_snapshot
            FROM facts
            WHERE validity = 'active' AND metadata->>'status' = 'fading';

            WITH corrected AS (
                UPDATE facts
                SET validity = 'fading',
                    metadata = metadata - 'status'
                WHERE validity = 'active' AND metadata->>'status' = 'fading'
                RETURNING 1
            )
            SELECT count(*) INTO v_updated FROM corrected;

            IF v_updated <> v_snapshot THEN
                RAISE EXCEPTION
                    'mem_008 backfill parity mismatch: snapshot=%, updated=%',
                    v_snapshot, v_updated;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Non-reversible data correction — see module docstring. Nothing to undo:
    # the pre-migration state was the bug (validity='active' + stale
    # metadata.status='fading'), not a legitimate prior state worth restoring.
    pass
