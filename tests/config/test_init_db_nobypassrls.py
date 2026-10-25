"""NOBYPASSRLS for every normal role the managed bootstrap normalizes (bu-kqnum.8.8).

A role created with BYPASSRLS skips the forced row-level-security policies that guard
the DND boundary, so the bootstrap must strip it and the prerequisite check must refuse
a database where a normal role still has it.

The first group is source/unit coverage that runs everywhere. The second group needs a
disposable PostgreSQL (Docker) and runs in CI only.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import create_engine, text

from butlers import bootstrap_prerequisite as prereq
from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError, check_bootstrap_database
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

_INIT_DB = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"

docker_available = shutil.which("docker") is not None


def needs_postgres(func):
    """Real-PostgreSQL tests: integration + db markers, skipped without Docker."""
    for mark in (
        pytest.mark.integration,
        pytest.mark.db,
        pytest.mark.skipif(not docker_available, reason="Docker not available"),
    ):
        func = mark(func)
    return func


# Managed runtime/connector roles; the separately selected ordinary migration
# identity is a refusal precondition rather than an automatic repair target.
_NORMAL_ROLES = ("butler_health_rw", "butler_general_rw", "connector_writer")


# --------------------------------------------------------------------------- #
# Source and unit coverage
# --------------------------------------------------------------------------- #


def test_every_normal_role_normalization_in_init_db_strips_bypassrls():
    source = _INIT_DB.read_text(encoding="utf-8")
    # PostgreSQL concatenates quoted literals separated by a newline. Preserve
    # that actual source grammar instead of truncating the format() statement.
    source = re.sub(r"'\s*\n\s*'", "", source)
    normalizations = re.findall(r"'(ALTER ROLE %I [^']*NOSUPERUSER[^']*)'", source)
    # One source-owned loop covers the exact compiled roles. Counting repeated
    # old SQL spellings would require the retired bootstrap producer to survive.
    from butlers.db_authority.roles import bootstrap_role_manifest

    manifest = bootstrap_role_manifest("butlers", "normal_migration", source_database="butlers")
    assert len(manifest["roles"]) >= 6
    assert len(normalizations) == 1
    missing = [statement for statement in normalizations if "NOBYPASSRLS" not in statement]
    assert missing == []


def _identity_connection(base_profile_result=True):
    """Driver protocol only; execute the actual source compiler/admission query."""
    calls = []

    def execute(statement, params=None):
        calls.append((str(statement), params))
        return SimpleNamespace(one=lambda: (base_profile_result, True, True, True, True))

    def exec_driver_sql(statement):
        if statement != "SELECT current_database(), session_user":
            raise AssertionError("Unexpected admission statement")
        return SimpleNamespace(one=lambda: ("butlers", "normal_migration"))

    return SimpleNamespace(execute=execute, exec_driver_sql=exec_driver_sql, calls=calls, info={})


def _recording_true(monkeypatch, base_profile_result: bool) -> list[tuple[str, dict]]:
    # Kept as a named helper successor, without patching a production validator
    # or reintroducing the removed _true/_BASE_PROFILE compatibility surface.
    connection = _identity_connection(base_profile_result)
    prereq.check_bootstrap_connection(connection)
    return connection.calls


def test_base_profile_covers_bypassrls_for_the_migration_login_and_normal_roles(monkeypatch):
    calls = _recording_true(monkeypatch, True)
    (base,) = [(sql, params) for sql, params in calls if sql == prereq._BOOTSTRAP_PROFILE_SQL]
    sql, params = base
    assert "rolbypassrls" in sql
    assert "rolname=session_user" in sql
    assert {
        "butler_health_rw",
        "connector_writer",
        "butler_chronicler_rw",
        "dashboard_auth_api",
        "credential_store_writer",
    } <= set(json.loads(params["roles"]))


def test_prerequisite_refuses_when_the_base_profile_finds_a_bypassing_role(monkeypatch):
    with pytest.raises(BootstrapPrerequisiteError, match="missing, unsafe or untrusted"):
        _recording_true(monkeypatch, False)


# --------------------------------------------------------------------------- #
# Real PostgreSQL (CI only)
# --------------------------------------------------------------------------- #


@pytest.fixture
def bootstrapped_db(postgres_container):
    """A core-migrated disposable database; yields (db_url, bootstrap_url, migration_user)."""
    db_name = migration_db_name()
    db_url = create_migrated_test_db(postgres_container, db_name, chains=["core"])
    migration_user = unquote(urlparse(db_url).username or "")
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        yield db_url, bootstrap_url, migration_user
    finally:
        # Cluster roles outlive the database; never leave BYPASSRLS on a shared role.
        with admin.connect() as conn:
            for role in (*_NORMAL_ROLES, migration_user):
                conn.execute(text(f'ALTER ROLE "{role}" NOBYPASSRLS'))
        admin.dispose()


def _set_bypass(bootstrap_url: str, roles: tuple[str, ...]) -> None:
    engine = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            for role in roles:
                conn.execute(text(f'ALTER ROLE "{role}" BYPASSRLS'))
    finally:
        engine.dispose()


def _bypassing(bootstrap_url: str, roles: tuple[str, ...]) -> set[str]:
    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT rolname FROM pg_roles WHERE rolbypassrls AND rolname = ANY(:roles)"),
                {"roles": list(roles)},
            )
            return {row[0] for row in rows}
    finally:
        engine.dispose()


def _insight_amendments_owner(bootstrap_url: str) -> str | None:
    engine = create_engine(bootstrap_url)
    try:
        with engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT pg_get_userbyid(relowner) FROM pg_class "
                    "WHERE oid = to_regclass('public.insight_amendments')"
                )
            ).scalar_one_or_none()
    finally:
        engine.dispose()


@needs_postgres
def test_bootstrap_rerun_strips_bypassrls_from_existing_normal_roles(bootstrapped_db):
    _db_url, bootstrap_url, migration_user = bootstrapped_db
    roles = (*_NORMAL_ROLES, migration_user)
    _set_bypass(bootstrap_url, roles)
    assert _bypassing(bootstrap_url, roles) == set(roles)

    # A source-selected migration login is an explicit ordinary-owner
    # prerequisite. The new bootstrap never repairs a privileged migration
    # identity behind a success marker; refusal must precede runtime changes.
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="distinct provisioned migration login required"):
        _bootstrap_migration_prerequisites(bootstrap_url, migration_user)
    assert _bypassing(bootstrap_url, roles) == set(roles)
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'ALTER ROLE "{migration_user}" NOBYPASSRLS'))
    finally:
        admin.dispose()
    # The real operator restores the declared prerequisite; only then may the
    # actual bootstrap normalize the managed runtime roles.
    _bootstrap_migration_prerequisites(bootstrap_url, migration_user)

    assert _bypassing(bootstrap_url, roles) == set()
    # Ownership still ends with the independently ordinary migration login.
    assert _insight_amendments_owner(bootstrap_url) == migration_user
    # Idempotent: a second run is a no-op.
    _bootstrap_migration_prerequisites(bootstrap_url, migration_user)
    assert _bypassing(bootstrap_url, roles) == set()


@needs_postgres
def test_prerequisite_refuses_a_bypassing_role_until_bootstrap_is_rerun(bootstrapped_db):
    db_url, bootstrap_url, migration_user = bootstrapped_db
    check_bootstrap_database(db_url)

    _set_bypass(bootstrap_url, ("butler_health_rw",))
    with pytest.raises(BootstrapPrerequisiteError, match="missing, unsafe or untrusted"):
        check_bootstrap_database(db_url)

    _bootstrap_migration_prerequisites(bootstrap_url, migration_user)
    check_bootstrap_database(db_url)


@needs_postgres
def test_bypassrls_no_longer_opens_the_forced_rls_dnd_boundary(bootstrapped_db):
    """A pre-existing BYPASSRLS role could rewrite the DND row; bootstrap closes that."""
    db_url, bootstrap_url, migration_user = bootstrapped_db
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO public.user_context (
                        signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
                    )
                    VALUES ('dnd', 'canonical', 'general', now(),
                            now() + interval '1 hour', 1.0, NULL)
                    """
                )
            )
    finally:
        admin.dispose()

    def direct_update_rowcount() -> int:
        engine = create_engine(db_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(text('SET ROLE "butler_general_rw"'))
                try:
                    return conn.execute(
                        text(
                            "UPDATE public.user_context SET value = 'rewritten' "
                            "WHERE signal_type = 'dnd' AND set_by_butler = 'general'"
                        )
                    ).rowcount
                finally:
                    conn.execute(text("RESET ROLE"))
        finally:
            engine.dispose()

    _set_bypass(bootstrap_url, ("butler_general_rw",))
    assert direct_update_rowcount() == 1  # the bypass really does open the boundary

    _bootstrap_migration_prerequisites(bootstrap_url, migration_user)
    assert direct_update_rowcount() == 0  # forced RLS binds the role again
