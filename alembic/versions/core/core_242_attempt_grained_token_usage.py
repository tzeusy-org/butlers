"""Make token usage evidence attempt-grained and uncertainty-aware.

Revision ID: core_242
Revises: core_241
Create Date: 2026-09-21 00:00:00.000000

``public.token_usage_ledger`` is database-global and partitioned even though
the core chain runs once per butler schema. A bounded session lock therefore
serializes both directions across chains. The attempt index is built on each
leaf concurrently and attached to an empty parent so live ledger writes are
not blocked by a partition scan.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import op

revision = "core_242"
down_revision = "core_241"
branch_labels = None
depends_on = None

_USAGE_SOURCE_CONSTRAINT = "ck_token_usage_ledger_usage_source"
_USAGE_EVIDENCE_CONSTRAINT = "ck_token_usage_ledger_usage_evidence"
_INDEX = "public.idx_token_usage_ledger_attempt"
_LOCK = 242_20260921


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _acquire_lock(connection) -> None:
    # A blocking advisory-lock waiter retains a snapshot that CIC can wait on.
    # Poll in autocommit so failed attempts release their snapshots before the
    # process waits outside PostgreSQL.
    deadline = monotonic() + 5
    while not connection.exec_driver_sql(f"SELECT pg_try_advisory_lock({_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("attempt usage migration is busy; retry this core-chain operation")
        sleep(0.1)


def _add_constraint(name: str, body: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'public.token_usage_ledger'::regclass
                   AND conname = '{name}'
            ) THEN
                ALTER TABLE public.token_usage_ledger
                    ADD CONSTRAINT {name} CHECK ({body}) NOT VALID;
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE public.token_usage_ledger VALIDATE CONSTRAINT {name}")


def _build_partitioned_attempt_index(connection) -> None:
    # Including recorded_at is required for a unique index on a range-
    # partitioned table. Runtime writes derive it from the attempt row, so a
    # replay of the same attempt addresses the same unique key.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_token_usage_ledger_attempt "
        "ON ONLY public.token_usage_ledger (attempt_id, recorded_at) "
        "WHERE attempt_id IS NOT NULL"
    )
    partitions = connection.exec_driver_sql(
        "SELECT child.oid, ns.nspname, child.relname "
        "FROM pg_inherits inheritance "
        "JOIN pg_class child ON child.oid = inheritance.inhrelid "
        "JOIN pg_namespace ns ON ns.oid = child.relnamespace "
        "WHERE inheritance.inhparent = 'public.token_usage_ledger'::regclass "
        "ORDER BY child.oid"
    ).fetchall()
    for oid, schema, table in partitions:
        attached = connection.exec_driver_sql(
            "SELECT 1 FROM pg_inherits inheritance "
            "JOIN pg_index idx ON idx.indexrelid = inheritance.inhrelid "
            f"WHERE inheritance.inhparent = '{_INDEX}'::regclass "
            f"AND idx.indrelid = {int(oid)} AND idx.indisvalid"
        ).scalar()
        if attached:
            continue
        name = f"idx_token_usage_ledger_attempt_{oid}"
        qualified = f"{_quote(schema)}.{_quote(name)}"
        valid = connection.exec_driver_sql(
            "SELECT idx.indisvalid FROM pg_index idx "
            "JOIN pg_class index_table ON index_table.oid = idx.indexrelid "
            "JOIN pg_namespace ns ON ns.oid = index_table.relnamespace "
            "WHERE ns.nspname = %s AND index_table.relname = %s",
            (schema, name),
        ).scalar()
        if valid is False:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {qualified}")
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_quote(name)} "
            f"ON {_quote(schema)}.{_quote(table)} (attempt_id, recorded_at) "
            "WHERE attempt_id IS NOT NULL"
        )
        op.execute(f"ALTER INDEX {_INDEX} ATTACH PARTITION {qualified}")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
        op.execute("SET lock_timeout = '5s'")
        try:
            _acquire_lock(connection)
            try:
                op.execute(
                    """
                    ALTER TABLE public.token_usage_ledger
                        ADD COLUMN IF NOT EXISTS attempt_id BIGINT
                            REFERENCES public.model_dispatch_attempts(id) ON DELETE SET NULL,
                        ADD COLUMN IF NOT EXISTS usage_source TEXT NOT NULL DEFAULT 'measured',
                        ALTER COLUMN input_tokens DROP NOT NULL,
                        ALTER COLUMN output_tokens DROP NOT NULL,
                        ALTER COLUMN cached_input_tokens DROP NOT NULL,
                        ALTER COLUMN cache_creation_tokens DROP NOT NULL
                    """
                )
                _add_constraint(
                    _USAGE_SOURCE_CONSTRAINT,
                    "usage_source IN ('measured', 'unmeasurable')",
                )
                _add_constraint(
                    _USAGE_EVIDENCE_CONSTRAINT,
                    "(usage_source = 'measured' "
                    "AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL "
                    "AND cached_input_tokens IS NOT NULL AND cache_creation_tokens IS NOT NULL) "
                    "OR (usage_source = 'unmeasurable' "
                    "AND input_tokens IS NULL AND output_tokens IS NULL "
                    "AND cached_input_tokens IS NULL AND cache_creation_tokens IS NULL)",
                )
                _build_partitioned_attempt_index(connection)
            finally:
                op.execute(f"SELECT pg_advisory_unlock({_LOCK})")
        finally:
            connection.exec_driver_sql(
                "SELECT set_config('lock_timeout', %s, false)", (previous_timeout,)
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
        op.execute("SET lock_timeout = '5s'")
        try:
            _acquire_lock(connection)
            try:
                op.execute(
                    """
                    DO $$
                    DECLARE
                        has_unmeasurable BOOLEAN;
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.columns
                             WHERE table_schema = 'public'
                               AND table_name = 'token_usage_ledger'
                               AND column_name = 'usage_source'
                        ) THEN
                            EXECUTE
                                'SELECT EXISTS (SELECT 1 FROM public.token_usage_ledger '
                                'WHERE usage_source = ''unmeasurable'')'
                                INTO has_unmeasurable;
                            IF has_unmeasurable THEN
                                RAISE EXCEPTION
                                    'core_242 downgrade blocked: unmeasurable usage exists';
                            END IF;
                        END IF;
                    END
                    $$
                    """
                )
                op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
                op.execute(
                    f"ALTER TABLE public.token_usage_ledger "
                    f"DROP CONSTRAINT IF EXISTS {_USAGE_EVIDENCE_CONSTRAINT}"
                )
                op.execute(
                    f"ALTER TABLE public.token_usage_ledger "
                    f"DROP CONSTRAINT IF EXISTS {_USAGE_SOURCE_CONSTRAINT}"
                )
                op.execute(
                    """
                    ALTER TABLE public.token_usage_ledger
                        ALTER COLUMN input_tokens SET NOT NULL,
                        ALTER COLUMN output_tokens SET NOT NULL,
                        ALTER COLUMN cached_input_tokens SET NOT NULL,
                        ALTER COLUMN cache_creation_tokens SET NOT NULL,
                        DROP COLUMN IF EXISTS usage_source,
                        DROP COLUMN IF EXISTS attempt_id
                    """
                )
            finally:
                op.execute(f"SELECT pg_advisory_unlock({_LOCK})")
        finally:
            connection.exec_driver_sql(
                "SELECT set_config('lock_timeout', %s, false)", (previous_timeout,)
            )
