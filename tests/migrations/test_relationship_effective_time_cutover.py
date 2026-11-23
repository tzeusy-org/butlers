"""Exercise the ordinary Relationship cutover against PostgreSQL and Alembic."""

from __future__ import annotations

import asyncio
from io import StringIO
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.pool import NullPool

from alembic import command
from butlers.db import register_jsonb_codec
from butlers.migrations import _build_alembic_config, check_chain_at_head, run_migrations
from butlers.testing.migration import (
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from butlers.tools.relationship.relationship_assert_fact import (
    AssertOutcome,
    relationship_assert_fact,
)

pytestmark = pytest.mark.integration

_PREDECESSOR = "rel_046"
_INDEXES = """
    SELECT indexname, indexdef FROM pg_catalog.pg_indexes
    WHERE schemaname = 'relationship' AND tablename = 'entity_facts' ORDER BY indexname
"""
_LEGACY = "uq_ef_spo_active"
_OCCURRENCE = "uq_ef_spo_occurrence_active"


@pytest.fixture
def cutover(postgres_container):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container,
        name,
        ["core", "relationship"],
        schemas={"relationship": "relationship"},
        revisions={"relationship": _PREDECESSOR},
    )
    migration = create_engine(url, poolclass=NullPool)
    admin = create_engine(migration_bootstrap_db_url(postgres_container, name), poolclass=NullPool)
    with migration.begin() as conn:
        subject = conn.exec_driver_sql(
            "INSERT INTO public.entities(canonical_name, entity_type) "
            "VALUES ('Synthetic cutover subject', 'person') RETURNING id"
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO relationship.entity_facts(subject, predicate, object, object_kind, src) "
                "VALUES (:subject, 'has-email', 'existing@example.test', 'literal', 'relationship')"
            ),
            {"subject": subject},
        )
    try:
        yield url, migration, admin
    finally:
        migration.dispose()
        admin.dispose()


def _snapshot(engine):
    with engine.connect() as conn:
        return (
            conn.exec_driver_sql(
                "SELECT version_num FROM relationship.alembic_version"
            ).scalar_one(),
            dict(conn.exec_driver_sql(_INDEXES).all()),
            conn.exec_driver_sql("SELECT * FROM relationship.entity_facts ORDER BY id").all(),
        )


def _upgrade(url):
    asyncio.run(run_migrations(url, chain="relationship", schema="relationship"))


def _config(url):
    return _build_alembic_config(url, ["relationship"], target_schema="relationship")


def test_different_role_client_refuses_without_changes(cutover):
    url, migration, admin = cutover
    before = _snapshot(migration)
    with admin.connect() as peer:
        assert peer.exec_driver_sql("SELECT current_user").scalar_one() != migration.url.username
        with pytest.raises(RuntimeError, match=r"^active_writer target_clients=1$"):
            _upgrade(url)
    assert _snapshot(migration) == before
    _upgrade(url)
    check_chain_at_head(url, "relationship", schema="relationship")


def test_missing_visibility_refuses_while_different_role_is_connected(cutover):
    url, migration, admin = cutover
    before = _snapshot(migration)
    role = admin.dialect.identifier_preparer.quote_identifier(migration.url.username)
    with admin.begin() as conn:
        conn.exec_driver_sql(f"REVOKE pg_read_all_stats FROM {role}")
    try:
        with migration.connect() as conn:
            assert not conn.exec_driver_sql(
                "SELECT pg_catalog.pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')"
            ).scalar_one()
        with admin.connect() as peer:
            assert (
                peer.exec_driver_sql("SELECT current_user").scalar_one() != migration.url.username
            )
            with pytest.raises(RuntimeError, match=r"^db_activity_unverifiable$"):
                _upgrade(url)
        assert _snapshot(migration) == before
    finally:
        with admin.begin() as conn:
            conn.exec_driver_sql(f"GRANT pg_read_all_stats TO {role} WITH INHERIT TRUE")


def test_activity_read_permission_denial_refuses_without_sql(cutover):
    url, migration, admin = cutover
    before = _snapshot(migration)
    with admin.begin() as conn:
        conn.exec_driver_sql("REVOKE SELECT ON pg_catalog.pg_stat_activity FROM PUBLIC")
    try:
        with migration.connect() as conn:
            assert conn.exec_driver_sql(
                "SELECT pg_catalog.pg_has_role(current_user, 'pg_read_all_stats', 'USAGE')"
            ).scalar_one()
            with pytest.raises(ProgrammingError) as denied:
                conn.exec_driver_sql("SELECT 1 FROM pg_catalog.pg_stat_activity")
            assert denied.value.orig.pgcode == "42501"
        with pytest.raises(RuntimeError, match=r"^db_activity_unverifiable$") as refusal:
            _upgrade(url)
        assert refusal.value.__suppress_context__
        assert _snapshot(migration) == before
    finally:
        with admin.begin() as conn:
            conn.exec_driver_sql("GRANT SELECT ON pg_catalog.pg_stat_activity TO PUBLIC")
    _upgrade(url)
    check_chain_at_head(url, "relationship", schema="relationship")


def test_nowait_lock_refuses_before_other_client_check(cutover):
    url, migration, admin = cutover
    before = _snapshot(migration)
    with admin.begin() as conn:
        conn.exec_driver_sql("LOCK TABLE relationship.entity_facts IN ACCESS SHARE MODE")
        with pytest.raises(RuntimeError, match=r"^lock_unavailable$") as error:
            _upgrade(url)
        assert error.value.__suppress_context__
    assert _snapshot(migration) == before


def test_quiescent_target_allows_other_database_and_reversible_cutover(cutover, postgres_container):
    url, migration, _ = cutover
    before = _snapshot(migration)
    with migration.connect() as conn:
        assert (
            conn.exec_driver_sql(
                """
            SELECT count(*) FROM pg_catalog.pg_stat_activity
            WHERE backend_type = 'checkpointer' AND datname IS NULL AND usename IS NULL
              AND client_addr IS NULL AND client_port IS NULL
            """
            ).scalar_one()
            == 1
        )
    cluster = create_engine(postgres_container.get_connection_url(), poolclass=NullPool)
    with cluster.connect() as other_database:
        assert (
            other_database.exec_driver_sql("SELECT current_database()").scalar_one()
            != make_url(url).database
        )
        _upgrade(url)
        check_chain_at_head(url, "relationship", schema="relationship")
        _, indexes, facts = _snapshot(migration)
        assert _OCCURRENCE in indexes
        assert indexes == {
            name: definition for name, definition in before[1].items() if name != _LEGACY
        }
        assert facts == before[2]
        command.downgrade(_config(url), _PREDECESSOR)
        assert _snapshot(migration) == before
        _upgrade(url)
        check_chain_at_head(url, "relationship", schema="relationship")


def test_cutover_enables_real_writer_to_keep_default_and_explicit_occurrences(cutover):
    url, migration, _ = cutover
    _upgrade(url)
    period = uuid4()
    with migration.connect() as conn:
        legacy_id, subject = conn.exec_driver_sql(
            "SELECT id, subject FROM relationship.entity_facts"
        ).one()

    async def assert_occurrence():
        async with asyncpg.create_pool(
            url,
            min_size=1,
            max_size=2,
            init=register_jsonb_codec,
            server_settings={"search_path": "relationship,public"},
        ) as pool:
            return await relationship_assert_fact(
                pool,
                subject,
                "has-email",
                "existing@example.test",
                src="relationship",
                effective_period_id=period,
                effective_from="2026",
                effective_from_precision="year",
            )

    assertion = asyncio.run(assert_occurrence())
    assert assertion.outcome == AssertOutcome.inserted
    with migration.connect() as conn:
        occurrence_ids = dict(
            conn.exec_driver_sql(
                "SELECT id, effective_period_id FROM relationship.entity_facts WHERE validity='active'"
            ).all()
        )
    assert occurrence_ids == {legacy_id: None, assertion.fact_id: period}


@pytest.mark.parametrize(
    "projection",
    [
        "'unknown background type', datid, datname, usesysid, usename, client_addr, client_port",
        "NULL, datid, datname, usesysid, usename, client_addr, client_port",
        "'client backend', NULL, NULL, usesysid, usename, client_addr, client_port",
        "'checkpointer', NULL, NULL, NULL, NULL, client_addr, client_port",
        "'autovacuum worker', datid, datname, NULL, NULL, NULL, NULL",
    ],
    ids=[
        "unknown",
        "masked",
        "missing-database",
        "background-type-with-client-socket",
        "database-worker-without-user",
    ],
)
def test_unreadable_activity_refuses_with_stable_counts(cutover, projection):
    url, migration, _ = cutover
    before = _snapshot(migration)

    def add_unreadable_row(_conn, _cursor, statement, parameters, _context, _executemany):
        source = "FROM pg_catalog.pg_stat_activity WHERE pid <> pg_catalog.pg_backend_pid()"
        if source in statement:
            # The real query sees normal server rows plus one malformed copy of
            # the current session. PostgreSQL evaluates the production predicate.
            augmented = f"""
                FROM (
                    SELECT backend_type, datid, datname, usesysid, usename, client_addr, client_port
                    FROM pg_catalog.pg_stat_activity WHERE pid <> pg_catalog.pg_backend_pid()
                    UNION ALL
                    SELECT {projection} FROM pg_catalog.pg_stat_activity
                    WHERE pid = pg_catalog.pg_backend_pid()
                ) AS activity_rows
            """  # noqa: S608
            statement = statement.replace(source, augmented)
        return statement, parameters

    event.listen(Engine, "before_cursor_execute", add_unreadable_row, retval=True)
    try:
        with pytest.raises(RuntimeError, match=r"^db_activity_unverifiable unreadable_rows=1$"):
            _upgrade(url)
    finally:
        event.remove(Engine, "before_cursor_execute", add_unreadable_row)
    assert _snapshot(migration) == before
    _upgrade(url)


@pytest.mark.parametrize("drift", ["changed", "invalid"])
def test_index_drift_refuses_without_changes(cutover, drift):
    url, migration, admin = cutover
    with admin.begin() as conn:
        if drift == "changed":
            conn.exec_driver_sql("DROP INDEX relationship.uq_ef_spo_active")
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_ef_spo_active ON relationship.entity_facts "
                "(subject, object, predicate) WHERE validity = 'active'"
            )
        else:
            # Mark the exact occurrence definition unusable in this private
            # catalog. Checking only its name or definition would now pass.
            conn.exec_driver_sql(
                "UPDATE pg_catalog.pg_index SET indisvalid = false "
                "WHERE indexrelid = 'relationship.uq_ef_spo_occurrence_active'::regclass"
            )
    before = _snapshot(migration)
    with pytest.raises(RuntimeError, match=r"^cutover_index_state_invalid$"):
        _upgrade(url)
    assert _snapshot(migration) == before


def test_late_client_waits_for_commit(cutover):
    url, migration, admin = cutover
    observed = []

    def late_client(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement != "DROP INDEX relationship.uq_ef_spo_active":
            return
        with admin.connect() as peer:
            peer.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
            with pytest.raises(OperationalError) as error:
                peer.exec_driver_sql("SELECT id FROM relationship.entity_facts")
            assert error.value.orig.pgcode == "55P03"
        observed.append(True)

    event.listen(Engine, "after_cursor_execute", late_client)
    try:
        _upgrade(url)
    finally:
        event.remove(Engine, "after_cursor_execute", late_client)
    assert observed == [True]
    with migration.connect() as conn:
        assert len(conn.exec_driver_sql("SELECT id FROM relationship.entity_facts").all()) == 1
    check_chain_at_head(url, "relationship", schema="relationship")


@pytest.mark.parametrize(
    "duplicate", [False, True], ids=["non-active-temporal-row", "duplicate-spo"]
)
def test_rollback_refuses_temporal_state_without_changes(cutover, duplicate):
    url, migration, _ = cutover
    _upgrade(url)
    with migration.begin() as conn:
        if duplicate:
            conn.exec_driver_sql(
                """
                INSERT INTO relationship.entity_facts(
                    subject, predicate, object, object_kind, src, effective_period_id
                )
                SELECT subject, predicate, object, object_kind, src, gen_random_uuid()
                FROM relationship.entity_facts
                """
            )
        else:
            conn.exec_driver_sql(
                "UPDATE relationship.entity_facts SET validity = 'retracted', "
                "effective_from_precision = 'unbounded'"
            )
    before = _snapshot(migration)
    with pytest.raises(
        RuntimeError,
        match=rf"^temporal_rollback_prohibited temporal_rows=1 duplicate_active_spo_groups={int(duplicate)}$",
    ):
        command.downgrade(_config(url), _PREDECESSOR)
    assert _snapshot(migration) == before


def test_offline_cutover_emits_no_index_ddl(cutover):
    url, migration, _ = cutover
    cfg = _config(url)
    sql = StringIO()
    cfg.output_buffer = sql
    before = _snapshot(migration)
    with pytest.raises(RuntimeError, match=r"^offline_cutover_unsupported$"):
        command.upgrade(cfg, f"{_PREDECESSOR}:relationship@head", sql=True)
    assert "DROP INDEX" not in sql.getvalue()
    assert _snapshot(migration) == before
