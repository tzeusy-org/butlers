"""core_258: per-schema personal baseline tables (bu-q7vx1q.15).

Real PostgreSQL: the tables are created in the butler's own schema (never in
``public``), a replay in a second schema is independent, the CHECK constraints
refuse a ``ready`` band with no center and a second open episode, and the
runtime-role grant stays inside the owning schema.
"""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]


def _scalar(db_url: str, statement: str):
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            return conn.execute(text(statement)).scalar()
    finally:
        engine.dispose()


def test_core_258_creates_per_schema_tables_and_round_trips(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    command.upgrade(config, "core_257")
    command.upgrade(config, "core_258")
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))
    asyncio.run(run_migrations(db_url, chain="core", schema="general"))

    for table in ("metric_baselines", "metric_deviation_episodes"):
        assert _scalar(db_url, f"SELECT to_regclass('health.{table}') IS NOT NULL") is True
        assert _scalar(db_url, f"SELECT to_regclass('general.{table}') IS NOT NULL") is True

    # Another butler's core run is its own copy, not a shared table.
    _scalar(
        db_url,
        "INSERT INTO health.metric_deviation_episodes"
        " (metric_key, opened_on, direction, peak_mad) VALUES ('resting_hr', now(), 'above', 3)"
        " RETURNING 1",
    )
    assert _scalar(db_url, "SELECT count(*) FROM general.metric_deviation_episodes") == 0

    with pytest.raises(IntegrityError):
        _scalar(
            db_url,
            "INSERT INTO health.metric_baselines (metric_key, bucket_value, method_version,"
            " status, measurability, unit, k_mad, n_observed, n_expected, coverage,"
            " input_digest, computed_at) VALUES ('m', 'all', 'v', 'ready', 'measurable', 'u',"
            " 2.6, 1, 2, 0.5, 'd', now()) RETURNING 1",
        )
    with pytest.raises(IntegrityError):
        _scalar(
            db_url,
            "INSERT INTO health.metric_deviation_episodes"
            " (metric_key, opened_on, direction, peak_mad)"
            " VALUES ('resting_hr', now() + interval '9 days', 'above', 3) RETURNING 1",
        )

    # The alembic downgrade acts on the unscoped (public) run only.
    command.downgrade(config, "core_257")
    assert _scalar(db_url, "SELECT to_regclass('public.metric_baselines') IS NULL") is True
    command.upgrade(config, "core_258")
    assert _scalar(db_url, "SELECT to_regclass('public.metric_baselines') IS NOT NULL") is True


def test_core_258_grant_stays_inside_the_owning_schema(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    command.upgrade(config, "core_258")
    asyncio.run(run_migrations(db_url, chain="core", schema="health"))

    roles = _scalar(
        db_url,
        "SELECT count(*) FROM pg_roles WHERE rolname IN ('butler_health_rw', 'butler_finance_rw')",
    )
    if roles != 2:
        pytest.skip("runtime roles are not provisioned in this database")
    privilege = "SELECT has_table_privilege('{role}', 'health.metric_baselines', 'SELECT')"
    assert _scalar(db_url, privilege.format(role="butler_health_rw")) is True
    assert _scalar(db_url, privilege.format(role="butler_finance_rw")) is False


def test_core_258_bootstrap_replay_leaves_tables_visible_to_the_migration_login(
    postgres_container,
) -> None:
    """The smoke test's shape: ordinary upgrade, managed-bootstrap down/up, same inventory."""
    from butlers.testing.migration import migration_bootstrap_db_url

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    inventory = (
        "SELECT string_agg(table_schema || '.' || table_name, ',' ORDER BY 1) FROM"
        " information_schema.tables WHERE table_name LIKE 'metric\\_%'"
    )
    before = _scalar(db_url, inventory)
    assert before == "public.metric_baselines,public.metric_deviation_episodes"

    bootstrap = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, db_name), ["core"]
    )
    command.downgrade(bootstrap, "core_257")
    assert _scalar(db_url, inventory) is None
    command.upgrade(bootstrap, "core_258")

    assert _scalar(db_url, inventory) == before
