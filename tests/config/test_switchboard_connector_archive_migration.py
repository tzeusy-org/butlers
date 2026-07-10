"""Integration tests for the connector_registry soft-archive migration (sw_022).

Covers bu-33dm2:
  - ``archived_at`` column + ``ix_connector_registry_live`` partial index exist
    after the switchboard chain runs.
  - The idempotent data-seed archives exactly the four dead identities (including
    the UUID-suffixed google_health identity matched by prefix) and leaves live
    identities untouched.
  - Downgrade cleanly drops the column and index.

The seed runs *inside* sw_022's upgrade, so the test upgrades to sw_021 first,
inserts the fixture rows, then applies sw_022 and asserts the resulting
``archived_at`` state.
"""

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
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _prepare_pre_seed_db(postgres_container) -> str:
    """Run core (full) + switchboard up to sw_021 (the revision before sw_022)."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_021")
    return db_url


def _apply_sw_022(db_url: str) -> None:
    from butlers.migrations import _build_alembic_config

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_022")


def _insert_connector(db_url: str, connector_type: str, endpoint_identity: str) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_registry (connector_type, endpoint_identity, state)"
                    " VALUES (:t, :i, 'unknown')"
                ),
                {"t": connector_type, "i": endpoint_identity},
            )
    finally:
        engine.dispose()


def _archived_at(db_url: str, connector_type: str, endpoint_identity: str):
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT archived_at FROM connector_registry"
                    " WHERE connector_type = :t AND endpoint_identity = :i"
                ),
                {"t": connector_type, "i": endpoint_identity},
            ).scalar()
    finally:
        engine.dispose()


def test_archived_at_column_and_index_exist(postgres_container):
    """sw_022 adds the archived_at column and the live-set partial index."""
    db_url = _prepare_pre_seed_db(postgres_container)
    _apply_sw_022(db_url)

    info = get_column_info(db_url, "connector_registry", "archived_at")
    assert info is not None
    assert "timestamp" in info["data_type"]  # timestamp with time zone
    assert info["is_nullable"] == "YES"

    assert index_exists(db_url, "ix_connector_registry_live")


def test_seed_archives_only_the_four_dead_identities(postgres_container):
    """The seed archives the four dead identities (UUID one by prefix) and no others."""
    db_url = _prepare_pre_seed_db(postgres_container)

    # Four dead identities. endpoint_identity is stored in its full,
    # connector-type-prefixed form (the value connectors emit + cursor_store
    # persists verbatim), so the fixtures — like the seed — use the prefixed
    # form. The google_health user one carries a volatile UUID + resource suffix,
    # matched by the stable ``google_health:user:<owner>:`` prefix.
    dead = [
        ("google_health", "google_health:degraded"),
        (
            "google_health",
            "google_health:user:uniquosity@gmail.com:3f9a1c22-dead-4beef-0000-000000000001:spo2",
        ),
        ("owntracks", "owntracks:unknown"),
        ("home_assistant", "home_assistant:homeassistant.parrot-hen.ts.net:443"),
    ]
    # Live identities that must remain active (archived_at NULL).
    live = [
        ("gmail", "gmail:live@example.com"),
        # different owner → no prefix match
        ("google_health", "google_health:user:someone-else@gmail.com:abc"),
        # canonical owner heartbeat (no trailing ``:`` after the email) → the
        # ``google_health:user:<owner>:`` prefix must NOT archive it.
        ("google_health", "google_health:user:uniquosity@gmail.com"),
        ("owntracks", "owntracks:phone-1"),
        ("home_assistant", "home_assistant:v-on-shenton.ts.net:8123"),
    ]
    for ctype, ident in dead + live:
        _insert_connector(db_url, ctype, ident)

    _apply_sw_022(db_url)

    for ctype, ident in dead:
        assert _archived_at(db_url, ctype, ident) is not None, f"{ctype}/{ident} should be archived"
    for ctype, ident in live:
        assert _archived_at(db_url, ctype, ident) is None, f"{ctype}/{ident} should stay active"


def test_seed_is_idempotent(postgres_container):
    """Re-running the seed statements does not change an already-archived timestamp."""
    db_url = _prepare_pre_seed_db(postgres_container)
    _insert_connector(db_url, "owntracks", "owntracks:unknown")
    _apply_sw_022(db_url)

    first = _archived_at(db_url, "owntracks", "owntracks:unknown")
    assert first is not None

    # Re-run the exact seed UPDATE — the ``archived_at IS NULL`` guard makes it a no-op.
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE connector_registry SET archived_at = now()"
                    " WHERE archived_at IS NULL AND connector_type = 'owntracks'"
                    " AND endpoint_identity = 'owntracks:unknown'"
                )
            )
    finally:
        engine.dispose()

    assert _archived_at(db_url, "owntracks", "owntracks:unknown") == first


def test_downgrade_drops_archived_column_and_index(postgres_container):
    """sw_022 downgrade removes the column and index cleanly."""
    from butlers.migrations import _build_alembic_config

    db_url = _prepare_pre_seed_db(postgres_container)
    _apply_sw_022(db_url)
    assert get_column_info(db_url, "connector_registry", "archived_at") is not None

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.downgrade(config, "switchboard@sw_021")

    assert get_column_info(db_url, "connector_registry", "archived_at") is None
    assert not index_exists(db_url, "ix_connector_registry_live")
