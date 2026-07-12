"""healing breaker resets: backfill-delete the forged pr_merged sentinel rows.

Revision ID: core_166
Revises: core_165
Create Date: 2026-07-12 00:00:00.000000

bu-dz7ac (discovered-from bu-533qx.1 / PR #3125) — the healing circuit
breaker's ``POST /api/healing/circuit-breaker/reset`` handler
(``src/butlers/api/routers/healing.py``) broke the consecutive-failure chain
by INSERTing a synthetic ``pr_merged`` ``healing_attempts`` row with a
``reset-sentinel-<uuid>`` fingerprint and a synthetic ``healing_session_id``
— the same fabricated-history class the QA breaker shed in core_164.
``get_recent_terminal_statuses`` (consumed by both the dispatch-admission
gate ``_is_circuit_breaker_tripped`` and the dashboard's
``_compute_breaker_state``) then read that forged row as a genuine
successful investigation, repainting the breaker closed exactly when real
history said otherwise.

``public.breaker_resets`` already exists (core_164) with a ``breaker`` text
discriminator built for exactly this reuse — no new table. This migration:

  - Backfill-deletes ONLY the provably-forged sentinel rows. The reset
    handler's fingerprint prefix ``reset-sentinel-`` is exclusive to that
    code path (real fingerprints are 64-character hex digests — see
    ``src/butlers/core/healing/tracking.py`` module docstring — so a
    ``reset-sentinel-`` prefix can never collide with a genuine fingerprint).
    The backfill snapshots the forged-row count, deletes with that exact
    prefix predicate, and RAISEs on any parity mismatch so a surprise aborts
    the migration rather than silently over- or under-deleting.
  - No schema change: ``public.breaker_resets`` and its grants already cover
    every butler runtime role from core_164.

Application code changes ship in the same PR: the reset endpoint now writes
one ``breaker_resets`` row (``breaker='healing'``) instead of forging a
``healing_attempts`` row, and ``get_recent_terminal_statuses`` only counts
launched attempts that closed after the latest ``breaker='healing'`` reset.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_166"
down_revision = "core_165"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Self-guarding backfill-delete of the old synthetic reset rows.
    #
    # Runs unconditionally: on a fresh/clean DB (or one that predates
    # public.healing_attempts) the snapshot count is 0 and the parity check
    # is trivially satisfied (0 == 0), so the migration is a no-op there.
    # ------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE
            v_snapshot_attempts BIGINT;
            v_deleted_attempts  BIGINT;
        BEGIN
            IF to_regclass('public.healing_attempts') IS NULL THEN
                RETURN;
            END IF;

            -- Forged rows: fingerprint prefix 'reset-sentinel-' is produced
            -- ONLY by the retired synthetic-reset handler. Real fingerprints
            -- are 64-character hex digests and can never collide with this
            -- prefix.
            SELECT count(*) INTO v_snapshot_attempts
            FROM public.healing_attempts
            WHERE fingerprint LIKE 'reset-sentinel-%';

            WITH del AS (
                DELETE FROM public.healing_attempts
                WHERE fingerprint LIKE 'reset-sentinel-%'
                RETURNING 1
            )
            SELECT count(*) INTO v_deleted_attempts FROM del;

            IF v_deleted_attempts <> v_snapshot_attempts THEN
                RAISE EXCEPTION
                    'core_166 backfill parity mismatch (attempts): snapshot=%, deleted=%',
                    v_snapshot_attempts, v_deleted_attempts;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Non-reversible backfill: the forged sentinel rows are gone for good (by
    # design — they were fabricated history). Nothing else to undo.
    pass
