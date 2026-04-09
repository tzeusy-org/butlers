"""healing_workflow_schema: workflow-level columns, child session/dispatch tables.

Revision ID: core_066
Revises: core_065
Create Date: 2026-04-09 00:00:00.000000

Implements the multi-session recovery workflow storage model:

1. Extend ``public.healing_attempts`` with workflow-level fields:
   - ``current_phase`` TEXT — the most recently launched workflow phase
     (``diagnose``, ``implement``, ``verify``, or NULL for single-session
     attempts created before multi-phase workflows were introduced).
   - ``workflow_deadline_at`` TIMESTAMPTZ — authoritative deadline for the
     entire workflow.  Set once at row creation, never updated.  NULL for
     legacy rows created before this migration; restart recovery falls back
     to the ``updated_at`` heuristic for those rows only.

2. Replace the partial unique index that included ``dispatch_pending`` with one
   that only covers ``('investigating', 'pr_open')``.  ``dispatch_pending`` is
   no longer a valid status — the novelty claim and row creation are atomic, so
   a row is inserted as ``investigating`` or not created at all.  Existing
   ``dispatch_pending`` rows (legacy hazard) are migrated to ``failed`` before
   the old index is dropped.

3. Create ``public.healing_attempt_sessions`` — child tracking for each
   launched runtime session within a healing workflow:
   - ``id`` UUID PK
   - ``attempt_id`` UUID FK → ``healing_attempts.id``
   - ``phase`` TEXT NOT NULL — workflow phase label
   - ``session_id`` UUID NOT NULL — spawned runtime session ID
   - ``status`` TEXT NOT NULL — ``running``, ``completed``, ``failed``,
     ``timeout``, ``cancelled``
   - ``started_at`` TIMESTAMPTZ NOT NULL DEFAULT now()
   - ``updated_at`` TIMESTAMPTZ NOT NULL DEFAULT now()
   - ``completed_at`` TIMESTAMPTZ
   - ``error_detail`` TEXT

4. Create ``public.healing_dispatch_events`` — persisted dispatch-decision
   tracking (gate rejections before any session is launched):
   - ``id`` UUID PK DEFAULT gen_random_uuid()
   - ``fingerprint`` TEXT NOT NULL
   - ``butler_name`` TEXT NOT NULL
   - ``decision`` TEXT NOT NULL — e.g. ``cooldown``, ``concurrency_cap``,
     ``circuit_breaker``, ``novelty_join``, ``no_model``, ``accepted``
   - ``reason`` TEXT — free-form detail
   - ``attempt_id`` UUID NULL FK → ``healing_attempts.id``
   - ``created_at`` TIMESTAMPTZ NOT NULL DEFAULT now()

Grants SELECT, INSERT, UPDATE, DELETE on all new / extended tables to all
butler runtime roles.

Legacy data hazard handling
----------------------------
Any ``dispatch_pending`` rows that exist before this migration (created by the
now-removed retry-endpoint path or by earlier bugs) are migrated to ``failed``
with an error_detail note.  This clears the old partial unique index before it
is dropped and prevents the partial unique index update from being blocked by
live ``dispatch_pending`` rows.

Backward-compatibility notes
-----------------------------
- ``current_phase`` and ``workflow_deadline_at`` default to NULL, so all
  existing rows are unaffected at read time.
- The new partial unique index covers the same fingerprints as before, minus
  ``dispatch_pending``.  After migration there are no ``dispatch_pending`` rows,
  so the uniqueness guarantee is equivalent.
- Legacy rows with ``workflow_deadline_at IS NULL`` are handled gracefully by
  the updated ``recover_stale_attempts`` function (falls back to
  ``updated_at + timeout_minutes`` heuristic for those rows only).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_066"
down_revision = "core_065"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
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
    # --------------------------------------------------------------------- #
    # 1. Legacy hazard: migrate dispatch_pending rows to failed
    # --------------------------------------------------------------------- #
    # Do this BEFORE modifying any index so we don't violate constraints.
    op.execute("""
        UPDATE public.healing_attempts
        SET
            status       = 'failed',
            updated_at   = now(),
            closed_at    = now(),
            error_detail = 'Migrated by core_066: dispatch_pending is no longer a valid status'
        WHERE status = 'dispatch_pending'
    """)

    # --------------------------------------------------------------------- #
    # 2. Extend healing_attempts with workflow-level columns
    # --------------------------------------------------------------------- #
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS current_phase TEXT DEFAULT NULL
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS workflow_deadline_at TIMESTAMPTZ DEFAULT NULL
    """)

    # --------------------------------------------------------------------- #
    # 3. Replace partial unique index (drop dispatch_pending from predicate)
    # --------------------------------------------------------------------- #
    # Drop the old index (includes dispatch_pending).
    op.execute("""
        DROP INDEX IF EXISTS public.uq_healing_attempts_active_fingerprint
    """)
    # Also drop the alias used in some tests
    op.execute("""
        DROP INDEX IF EXISTS public.idx_healing_active_fingerprint
    """)
    # Create new index without dispatch_pending.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_healing_attempts_active_fingerprint
        ON public.healing_attempts (fingerprint)
        WHERE status IN ('investigating', 'pr_open')
    """)

    # --------------------------------------------------------------------- #
    # 4. Create public.healing_attempt_sessions
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.healing_attempt_sessions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            attempt_id      UUID NOT NULL,
            phase           TEXT NOT NULL,
            session_id      UUID NOT NULL,
            status          TEXT NOT NULL DEFAULT 'running',
            started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at    TIMESTAMPTZ,
            error_detail    TEXT,
            CONSTRAINT fk_has_attempt_id
                FOREIGN KEY (attempt_id)
                REFERENCES public.healing_attempts(id)
                ON DELETE CASCADE
        )
    """)

    # Index for looking up sessions by attempt
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_has_attempt_id
        ON public.healing_attempt_sessions (attempt_id)
    """)

    # Index for looking up sessions by session_id (useful for watchdog queries)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_has_session_id
        ON public.healing_attempt_sessions (session_id)
    """)

    # --------------------------------------------------------------------- #
    # 5. Create public.healing_dispatch_events
    # --------------------------------------------------------------------- #
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.healing_dispatch_events (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            fingerprint     TEXT NOT NULL,
            butler_name     TEXT NOT NULL,
            decision        TEXT NOT NULL,
            reason          TEXT,
            attempt_id      UUID,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_hde_attempt_id
                FOREIGN KEY (attempt_id)
                REFERENCES public.healing_attempts(id)
                ON DELETE SET NULL
        )
    """)

    # Index on fingerprint for per-fingerprint decision history lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_hde_fingerprint
        ON public.healing_dispatch_events (fingerprint)
    """)

    # Index on created_at for ordered pagination queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_hde_created_at
        ON public.healing_dispatch_events (created_at DESC)
    """)

    # --------------------------------------------------------------------- #
    # 6. Grants
    # --------------------------------------------------------------------- #
    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.healing_attempt_sessions", _TABLE_PRIVILEGES, role)
        _grant_best_effort("public.healing_dispatch_events", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    # Drop new tables first (they FK into healing_attempts)
    op.execute("DROP TABLE IF EXISTS public.healing_dispatch_events")
    op.execute("DROP TABLE IF EXISTS public.healing_attempt_sessions")

    # Restore old partial unique index (with dispatch_pending)
    op.execute("DROP INDEX IF EXISTS public.uq_healing_attempts_active_fingerprint")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_healing_attempts_active_fingerprint
        ON public.healing_attempts (fingerprint)
        WHERE status IN ('dispatch_pending', 'investigating', 'pr_open')
    """)

    # Drop new columns
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS workflow_deadline_at
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS current_phase
    """)
