"""Genuine source/catalog diagnostic successors. Authored, not run locally.

The old parser accepted cosmetic/older variants. The adopted exact final body
contract now reports those changes, and converges only through the real held
migrator. Historical named tests/case population below retain explicit successor
controls for scope, body/overload/missing facts, independent functions and privacy.
No setup GRANT supplies a positive operation: the real bootstrap/installer does.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.core.stored_function_drift import compute_database_authority, log_database_authority
from butlers.db_authority.grants import serving_catalog_contract
from butlers.migrations import finalize_authority, migration_session, run_migrations
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]
_TARGET = "public.runtime_attention_active_switchboard_role()"


@dataclass(frozen=True)
class _Database:
    url: str
    bootstrap_url: str
    plans: tuple


def _source_plans():
    from butlers.config import load_config
    from butlers.db_authority.roles import _ROSTER_ROOT
    from butlers.migrations import get_module_migration_chains, has_butler_chain
    from butlers.modules.registry import default_registry

    registry = default_registry()
    plans = []
    for directory in sorted(_ROSTER_ROOT.iterdir()):
        if not (directory / "butler.toml").is_file():
            continue
        config = load_config(directory)
        plans.append(("core", config.db_schema, None))
        if has_butler_chain(config.name):
            plans.append((config.name, config.db_schema, None))
        modules = registry.load_from_config(config.modules)
        for name, chain in get_module_migration_chains(modules, config.name).items():
            settings = config.modules.get(name) or {}
            plans.append((chain, settings.get("memory_schema") or config.db_schema, None))
    return tuple(plans)


def _converge(database):
    with migration_session(database.bootstrap_url, source_database="butlers") as connection:
        for chain, schema, _ in database.plans:
            asyncio.run(
                run_migrations(database.url, chain=chain, schema=schema, connection=connection)
            )
        finalize_authority(connection, source_database="butlers", plans=database.plans)


@pytest.fixture(scope="module")
def head_db(postgres_container):
    name = migration_db_name()
    database = _Database(
        create_migration_db(postgres_container, name),
        migration_bootstrap_db_url(postgres_container, name),
        _source_plans(),
    )
    _converge(database)
    return database


@pytest.fixture(scope="module")
def bare_db(postgres_container):
    name = migration_db_name()
    return _Database(
        create_migration_db(postgres_container, name),
        migration_bootstrap_db_url(postgres_container, name),
        (),
    )


def _execute(database, sql):
    engine = create_engine(database.bootstrap_url)
    try:
        with engine.begin() as connection:
            return connection.exec_driver_sql(sql)
    finally:
        engine.dispose()


@pytest.fixture
def rewrite_planter(head_db):
    # Historical fixture name, current source-owned pure role predicate.
    engine = create_engine(head_db.bootstrap_url)
    with engine.connect() as connection:
        original = connection.execute(
            text("SELECT pg_get_functiondef(to_regprocedure(:name))"), {"name": _TARGET}
        ).scalar_one()

    def rewrite(sql):
        assert sql != original
        _execute(head_db, sql)

    try:
        yield original, rewrite
    finally:
        _execute(head_db, original)
        engine.dispose()


def _probe(database):
    async def run():
        pool = await asyncpg.create_pool(
            database.url.replace("postgresql+psycopg2:", "postgresql:"), min_size=1, max_size=1
        )
        try:
            return await compute_database_authority(pool)
        finally:
            await pool.close()

    return asyncio.run(run())


def _entry(report):
    return next(row for row in report.functions if row.function == _TARGET)


def test_scope_is_every_committed_function_with_no_opt_in_list(head_db):
    report = _probe(head_db)
    source = serving_catalog_contract("butlers")["functions"]
    expected = {f"{row['schema']}.{row['name']}({row['signature']})" for row in source}
    assert report.is_available
    assert {row.function for row in report.functions} == expected
    assert all(row.status == "conforming" for row in report.functions)


def test_reports_drift_when_a_deployed_body_diverges(head_db, rewrite_planter):
    original, rewrite = rewrite_planter
    rewritten = original.replace("butler_switchboard_rw", "synthetic_forbidden_role")
    assert rewritten != original
    rewrite(rewritten)
    report = _probe(head_db)
    assert report.status == "mismatch" and report.objects_ok is False
    entry = _entry(report)
    assert entry.status == "mismatch"
    assert entry.actual_digest != entry.expected_digest


def test_drift_of_one_function_does_not_taint_the_others(head_db, rewrite_planter):
    original, rewrite = rewrite_planter
    rewrite(original.replace("butler_switchboard_rw", "synthetic_forbidden_role"))
    report = _probe(head_db)
    assert {row.function for row in report.functions if row.status != "conforming"} == {_TARGET}
    assert sum(row.status == "conforming" for row in report.functions) > 20


def test_a_freshly_bootstrapped_database_reports_no_drift(head_db):
    report = _probe(head_db)
    assert report.is_available and report.status == "conforming"
    assert report.objects_ok is True
    assert all(row.owner_ok and row.settings_ok and row.acl_ok for row in report.functions)


@pytest.mark.parametrize(
    ("rule", "perturb"),
    [
        pytest.param(
            "line endings", lambda body: body.replace("\n", "\r\n"), id="crlf-line-endings"
        ),
        pytest.param(
            "trailing whitespace",
            lambda body: "\n".join(line + "  \t" for line in body.split("\n")),
            id="trailing-whitespace",
        ),
        pytest.param(
            "uniform indentation",
            lambda body: "\n".join("    " + line for line in body.split("\n")),
            id="uniform-reindent",
        ),
        pytest.param(
            "blank edges", lambda body: "\n\n" + body + "\n\n", id="blank-lines-around-body"
        ),
    ],
)
def test_a_cosmetically_rewritten_body_is_not_reported_as_drift(
    head_db, rewrite_planter, rule, perturb
):
    # Explicit successor: exact source digest, then actual installer convergence.
    original, rewrite = rewrite_planter
    source = serving_catalog_contract("butlers")
    from butlers.db_authority.interfaces import final_functions
    from butlers.db_authority.roles import runtime_profiles

    definition = next(
        row
        for row in final_functions(runtime_profiles("butlers"), "normal")
        if row.name == "runtime_attention_active_switchboard_role"
    )
    body = perturb(definition.body)
    assert body != definition.body, rule
    altered = definition.header + "AS $diagnostic$" + body + "$diagnostic$;"
    rewrite(altered)
    assert _entry(_probe(head_db)).status == "mismatch"
    _converge(head_db)
    restored = _entry(_probe(head_db))
    assert restored.status == "conforming"
    assert restored.actual_digest == restored.expected_digest
    assert source["functions"]


def test_either_committed_variant_of_a_two_body_function_matches(head_db, rewrite_planter):
    # Only one current function body may pass the replacement authority check.
    original, rewrite = rewrite_planter
    rewrite(original.replace("butler_switchboard_rw", "synthetic_old_variant"))
    assert _entry(_probe(head_db)).status == "mismatch"
    _converge(head_db)
    assert _entry(_probe(head_db)).status == "conforming"


def test_an_undeployed_function_is_named_rather_than_silently_passed(bare_db):
    report = _probe(bare_db)
    assert report.is_available and report.status == "mismatch"
    assert _entry(report).status == "missing"
    assert _entry(report).actual_digest is None
    assert report.objects_ok is False


def test_the_report_carries_digests_and_never_a_function_body(head_db):
    report = _probe(head_db)
    assert report.functions
    rendered = repr(report)
    assert "CREATE OR REPLACE" not in rendered
    assert "SELECT current_setting" not in rendered
    assert all(len(row.expected_digest) == 64 for row in report.functions)
    assert all(
        row.actual_digest is None or len(row.actual_digest) == 64 for row in report.functions
    )


def test_the_startup_log_line_names_functions_not_bodies(head_db, rewrite_planter, caplog):
    original, rewrite = rewrite_planter
    rewrite(original.replace("butler_switchboard_rw", "synthetic_forbidden_role"))
    report = _probe(head_db)
    log_database_authority(report)
    assert report.status == "mismatch"
    assert "controlled migrator" in caplog.text
    assert "synthetic_forbidden_role" not in caplog.text
    assert "CREATE OR REPLACE" not in caplog.text


@pytest.mark.parametrize("kind", ["namespace", "relation", "function", "acl"])
def test_literal_system_prefix_does_not_hide_pgx_user_objects(head_db, kind):
    """Genuine catalog execution, including the intended old-pattern mutant.

    These are disposable adversarial objects, never production GRANT sources.
    Only pg_ is reserved here: pgx_custom is an ordinary user namespace.
    """
    import json

    from butlers.db_authority.catalog import _CATALOG_OBJECT_SQL

    assert _probe(head_db).status == "conforming"
    try:
        _execute(head_db, "CREATE SCHEMA pgx_custom")
        if kind == "relation":
            _execute(head_db, "CREATE TABLE pgx_custom.unlisted (value integer)")
        elif kind == "function":
            _execute(
                head_db,
                "CREATE FUNCTION pgx_custom.unlisted() RETURNS integer LANGUAGE sql AS 'SELECT 1'",
            )
        elif kind == "acl":
            _execute(head_db, "GRANT USAGE ON SCHEMA pgx_custom TO PUBLIC")
        report = _probe(head_db)
        assert report.objects_ok is False and report.status == "mismatch"
        engine = create_engine(head_db.bootstrap_url)
        try:
            with engine.connect() as connection:
                encoded = json.dumps(serving_catalog_contract("butlers"))
                # Reintroducing only the four defective prefix comparisons
                # must conceal this actual namespace, without changing rows.
                mutant = _CATALOG_OBJECT_SQL.replace(
                    "left(n.nspname,3)<>'pg_'", "n.nspname NOT LIKE 'pg_%'"
                )
                assert mutant != _CATALOG_OBJECT_SQL
                assert (
                    connection.execute(
                        text(mutant.replace("$1", ":catalog")), {"catalog": encoded}
                    ).scalar_one()
                    is True
                )
        finally:
            engine.dispose()
    finally:
        _execute(head_db, "DROP SCHEMA IF EXISTS pgx_custom CASCADE")
    assert _probe(head_db).status == "conforming"


def test_oauth_actual_catalog_refuses_pgx_before_token_read(head_db, monkeypatch, tmp_path):
    """Actual connector session and catalog, observed at the token-query boundary.

    The operator sets up a disposable LOGIN-to-capability membership only; the
    actual converger supplies object grants. No fixture grants entity_info.
    """
    import importlib.util
    import uuid
    from pathlib import Path
    from urllib.parse import urlparse

    import psycopg2

    from butlers.db_authority import roles

    selected = urlparse(head_db.bootstrap_url)
    database_name = selected.path.removeprefix("/")
    # A disposable deployment names its database in the same real roster
    # format; the strict production source compiler still reads that format.
    source_root = roles._ROSTER_ROOT
    deployment = tmp_path / "roster"
    deployment.mkdir()
    for directory in source_root.iterdir():
        config = directory / "butler.toml"
        if config.is_file():
            target = deployment / directory.name
            target.mkdir()
            target.joinpath("butler.toml").write_text(
                config.read_text().replace('name = "butlers"', f'name = "{database_name}"')
            )
    monkeypatch.setattr(roles, "_ROSTER_ROOT", deployment)
    login = "catalog_probe_" + uuid.uuid4().hex[:16]
    password = "synthetic-disposable-password"
    _execute(head_db, f"CREATE ROLE {login} LOGIN NOINHERIT PASSWORD '{password}'")
    _execute(head_db, f"GRANT connector_writer TO {login}")
    for key, value in {
        "POSTGRES_USER": login,
        "POSTGRES_PASSWORD": password,
        "POSTGRES_DB": database_name,
        "POSTGRES_HOST": selected.hostname,
        "POSTGRES_PORT": str(selected.port or 5432),
        "POSTGRES_SSLMODE": "disable",
    }.items():
        monkeypatch.setenv(key, value)
    source = Path(__file__).resolve().parents[2] / "scripts" / "oauth_gate.py"
    spec = importlib.util.spec_from_file_location("catalog_oauth_actual", source)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    connect = psycopg2.connect
    observations = []
    statements = []

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.cursor.close()

        def execute(self, statement, *args):
            statements.append(statement)
            return self.cursor.execute(statement, *args)

        def fetchone(self):
            row = self.cursor.fetchone()
            observations.append(row)
            return row

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        @property
        def autocommit(self):
            return self.connection.autocommit

        @autocommit.setter
        def autocommit(self, value):
            self.connection.autocommit = value

        def cursor(self):
            return Cursor(self.connection.cursor())

        def close(self):
            return self.connection.close()

    monkeypatch.setattr(psycopg2, "connect", lambda **options: Connection(connect(**options)))
    try:
        # Healthy role/catalog reaches the genuine token query regardless of
        # whether this isolated database has any OAuth token (no token seeded).
        gate._check_token()
        assert observations[0] == (True,) * 6
        assert observations[1] == (True,)
        assert sum("public.entity_info ei" in statement for statement in statements) == 1
        _execute(head_db, "CREATE SCHEMA pgx_custom")
        observations.clear()
        statements.clear()
        assert gate._check_token() is False
        assert observations[0] == (True,) * 6
        assert observations[1] == (False,)
        assert not any("public.entity_info ei" in statement for statement in statements)
    finally:
        _execute(head_db, "DROP SCHEMA IF EXISTS pgx_custom CASCADE")
        _execute(head_db, f"DROP ROLE {login}")
