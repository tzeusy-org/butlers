"""qa_investigation_events: store QA dossier timeline events.

Revision ID: core_091
Revises: core_090
Create Date: 2026-05-15 00:00:00.000000

Creates ``public.qa_investigation_events`` for recording the ordered event
stream behind QA investigation dossiers.
"""

from __future__ import annotations

from alembic import op

revision = "core_091"
down_revision = "core_090"
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

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


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
        CREATE TABLE IF NOT EXISTS public.qa_investigation_events (
            id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            attempt_id UUID NOT NULL
                       REFERENCES public.healing_attempts(id)
                       ON DELETE CASCADE,
            finding_id UUID
                       REFERENCES public.qa_findings(id)
                       ON DELETE SET NULL,
            ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
            step       TEXT NOT NULL
                       CHECK (
                           step IN (
                               'flagged',
                               'sampled',
                               'cross-checked',
                               'considered',
                               'concluded',
                               'drafted',
                               'wait',
                               'merged',
                               'tick',
                               'escalated'
                           )
                       ),
            text       TEXT NOT NULL,
            detail     TEXT,
            data       JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_inv_events_attempt_ts
        ON public.qa_investigation_events (attempt_id, ts)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_inv_events_step
        ON public.qa_investigation_events (step)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.qa_investigation_events", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_qa_inv_events_step")
    op.execute("DROP INDEX IF EXISTS public.idx_qa_inv_events_attempt_ts")
    op.execute("DROP TABLE IF EXISTS public.qa_investigation_events")
