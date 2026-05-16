"""memory_retention_policies: add public.memory_retention_policies and public.memory_compaction_log.

Revision ID: core_095
Revises: core_094
Create Date: 2026-05-16 00:00:00.000000

Adds two tables supporting the §10.1-§10.2 memory fold-in (Phase 8):

  public.memory_retention_policies — per-kind TTL / max_rows policy (admin-editable)
  public.memory_compaction_log     — one row per cleanup run (ts, kind, rows_removed,
                                     bytes_freed)

Kinds seeded: event, fact, preference, summary, transcript, embedding.

Current defaults derived from the memory module's existing cleanup logic:
- run_episode_cleanup() enforces max_entries=10_000 (mapped to 'event' / 'transcript')
- purge_superseded_facts() defaults older_than_days=7 (mapped to 'fact')
- Other kinds have no programmatic default today; seed with NULL (no limit).
"""

from __future__ import annotations

from alembic import op

revision = "core_095"
down_revision = "core_094"
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
    # memory_retention_policies: admin-editable per-kind TTL and row-cap.
    # ttl_days NULL = no time-based expiry.
    # max_rows NULL = no row-count cap.
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.memory_retention_policies (
            kind        TEXT PRIMARY KEY,
            ttl_days    INT,
            max_rows    BIGINT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_by  TEXT
        )
    """)

    # Seed with defaults derived from existing cleanup logic.
    # event / transcript: max_entries default 10 000 in run_episode_cleanup().
    # fact: older_than_days default 7 in purge_superseded_facts().
    # preference, summary, embedding: no programmatic cap today → NULL.
    op.execute("""
        INSERT INTO public.memory_retention_policies
            (kind, ttl_days, max_rows, updated_by)
        VALUES
            ('event',       NULL, 10000, 'system'),
            ('fact',        7,    NULL,  'system'),
            ('preference',  NULL, NULL,  'system'),
            ('summary',     NULL, NULL,  'system'),
            ('transcript',  NULL, 10000, 'system'),
            ('embedding',   NULL, NULL,  'system')
        ON CONFLICT (kind) DO NOTHING
    """)

    # memory_compaction_log: one row per cleanup run.
    # bytes_freed may be NULL when the DB does not expose pg_total_relation_size
    # for the affected table (e.g. no SUPERUSER privilege in the cleanup role).
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.memory_compaction_log (
            id           BIGSERIAL PRIMARY KEY,
            ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
            kind         TEXT NOT NULL,
            rows_removed BIGINT NOT NULL DEFAULT 0,
            bytes_freed  BIGINT
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_compaction_log_ts
        ON public.memory_compaction_log (ts DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_compaction_log_kind
        ON public.memory_compaction_log (kind)
    """)

    for role in _ALL_RUNTIME_ROLES:
        _grant_best_effort("public.memory_retention_policies", _TABLE_PRIVILEGES, role)
        _grant_best_effort("public.memory_compaction_log", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.idx_memory_compaction_log_kind")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_compaction_log_ts")
    op.execute("DROP TABLE IF EXISTS public.memory_compaction_log")
    op.execute("DROP TABLE IF EXISTS public.memory_retention_policies")
