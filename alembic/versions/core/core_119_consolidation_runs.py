"""consolidation_runs: add public.consolidation_runs audit table.

Revision ID: core_119
Revises: core_118
Create Date: 2026-06-12 00:00:00.000000

Adds ``public.consolidation_runs`` — one row written per successful memory
consolidation run (memory redesign brief §3, Phase D note 3). The
consolidation pipeline (``run_consolidation`` in
``src/butlers/modules/memory/consolidation.py``) already computes per-group
counts (facts created/updated, rules created, confirmations, episodes
processed, errors); this table persists them so the read-side
``GET /api/memory/stats`` delta (``last_consolidation_at`` /
``last_consolidation_facts_produced``) is derivable. Without it that field is
otherwise underivable from the live schema.

Columns
-------
id                  BIGSERIAL PRIMARY KEY
butler              TEXT NOT NULL          -- source butler whose episodes were consolidated
consolidated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
episodes_processed  INT NOT NULL DEFAULT 0
facts_produced      INT NOT NULL DEFAULT 0 -- new facts stored this run
facts_updated       INT NOT NULL DEFAULT 0
rules_created       INT NOT NULL DEFAULT 0
confirmations_made  INT NOT NULL DEFAULT 0
errors              INT NOT NULL DEFAULT 0 -- count of error messages from the run

Indexes
-------
idx_consolidation_runs_butler_consolidated_at  (butler, consolidated_at DESC)
    Supports the per-butler "latest run" lookup used by the stats fan-out.

ADDITIVE-ONLY
-------------
This migration only CREATEs a new public table — it does not touch any
existing memory table (episodes, facts, rules, ...), keeping faith with the
VISION "no storage or schema migration" rule, which the brief carves out an
explicit exception to for this additive audit table.

Grants
------
Butler runtime roles receive read-only ``SELECT`` only. The consolidation job
writes through the owning database role (the migration/runtime owner), which
retains full access regardless of these grants — mirroring the privilege
posture for a cross-butler read surface.
"""

from __future__ import annotations

from alembic import op

revision = "core_119"
down_revision = "core_118"
branch_labels = None
depends_on = None

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
    "connector_writer",
)

# Read-only: the audit table is a cross-butler read surface. The consolidation
# job writes via the owning role, which is unaffected by these grants.
_TABLE_PRIVILEGES = "SELECT"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_best_effort(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role; tolerate older DBs missing roles."""
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
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.consolidation_runs (
            id                 BIGSERIAL PRIMARY KEY,
            butler             TEXT NOT NULL,
            consolidated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            episodes_processed INT NOT NULL DEFAULT 0,
            facts_produced     INT NOT NULL DEFAULT 0,
            facts_updated      INT NOT NULL DEFAULT 0,
            rules_created      INT NOT NULL DEFAULT 0,
            confirmations_made INT NOT NULL DEFAULT 0,
            errors             INT NOT NULL DEFAULT 0
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_consolidation_runs_butler_consolidated_at
        ON public.consolidation_runs (butler, consolidated_at DESC)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.consolidation_runs", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_consolidation_runs_butler_consolidated_at")
    op.execute("DROP TABLE IF EXISTS public.consolidation_runs")
