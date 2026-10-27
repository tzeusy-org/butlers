"""sw_039: every Switchboard SECURITY DEFINER search path is pinned.

bu-mzm3su.3.  Real PostgreSQL: the core chain runs in ``public`` and the
Switchboard chain in ``switchboard``, the production layout.  The decoy test
first downgrades to sw_038 to prove the runtime role's decoy is live on the
prior path, then upgrades and proves no decoy runs while partition maintenance
still works under the pinned path.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers.core.definer_search_path import is_pinned
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_SCHEMA = "switchboard"
_RUNTIME_ROLE = "butler_switchboard_rw"
_LOCK_NAME = "switchboard_connector_heartbeat_log_ensure_partition"

_TARGETS_SQL = """
SELECT format(
           '%I.%I(%s)', n.nspname, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
       ) AS signature,
       p.prosrc,
       p.proconfig
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
WHERE p.prosecdef
  AND (
        (n.nspname = 'public' AND p.proname IN (
            'register_butler_boot', 'reserve_butler_probe', 'record_butler_probe',
            'set_butler_registry_policy', 'qa_local_schedule_policy'
        ))
     OR (n.nspname = 'switchboard' AND p.proname IN (
            'ensure_registry_control',
            'switchboard_message_inbox_ensure_partition',
            'switchboard_message_inbox_drop_expired_partitions',
            'switchboard_connector_heartbeat_log_ensure_partition',
            'switchboard_connector_heartbeat_log_drop_expired_partitions'
        ))
  )
ORDER BY 1
"""

# butler_switchboard_rw holds CREATE on its own schema.  An exact (text, text)
# overload beats the catalog's format(text, VARIADIC "any") on any path that
# lists the schema.
_RUNTIME_ROLE_DECOY = """
CREATE FUNCTION switchboard.format(text, text) RETURNS text
LANGUAGE plpgsql AS $decoy$
BEGIN
    RAISE EXCEPTION 'hijacked as %', current_user;
END;
$decoy$
"""

_PARTITION_CALLS = {
    "inbox_ensure": (
        "SELECT switchboard.switchboard_message_inbox_ensure_partition("
        "'2099-02-15T00:00:00+00:00'::timestamptz)"
    ),
    "inbox_drop": (
        "SELECT switchboard.switchboard_message_inbox_drop_expired_partitions("
        "INTERVAL '1 month', '2099-05-15T00:00:00+00:00'::timestamptz)"
    ),
    "heartbeat_ensure": (
        "SELECT switchboard.switchboard_connector_heartbeat_log_ensure_partition("
        "'2099-02-15T00:00:00+00:00'::timestamptz)"
    ),
    "heartbeat_drop": (
        "SELECT switchboard.switchboard_connector_heartbeat_log_drop_expired_partitions("
        "INTERVAL '7 days', '2099-05-15T00:00:00+00:00'::timestamptz)"
    ),
}


def _switchboard_config(db_url: str):
    return _build_alembic_config(db_url, ["switchboard"], target_schema=_SCHEMA)


def _migrated_db(postgres_container) -> str:
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    asyncio.run(run_migrations(db_url, chain="switchboard", schema=_SCHEMA))
    return db_url


def _normalize(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()


def _targets(db_url: str) -> dict[str, tuple[str, list[str]]]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return {
                row.signature: (_normalize(row.prosrc), list(row.proconfig or []))
                for row in conn.execute(text(_TARGETS_SQL))
            }
    finally:
        engine.dispose()


def _as_runtime_role(db_url: str, statement: str) -> str:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'SET ROLE "{_RUNTIME_ROLE}"'))
            try:
                return str(conn.execute(text(statement)).scalar())
            except DBAPIError as exc:
                return f"error: {exc.orig}"
    finally:
        engine.dispose()


def _partitions(db_url: str, parent: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return set(
                conn.execute(
                    text(
                        "SELECT child.relname FROM pg_inherits "
                        "JOIN pg_class parent ON parent.oid = pg_inherits.inhparent "
                        "JOIN pg_namespace ns ON ns.oid = parent.relnamespace "
                        "JOIN pg_class child ON child.oid = pg_inherits.inhrelid "
                        "WHERE ns.nspname = :schema AND parent.relname = :parent"
                    ),
                    {"schema": _SCHEMA, "parent": parent},
                ).scalars()
            )
    finally:
        engine.dispose()


def _ensure_holds_the_literal_schema_lock(db_url: str) -> bool:
    """While ensure_partition's transaction is open, is the literal key taken?

    The key is spelled out here exactly as the pre-rewrite body evaluated it
    (``current_schema()`` under ``switchboard, pg_temp``).  If the rewritten
    body hashed anything else, the probe would acquire the lock.
    """
    holder = create_engine(db_url)
    probe = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with holder.connect() as held, probe.connect() as prober:
            held.execute(text(f'SET ROLE "{_RUNTIME_ROLE}"'))
            held.execute(text(_PARTITION_CALLS["heartbeat_ensure"]))
            acquired = prober.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:schema), hashtext(:name))"),
                {"schema": _SCHEMA, "name": _LOCK_NAME},
            ).scalar_one()
            held.rollback()
            return not acquired
    finally:
        holder.dispose()
        probe.dispose()


def test_sw_039_pins_targets_and_partition_maintenance_works_with_the_same_lock_key(
    postgres_container,
) -> None:
    """Fresh chain: all ten definers pinned; next-month create and expired drop work."""
    db_url = _migrated_db(postgres_container)
    targets = _targets(db_url)
    assert len(targets) == 10, sorted(targets)
    for signature, (body, config) in targets.items():
        assert is_pinned(config), (signature, config)
        assert "current_schema" not in body, signature

    assert _ensure_holds_the_literal_schema_lock(db_url)

    for parent, ensure, drop in (
        ("message_inbox", "inbox_ensure", "inbox_drop"),
        ("connector_heartbeat_log", "heartbeat_ensure", "heartbeat_drop"),
    ):
        assert _as_runtime_role(db_url, _PARTITION_CALLS[ensure]) == f"{parent}_p209902"
        assert {f"{parent}_p209902", f"{parent}_p209903"} <= _partitions(db_url, parent)
        # Retention cutoff is April 2099: February and March are expired.
        dropped = int(_as_runtime_role(db_url, _PARTITION_CALLS[drop]))
        assert dropped >= 2
        assert not {f"{parent}_p209902", f"{parent}_p209903"} & _partitions(db_url, parent)


def test_sw_039_downgrade_restores_prior_bodies_and_reupgrade_is_idempotent(
    postgres_container,
) -> None:
    """sw_038 bodies and paths come back exactly, and the old key is the same key."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _switchboard_config(db_url)
    command.upgrade(config, "switchboard@sw_038")
    prior = _targets(db_url)
    assert len(prior) == 10
    assert not any(is_pinned(config) for _body, config in prior.values())
    # Old and new callers queue on one key during a rolling deploy.
    assert _ensure_holds_the_literal_schema_lock(db_url)

    command.upgrade(config, "switchboard@sw_039")
    pinned = _targets(db_url)
    command.downgrade(config, "switchboard@sw_038")
    assert _targets(db_url) == prior

    command.upgrade(config, "switchboard@sw_039")
    assert _targets(db_url) == pinned
    # Re-running the revision body against an already pinned database is a no-op.
    command.downgrade(config, "switchboard@sw_038")
    command.upgrade(config, "switchboard@sw_039")
    assert _targets(db_url) == pinned


def test_sw_039_definers_never_run_a_runtime_role_decoy(postgres_container) -> None:
    """The runtime role's decoy runs on the prior path and never after sw_039."""
    db_url = _migrated_db(postgres_container)
    config = _switchboard_config(db_url)
    command.downgrade(config, "switchboard@sw_038")
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'SET ROLE "{_RUNTIME_ROLE}"'))
            conn.execute(text(_RUNTIME_ROLE_DECOY))
            conn.execute(text("RESET ROLE"))
            owner = conn.execute(
                text(
                    "SELECT pg_get_userbyid(proowner) FROM pg_proc "
                    "WHERE oid = 'switchboard.switchboard_message_inbox_ensure_partition"
                    "(timestamptz)'::regprocedure"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    # The hole: the runtime role's code runs as the migration owner.
    for name, statement in _PARTITION_CALLS.items():
        outcome = _as_runtime_role(db_url, statement)
        assert outcome.startswith(f"error: hijacked as {owner}"), (name, outcome)

    command.upgrade(config, "switchboard@sw_039")
    for name, statement in _PARTITION_CALLS.items():
        outcome = _as_runtime_role(db_url, statement)
        assert not outcome.startswith("error"), (name, outcome)

    # Registry definers still work with the decoy planted.
    boot = _as_runtime_role(db_url, "SELECT count(*) FROM public.reserve_butler_probe('ghost')")
    assert boot == "0"
    probe = _as_runtime_role(
        db_url,
        "SELECT public.record_butler_probe('ghost', 1, 1, false, NULL, NULL, 'timeout')",
    )
    assert probe == "False"
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            name = f"decoy-{uuid.uuid4().hex[:8]}"
            conn.execute(
                text(
                    "INSERT INTO switchboard.butler_registry (name, endpoint_url) "
                    "VALUES (:name, 'http://localhost:1')"
                ),
                {"name": name},
            )
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM switchboard.butler_registry_control_plane WHERE name = :n"
                    ),
                    {"n": name},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
