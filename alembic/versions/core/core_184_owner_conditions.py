"""owner_conditions: durable append-per-episode owner-facing standing concern ledger.

Revision ID: core_184
Revises: core_183
Create Date: 2026-07-26 00:00:00.000000

bu-ep4ks.6 — generalizes ``core_182_infra_conditions``' table design to
owner-facing standing concerns (overdue bill, refill due, expiring document,
overloaded day). See ``src/butlers/core/owner_conditions.py`` for the
lifecycle service (``reconcile_snapshot``, identity fingerprinting, and
reads) that owns all writes to this table, and
``src/butlers/core/condition_ledger.py`` for the shared engine both this
table and ``public.infra_conditions`` are reconciled through.

Deliberately a SEPARATE table from ``infra_conditions`` rather than a shared
one with a discriminator column: infrastructure reliability and owner-facing
standing concerns have distinct producers, distinct audiences (the reliability
ledger is an operator/system-health surface; this one is an owner-attention
surface), and distinct growth/retention expectations. Sharing one table would
also collapse ``uq_*_active_episode``'s "at most one active episode per
identity" invariant across two conceptually unrelated domains for no benefit.

Table design mirrors core_182 exactly (see its docstring for the full
rationale): one row per EPISODE; ``(source, fingerprint)`` identifies a
condition; confirmations mutate the active episode's row in place; a new
episode row is only inserted when a condition opens for the first time or
recurs after a prior resolution.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_184"
down_revision = "core_183"
branch_labels = None
depends_on = None

# Every butler role whose daemon may host an owner-condition producer.
# Mirrors core_182's _ALL_RUNTIME_ROLES — kept identical since any butler
# schema may eventually reconcile a standing owner-facing concern (finance
# is the first producer; bu-ep4ks.6 slice 2).
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
        CREATE TABLE IF NOT EXISTS public.owner_conditions (
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
            CONSTRAINT chk_owner_conditions_state
                CHECK (state IN ('open', 'aging', 'resolved')),
            CONSTRAINT chk_owner_conditions_escalation_level
                CHECK (escalation_level IN ('L0', 'L1', 'L2', 'L3')),
            CONSTRAINT chk_owner_conditions_episode_positive
                CHECK (episode >= 1),
            CONSTRAINT chk_owner_conditions_resolved_fields
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
        CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_conditions_active_episode
        ON public.owner_conditions (source, fingerprint)
        WHERE state IN ('open', 'aging')
    """)

    # Episode numbering is unique per identity (append-per-episode history).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_owner_conditions_identity_episode
        ON public.owner_conditions (source, fingerprint, episode)
    """)

    # "What's due for escalation right now" sweep/lookup.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_owner_conditions_due
        ON public.owner_conditions (next_reescalate_at)
        WHERE state IN ('open', 'aging') AND next_reescalate_at IS NOT NULL
    """)

    # Dashboard/history listing: recent-first per source, all states.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_owner_conditions_source_state
        ON public.owner_conditions (source, state, first_detected_at DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.owner_conditions", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.owner_conditions CASCADE")
