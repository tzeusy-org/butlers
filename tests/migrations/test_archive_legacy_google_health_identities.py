"""Unit coverage for the sw_026 legacy-identity classification boundary."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "roster"
    / "switchboard"
    / "migrations"
    / "026_archive_legacy_google_health_identities.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("switchboard_sw_026", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "identity",
    [
        "google_health:user:owner@example.invalid:f836cb4d-11e9-4a89-8d7a-caac8e6c0d06:activity",
        "google_health:user:second@example.invalid:01234567-89ab-cdef-0123-456789abcdef:spo2",
    ],
)
def test_matches_superseded_resource_scoped_identity(identity: str) -> None:
    migration = _load_migration()

    assert re.fullmatch(migration._LEGACY_GOOGLE_HEALTH_IDENTITY_RE, identity)


@pytest.mark.parametrize(
    "identity",
    [
        "google_health:user:owner@example.invalid",
        "google_health:user:owner@example.invalid:not-a-uuid:activity",
        "google_health:user:owner@example.invalid:f836cb4d-11e9-4a89-8d7a-caac8e6c0d06:unknown",
    ],
)
def test_rejects_current_or_unknown_identity_shapes(identity: str) -> None:
    migration = _load_migration()

    assert re.fullmatch(migration._LEGACY_GOOGLE_HEALTH_IDENTITY_RE, identity) is None


def test_upgrade_sql_is_guarded_and_uses_structural_pattern() -> None:
    migration = _load_migration()
    select_sql = migration._SELECT_CANDIDATE_IDENTITIES_SQL
    update_sql = migration._UPDATE_LEGACY_IDENTITIES_SQL

    assert "archived_at IS NULL" in select_sql
    assert "deleted_at IS NULL" in select_sql
    assert "connector_type = 'google_health'" in select_sql

    assert "archived_at IS NULL" in update_sql
    assert "deleted_at IS NULL" in update_sql
    assert "connector_type = 'google_health'" in update_sql
    assert "endpoint_identity = ANY(:identities)" in update_sql
    assert "endpoint_identity ~" not in select_sql
    assert "endpoint_identity ~" not in update_sql


def test_upgrade_filters_candidates_and_updates_exact_legacy_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    legacy_identity = (
        "google_health:user:owner@example.invalid:f836cb4d-11e9-4a89-8d7a-caac8e6c0d06:activity"
    )
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [
        (legacy_identity,),
        ("google_health:user:owner@example.invalid",),
    ]
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.upgrade()

    assert connection.execute.call_count == 2
    select_call, update_call = connection.execute.call_args_list
    assert str(select_call.args[0]) == migration._SELECT_CANDIDATE_IDENTITIES_SQL
    assert str(update_call.args[0]) == migration._UPDATE_LEGACY_IDENTITIES_SQL
    assert update_call.args[1] == {"identities": [legacy_identity]}


def test_upgrade_skips_update_when_no_candidates_match(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()
    connection = MagicMock()
    connection.execute.return_value.fetchall.return_value = [
        ("google_health:user:owner@example.invalid",),
    ]
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.upgrade()

    connection.execute.assert_called_once()
