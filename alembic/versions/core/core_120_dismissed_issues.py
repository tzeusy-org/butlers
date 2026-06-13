"""dismissed_issues: create public.dismissed_issues table.

Revision ID: core_120
Revises: core_119
Create Date: 2026-06-14 00:00:00.000000

Backs the dashboard Issues "Dismiss" control with a real, server-side
acknowledgement so a dismissal persists across browsers and sessions instead
of living only in one browser's localStorage.

Issues are derived/ephemeral (live reachability checks + grouped audit-log
errors) — there is no concrete issues row to flag — so the ack is keyed by a
stable, deterministic ``issue_key`` (see ``butlers.api.models.compute_issue_key``).

Columns:
  issue_key     TEXT PK — deterministic key identifying an issue group
  dismissed_by  TEXT NOT NULL — who dismissed (e.g. 'dashboard_user', 'owner')
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()

Upsert semantics: INSERT … ON CONFLICT (issue_key) DO UPDATE keeps the row
idempotent so re-dismissing the same group does not error.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_120"
down_revision = "core_119"
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
        CREATE TABLE IF NOT EXISTS public.dismissed_issues (
            issue_key    TEXT PRIMARY KEY,
            dismissed_by TEXT NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    for role in _ALL_BUTLER_ROLES:
        _grant_best_effort("public.dismissed_issues", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public.dismissed_issues")
