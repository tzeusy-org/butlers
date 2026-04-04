"""qa_patrols: create public.qa_patrols table.

Revision ID: core_051
Revises: core_050
Create Date: 2026-04-05 00:00:00.000000

Creates the QA Staffer patrol lifecycle table in the public schema.
Each row represents one patrol cycle executed by the QA staffer daemon.

Columns:
  id                    UUIDv7 PK (gen_random_uuid() as fallback before UUIDv7 extension)
  started_at            TIMESTAMPTZ NOT NULL DEFAULT now()
  completed_at          TIMESTAMPTZ (NULL while running)
  status                TEXT NOT NULL — one of:
                            running, clean, findings_dispatched, error, skipped_overlap
  findings_count        INTEGER NOT NULL DEFAULT 0
  novel_count           INTEGER NOT NULL DEFAULT 0
  dispatched_count      INTEGER NOT NULL DEFAULT 0
  log_lookback_minutes  INTEGER NOT NULL DEFAULT 15
  sources_polled        TEXT[] NOT NULL DEFAULT '{}'
  error_detail          TEXT (NULL on success)

Indexes:
  idx_qa_patrols_started_at  — range queries for dashboard
  idx_qa_patrols_status      — filter by status for recovery

Grants SELECT, INSERT, UPDATE to butler_qa_rw (created if it exists).
Also grants to all existing butler roles for observability reads.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_051"
down_revision = "core_050"
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
        CREATE TABLE IF NOT EXISTS public.qa_patrols (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at         TIMESTAMPTZ,
            status               TEXT NOT NULL DEFAULT 'running',
            findings_count       INTEGER NOT NULL DEFAULT 0,
            novel_count          INTEGER NOT NULL DEFAULT 0,
            dispatched_count     INTEGER NOT NULL DEFAULT 0,
            log_lookback_minutes INTEGER NOT NULL DEFAULT 15,
            sources_polled       TEXT[] NOT NULL DEFAULT '{}',
            error_detail         TEXT,
            CONSTRAINT ck_qa_patrols_status CHECK (
                status IN (
                    'running',
                    'clean',
                    'findings_dispatched',
                    'error',
                    'skipped_overlap'
                )
            )
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_patrols_started_at
        ON public.qa_patrols (started_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_patrols_status
        ON public.qa_patrols (status)
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.qa_patrols", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_qa_patrols_status")
    op.execute("DROP INDEX IF EXISTS public.idx_qa_patrols_started_at")
    op.execute("DROP TABLE IF EXISTS public.qa_patrols")
