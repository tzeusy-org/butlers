"""infra_conditions: durable append-per-episode infrastructure condition ledger.

Revision ID: core_182
Revises: core_181
Create Date: 2026-07-24 00:00:00.000000

bu-27dxl.6.2 — implements the durable representation defined by the merged
``define-infrastructure-reliability-lifecycle`` OpenSpec change (bu-27dxl.6.1,
PR #3522). See ``src/butlers/core/infra_conditions.py`` for the lifecycle
service (``reconcile_snapshot``, identity fingerprinting, and reads) that
owns all writes to this table.

This migration creates representation only — no producer (calendar_sync_
deadman.py, deploy_drift.py), QA dispatch, dashboard lifespan loop, or
connector reader is wired to it yet (later children, bu-27dxl.6.3+).

Table design (design.md "Decisions" #1-#3):
  - One row per EPISODE, not per confirmation. ``(source, fingerprint)``
    identifies a condition; ``episode`` is a 1-based per-identity counter.
    Confirmations mutate the active episode's row in place
    (``last_confirmed_at``); a new episode row is only inserted when a
    condition opens for the first time or recurs after a prior resolution.
  - ``uq_infra_conditions_active_episode`` (partial unique on
    ``(source, fingerprint) WHERE state IN ('open', 'aging')``) enforces "at
    most one active episode per identity" as a DB-level invariant, even
    though ``infra_conditions.reconcile_snapshot`` already serializes writers
    per ``source`` with a transaction-scoped advisory lock.
  - ``resolved_at`` / ``recovered_after_s`` are set exactly once, together,
    by the same UPDATE that transitions a row out of the active states; no
    later write ever touches a ``resolved`` row (the application-level
    immutability the design calls for).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_182"
down_revision = "core_181"
branch_labels = None
depends_on = None

# Mirrors core_162/core_181's _ALL_RUNTIME_ROLES — every butler role whose
# daemon may host a reliability producer (deploy drift, calendar deadman,
# QA infra_state suppression reads) once bu-27dxl.6.3+ wires them up.
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

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE"


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
        CREATE TABLE IF NOT EXISTS public.infra_conditions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source              TEXT NOT NULL,
            fingerprint         TEXT NOT NULL,
            episode             INTEGER NOT NULL,
            state               TEXT NOT NULL DEFAULT 'open',
            first_detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_confirmed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_escalated_at   TIMESTAMPTZ,
            next_reescalate_at  TIMESTAMPTZ,
            escalation_level    TEXT NOT NULL DEFAULT 'L0',
            resolved_at         TIMESTAMPTZ,
            recovered_after_s   DOUBLE PRECISION,
            summary             TEXT,
            metadata            JSONB,
            CONSTRAINT chk_infra_conditions_state
                CHECK (state IN ('open', 'aging', 'resolved')),
            CONSTRAINT chk_infra_conditions_escalation_level
                CHECK (escalation_level IN ('L0', 'L1', 'L2', 'L3')),
            CONSTRAINT chk_infra_conditions_episode_positive
                CHECK (episode >= 1),
            CONSTRAINT chk_infra_conditions_resolved_fields
                CHECK (
                    (state = 'resolved' AND resolved_at IS NOT NULL
                        AND recovered_after_s IS NOT NULL)
                    OR
                    (state != 'resolved' AND resolved_at IS NULL
                        AND recovered_after_s IS NULL)
                )
        )
    """)

    # At most one active (open/aging) episode per (source, fingerprint) —
    # the DB-level backstop for the "one active condition" invariant.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_infra_conditions_active_episode
        ON public.infra_conditions (source, fingerprint)
        WHERE state IN ('open', 'aging')
    """)

    # Episode numbering is unique per identity (append-per-episode history).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_infra_conditions_identity_episode
        ON public.infra_conditions (source, fingerprint, episode)
    """)

    # "What's due for escalation right now" sweep/lookup.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_infra_conditions_due
        ON public.infra_conditions (next_reescalate_at)
        WHERE state IN ('open', 'aging') AND next_reescalate_at IS NOT NULL
    """)

    # Dashboard/history listing: recent-first per source, all states.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_infra_conditions_source_state
        ON public.infra_conditions (source, state, first_detected_at DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.infra_conditions", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.infra_conditions CASCADE")
