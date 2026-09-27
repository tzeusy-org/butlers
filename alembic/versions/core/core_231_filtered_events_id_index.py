"""Index filtered event IDs without blocking writes during partition scans.

Revision ID: core_231
Revises: core_230
Create Date: 2026-09-12 00:00:00.000000

The existing received_at-leading primary key cannot efficiently serve Timeline
detail and replay lookups by ID alone. Build each leaf index concurrently, then
attach it to an initially empty partitioned index. PostgreSQL marks the parent
valid after every partition is attached and indexes future partitions itself.

Autocommit makes completed steps durable if interrupted. A retry repairs invalid
standalone indexes left by interrupted concurrent builds and reuses completed
ones. A session advisory lock serializes repeated per-butler core migrations;
short DDL lock waits fail for retry rather than stalling ingestion indefinitely.
Upgrade chains from older revisions should still be serialized operationally:
older migrations do not participate in this lock and can conflict with CIC.
"""

from __future__ import annotations

from time import monotonic, sleep

from alembic import op

revision = "core_231"
down_revision = "core_230"
branch_labels = None
depends_on = None

_INDEX = "connectors.ix_filtered_events_id"
_LOCK = 231_20260912


def _quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _acquire_lock(connection) -> None:
    # A blocking SELECT pg_advisory_lock retains a snapshot that CIC must wait
    # for, deadlocking the builder and waiter. Poll in autocommit so each failed
    # attempt releases its snapshot before waiting outside PostgreSQL.
    deadline = monotonic() + 5
    while not connection.exec_driver_sql(f"SELECT pg_try_advisory_lock({_LOCK})").scalar_one():
        if monotonic() >= deadline:
            raise RuntimeError("filtered event index migration is busy; retry this upgrade")
        sleep(0.1)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
        op.execute("SET lock_timeout = '5s'")
        try:
            _acquire_lock(connection)
            try:
                op.execute(
                    "CREATE INDEX IF NOT EXISTS ix_filtered_events_id "
                    "ON ONLY connectors.filtered_events (id)"
                )
                partitions = connection.exec_driver_sql(
                    "SELECT child.oid, ns.nspname, child.relname "
                    "FROM pg_inherits inheritance "
                    "JOIN pg_class child ON child.oid = inheritance.inhrelid "
                    "JOIN pg_namespace ns ON ns.oid = child.relnamespace "
                    "WHERE inheritance.inhparent = 'connectors.filtered_events'::regclass "
                    "ORDER BY child.oid"
                ).fetchall()
                for oid, schema, table in partitions:
                    # Already attached indexes include those PostgreSQL creates
                    # automatically when a new partition appears during upgrade.
                    attached = connection.exec_driver_sql(
                        "SELECT 1 FROM pg_inherits inheritance "
                        "JOIN pg_index idx ON idx.indexrelid = inheritance.inhrelid "
                        f"WHERE inheritance.inhparent = '{_INDEX}'::regclass "
                        f"AND idx.indrelid = {int(oid)} AND idx.indisvalid"
                    ).scalar()
                    if attached:
                        continue
                    name = f"ix_filtered_events_id_{oid}"
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
                        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_quote(name)} "
                        f"ON {_quote(schema)}.{_quote(table)} (id)"
                    )
                    op.execute(f"ALTER INDEX {_INDEX} ATTACH PARTITION {qualified}")
            finally:
                op.execute(f"SELECT pg_advisory_unlock({_LOCK})")
        finally:
            connection.exec_driver_sql(
                "SELECT set_config('lock_timeout', %s, false)", (previous_timeout,)
            )


def downgrade() -> None:
    # PostgreSQL cannot drop a partitioned index CONCURRENTLY. This is a short
    # metadata operation; the same bounded lock wait protects active ingestion.
    with op.get_context().autocommit_block():
        connection = op.get_bind()
        previous_timeout = connection.exec_driver_sql("SHOW lock_timeout").scalar_one()
        op.execute("SET lock_timeout = '5s'")
        try:
            _acquire_lock(connection)
            try:
                op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
            finally:
                op.execute(f"SELECT pg_advisory_unlock({_LOCK})")
        finally:
            connection.exec_driver_sql(
                "SELECT set_config('lock_timeout', %s, false)", (previous_timeout,)
            )
