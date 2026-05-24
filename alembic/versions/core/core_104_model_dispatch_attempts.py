"""model_dispatch_attempts: create public.model_dispatch_attempts table.

Revision ID: core_104
Revises: core_103
Create Date: 2026-05-24 00:00:00.000000

Tracks per-session model dispatch attempts for durable failover provenance.
Each attempt (quota-skip, runtime failure, successful fallback, suppressed
failover) writes one row.  Rows are written best-effort from the spawner and
never cause the session to fail.

Schema
------
id               BIGSERIAL PK
session_id       UUID                  -- optional; None for pre-session quota skips
catalog_entry_id UUID NOT NULL FK → public.model_catalog(id) ON DELETE CASCADE
ts               TIMESTAMPTZ NOT NULL DEFAULT now()
butler           TEXT NOT NULL         -- butler name
outcome          TEXT NOT NULL         -- 'quota_skip' | 'runtime_failure' | 'success'
                                       --   | 'suppressed' | 'exhausted'
failure_reason   TEXT                  -- classifier reason or quota window description
error_code       TEXT                  -- short slug, e.g. "TimeoutError"
error_message    TEXT                  -- error string (truncated to 4096 chars)
tool_call_count  INT                   -- captured tool calls at failure point (0 on skip)
attempt_index    INT NOT NULL DEFAULT 0 -- 0 = primary, 1+ = fallback candidates
logical_session_id TEXT               -- correlation key tying all attempts together
                                       --   (request_id or generated UUID7)
"""

from __future__ import annotations

from alembic import op

revision = "core_104"
down_revision = "core_103"
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
        CREATE TABLE IF NOT EXISTS public.model_dispatch_attempts (
            id                 BIGSERIAL    PRIMARY KEY,
            session_id         UUID,
            catalog_entry_id   UUID         NOT NULL
                REFERENCES public.model_catalog(id) ON DELETE CASCADE,
            ts                 TIMESTAMPTZ  NOT NULL DEFAULT now(),
            butler             TEXT         NOT NULL,
            outcome            TEXT         NOT NULL,
            failure_reason     TEXT,
            error_code         TEXT,
            error_message      TEXT,
            tool_call_count    INT,
            attempt_index      INT          NOT NULL DEFAULT 0,
            logical_session_id TEXT
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_dispatch_attempts_catalog_ts
        ON public.model_dispatch_attempts (catalog_entry_id, ts DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_dispatch_attempts_session
        ON public.model_dispatch_attempts (session_id)
        WHERE session_id IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_model_dispatch_attempts_logical_session
        ON public.model_dispatch_attempts (logical_session_id)
        WHERE logical_session_id IS NOT NULL
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.model_dispatch_attempts", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_logical_session")
    op.execute("DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_session")
    op.execute("DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_catalog_ts")
    op.execute("DROP TABLE IF EXISTS public.model_dispatch_attempts")
