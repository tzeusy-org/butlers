"""init-db repairs an existing dashboard_auth_api role (bu-kqnum.17).

The prerequisite profile refuses a bypassing ``dashboard_auth_api``, so the documented
repair (rerun ``scripts/init-db.sql``) must actually normalize it, not only create it.

The first test is source coverage that runs everywhere. The second needs a disposable
PostgreSQL (Docker) and runs in CI only.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from sqlalchemy import create_engine, text

from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError, check_bootstrap_database
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

_INIT_DB = Path(__file__).resolve().parents[2] / "scripts" / "init-db.sql"

docker_available = shutil.which("docker") is not None


def test_init_db_alters_dashboard_auth_api_to_the_least_privilege_profile():
    source = _INIT_DB.read_text(encoding="utf-8")
    statements = re.findall(r"ALTER ROLE dashboard_auth_api\s+([^;]*);", source)
    assert len(statements) == 1
    attributes = set(statements[0].split())
    assert {
        "NOLOGIN",
        "NOINHERIT",
        "NOSUPERUSER",
        "NOCREATEROLE",
        "NOCREATEDB",
        "NOREPLICATION",
        "NOBYPASSRLS",
    } <= attributes


@pytest.mark.integration
@pytest.mark.db
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_bootstrap_rerun_strips_bypassrls_from_an_existing_dashboard_auth_api(postgres_container):
    db_name = migration_db_name()
    db_url = create_migrated_test_db(postgres_container, db_name, chains=["core"])
    migration_user = unquote(urlparse(db_url).username or "")
    bootstrap_url = migration_bootstrap_db_url(postgres_container, db_name)
    admin = create_engine(bootstrap_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text("ALTER ROLE dashboard_auth_api BYPASSRLS"))
        with pytest.raises(BootstrapPrerequisiteError, match="missing or untrusted"):
            check_bootstrap_database(db_url)

        _bootstrap_migration_prerequisites(bootstrap_url, migration_user)

        with admin.connect() as conn:
            bypassing = conn.execute(
                text("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'dashboard_auth_api'")
            ).scalar_one()
        assert bypassing is False
        check_bootstrap_database(db_url)
    finally:
        with admin.connect() as conn:
            # Cluster roles outlive the database; never leave BYPASSRLS on a shared role.
            conn.execute(text("ALTER ROLE dashboard_auth_api NOBYPASSRLS"))
        admin.dispose()
