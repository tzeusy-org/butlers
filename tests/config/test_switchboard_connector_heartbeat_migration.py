"""Integration tests for Switchboard connector heartbeat tables migration."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import (
    create_migration_db,
    get_column_info,
    index_exists,
    migration_db_name,
    table_exists,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _function_exists(db_url: str, function_name: str, *, schema_name: str = "public") -> bool:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_proc JOIN pg_namespace ON pg_proc.pronamespace = pg_namespace.oid WHERE pg_namespace.nspname = :s AND pg_proc.proname = :f)"
            ),
            {"s": schema_name, "f": function_name},
        )
        exists = result.scalar()
    engine.dispose()
    return bool(exists)


def _function_security_config(
    db_url: str, function_name: str, *, schema_name: str = "public"
) -> tuple[bool, list[str]]:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT p.prosecdef, COALESCE(p.proconfig, ARRAY[]::text[]) "
                "FROM pg_proc p "
                "JOIN pg_namespace n ON p.pronamespace = n.oid "
                "WHERE n.nspname = :s AND p.proname = :f"
            ),
            {"s": schema_name, "f": function_name},
        )
        row = result.one()
    engine.dispose()
    return bool(row[0]), list(row[1])


def _get_partition_count(db_url: str, parent_table: str, *, schema_name: str = "public") -> int:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT COUNT(*) FROM pg_inherits "
                "JOIN pg_class parent ON parent.oid = pg_inherits.inhparent "
                "JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace "
                "JOIN pg_class child ON child.oid = pg_inherits.inhrelid "
                "WHERE parent_ns.nspname = :s AND parent.relname = :t"
            ),
            {"s": schema_name, "t": parent_table},
        )
        count = result.scalar()
    engine.dispose()
    return int(count or 0)


def _run_core_and_switchboard(postgres_container) -> str:
    from butlers.migrations import run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard"))
    return db_url


def _run_schema_scoped_core_and_switchboard(postgres_container, schema_name: str) -> str:
    from butlers.migrations import run_migrations

    # Production layout: core lives in public, switchboard lives in its own
    # schema. Mirroring that here keeps the search_path/SECURITY DEFINER
    # behavior under test honest.
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard", schema=schema_name))
    return db_url


def _execute_as_role(db_url: str, role_name: str, sql: str, *, scalar: bool = False):
    quoted_role = _quote_ident(role_name)
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET ROLE {quoted_role}"))
            try:
                result = conn.execute(text(sql))
                if scalar:
                    return result.scalar()
                return None
            finally:
                conn.execute(text("RESET ROLE"))
    finally:
        engine.dispose()


def test_connector_heartbeat_tables_schema(postgres_container):
    """Verify both tables exist, have required columns and indexes."""
    db_url = _run_core_and_switchboard(postgres_container)

    assert table_exists(db_url, "connector_registry")
    assert table_exists(db_url, "connector_heartbeat_log")

    # connector_registry key columns
    for col in ("connector_type", "endpoint_identity", "state"):
        info = get_column_info(db_url, "connector_registry", col)
        assert info is not None and info["data_type"] == "text"
    for col in (
        "counter_messages_ingested",
        "counter_messages_failed",
        "counter_checkpoint_saves",
        "counter_dedupe_accepted",
    ):
        info = get_column_info(db_url, "connector_registry", col)
        assert info is not None and info["data_type"] == "bigint"

    # connector_heartbeat_log key columns
    assert get_column_info(db_url, "connector_heartbeat_log", "id")["data_type"] == "bigint"
    for col in (
        "counter_messages_ingested",
        "counter_messages_failed",
        "counter_checkpoint_saves",
        "counter_dedupe_accepted",
    ):
        info = get_column_info(db_url, "connector_heartbeat_log", col)
        assert info is not None and info["data_type"] == "bigint"

    # Indexes
    assert index_exists(db_url, "ix_connector_registry_last_heartbeat_at")
    assert index_exists(db_url, "ix_connector_heartbeat_log_connector_type_received_at")

    # Partition management functions
    assert _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition")
    assert _get_partition_count(db_url, "connector_heartbeat_log") >= 1


def _assert_search_path_is_minimal(config: list[str], expected_schema: str) -> None:
    """Assert SECURITY DEFINER search_path is pinned to the expected schema only.

    Guards against regressions that would re-expose ``public`` (or any other
    arbitrary schema) inside a ``SECURITY DEFINER`` function.
    """
    search_path_settings = [s for s in config if s.startswith("search_path=")]
    assert len(search_path_settings) == 1, (
        f"Expected exactly one search_path setting, got: {search_path_settings}"
    )
    setting = search_path_settings[0]
    value = setting.split("=", 1)[1]
    parts = [p.strip().strip('"') for p in value.split(",")]
    # Allow ``pg_temp`` (PostgreSQL appends it implicitly anyway) and the
    # function's home schema. Reject anything else — especially ``public``
    # and ``$user``.
    allowed = {expected_schema, "pg_temp"}
    assert set(parts) <= allowed, (
        f"search_path leaks unexpected schemas: {parts} (allowed: {allowed})"
    )
    assert expected_schema in parts, f"search_path missing target schema {expected_schema}: {parts}"


def test_runtime_role_can_ensure_connector_heartbeat_partition(postgres_container):
    """Runtime role can create heartbeat partitions without parent ownership."""
    db_url = _run_schema_scoped_core_and_switchboard(postgres_container, "switchboard")

    for function_name in (
        "switchboard_connector_heartbeat_log_ensure_partition",
        "switchboard_connector_heartbeat_log_drop_expired_partitions",
    ):
        assert _function_exists(db_url, function_name, schema_name="switchboard")
        security_definer, config = _function_security_config(
            db_url, function_name, schema_name="switchboard"
        )
        assert security_definer, f"{function_name} must be SECURITY DEFINER"
        _assert_search_path_is_minimal(config, expected_schema="switchboard")

    partition_name = _execute_as_role(
        db_url,
        "butler_switchboard_rw",
        "SELECT switchboard.switchboard_connector_heartbeat_log_ensure_partition("
        "'2027-08-15T00:00:00+00:00'::timestamptz"
        ")",
        scalar=True,
    )

    assert partition_name == "connector_heartbeat_log_p202708"
    assert _get_partition_count(db_url, "connector_heartbeat_log", schema_name="switchboard") >= 3

    # Cleanup is also a runtime-role responsibility once the maintenance
    # cron is wired up; verify the role can call it without owning the
    # parent table.
    _execute_as_role(
        db_url,
        "butler_switchboard_rw",
        "SELECT switchboard.switchboard_connector_heartbeat_log_drop_expired_partitions("
        "INTERVAL '7 days', '2027-08-15T00:00:00+00:00'::timestamptz"
        ")",
        scalar=True,
    )


def test_downgrade_drops_all_objects(postgres_container):
    """Verify downgrade cleanly drops all tables and functions."""
    from butlers.migrations import _build_alembic_config

    db_url = _run_core_and_switchboard(postgres_container)

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_001")

    assert not table_exists(db_url, "connector_registry")
    assert not table_exists(db_url, "connector_heartbeat_log")
    assert not _function_exists(db_url, "switchboard_connector_heartbeat_log_ensure_partition")
