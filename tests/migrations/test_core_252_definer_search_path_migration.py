"""core_252: remaining migration-owned SECURITY DEFINER search paths are pinned.

bu-mzm3su.2.  Real PostgreSQL: the core chain runs in ``public`` and in two
butler schemas (so every ``connectors_filtered_events_ensure_partition`` copy
exists), plus the Relationship chain for the ``resolve_*`` helpers.  Each
decoy test first downgrades to core_251 to prove the decoy is live on the
prior path, then upgrades and proves no decoy runs.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_PINNED = "search_path=pg_catalog, pg_temp"
_CORE_252_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "core"
    / "core_252_pin_definer_search_paths.py"
)
_CORE_SCHEMAS = (None, "general", "switchboard")

_TARGETS_SQL = """
SELECT format(
           '%I.%I(%s)', n.nspname, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
       ) AS signature,
       n.nspname AS schema_name,
       p.proname AS function_name,
       p.proconfig
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
WHERE p.prosecdef
  AND (
        (n.nspname = 'public' AND (
            p.proname LIKE 'dashboard\\_turn\\_%'
            OR p.proname IN (
                'resolve_owner_triple',
                'resolve_relationship_prepared_action_status',
                'record_runtime_probe_verification',
                'cost_claim_restore_row'
            )
        ))
     OR (n.nspname = 'dashboard_auth' AND p.proname IN ('api', 'host', 'cleanup'))
     OR p.proname = 'connectors_filtered_events_ensure_partition'
  )
ORDER BY 1
"""

# A caller with TEMP owns pg_temp.  While a definer's path leaves pg_temp
# implicit, pg_temp is searched first for type names, so these domains replace
# the catalog types in the definer's DECLAREs and casts and run the decoy
# function as the definer's owner.
_TEMP_TYPE_DECOYS = """
CREATE FUNCTION pg_temp.butlers_decoy(anyelement) RETURNS boolean
LANGUAGE plpgsql AS $decoy$
BEGIN
    RAISE EXCEPTION 'hijacked as %', current_user;
END;
$decoy$;
CREATE DOMAIN pg_temp.text AS pg_catalog.text CHECK (pg_temp.butlers_decoy(VALUE));
CREATE DOMAIN pg_temp.uuid AS pg_catalog.uuid CHECK (pg_temp.butlers_decoy(VALUE));
CREATE DOMAIN pg_temp.timestamptz AS pg_catalog.timestamptz
    CHECK (pg_temp.butlers_decoy(VALUE));
CREATE DOMAIN pg_temp.boolean AS pg_catalog.bool CHECK (pg_temp.butlers_decoy(VALUE));
"""

# The migration login can CREATE in public and owns connectors.  An exact
# (text, text) overload beats the catalog's format(text, VARIADIC "any"), and
# an exact row-type overload beats the polymorphic jsonb_populate_record.
_SCHEMA_DECOYS = """
CREATE FUNCTION {schema}.format(text, text) RETURNS text
LANGUAGE plpgsql AS $decoy$
BEGIN
    RAISE EXCEPTION 'hijacked as %', current_user;
END;
$decoy$;
"""
_POPULATE_DECOY = """
CREATE FUNCTION public.jsonb_populate_record(public.cost_claims, jsonb)
RETURNS public.cost_claims
LANGUAGE plpgsql AS $decoy$
BEGIN
    RAISE EXCEPTION 'hijacked as %', current_user;
END;
$decoy$;
"""
_DROP_SCHEMA_DECOYS = """
DROP FUNCTION IF EXISTS public.format(text, text);
DROP FUNCTION IF EXISTS connectors.format(text, text);
DROP FUNCTION IF EXISTS public.jsonb_populate_record(public.cost_claims, jsonb);
"""


def _core_config(db_url: str, schema: str | None):
    return _build_alembic_config(db_url, ["core"], target_schema=schema)


def _migrated_db(postgres_container) -> str:
    db_url = create_migration_db(postgres_container, migration_db_name())
    for schema in _CORE_SCHEMAS:
        asyncio.run(run_migrations(db_url, chain="core", schema=schema))
    asyncio.run(run_migrations(db_url, chain="relationship", schema="relationship"))
    return db_url


def _definers(db_url: str) -> dict[str, tuple[str, str, list[str]]]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return {
                row.signature: (row.schema_name, row.function_name, list(row.proconfig or []))
                for row in conn.execute(text(_TARGETS_SQL))
            }
    finally:
        engine.dispose()


def _load_migration():
    spec = importlib.util.spec_from_file_location("core_252_under_test", _CORE_252_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_prior(schema: str, name: str) -> str:
    priors = _load_migration().PRIOR_SEARCH_PATHS
    prior = priors.get((schema, name)) or priors[(None, name)]
    return f"search_path={prior}"


def _outcome(db_url: str, statement: str, params: dict[str, object] | None = None) -> str:
    """Run *statement* in a fresh session holding the temp-type decoys."""
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(_TEMP_TYPE_DECOYS))
            try:
                conn.execute(text(statement), params or {}).all()
            except DBAPIError as exc:
                return f"error: {exc.orig}"
            return "ok"
    finally:
        engine.dispose()


def _cost_claim_payload() -> str:
    return json.dumps(
        {
            "id": str(uuid.uuid4()),
            "claim_key": f"test:core-252:{uuid.uuid4()}",
            "asserted_by": "relationship",
            "kind": "receivable",
            "direction": "inbound",
            "amount": 1,
            "currency": "SGD",
        }
    )


_CALLS: dict[str, tuple[str, object]] = {
    "dashboard_turn": (
        "SELECT * FROM public.dashboard_turn_dispatch_status(:id)",
        lambda: {"id": str(uuid.uuid4())},
    ),
    "runtime_probe": (
        "SELECT public.record_runtime_probe_verification(:id, false, 5, 'probe failed')",
        lambda: {"id": str(uuid.uuid4())},
    ),
    "dashboard_auth": ("SELECT dashboard_auth.cleanup()", lambda: {}),
    "cost_claim": (
        "SELECT public.cost_claim_restore_row('cost_claims', :payload)",
        lambda: {"payload": _cost_claim_payload()},
    ),
    "resolve_owner": (
        "SELECT * FROM public.resolve_owner_triple('owner-channel', "
        "ARRAY['has-email:nobody@example.invalid'])",
        lambda: {},
    ),
    "resolve_prepared": (
        "SELECT * FROM public.resolve_relationship_prepared_action_status(:id)",
        lambda: {"id": str(uuid.uuid4())},
    ),
}


def _partition_calls(db_url: str) -> dict[str, tuple[str, object]]:
    return {
        f"ensure_partition:{schema}": (
            f"SELECT {schema}.connectors_filtered_events_ensure_partition(now())",
            lambda: {},
        )
        for signature, (schema, name, _config) in _definers(db_url).items()
        if name == "connectors_filtered_events_ensure_partition"
    }


def _plant_schema_decoys(db_url: str) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(_SCHEMA_DECOYS.format(schema="public")))
            conn.execute(text(_SCHEMA_DECOYS.format(schema="connectors")))
            conn.execute(text(_POPULATE_DECOY))
    finally:
        engine.dispose()


def test_core_252_pins_every_target_on_a_fresh_multi_schema_database(postgres_container) -> None:
    """Fresh chain: every group-B definer and every ensure-partition copy is pinned."""
    db_url = _migrated_db(postgres_container)
    definers = _definers(db_url)

    names = {name for _schema, name, _config in definers.values()}
    assert {
        "dashboard_turn_require_role",
        "resolve_owner_triple",
        "resolve_relationship_prepared_action_status",
        "record_runtime_probe_verification",
        "cost_claim_restore_row",
        "api",
        "host",
        "cleanup",
    } <= names
    assert sum(1 for _s, name, _c in definers.values() if name.startswith("dashboard_turn_")) == 19

    partition_schemas = {
        schema
        for schema, name, _config in definers.values()
        if name == "connectors_filtered_events_ensure_partition"
    }
    assert {"connectors", "public", "general", "switchboard"} <= partition_schemas

    for signature, (_schema, name, config) in definers.items():
        expected = [_PINNED, "row_security=on"] if name == "cost_claim_restore_row" else [_PINNED]
        assert config == expected, signature


def test_core_252_downgrade_restores_exact_prior_paths_and_reupgrade_is_idempotent(
    postgres_container,
) -> None:
    db_url = _migrated_db(postgres_container)
    pinned = _definers(db_url)

    command.downgrade(_core_config(db_url, "general"), "core_251")
    restored = _definers(db_url)
    assert set(restored) == set(pinned)
    for signature, (schema, name, config) in restored.items():
        prior = [_expected_prior(schema, name)]
        if name == "cost_claim_restore_row":
            prior.append("row_security=on")
        assert config == prior, signature

    # A second schema's downgrade finds nothing left to restore.
    command.downgrade(_core_config(db_url, "switchboard"), "core_251")
    assert _definers(db_url) == restored

    command.upgrade(_core_config(db_url, "general"), "core_252")
    assert _definers(db_url) == pinned
    command.upgrade(_core_config(db_url, "switchboard"), "core_252")
    assert _definers(db_url) == pinned


def test_core_252_refuses_an_unknown_prior_path_without_changing_anything(
    postgres_container,
) -> None:
    db_url = _migrated_db(postgres_container)
    command.downgrade(_core_config(db_url, "general"), "core_251")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "ALTER FUNCTION public.dashboard_turn_require_role(text) "
                    "SET search_path = pg_catalog, public"
                )
            )
    finally:
        engine.dispose()
    before = _definers(db_url)

    with pytest.raises(RuntimeError, match="core_252 refuses to pin"):
        command.upgrade(_core_config(db_url, "general"), "core_252")
    assert _definers(db_url) == before


def test_core_252_definers_never_run_a_temp_or_schema_decoy(postgres_container) -> None:
    """Prior paths run the decoys; pinned paths never do, and the calls still work."""
    db_url = _migrated_db(postgres_container)
    calls = {**_CALLS, **_partition_calls(db_url)}
    assert len(calls) >= len(_CALLS) + 4

    for schema in _CORE_SCHEMAS:
        command.downgrade(_core_config(db_url, schema), "core_251")
    _plant_schema_decoys(db_url)

    # The hole: every group whose body resolves a type or overload by name.
    exploitable = [
        "dashboard_turn",
        "runtime_probe",
        "dashboard_auth",
        "cost_claim",
        *(name for name in calls if name.startswith("ensure_partition:")),
    ]
    for name in exploitable:
        statement, params = calls[name]
        outcome = _outcome(db_url, statement, params())
        assert "hijacked as" in outcome, (name, outcome)

    for schema in _CORE_SCHEMAS:
        command.upgrade(_core_config(db_url, schema), "core_252")

    for name, (statement, params) in calls.items():
        outcome = _outcome(db_url, statement, params())
        assert "hijacked" not in outcome, (name, outcome)
        if name == "cost_claim":
            # The restore helper still enforces its own restore contract.
            continue
        if name == "resolve_prepared":
            # relationship.pending_actions belongs to the approvals module chain,
            # which this database does not run; reaching that relation proves the
            # body executed on the catalog path.
            assert 'relation "relationship.pending_actions" does not exist' in outcome
            continue
        assert outcome == "ok", (name, outcome)

    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(_DROP_SCHEMA_DECOYS))
    finally:
        engine.dispose()
