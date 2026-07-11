"""breaker_resets: durable audit record of manual QA circuit-breaker resets.

Revision ID: core_164
Revises: core_163
Create Date: 2026-07-11 00:00:00.000000

bu-533qx.1 (epic bu-533qx "QA breaker truth") — per the 2026-07-10 JARVIS
pursuit dossier (``docs/redesigns/2026-07-10-jarvis-pursuit.md`` §Ranked
moves): the old ``POST /api/qa/circuit-breaker/reset`` handler broke the
consecutive-failure chain by INSERTing a *synthetic* clean ``qa_patrols`` row
plus a fake ``manual_reset`` ``healing_attempts`` row. Those forged rows were
then consumed by ``GET /api/qa/summary`` (last-patrol + 24h/all-time stats)
and the derived ``staffer_status``, repainting the QA staffer "healthy"
exactly at the moment it had failed five consecutive times.

This migration replaces that fabrication with an honest, auditable primitive:

  - ``public.breaker_resets`` records *who / when / why* a breaker was reset.
    The QA dispatch admission gate and the dashboard breaker queries consult
    the latest reset timestamp instead of counting a forged clean patrol, so
    the real failure history stays visible while the breaker still admits new
    dispatches after an operator reset.

  - A self-guarding backfill deletes ONLY the provably-forged rows left behind
    by the old handler. ``status = 'manual_reset'`` is exclusive to that code
    path — it is not a member of the healing state machine's ``VALID_STATUSES``
    and can only have been produced by the synthetic INSERT — so it is a
    reliable forged-row marker. The synthetic ``qa_patrols`` rows are exactly
    those referenced by a ``manual_reset`` attempt and referenced by nothing
    else (a fresh UUID minted per reset). The backfill snapshots the forged
    counts, deletes with that narrow predicate, and RAISEs on any parity
    mismatch so a surprise (e.g. a synthetic patrol unexpectedly shared with a
    genuine attempt) aborts the migration rather than silently over-deleting.

Table design mirrors ``public.deployments`` (core_163): one row per event,
granted ``SELECT, INSERT`` to every butler runtime role (the dashboard API's
shared pool writes it; the QA staffer's ``butler_qa_rw`` role reads it during
dispatch admission).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_164"
down_revision = "core_163"
branch_labels = None
depends_on = None

# Mirrors core_163's _ALL_RUNTIME_ROLES — every butler role whose runtime may
# read (QA dispatch admission) or write (dashboard reset) this table.
_ALL_RUNTIME_ROLES = (
    "butler_chronicler_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO "{role}"';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.breaker_resets (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            breaker    TEXT NOT NULL,
            reset_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            reset_by   TEXT NOT NULL DEFAULT 'dashboard',
            reason     TEXT,
            CONSTRAINT chk_breaker_resets_breaker CHECK (breaker <> '')
        )
    """)

    # Admission + dashboard both fetch "latest reset for breaker X", so index
    # the exact (breaker, reset_at DESC) access path.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_breaker_resets_breaker_reset_at
        ON public.breaker_resets (breaker, reset_at DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.breaker_resets", _TABLE_PRIVILEGES, role)

    # ------------------------------------------------------------------
    # Self-guarding backfill-delete of the old synthetic reset rows.
    #
    # Runs unconditionally: on a fresh/clean DB every snapshot count is 0 and
    # the parity checks are trivially satisfied (0 == 0), so the migration is a
    # no-op there and the upgrade+downgrade integrity path stays green.
    # ------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE
            v_patrol_ids        UUID[];
            v_snapshot_attempts BIGINT;
            v_snapshot_patrols  BIGINT;
            v_deleted_attempts  BIGINT;
            v_deleted_patrols   BIGINT;
        BEGIN
            -- Forged attempt rows: status='manual_reset' is produced ONLY by the
            -- retired synthetic-reset handler (not a healing state-machine status).
            SELECT count(*) INTO v_snapshot_attempts
            FROM public.healing_attempts
            WHERE status = 'manual_reset';

            -- Forged patrol rows: the synthetic 'clean' patrols those attempts
            -- point at, minted per-reset and referenced by NOTHING else. The
            -- NOT EXISTS guard means a synthetic patrol somehow shared with a
            -- genuine attempt is deliberately left intact.
            SELECT array_agg(p.id) INTO v_patrol_ids
            FROM public.qa_patrols p
            WHERE p.id IN (
                    SELECT qa_patrol_id FROM public.healing_attempts
                    WHERE status = 'manual_reset' AND qa_patrol_id IS NOT NULL
                  )
              AND NOT EXISTS (
                    SELECT 1 FROM public.healing_attempts h
                    WHERE h.qa_patrol_id = p.id
                      AND h.status <> 'manual_reset'
                  );

            v_patrol_ids := COALESCE(v_patrol_ids, ARRAY[]::uuid[]);
            v_snapshot_patrols := array_length(v_patrol_ids, 1);
            IF v_snapshot_patrols IS NULL THEN
                v_snapshot_patrols := 0;
            END IF;

            -- Delete forged attempts first (children), then their orphan patrols.
            WITH del AS (
                DELETE FROM public.healing_attempts
                WHERE status = 'manual_reset'
                RETURNING 1
            )
            SELECT count(*) INTO v_deleted_attempts FROM del;

            WITH del AS (
                DELETE FROM public.qa_patrols
                WHERE id = ANY(v_patrol_ids)
                RETURNING 1
            )
            SELECT count(*) INTO v_deleted_patrols FROM del;

            IF v_deleted_attempts <> v_snapshot_attempts THEN
                RAISE EXCEPTION
                    'core_164 backfill parity mismatch (attempts): snapshot=%, deleted=%',
                    v_snapshot_attempts, v_deleted_attempts;
            END IF;
            IF v_deleted_patrols <> v_snapshot_patrols THEN
                RAISE EXCEPTION
                    'core_164 backfill parity mismatch (patrols): snapshot=%, deleted=%',
                    v_snapshot_patrols, v_deleted_patrols;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Non-reversible backfill: the forged synthetic rows are gone for good (by
    # design — they were fabricated history). Only the table/index are dropped.
    op.execute("DROP INDEX IF EXISTS public.idx_breaker_resets_breaker_reset_at")
    op.execute("DROP TABLE IF EXISTS public.breaker_resets CASCADE")
