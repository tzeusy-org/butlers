"""butler_reachability_conditions: durable outage-episode ledger for the Issues feed.

Revision ID: core_200
Revises: core_199
Create Date: 2026-08-22 00:00:00.000000

Backs the JARVIS pursuit run-08 move 3 (bu-6jv4m.3) condition ledger.

``GET /api/issues`` derives butler reachability from a live MCP ping, so before
this table the feed had no memory at all: every poll stamped
``first_seen_at == last_seen_at == now()`` onto the resulting issue.  The
acknowledge-until-recurrence check (core_152) compares an issue's recurrence
epoch against the ack watermark, so for reachability that comparison was always
``now <= ack_time`` -- false on the very next poll.  Acknowledging a
continuously-unreachable butler was structurally impossible to make stick.

This table gives the condition an identity that outlives the probe:

  butler        TEXT        which butler the condition is about
  started_at    TIMESTAMPTZ episode ONSET -- stable for one uninterrupted
                            outage; this is the recurrence epoch the ack is
                            held against
  last_seen_at  TIMESTAMPTZ when the outage was last OBSERVED (the probe
                            clock; advances every poll, and is deliberately
                            NOT the ack watermark)
  resolved_at   TIMESTAMPTZ set when the butler answers again; NULL while the
                            condition is open
  observations  INTEGER     consecutive failed probes in this episode
  detail        TEXT        the probe's own description of the failure

``ux_butler_reachability_conditions_open`` is a PARTIAL unique index on
``(butler) WHERE resolved_at IS NULL``: at most one episode is open per butler,
which is what lets the router's open-or-extend be a single atomic
``INSERT ... ON CONFLICT (butler) WHERE resolved_at IS NULL DO UPDATE``.
Resolved rows are unconstrained, so the same butler accumulates a real history
of distinct outages -- and a post-recovery outage gets a genuinely new
``started_at`` that correctly un-acks the earlier acknowledgement.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_200"
down_revision = "core_199"
branch_labels = None
depends_on = None

_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
    "butler_qa_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerates missing role/table."""
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


def _grant_sequence_best_effort(sequence_fqn: str, role: str) -> None:
    """GRANT USAGE, SELECT on the identity sequence; tolerates missing role."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{sequence_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE {sequence_fqn} TO "{role}"';
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
        CREATE TABLE IF NOT EXISTS public.butler_reachability_conditions (
            id           BIGSERIAL PRIMARY KEY,
            butler       TEXT NOT NULL,
            started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at  TIMESTAMPTZ NULL,
            observations INTEGER NOT NULL DEFAULT 1,
            detail       TEXT NULL
        )
    """)

    # At most one OPEN condition per butler. This is the constraint the
    # router's atomic open-or-extend upsert infers on; resolved rows are
    # intentionally left unconstrained so outage history accumulates.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_butler_reachability_conditions_open
            ON public.butler_reachability_conditions (butler)
            WHERE resolved_at IS NULL
    """)

    # History reads ("show me this butler's past outages") walk newest-first.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_butler_reachability_conditions_history
            ON public.butler_reachability_conditions (butler, started_at DESC)
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.butler_reachability_conditions", _TABLE_PRIVILEGES, role)
        _grant_sequence_best_effort("public.butler_reachability_conditions_id_seq", role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_butler_reachability_conditions_history")
    op.execute("DROP INDEX IF EXISTS ux_butler_reachability_conditions_open")
    op.execute("DROP TABLE IF EXISTS public.butler_reachability_conditions")
