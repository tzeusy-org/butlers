"""Schema matrix verification for one-db migration runs."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from butlers.config import ButlerConfig, load_config
from butlers.migrations import ROSTER_DIR, has_butler_chain, run_migrations

# Skip all tests if Docker is not available.
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

CORE_TABLES = {
    "state",
    "scheduled_tasks",
    "sessions",
    "route_inbox",
    "butler_secrets",
}

CHAIN_TABLES: dict[str, set[str]] = {
    "core": CORE_TABLES,
    "general": {"collections", "entities"},
    "health": {
        "conditions",
        "meals",
        "measurements",
        "medication_doses",
        "medications",
        "research",
        "symptoms",
    },
    "messenger": {
        "delivery_requests",
        "delivery_attempts",
        "delivery_receipts",
        "delivery_dead_letter",
    },
    "relationship": {
        "activity_feed",
        "addresses",
        "contact_info",
        "contact_labels",
        "contacts",
        "gifts",
        "group_members",
        "groups",
        "important_dates",
        "interactions",
        "labels",
        "life_event_categories",
        "life_event_types",
        "life_events",
        "loans",
        "notes",
        "quick_facts",
        "relationship_types",
        "relationships",
        "reminders",
        "tasks",
    },
    "switchboard": {
        "butler_registry",
        "butler_registry_eligibility_log",
        "connector_fanout_daily",
        "connector_heartbeat_log",
        "connector_registry",
        "connector_stats_daily",
        "connector_stats_hourly",
        "dashboard_audit_log",
        "dead_letter_queue",
        "extraction_log",
        "extraction_queue",
        "fanout_execution_log",
        "message_inbox",
        "notifications",
        "operator_audit_log",
        "routing_log",
    },
    "approvals": {"approval_events", "approval_rules", "pending_actions"},
    "mailbox": {"mailbox"},
    "memory": {"episodes", "facts", "rules", "memory_links"},
}


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container with pgvector support."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as postgres:
        yield postgres


def _create_db(postgres_container, db_name: str) -> str:
    """Create a fresh database and return its SQLAlchemy URL."""
    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        safe = db_name.replace('"', '""')
        conn.execute(text(f'CREATE DATABASE "{safe}"'))
    engine.dispose()

    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _load_one_db_roster_configs() -> list[ButlerConfig]:
    configs: list[ButlerConfig] = []
    for entry in sorted(Path(ROSTER_DIR).iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / "butler.toml").exists():
            continue
        cfg = load_config(entry)
        if cfg.db_name == "butlers":
            assert cfg.db_schema is not None, f"{cfg.name}: expected schema in one-db config"
            configs.append(cfg)
    assert configs, "Expected at least one one-db roster config"
    return configs


def _enabled_module_chains(config: ButlerConfig) -> tuple[str, ...]:
    chains: list[str] = []
    for module_name in sorted(config.modules.keys()):
        if module_name in CHAIN_TABLES:
            chains.append(module_name)
    return tuple(chains)


def _expected_schema_matrix(
    configs: list[ButlerConfig],
) -> tuple[dict[str, set[str]], dict[str, tuple[str, ...]]]:
    expected_by_schema: dict[str, set[str]] = {}
    chain_by_schema: dict[str, tuple[str, ...]] = {}

    for config in configs:
        schema = config.db_schema
        assert schema is not None
        chains = ["core"]
        if has_butler_chain(config.name):
            chains.append(config.name)
        chains.extend(_enabled_module_chains(config))

        expected_tables: set[str] = {"alembic_version"}
        for chain in chains:
            chain_tables = CHAIN_TABLES.get(chain)
            assert chain_tables is not None, (
                f"Missing CHAIN_TABLES entry for chain={chain!r} schema={schema!r}"
            )
            expected_tables.update(chain_tables)

        expected_by_schema[schema] = expected_tables
        chain_by_schema[schema] = tuple(chains)

    return expected_by_schema, chain_by_schema


def _fetch_tables_by_schema(db_url: str, schemas: set[str]) -> dict[str, set[str]]:
    if not schemas:
        return {}

    sql_schemas = ", ".join(f"'{schema}'" for schema in sorted(schemas))
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema IN ({sql_schemas})
                      AND table_type = 'BASE TABLE'
                    """
                )
            )
            by_schema: dict[str, set[str]] = {schema: set() for schema in schemas}
            for table_schema, table_name in rows:
                by_schema[str(table_schema)].add(str(table_name))
            return by_schema
    finally:
        engine.dispose()


def test_one_db_schema_table_matrix_for_core_and_enabled_modules(postgres_container):
    """Enabled module + core table sets should exist in every configured one-db schema."""
    db_url = _create_db(postgres_container, _unique_db_name())
    configs = _load_one_db_roster_configs()

    for config in configs:
        schema = config.db_schema
        assert schema is not None
        asyncio.run(run_migrations(db_url, chain="core", schema=schema))
        if has_butler_chain(config.name):
            asyncio.run(run_migrations(db_url, chain=config.name, schema=schema))
        for module_chain in _enabled_module_chains(config):
            asyncio.run(run_migrations(db_url, chain=module_chain, schema=schema))

    expected_by_schema, chain_by_schema = _expected_schema_matrix(configs)
    actual_by_schema = _fetch_tables_by_schema(db_url, set(expected_by_schema.keys()))

    diagnostics: list[str] = []
    for schema in sorted(expected_by_schema):
        expected_tables = expected_by_schema[schema]
        actual_tables = actual_by_schema.get(schema, set())
        missing_tables = sorted(expected_tables - actual_tables)
        if not missing_tables:
            continue

        diagnostics.append(
            f"schema={schema} chains={','.join(chain_by_schema[schema])} "
            f"missing={missing_tables} present={sorted(actual_tables)}"
        )

    assert not diagnostics, (
        "Schema/table migration matrix verification failed. "
        "Each schema must contain all expected core + enabled module tables.\n"
        + "\n".join(diagnostics)
    )
