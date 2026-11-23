"""core_380: ``public.owner_watch_covers_large_transaction``, Finance's one cover question.

Real migrated databases only, synthetic rows. The function answers a boolean and nothing else,
only to the finance role, from the ``owner_watches`` table and the feed-health ``state`` row, on
the database's clock. No runtime role can replace or alter it (bu-4c7nvp.1.5 S7).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SWITCHBOARD_SCHEMA, _SECOND = "switchboard", "health"
_FINANCE = "butler_finance_rw"
_CAP = "finance.transaction_amount.usd"
_FN = "public.owner_watch_covers_large_transaction"


def _module():
    path = (
        Path(__file__).resolve().parents[2] / "alembic/versions/core/core_380_owner_watch_cover.py"
    )
    spec = importlib.util.spec_from_file_location("core_380_for_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _as(url: str, role: str, statement: str):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            return connection.execute(text(statement)).scalar()
    finally:
        engine.dispose()


def _admin(url: str, statement: str):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement))
            return result.scalar() if result.returns_rows else None
    finally:
        engine.dispose()


@pytest.fixture
def migrated(postgres_container):
    url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(url, chains=["core"], target_schema=_SWITCHBOARD_SCHEMA)
    command.upgrade(config, "core_380")
    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(url).path.lstrip("/"))
    return url, bootstrap, config


def _watch(bootstrap, watch_id="w1", *, predicate=None, state="active", expires="1 day"):
    predicate = predicate or {
        "tag": "read_check",
        "capability": _CAP,
        "field": "amount",
        "op": "gt",
        "value": 500,
    }
    _admin(
        bootstrap,
        "ALTER TABLE public.owner_watches DISABLE TRIGGER ALL; "
        "INSERT INTO public.owner_watches (id, version, generation, predicate, predicate_digest, "
        "grant_digest, sources, categories, delivery, state, created_at, expires_at, "
        "approval_digest) "
        f"VALUES ('{watch_id}', 1, 1, '{json.dumps(predicate)}'::jsonb, repeat('a', 64), "
        "repeat('a', 64), '{x}', '{}', 'digest', "
        f"'{state}', now() - interval '2 days', now() + interval '{expires}', repeat('a', 64)); "
        "ALTER TABLE public.owner_watches ENABLE TRIGGER ALL",
    )


def _feed(bootstrap, value):
    _admin(bootstrap, "DELETE FROM switchboard.state WHERE key = 'owner_watch:feed_health'")
    if value is not None:
        _admin(
            bootstrap,
            "INSERT INTO switchboard.state (key, value) VALUES "
            f"('owner_watch:feed_health', '{json.dumps(value)}'::jsonb)",
        )


def _fresh(bootstrap, seconds_ago=30):
    return _admin(
        bootstrap,
        f"SELECT to_char(now() - interval '{seconds_ago} seconds', "
        '\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\')',
    )


def _covers(bootstrap, currency="USD", amount="900.00"):
    return _as(bootstrap, _FINANCE, f"SELECT {_FN}('{currency}', {amount}::numeric)")


@pytest.fixture
def live(migrated):
    _, bootstrap, _ = migrated
    _watch(bootstrap)
    _feed(bootstrap, {_CAP: _fresh(bootstrap)})
    return bootstrap


def test_two_schemas_install_once_and_only_the_last_to_leave_drops_it(postgres_container):
    url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(url, chains=["core"], target_schema=_SWITCHBOARD_SCHEMA)
    second = _build_alembic_config(url, chains=["core"], target_schema=_SECOND)
    installed = _module()._INSTALLED
    command.upgrade(first, "core_380")
    command.upgrade(second, "core_380")
    command.upgrade(second, "core_380")  # an idempotent replay
    assert _admin(url, installed) is True
    command.downgrade(first, "core_368")
    assert _admin(url, installed) is True  # the other schema still depends on it
    command.downgrade(second, "core_368")
    assert _admin(url, installed) is False
    command.upgrade(first, "core_380")
    assert _admin(url, installed) is True


def test_the_answer_is_true_only_for_an_active_covering_watch_with_a_live_feed(live):
    assert _covers(live) is True


@pytest.mark.parametrize(
    ("currency", "amount", "expected"),
    [
        ("USD", "500.00", False),  # gt: not strictly above the threshold
        ("USD", "500.01", True),
        ("usd", "900", True),  # a currency in any case
        ("EUR", "900.00", False),  # another currency has no covering watch
        ("USD", "0", False),
    ],
)
def test_the_boundary_and_the_currency(live, currency, amount, expected):
    assert _covers(live, currency, amount) is expected


def test_ge_includes_the_threshold(migrated):
    _, bootstrap, _ = migrated
    _watch(
        bootstrap,
        predicate={
            "tag": "read_check",
            "capability": _CAP,
            "field": "amount",
            "op": "ge",
            "value": 500,
        },
    )
    _feed(bootstrap, {_CAP: _fresh(bootstrap)})
    assert _covers(bootstrap, amount="500.00") is True
    assert _covers(bootstrap, amount="499.99") is False


@pytest.mark.parametrize("state", ["paused", "expired", "deleted"])
def test_a_watch_that_is_not_active_never_covers(migrated, state):
    _, bootstrap, _ = migrated
    _watch(bootstrap, state=state)
    _feed(bootstrap, {_CAP: _fresh(bootstrap)})
    assert _covers(bootstrap) is False


def test_a_watch_past_its_expiry_never_covers(migrated):
    _, bootstrap, _ = migrated
    _watch(bootstrap, expires="-1 hour")
    _feed(bootstrap, {_CAP: _fresh(bootstrap)})
    assert _covers(bootstrap) is False


def test_a_compound_or_other_predicate_never_covers(migrated):
    _, bootstrap, _ = migrated
    leaf = {
        "tag": "read_check",
        "capability": _CAP,
        "field": "amount",
        "op": "gt",
        "value": 500,
    }
    _watch(bootstrap, predicate={"tag": "all", "children": [leaf, leaf]})
    _feed(bootstrap, {_CAP: _fresh(bootstrap)})
    assert _covers(bootstrap) is False


@pytest.mark.parametrize(
    "feed",
    [
        None,  # no health row at all
        {},  # a row without the capability
        {"finance.transaction_amount.eur": "2099-01-01T00:00:00.000000Z"},
        {_CAP: "not a time"},
        {_CAP: 5},
    ],
    ids=["no row", "no capability", "other capability", "garbage", "wrong type"],
)
def test_a_missing_or_unreadable_feed_is_not_live(migrated, feed):
    _, bootstrap, _ = migrated
    _watch(bootstrap)
    _feed(bootstrap, feed)
    assert _covers(bootstrap) is False


def test_a_stale_feed_is_not_live_and_a_future_one_is_not_believed(migrated):
    _, bootstrap, _ = migrated
    _watch(bootstrap)
    _feed(bootstrap, {_CAP: _fresh(bootstrap, seconds_ago=8 * 60)})
    assert _covers(bootstrap) is False  # older than the 7 minute window
    _feed(bootstrap, {_CAP: _fresh(bootstrap, seconds_ago=6 * 60)})
    assert _covers(bootstrap) is True  # inside it
    _feed(bootstrap, {_CAP: _fresh(bootstrap, seconds_ago=-3600)})
    assert _covers(bootstrap) is False  # a time from the future is not a live feed


def test_the_function_returns_a_boolean_and_nothing_else(live):
    shape = _admin(
        live,
        f"SELECT pg_get_function_result(to_regprocedure('{_FN}(text, numeric)')) || ' ' || "
        f"(SELECT count(*) FROM pg_proc WHERE proname = 'owner_watch_covers_large_transaction')",
    )
    assert shape == "boolean 1"


@pytest.mark.parametrize(
    "role",
    [
        "butler_switchboard_rw",
        "butler_general_rw",
        "butler_health_rw",
        "connector_writer",
    ],
)
def test_every_other_role_is_denied(live, role):
    with pytest.raises(DBAPIError) as excinfo:
        _as(live, role, f"SELECT {_FN}('USD', 900::numeric)")
    assert "permission denied" in str(excinfo.value), role


def test_a_later_broad_grant_still_answers_nothing(live):
    _admin(live, f"GRANT EXECUTE ON FUNCTION {_FN}(text, numeric) TO butler_general_rw")
    with pytest.raises(DBAPIError) as excinfo:
        _as(live, "butler_general_rw", f"SELECT {_FN}('USD', 900::numeric)")
    assert "requires the finance identity" in str(excinfo.value)


@pytest.mark.parametrize("role", [_FINANCE, "butler_switchboard_rw"])
def test_no_runtime_role_can_replace_or_alter_the_function(live, role):
    replacement = (
        f"CREATE OR REPLACE FUNCTION {_FN}(p_currency text, p_amount numeric) RETURNS boolean "
        "LANGUAGE sql AS $$ SELECT true $$"
    )
    for statement in (
        replacement,
        f"ALTER FUNCTION {_FN}(text, numeric) OWNER TO {role}",
        f"ALTER FUNCTION {_FN}(text, numeric) SECURITY INVOKER",
        f"DROP FUNCTION {_FN}(text, numeric)",
    ):
        with pytest.raises(DBAPIError) as excinfo:
            _as(live, role, statement)
        assert "must be owner" in str(excinfo.value) or "permission denied" in str(excinfo.value), (
            statement
        )
    assert _covers(live) is True


def test_the_acl_is_the_owner_plus_the_finance_role_alone(live):
    grantees = _admin(
        live,
        "SELECT string_agg(coalesce(r.rolname, 'PUBLIC') || ':' || a.privilege_type || ':' "
        "|| a.is_grantable::text, ',' ORDER BY r.rolname) "
        f"FROM pg_proc p CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, "
        "acldefault('f', p.proowner))) a LEFT JOIN pg_roles r ON r.oid = a.grantee "
        f"WHERE p.oid = to_regprocedure('{_FN}(text, numeric)') AND a.grantee <> p.proowner",
    )
    assert grantees == f"{_FINANCE}:EXECUTE:false"


def test_the_function_is_owned_by_the_table_owner_not_a_runtime_role(live):
    same = _admin(
        live,
        f"SELECT p.proowner = c.relowner AND NOT EXISTS (SELECT 1 FROM pg_roles r "
        "WHERE r.oid = p.proowner AND r.rolname LIKE 'butler\\_%rw') "
        f"FROM pg_proc p, pg_class c WHERE p.oid = to_regprocedure('{_FN}(text, numeric)') "
        "AND c.oid = to_regclass('public.owner_watches')",
    )
    assert same is True


@pytest.mark.parametrize(
    "drift",
    [
        f"GRANT EXECUTE ON FUNCTION {_FN}(text, numeric) TO PUBLIC",
        f"GRANT EXECUTE ON FUNCTION {_FN}(text, numeric) TO butler_general_rw",
        f"ALTER FUNCTION {_FN}(text, numeric) RESET search_path",
        f"ALTER FUNCTION {_FN}(text, numeric) SECURITY INVOKER",
        f"ALTER FUNCTION {_FN}(text, numeric) VOLATILE",
    ],
    ids=["public grant", "other grant", "search_path", "invoker", "volatile"],
)
def test_a_drifted_function_is_repaired_not_skipped(postgres_container, drift):
    url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(url, chains=["core"], target_schema=_SWITCHBOARD_SCHEMA)
    installed = _module()._INSTALLED
    command.upgrade(config, "core_380")
    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(url).path.lstrip("/"))
    _admin(bootstrap, drift)
    assert _admin(bootstrap, installed) is False  # the catalog check sees the drift
    command.downgrade(config, "core_368")
    command.upgrade(config, "core_380")
    assert _admin(bootstrap, installed) is True


def test_a_changed_body_is_repaired(postgres_container):
    url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(url, chains=["core"], target_schema=_SWITCHBOARD_SCHEMA)
    installed = _module()._INSTALLED
    command.upgrade(config, "core_380")
    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(url).path.lstrip("/"))
    _admin(
        bootstrap,
        f"CREATE OR REPLACE FUNCTION {_FN}(p_currency text, p_amount numeric) RETURNS boolean "
        "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
        "AS $$ SELECT true $$",
    )
    assert _admin(bootstrap, installed) is False
    command.downgrade(config, "core_368")
    command.upgrade(config, "core_380")
    assert _admin(bootstrap, installed) is True
