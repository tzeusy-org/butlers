"""qa_repo_config: repository URL configuration for QA investigations.

Revision ID: core_060
Revises: core_059
Create Date: 2026-04-07 00:00:00.000000

Creates the ``public.qa_repo_config`` table — a single-row configuration
table storing the git repository URL used by the QA staffer for
investigation worktrees.

The QA module clones this repository to an ephemeral cache directory and
creates per-investigation worktrees from it.  A singleton constraint
ensures only one configuration row exists.

Columns:
  id              UUID PK (gen_random_uuid())
  repo_url        TEXT NOT NULL — git remote URL (HTTPS or SSH)
  clone_path      TEXT — on-disk path to managed clone (set by daemon)
  last_synced_at  TIMESTAMPTZ — last successful git fetch + reset
  last_sync_error TEXT — last sync error message (NULL when healthy)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
"""

from __future__ import annotations

from alembic import op

revision = "core_060"
down_revision = "core_059"
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
        CREATE TABLE IF NOT EXISTS public.qa_repo_config (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            repo_url        TEXT NOT NULL DEFAULT 'https://github.com/Tzeusy/butlers',
            clone_path      TEXT,
            last_synced_at  TIMESTAMPTZ,
            last_sync_error TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            singleton       BOOLEAN NOT NULL DEFAULT TRUE UNIQUE CHECK (singleton = TRUE)
        )
    """)

    # Seed the default row
    op.execute("""
        INSERT INTO public.qa_repo_config (repo_url)
        VALUES ('https://github.com/Tzeusy/butlers')
        ON CONFLICT DO NOTHING
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.qa_repo_config", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.qa_repo_config")
