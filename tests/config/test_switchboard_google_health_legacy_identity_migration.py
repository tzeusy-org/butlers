"""Regression tests for the Google Health legacy-identity repair (sw_026)."""

from __future__ import annotations

import asyncio
import shutil

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.testing.migration import create_migration_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _prepare_pre_migration_db(postgres_container) -> str:
    """Run core plus the Switchboard chain through the sw_025 predecessor."""
    from butlers.migrations import _build_alembic_config, run_migrations

    db_name = migration_db_name()
    db_url = create_migration_db(postgres_container, db_name)
    asyncio.run(run_migrations(db_url, chain="core"))
    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_025")
    return db_url


def _apply_sw_026(db_url: str) -> None:
    from butlers.migrations import _build_alembic_config

    config = _build_alembic_config(db_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@sw_026")


def _insert_connector(
    db_url: str,
    connector_type: str,
    endpoint_identity: str,
    *,
    archived: bool = False,
) -> None:
    engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO connector_registry"
                    " (connector_type, endpoint_identity, state, archived_at)"
                    " VALUES (:connector_type, :endpoint_identity, 'unknown',"
                    " CASE WHEN :archived THEN now() ELSE NULL END)"
                ),
                {
                    "connector_type": connector_type,
                    "endpoint_identity": endpoint_identity,
                    "archived": archived,
                },
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
                    " WHERE connector_type = :connector_type"
                    " AND endpoint_identity = :endpoint_identity"
                ),
                {
                    "connector_type": connector_type,
                    "endpoint_identity": endpoint_identity,
                },
            ).scalar()
    finally:
        engine.dispose()


def test_archives_all_legacy_resource_identities_without_account_specific_matching(
    postgres_container,
) -> None:
    db_url = _prepare_pre_migration_db(postgres_container)
    account_uuid = "f836cb4d-11e9-4a89-8d7a-caac8e6c0d06"
    legacy_identities = [
        f"google_health:user:owner@example.invalid:{account_uuid}:activity",
        f"google_health:user:second@example.invalid:{account_uuid}:spo2",
    ]
    for identity in legacy_identities:
        _insert_connector(db_url, "google_health", identity)

    _apply_sw_026(db_url)

    for identity in legacy_identities:
        assert _archived_at(db_url, "google_health", identity) is not None


def test_leaves_canonical_and_nonlegacy_identities_active(postgres_container) -> None:
    db_url = _prepare_pre_migration_db(postgres_container)
    account_uuid = "f836cb4d-11e9-4a89-8d7a-caac8e6c0d06"
    active_identities = [
        ("google_health", "google_health:user:owner@example.invalid"),
        ("google_health", f"google_health:user:owner@example.invalid:{account_uuid}:unknown"),
        ("google_health", "google_health:user:owner@example.invalid:not-a-uuid:activity"),
        ("other", f"google_health:user:owner@example.invalid:{account_uuid}:activity"),
    ]
    for connector_type, identity in active_identities:
        _insert_connector(db_url, connector_type, identity)

    _apply_sw_026(db_url)

    for connector_type, identity in active_identities:
        assert _archived_at(db_url, connector_type, identity) is None


def test_preserves_existing_archive_timestamp(postgres_container) -> None:
    db_url = _prepare_pre_migration_db(postgres_container)
    identity = (
        "google_health:user:owner@example.invalid:f836cb4d-11e9-4a89-8d7a-caac8e6c0d06:activity"
    )
    _insert_connector(db_url, "google_health", identity, archived=True)
    before = _archived_at(db_url, "google_health", identity)

    _apply_sw_026(db_url)

    assert _archived_at(db_url, "google_health", identity) == before
