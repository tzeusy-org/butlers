"""qa_dismissals: create public.qa_dismissals table.

Revision ID: core_053
Revises: core_052
Create Date: 2026-04-05 00:00:00.000000

Creates the QA dismissals table in the public schema.
A dismissal suppresses investigation dispatch for a specific error fingerprint
until the dismissal expires or is manually removed.

Columns:
  fingerprint       TEXT PK — SHA-256 hex fingerprint of the suppressed error
  dismissed_until   TIMESTAMPTZ NOT NULL — expiry timestamp (may be far future
                        for indefinite suppression)
  dismissed_by      TEXT NOT NULL — who dismissed (e.g. 'owner', 'dashboard_user')
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()

The primary key on fingerprint ensures at most one active dismissal per error.
Upsert semantics: INSERT … ON CONFLICT (fingerprint) DO UPDATE allows extending
or replacing dismissals without needing a separate UPDATE path.

Indexes:
  idx_qa_dismissals_dismissed_until — for expiry sweeps (cleanup of expired rows)
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_053"
down_revision = "core_052"
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


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.qa_dismissals (
            fingerprint      TEXT PRIMARY KEY,
            dismissed_until  TIMESTAMPTZ NOT NULL,
            dismissed_by     TEXT NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_dismissals_dismissed_until
        ON public.qa_dismissals (dismissed_until)
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.qa_dismissals", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_qa_dismissals_dismissed_until")
    op.execute("DROP TABLE IF EXISTS public.qa_dismissals")
