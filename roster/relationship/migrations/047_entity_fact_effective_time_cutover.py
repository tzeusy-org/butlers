"""Enable effective time by removing the legacy Relationship SPO index.

Revision ID: rel_047
Revises: rel_046

The migration login runs the same lock-and-refuse check on every database.
Stopping all target clients before G and starting only the carrying release
afterward is the owner's rollout procedure.
"""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError

from alembic import op

revision = "rel_047"
down_revision = "rel_046"
branch_labels = None
depends_on = None

_LEGACY = "uq_ef_spo_active"
_OCCURRENCE = "uq_ef_spo_occurrence_active"
_LEGACY_COLUMNS = "subject, predicate, object"
_OCCURRENCE_COLUMNS = (
    _LEGACY_COLUMNS
    + ", COALESCE(effective_period_id, '00000000-0000-0000-0000-000000000000'::uuid)"
)


def _index_definition(name: str, columns: str) -> str:
    return (
        f"CREATE UNIQUE INDEX {name} ON relationship.entity_facts USING btree "
        f"({columns}) WHERE (validity = 'active'::text)"
    )


def _guard():
    if op.get_context().as_sql:
        raise RuntimeError("offline_cutover_unsupported")
    connection = op.get_bind()
    try:
        connection.exec_driver_sql(
            "LOCK TABLE relationship.entity_facts IN ACCESS EXCLUSIVE MODE NOWAIT"
        )
    except Exception as exc:
        driver = getattr(exc, "orig", exc)
        sqlstate = getattr(driver, "sqlstate", None) or getattr(driver, "pgcode", None)
        if sqlstate == "55P03":
            raise RuntimeError("lock_unavailable") from None
        if sqlstate == "42501":
            raise RuntimeError("db_activity_unverifiable") from None
        if sqlstate in {"42P01", "42809"}:
            raise RuntimeError("cutover_index_state_invalid") from None
        raise

    try:
        unreadable, target_clients = _activity_counts(connection)
    except DBAPIError as exc:
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        if sqlstate == "42501":
            raise RuntimeError("db_activity_unverifiable") from None
        raise
    if unreadable:
        raise RuntimeError(f"db_activity_unverifiable unreadable_rows={unreadable}")
    if target_clients:
        raise RuntimeError(f"active_writer target_clients={target_clients}")
    return connection


def _activity_counts(connection) -> tuple[int, int]:
    visible = connection.exec_driver_sql(
        """
        SELECT current_user = session_user
           AND pg_catalog.pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')
        """
    ).scalar_one()
    if not visible:
        raise RuntimeError("db_activity_unverifiable")

    # A caller can already have read activity earlier in this transaction.
    # Clear its snapshot only after we own the lock, before taking the census.
    connection.exec_driver_sql("SELECT pg_catalog.pg_stat_clear_snapshot()")
    unreadable, target_clients = connection.exec_driver_sql(
        """
        WITH activity AS (
            SELECT CASE
                WHEN backend_type IN (
                    'autovacuum launcher', 'background writer', 'checkpointer',
                    'logical replication launcher', 'walwriter'
                )
                 AND datid IS NULL AND datname IS NULL
                 AND ((usesysid IS NULL AND usename IS NULL)
                      OR (backend_type = 'logical replication launcher'
                          AND usesysid IS NOT NULL AND usename IS NOT NULL))
                 AND client_addr IS NULL AND client_port IS NULL
                    THEN 'cluster'
                WHEN datid IS NULL OR datname IS NULL OR usesysid IS NULL OR usename IS NULL
                    THEN 'unreadable'
                WHEN backend_type IN ('client backend', 'walsender')
                 AND ((client_port = -1 AND client_addr IS NULL)
                      OR (client_port > 0 AND client_addr IS NOT NULL))
                    THEN CASE WHEN datname = pg_catalog.current_database()
                              THEN 'target_client' ELSE 'other_client' END
                WHEN backend_type IN ('autovacuum worker', 'logical replication worker',
                                      'parallel worker')
                 AND client_addr IS NULL AND client_port IS NULL
                    THEN 'internal'
                ELSE 'unreadable'
            END AS classification
            FROM pg_catalog.pg_stat_activity WHERE pid <> pg_catalog.pg_backend_pid()
        )
        SELECT count(*) FILTER (WHERE classification = 'unreadable'),
               count(*) FILTER (WHERE classification = 'target_client')
        FROM activity
        """
    ).one()
    return unreadable, target_clients


def _check_indexes(connection, *, legacy_expected: bool) -> None:
    # Check both names in the owning schema, including a same-named object on
    # the wrong table. An unusable index must never look like an absent index.
    indexes = dict(
        connection.exec_driver_sql(
            """
            SELECT idx.relname,
                   CASE WHEN i.indisvalid AND i.indisready AND i.indislive
                        THEN pg_catalog.pg_get_indexdef(idx.oid) END
              FROM pg_catalog.pg_class AS idx
              JOIN pg_catalog.pg_namespace AS ns ON ns.oid = idx.relnamespace
              LEFT JOIN pg_catalog.pg_index AS i
                ON i.indexrelid = idx.oid
               AND i.indrelid = 'relationship.entity_facts'::pg_catalog.regclass
             WHERE ns.nspname = 'relationship'
               AND idx.relname IN ('uq_ef_spo_active', 'uq_ef_spo_occurrence_active')
            """
        ).all()
    )
    expected = {_OCCURRENCE: _index_definition(_OCCURRENCE, _OCCURRENCE_COLUMNS)}
    if legacy_expected:
        expected[_LEGACY] = _index_definition(_LEGACY, _LEGACY_COLUMNS)
    if indexes != expected:
        raise RuntimeError("cutover_index_state_invalid")


def upgrade() -> None:
    connection = _guard()
    _check_indexes(connection, legacy_expected=True)
    connection.exec_driver_sql("DROP INDEX relationship.uq_ef_spo_active")


def downgrade() -> None:
    connection = _guard()
    _check_indexes(connection, legacy_expected=False)
    temporal_rows, duplicate_groups = connection.exec_driver_sql(
        """
        SELECT (
            SELECT count(*) FROM relationship.entity_facts
            WHERE effective_period_id IS NOT NULL
               OR effective_from IS NOT NULL OR effective_from_precision IS NOT NULL
               OR effective_to IS NOT NULL OR effective_to_precision IS NOT NULL
        ), (
            SELECT count(*) FROM (
                SELECT 1 FROM relationship.entity_facts
                WHERE validity = 'active'
                GROUP BY subject, predicate, object HAVING count(*) > 1
            ) AS duplicates
        )
        """
    ).one()
    if temporal_rows or duplicate_groups:
        raise RuntimeError(
            f"temporal_rollback_prohibited temporal_rows={temporal_rows} "
            f"duplicate_active_spo_groups={duplicate_groups}"
        )
    connection.exec_driver_sql(_index_definition(_LEGACY, _LEGACY_COLUMNS))
