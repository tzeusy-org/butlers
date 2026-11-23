"""core_368: ``public.owner_watch_sources()``, the owner-watch providers' one perception read.

Real migrated databases only. The function returns the registered perception kinds and when each
last had a row, and nothing else; only the Switchboard role may call it (bu-4c7nvp.1.5.1).
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

_FIRST, _SECOND = "general", "health"
_SWITCHBOARD = "butler_switchboard_rw"
_KINDS = [
    "expect.window",
    "home.sensing",
    "owner.activity",
    "owner.clock",
    "owner.mode",
    "owner.place",
    "owner.suppression",
    "system.dep",
]


def _module():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/core/core_368_owner_watch_sources.py"
    )
    spec = importlib.util.spec_from_file_location("core_368_for_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _as(url: str, role: str, statement: str):
    """Run one statement as ``role`` (SET ROLE from the bootstrap login); return the scalar."""
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
    command.upgrade(_build_alembic_config(url, chains=["core"], target_schema=_FIRST), "core_368")
    bootstrap = migration_bootstrap_db_url(postgres_container, urlparse(url).path.lstrip("/"))
    return url, bootstrap


def test_two_schemas_install_once_and_a_downgrade_keeps_the_function(postgres_container):
    url = create_migration_db(postgres_container, migration_db_name())
    first = _build_alembic_config(url, chains=["core"], target_schema=_FIRST)
    second = _build_alembic_config(url, chains=["core"], target_schema=_SECOND)
    installed = _module()._INSTALLED

    command.upgrade(first, "core_368")
    command.upgrade(second, "core_368")
    command.upgrade(second, "core_368")  # an idempotent replay
    assert _admin(url, installed) is True
    command.downgrade(first, "core_364")
    command.downgrade(second, "core_364")
    assert _admin(url, installed) is True  # additive and read-only: only perception_owner drops it
    command.upgrade(first, "core_368")
    assert _admin(url, installed) is True


def test_the_switchboard_role_reads_kinds_and_last_read_at_only(migrated):
    url, bootstrap = migrated
    raw = _as(bootstrap, _SWITCHBOARD, "SELECT public.owner_watch_sources()::text")
    rows = json.loads(raw)
    assert [row["kind"] for row in rows] == _KINDS
    assert all(set(row) == {"kind", "last_read_at"} for row in rows)  # no content, subject, label
    assert all(row["last_read_at"] is None for row in rows)  # nothing written yet


def test_every_other_role_is_denied(migrated):
    _, bootstrap = migrated
    for role in (
        "butler_general_rw",
        "butler_health_rw",
        "connector_writer",
        "perception_owner_reader",
        "perception_availability_reader",
        "perception_dots_writer",
    ):
        with pytest.raises(DBAPIError) as excinfo:
            _as(bootstrap, role, "SELECT public.owner_watch_sources()")
        assert "permission denied" in str(excinfo.value), role


def test_a_later_broad_grant_still_reads_nothing(migrated):
    _, bootstrap = migrated
    _admin(bootstrap, "GRANT EXECUTE ON FUNCTION public.owner_watch_sources() TO butler_general_rw")
    with pytest.raises(DBAPIError) as excinfo:
        _as(bootstrap, "butler_general_rw", "SELECT public.owner_watch_sources()")
    assert "requires the switchboard identity" in str(excinfo.value)


def test_the_function_is_the_pinned_definer_with_the_exact_acl(migrated):
    url, _ = migrated
    row = _admin(
        url,
        """
        SELECT jsonb_build_object(
            'definer', p.prosecdef, 'owner', pg_get_userbyid(p.proowner),
            'config', p.proconfig, 'language', l.lanname, 'args', p.pronargs,
            'public', EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0),
            'grantees', (SELECT jsonb_agg(DISTINCT pg_get_userbyid(a.grantee)
                                           ORDER BY pg_get_userbyid(a.grantee))
                         FROM aclexplode(p.proacl) a),
            'grantable', EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.is_grantable))::text
        FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang
        WHERE p.oid = 'public.owner_watch_sources()'::regprocedure
        """,
    )
    info = json.loads(row)
    assert info["definer"] is True and info["owner"] == "perception_owner"
    assert info["config"] == ["search_path=pg_catalog, pg_temp"] and info["args"] == 0
    assert info["public"] is False and info["grantable"] is False
    assert sorted(info["grantees"]) == sorted(["perception_owner", _SWITCHBOARD])


def test_drift_is_not_skipped(migrated):
    url, bootstrap = migrated
    installed = _module()._INSTALLED
    assert _admin(url, installed) is True
    _admin(bootstrap, "GRANT EXECUTE ON FUNCTION public.owner_watch_sources() TO butler_general_rw")
    assert _admin(url, installed) is False  # an extra grantee
    _admin(
        bootstrap, "REVOKE EXECUTE ON FUNCTION public.owner_watch_sources() FROM butler_general_rw"
    )
    assert _admin(url, installed) is True
    _admin(
        bootstrap,
        "GRANT EXECUTE ON FUNCTION public.owner_watch_sources() TO butler_switchboard_rw"
        " WITH GRANT OPTION",
    )
    assert _admin(url, installed) is False  # grant-option drift
    _admin(
        bootstrap,
        "REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION public.owner_watch_sources()"
        " FROM butler_switchboard_rw",
    )
    assert _admin(url, installed) is True
    _admin(
        bootstrap,
        "REVOKE EXECUTE ON FUNCTION public.owner_watch_sources() FROM butler_switchboard_rw",
    )
    assert _admin(url, installed) is False  # the Switchboard grant is gone
