"""core_258: per-schema personal baseline tables (bu-q7vx1q.15).

Real PostgreSQL: the tables are created in the butler's own schema (never in
``public``), a replay in a second schema is independent, the CHECK constraints
refuse a ``ready`` band with no center and a second open episode, and the
runtime-role grant stays inside the owning schema.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    bind_historical_test_environment,
    create_migration_db,
    migrate_test_database,
    migration_db_name,
    run_historical_test_alembic_command,
    run_test_alembic_command,
    stage_test_migration_history,
)

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
    run_test_alembic_command(postgres_container, command.upgrade, config, "core_257")
    run_test_alembic_command(postgres_container, command.upgrade, config, "core_258")
    migrate_test_database(postgres_container, db_url, plans=(("core", "health", None),))
    migrate_test_database(postgres_container, db_url, plans=(("core", "general", None),))

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
    run_test_alembic_command(postgres_container, command.downgrade, config, "core_257")
    assert _scalar(db_url, "SELECT to_regclass('public.metric_baselines') IS NULL") is True
    run_test_alembic_command(postgres_container, command.upgrade, config, "core_258")
    assert _scalar(db_url, "SELECT to_regclass('public.metric_baselines') IS NOT NULL") is True


def test_core_258_grant_stays_inside_the_owning_schema(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, ["core"])
    run_test_alembic_command(postgres_container, command.upgrade, config, "core_258")
    migrate_test_database(postgres_container, db_url, plans=(("core", "health", None),))

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
    tmp_path,
) -> None:
    """The smoke test's shape: ordinary upgrade, managed-bootstrap down/up, same inventory."""
    from butlers.testing.migration import migration_bootstrap_db_url

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    # Historical metadata/ownership diagnostic: observe real258 through280
    # before current281 finalization changes authority.
    stage_test_migration_history(postgres_container, db_url, plans=(("core", None, "core_280"),))
    # An aggregate's ORDER BY 1 is a constant, not an output-column ordinal.
    # Bootstrap recreates the tables, so their catalog input order can change.
    aggregate = (
        "SELECT string_agg(table_schema || '.' || table_name, ','"
        " ORDER BY table_schema, table_name) FROM "
    )
    inventory = aggregate + " information_schema.tables WHERE table_name LIKE 'metric\\_%'"
    before = _scalar(db_url, inventory)
    assert before == "public.metric_baselines,public.metric_deviation_episodes"

    # Real SQL controls isolate ordering from table visibility or grants.
    forward = "('public', 'metric_baselines'), ('public', 'metric_deviation_episodes')"
    reverse = "('public', 'metric_deviation_episodes'), ('public', 'metric_baselines')"
    for rows in (forward, reverse):
        source = f"(VALUES {rows}) AS fixture(table_schema, table_name)"
        assert _scalar(db_url, aggregate + source) == before
    legacy = (
        "SELECT string_agg(table_schema || '.' || table_name, ',' ORDER BY 1) FROM "
        f"(VALUES {reverse}) AS fixture(table_schema, table_name)"
    )
    assert _scalar(db_url, legacy) == "public.metric_deviation_episodes,public.metric_baselines"

    ordinary_login = (
        "SELECT NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication"
        " FROM pg_roles WHERE rolname = current_user"
    )
    assert _scalar(db_url, ordinary_login) is True

    bootstrap = _build_alembic_config(
        migration_bootstrap_db_url(postgres_container, db_name), ["core"]
    )
    pinned_environment = (
        Path(__file__).with_name("data") / "core_255_env_a090a8ac.py.txt"
    ).read_text()
    bind_historical_test_environment(
        bootstrap,
        tmp_path / "historical_admin",
        pinned_environment,
        expected_sha256="d8a717988914d2c697db58d3e13212ed3ca3d69df6f9e66f8ffdc61487b46a1d",
    )
    run_historical_test_alembic_command(
        postgres_container,
        command.downgrade,
        bootstrap,
        "core_257",
        normal_url=db_url,
        administrative=True,
    )
    assert _scalar(db_url, inventory) is None
    run_historical_test_alembic_command(
        postgres_container,
        command.upgrade,
        bootstrap,
        "core_258",
        normal_url=db_url,
        administrative=True,
    )

    assert _scalar(db_url, inventory) == before
    assert _scalar(db_url, ordinary_login) is True
    for table in ("metric_baselines", "metric_deviation_episodes"):
        assert _scalar(db_url, f"SELECT count(*) FROM public.{table}") == 0
