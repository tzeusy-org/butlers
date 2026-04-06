"""qa_allowed_repositories: repository whitelist for QA PR creation.

Revision ID: core_057
Revises: core_056
Create Date: 2026-04-06 00:00:00.000000

Creates the ``public.qa_allowed_repositories`` table.  PR creation in the
QA dispatch engine is blocked (fail-closed) for any repository not present in
this table.  When the table is empty, ALL PR creation is blocked.

Columns:
  id              UUID PK (gen_random_uuid())
  owner           TEXT NOT NULL — GitHub organisation or user name
  repo            TEXT NOT NULL — repository name (without owner prefix)
  enabled         BOOLEAN NOT NULL DEFAULT TRUE — soft toggle
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()

Constraints:
  uq_qa_allowed_repositories_owner_repo — UNIQUE(owner, repo)

Indexes:
  idx_qa_allowed_repos_enabled — partial index on (owner, repo) WHERE enabled
"""

from __future__ import annotations

from alembic import op

revision = "core_057"
down_revision = "core_056"
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
        CREATE TABLE IF NOT EXISTS public.qa_allowed_repositories (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            owner      TEXT NOT NULL,
            repo       TEXT NOT NULL,
            enabled    BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_qa_allowed_repositories_owner_repo UNIQUE (owner, repo)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_allowed_repos_enabled
        ON public.qa_allowed_repositories (owner, repo)
        WHERE enabled = TRUE
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.qa_allowed_repositories", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_qa_allowed_repos_enabled")
    op.execute("DROP TABLE IF EXISTS public.qa_allowed_repositories")
