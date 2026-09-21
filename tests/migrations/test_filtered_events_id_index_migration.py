"""Real partition-index lifecycle and bounded lookup work on synthetic events."""

from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.db]


def _load_migration(revision, filename):
    path = Path(__file__).resolve().parents[2] / f"alembic/versions/core/{filename}"
    spec = importlib.util.spec_from_file_location(revision, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rerun(connection, revision, filename):
    migration = _load_migration(revision, filename)
    context = MigrationContext.configure(connection)
    with context.begin_transaction(), patch.object(migration, "op", Operations(context)):
        migration.upgrade()


@pytest.mark.parametrize(
    "revision, previous, filename, index_name",
    [
        ("core_231", "core_230", "core_231_filtered_events_id_index.py", "ix_filtered_events_id"),
        (
            "core_245",
            "core_244",
            "core_245_filtered_events_status_received_index.py",
            "ix_filtered_events_status_received",
        ),
    ],
    ids=["id", "status-received"],
)
def test_filtered_index_lifecycle_and_lookup_work(
    postgres_container, revision, previous, filename, index_name
):
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, f"core@{previous}")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            for month in range(1, 7):
                connection.exec_driver_sql(
                    "SELECT connectors.connectors_filtered_events_ensure_partition"
                    f"('2040-{month:02d}-01'::timestamptz)"
                )
            connection.exec_driver_sql(
                "INSERT INTO connectors.filtered_events "
                "(id, received_at, connector_type, endpoint_identity, external_message_id, "
                "source_channel, sender_identity, filter_reason, full_payload, status) "
                "SELECT md5(g::text)::uuid, "
                "'2040-01-01'::timestamptz + ((g - 1) / 20000) * interval '1 month' "
                "+ (g %% 20000) * interval '1 second', "
                "'synthetic', 'synthetic', g::text, 'synthetic', 'synthetic', "
                "'synthetic', '{}'::jsonb, CASE WHEN g = 12345 THEN 'error' "
                "ELSE 'filtered' END FROM generate_series(1, 120000) g"
            )
            connection.exec_driver_sql("ANALYZE connectors.filtered_events")

        lookup = (
            "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
            "SELECT id, filter_reason, full_payload FROM connectors.filtered_events "
            "WHERE id = ANY(ARRAY[md5('12345')::uuid, md5('missing')::uuid])"
        )
        if revision == "core_245":
            lookup = (
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                "SELECT date_trunc('hour', received_at), status, count(*) "
                "FROM connectors.filtered_events "
                "WHERE status = ANY(ARRAY['error', 'replay_pending', 'replay_complete']) "
                "AND received_at >= '2040-01-01' AND received_at < '2040-07-01' "
                "GROUP BY 1, 2"
            )
        with engine.connect() as connection:
            before = connection.exec_driver_sql(lookup).scalar_one()[0]
        command.upgrade(config, f"core@{revision}")
        with engine.connect() as connection:
            after = connection.exec_driver_sql(lookup).scalar_one()[0]
            indexes = (
                connection.exec_driver_sql(
                    "SELECT indexrelid::regclass::text FROM pg_index "
                    "WHERE indexrelid IN (SELECT inhrelid FROM pg_inherits "
                    f"WHERE inhparent = 'connectors.{index_name}'::regclass) "
                    "AND indisvalid"
                )
                .scalars()
                .all()
            )
            assert len(indexes) >= 7
            assert connection.exec_driver_sql(
                "SELECT indisvalid FROM pg_index "
                f"WHERE indexrelid = 'connectors.{index_name}'::regclass"
            ).scalar_one()
            connection.commit()
            connection.exec_driver_sql("CREATE SCHEMA retry_schema")
            connection.exec_driver_sql("SET search_path TO retry_schema, public")
            connection.exec_driver_sql("SET lock_timeout = '2s'")
            connection.commit()
            _rerun(connection, revision, filename)
            assert connection.exec_driver_sql("SHOW lock_timeout").scalar_one() == "2s"
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = 'retry_schema'"
                ).scalar_one()
                == 0
            )
            connection.commit()

        def blocks(plan):
            return plan["Plan"]["Shared Hit Blocks"] + plan["Plan"]["Shared Read Blocks"]

        # Assert reduced work, not wall time: host load should not make this flaky.
        assert after["Plan"]["Actual Rows"] == before["Plan"]["Actual Rows"] == 1
        assert blocks(after) < blocks(before) / 4
        assert any(index.split(".")[-1] in str(after) for index in indexes)
        print(
            f"synthetic filtered_events {index_name}: rows=120000; "
            f"before_ms={before['Execution Time']}; after_ms={after['Execution Time']}; "
            f"before_blocks={blocks(before)}; after_blocks={blocks(after)}"
        )

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "SELECT connectors.connectors_filtered_events_ensure_partition"
                "('2041-01-01'::timestamptz)"
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM pg_index idx JOIN pg_inherits inheritance "
                    "ON inheritance.inhrelid = idx.indexrelid "
                    f"WHERE inheritance.inhparent = 'connectors.{index_name}'::regclass "
                    "AND idx.indrelid = 'connectors.filtered_events_204101'::regclass "
                    "AND idx.indisvalid"
                ).scalar_one()
                == 1
            )

        command.downgrade(config, f"core@{previous}")
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            assert (
                connection.exec_driver_sql(
                    f"SELECT to_regclass('connectors.{index_name}')"
                ).scalar_one()
                is None
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT count(*) FROM connectors.filtered_events"
                ).scalar_one()
                == 120000
            )
            oid = connection.exec_driver_sql(
                "SELECT 'connectors.filtered_events_204001'::regclass::oid"
            ).scalar_one()
            # A failed unique CIC leaves the same invalid-index state as an
            # interrupted normal build. The migration must replace, not trust it.
            connection.exec_driver_sql(
                "INSERT INTO connectors.filtered_events "
                "(id, received_at, connector_type, endpoint_identity, external_message_id, "
                "source_channel, sender_identity, filter_reason, full_payload) SELECT id, "
                "received_at + interval '1 day', connector_type, endpoint_identity, "
                "external_message_id, source_channel, sender_identity, "
                "filter_reason, full_payload FROM connectors.filtered_events "
                "WHERE id = md5('12345')::uuid"
            )
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql(
                    f"CREATE UNIQUE INDEX CONCURRENTLY {index_name}_{oid} "
                    "ON connectors.filtered_events_204001 (id)"
                )
            assert (
                connection.exec_driver_sql(
                    "SELECT indisvalid FROM pg_index WHERE indexrelid = "
                    f"'connectors.{index_name}_{oid}'::regclass"
                ).scalar_one()
                is False
            )
        ready = Barrier(2)

        def concurrent_rerun(schema):
            with engine.connect() as connection:
                connection.exec_driver_sql(f"SET search_path TO {schema}, public")
                connection.commit()
                ready.wait(timeout=10)
                _rerun(connection, revision, filename)

        # The core revision is applied independently by each butler schema, but
        # both runs operate on the same physical partitioned table.
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(concurrent_rerun, s) for s in ("public", "retry_schema")]
            for future in futures:
                future.result(timeout=15)
        command.upgrade(config, f"core@{revision}")
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT indisvalid AND NOT indisunique FROM pg_index WHERE indexrelid = "
                f"'connectors.{index_name}_{oid}'::regclass"
            ).scalar_one()
    finally:
        engine.dispose()
