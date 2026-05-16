"""audit_log: append-only audit primitive in public schema.

Revision ID: core_092
Revises: core_091
Create Date: 2026-05-16 00:00:00.000000

Creates ``public.audit_log`` — a single, append-only audit table used by
every mutation endpoint that changes system state.

Columns
-------
id          BIGSERIAL PRIMARY KEY
ts          TIMESTAMPTZ NOT NULL DEFAULT now()
actor       TEXT NOT NULL
action      TEXT NOT NULL
target      TEXT
note        TEXT
ip          INET
request_id  UUID

Indexes
-------
idx_audit_log_ts_desc   (ts DESC)
idx_audit_log_action    (action)
idx_audit_log_actor     (actor)

The table is append-only by policy — no DELETE or UPDATE is ever
performed against it.  This is enforced by a static-check test in the
test suite.
"""

from __future__ import annotations

from alembic import op

revision = "core_092"
down_revision = "core_091"
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

_TABLE_PRIVILEGES = "SELECT, INSERT"


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
        CREATE TABLE IF NOT EXISTS public.audit_log (
            id         BIGSERIAL PRIMARY KEY,
            ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
            actor      TEXT NOT NULL,
            action     TEXT NOT NULL,
            target     TEXT,
            note       TEXT,
            ip         INET,
            request_id UUID
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_ts_desc
        ON public.audit_log (ts DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_action
        ON public.audit_log (action)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_actor
        ON public.audit_log (actor)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.audit_log", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_audit_log_actor")
    op.execute("DROP INDEX IF EXISTS public.idx_audit_log_action")
    op.execute("DROP INDEX IF EXISTS public.idx_audit_log_ts_desc")
    op.execute("DROP TABLE IF EXISTS public.audit_log")
