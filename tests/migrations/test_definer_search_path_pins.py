"""Catalog-wide guard: every SECURITY DEFINER search path is exactly ``pg_catalog, pg_temp``.

bu-mzm3su.3.  The guard enumerates ``pg_proc`` on a real database after the
privileged bootstrap (``scripts/init-db.sql``) and every migration chain that
defines a SECURITY DEFINER, in the production layout.  It judges each function
with the one shared predicate, :func:`butlers.core.definer_search_path.is_pinned`.

The allowlist is empty and must stay empty.  A new definer either pins its path
and schema-qualifies its body, or this guard fails.

RFC 0024 (Messenger correspondence ledger) currently prescribes
``SET search_path = pg_catalog`` for its projection definer.  That text is an
owner decision (bu-i0aqyi) and is deliberately not edited here; no such
function exists yet, and when one is added this guard will reject that path.
"""

from __future__ import annotations

import asyncio
import re
import shutil

import pytest
from sqlalchemy import create_engine, text

from butlers.core.definer_search_path import DEFINERS_SQL, DefinerConfig, unpinned
from butlers.migrations import _resolve_chain_dir, get_all_chains, run_migrations
from butlers.testing.migration import (
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

#: Exceptions to the pin rule.  Empty by contract (bu-mzm3su.3).
ALLOWLIST: frozenset[str] = frozenset()

#: (chain, schema) in run order.  The core chain runs once per butler schema in
#: production, and each run leaves its own per-schema definer copies.
_CHAIN_RUNS: tuple[tuple[str, str | None], ...] = (
    ("core", None),
    ("relationship", "relationship"),
    ("core", "general"),
    ("core", "switchboard"),
    ("switchboard", "switchboard"),
)

_DEFINER_SOURCE = re.compile(r"security\s+definer", re.IGNORECASE)


def _definer_chains() -> set[str]:
    """Every migration chain whose source declares a SECURITY DEFINER."""
    chains: set[str] = set()
    for chain in get_all_chains():
        chain_dir = _resolve_chain_dir(chain)
        if chain_dir is None:
            continue
        if any(_DEFINER_SOURCE.search(path.read_text()) for path in chain_dir.glob("*.py")):
            chains.add(chain)
    return chains


@pytest.fixture(scope="module")
def bootstrapped_db(postgres_container) -> tuple[str, str]:
    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    for chain, schema in _CHAIN_RUNS:
        asyncio.run(run_migrations(db_url, chain=chain, schema=schema))
    return db_url, migration_bootstrap_db_url(postgres_container, db_name)


def _definers(db_url: str) -> list[DefinerConfig]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return [
                DefinerConfig(row.signature, tuple(row.proconfig or ()))
                for row in conn.execute(text(DEFINERS_SQL))
            ]
    finally:
        engine.dispose()


def test_every_bootstrapped_definer_is_pinned_with_an_empty_allowlist(bootstrapped_db) -> None:
    # A chain that gains a definer must join the bootstrap above, or the guard
    # would silently stop covering it.
    assert _definer_chains() <= {chain for chain, _schema in _CHAIN_RUNS}
    assert not ALLOWLIST

    _db_url, bootstrap_url = bootstrapped_db
    definers = _definers(bootstrap_url)
    signatures = {definer.signature for definer in definers}
    # Non-vacuous: init-db, core and Switchboard definers are all in view.
    assert len(definers) > 60, len(definers)
    for expected in (
        "dnd_generation_private.mutate",
        "restore_drill_executor.is_due",
        "public.dashboard_turn_open",
        "public.register_butler_boot",
        "switchboard.switchboard_connector_heartbeat_log_ensure_partition",
        "general.connectors_filtered_events_ensure_partition",
    ):
        assert any(signature.startswith(expected + "(") for signature in signatures), expected

    offenders = [
        f"{definer.signature}: {list(definer.proconfig)}"
        for definer in unpinned(definers)
        if definer.signature not in ALLOWLIST
    ]
    assert offenders == []


def test_guard_rejects_a_planted_unpinned_definer(bootstrapped_db) -> None:
    """The same enumeration and predicate catch a definer the chains never made."""
    db_url, _bootstrap_url = bootstrapped_db
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text(
                        """
                        CREATE FUNCTION public.guard_probe_no_path() RETURNS int
                        LANGUAGE sql SECURITY DEFINER AS 'SELECT 1';
                        CREATE FUNCTION public.guard_probe_public_path() RETURNS int
                        LANGUAGE sql SECURITY DEFINER
                        SET search_path = pg_catalog, public, pg_temp AS 'SELECT 1';
                        CREATE FUNCTION public.guard_probe_pinned() RETURNS int
                        LANGUAGE sql SECURITY DEFINER
                        SET search_path = pg_catalog, pg_temp AS 'SELECT 1';
                        """
                    )
                )
                definers = [
                    DefinerConfig(row.signature, tuple(row.proconfig or ()))
                    for row in conn.execute(text(DEFINERS_SQL))
                ]
                conn.rollback()
    finally:
        engine.dispose()

    flagged = {definer.signature for definer in unpinned(definers)}
    assert flagged == {"public.guard_probe_no_path()", "public.guard_probe_public_path()"}
