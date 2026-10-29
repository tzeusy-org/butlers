"""core_272: the narrow Health refill-probe definer (bu-q7vx1q.30).

Real PostgreSQL role matrix, modelled on core_255's finance bill probe: the Switchboard role gets
a single nullable boolean verdict and still cannot read ``health.facts`` directly; any other runtime
role is refused the function; replaying the upgrade keeps one pinned function; downgrade drops it.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/core/core_272_health_refill_premise_probe.py"
)
_CALL = "SELECT public.resolve_health_medication_refill('medication:Sample', 1, 14)"


def _migration():
    spec = importlib.util.spec_from_file_location("_core_272_matrix", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(postgres_container):
    db_url = create_migrated_test_db(postgres_container, migration_db_name(), chains=["core"])
    engine = create_engine(db_url)
    with engine.begin() as conn:
        # The managed bootstrap already created the role schemas in this fresh per-fixture
        # database; creating the Health schema again raises DuplicateSchema.
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS health"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS health.facts (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
                "subject text, predicate text, scope text, validity text, metadata jsonb, "
                "valid_at timestamptz, created_at timestamptz DEFAULT clock_timestamp())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO health.facts (subject, predicate, scope, validity, metadata) "
                "VALUES ('medication:Sample', 'medication', 'health', 'active', "
                "jsonb_build_object('active', true, 'quantity', 10, "
                "'quantity_updated_at', (now() - interval '2 days')::text))"
            )
        )
    yield engine
    engine.dispose()


def _as(engine, role: str, sql: str):
    with engine.connect() as conn:
        conn.execute(text(f"SET ROLE {role}"))
        try:
            return conn.execute(text(sql)).scalar()
        finally:
            conn.rollback()


def _pgcode(engine, role: str, sql: str) -> str:
    with pytest.raises(DBAPIError) as error:
        _as(engine, role, sql)
    return error.value.orig.pgcode


def _functions(engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(row)
            for row in conn.execute(
                text(
                    "SELECT prosecdef, proconfig::text AS config FROM pg_proc "
                    "WHERE proname = 'resolve_health_medication_refill' "
                    "AND pronamespace = 'public'::regnamespace"
                )
            ).mappings()
        ]


def test_switchboard_gets_a_verdict_and_no_direct_health_read(engine) -> None:
    assert _as(engine, "butler_switchboard_rw", _CALL) is True
    assert _pgcode(engine, "butler_switchboard_rw", "SELECT * FROM health.facts") == "42501"


def test_the_verdict_is_a_single_boolean_with_no_supply_figure(engine) -> None:
    assert _as(engine, "butler_switchboard_rw", _CALL.replace(", 14)", ", 1)")) is False
    with engine.connect() as conn:
        rettype = conn.execute(
            text(
                "SELECT prorettype::regtype::text FROM pg_proc "
                "WHERE proname = 'resolve_health_medication_refill'"
            )
        ).scalar()
    assert rettype == "boolean"


@pytest.mark.parametrize(
    "role", ["butler_finance_rw", "butler_relationship_rw", "butler_health_rw"]
)
def test_other_runtime_roles_are_refused_the_function(engine, role: str) -> None:
    assert _pgcode(engine, role, _CALL) == "42501"


def test_replaying_the_upgrade_keeps_one_pinned_function(engine) -> None:
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
    (function,) = _functions(engine)
    assert function["prosecdef"] is True
    assert "pg_catalog, pg_temp" in function["config"]
    assert _as(engine, "butler_switchboard_rw", _CALL) is True
    assert _pgcode(engine, "butler_finance_rw", _CALL) == "42501"


def test_downgrade_drops_the_function(engine) -> None:
    migration = _migration()
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            migration.downgrade()
    assert _functions(engine) == []
