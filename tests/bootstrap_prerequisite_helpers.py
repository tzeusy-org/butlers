"""Actual disposable PG controls; no live connection or fixture DSN is emitted."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.bootstrap_prerequisite import (
    BootstrapPrerequisiteError,
    check_bootstrap_connection,
    check_bootstrap_database,
)
from butlers.migrations import _build_alembic_config, get_chain_head, run_migrations

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _ROOT / "tests" / "fixtures" / "bootstrap_prerequisite"


def _historical_runner(tmp_path: Path) -> dict:
    manifest = json.loads((_FIXTURES / "manifest.json").read_text())
    assert manifest["source_git_sha"] == "795d5d158b6a33d12ee77cd64fb761d6042c6b6a"
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((_FIXTURES / filename).read_bytes()).hexdigest() == digest
    old_root = tmp_path / "complete-historical-entrypoints"
    shutil.copytree(
        _ROOT / "alembic", old_root / "alembic", ignore=shutil.ignore_patterns("__pycache__")
    )
    (old_root / "alembic" / "env.py").write_bytes((_FIXTURES / "env_795d.py.txt").read_bytes())
    # Full canonical module/roster migration trees, not hand-copied revisions.
    (old_root / "roster").symlink_to(_ROOT / "roster", target_is_directory=True)
    (old_root / "src" / "butlers").mkdir(parents=True)
    (old_root / "src" / "butlers" / "modules").symlink_to(
        _ROOT / "src" / "butlers" / "modules", target_is_directory=True
    )
    filename = old_root / "src" / "butlers" / "migrations.py"
    filename.write_bytes((_FIXTURES / "migrations_795d.py.txt").read_bytes())
    module = ModuleType("historical_bootstrap_runner")
    module.__file__ = str(filename)
    sys.modules[module.__name__] = module
    try:
        exec(compile(filename.read_text(), str(filename), "exec"), module.__dict__)
    finally:
        sys.modules.pop(module.__name__, None)
    return module.__dict__


def _readback(url: str) -> dict:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                "functions": tuple(
                    connection.execute(
                        text(
                            "SELECT n.nspname, p.proname, p.proargtypes, p.proowner, p.prosecdef, "
                            "p.proconfig FROM pg_catalog.pg_proc AS p JOIN pg_catalog.pg_namespace AS n "
                            "ON n.oid = p.pronamespace WHERE n.nspname !~ '^pg_' "
                            "AND n.nspname <> 'information_schema' ORDER BY 1, 2, 3"
                        )
                    )
                ),
                "versions": tuple(
                    connection.execute(
                        text("SELECT version_num FROM public.alembic_version ORDER BY version_num")
                    ).scalars()
                ),
                "row": connection.execute(
                    text(
                        "SELECT value FROM public.state WHERE key = 'bootstrap_prerequisite_sentinel'"
                    )
                ).scalar_one(),
                "objects": tuple(
                    connection.execute(
                        text("""
                    SELECT n.nspname, c.relname, c.relkind, c.relowner
                    FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n
                      ON n.oid = c.relnamespace
                    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                    ORDER BY 1, 2, 3, 4
                """)
                    )
                ),
                "schemas": tuple(
                    connection.execute(
                        text(
                            "SELECT nspname, nspowner FROM pg_catalog.pg_namespace "
                            "WHERE nspname !~ '^pg_' ORDER BY nspname"
                        )
                    )
                ),
                "extensions": tuple(
                    connection.execute(
                        text(
                            "SELECT extname, extversion, extowner FROM pg_catalog.pg_extension ORDER BY extname"
                        )
                    )
                ),
            }
    finally:
        engine.dispose()


def exercise_historical_bootstrap(postgres_container, tmp_path: Path, prepare, bootstrap) -> None:
    """REQ-database-security-011: genuine old195 → refuse → bootstrap → normal head.

    Includes actual read-only catalog poisoning controls and normal-role denial;
    existing owning-node protected ACL/rollback species remain independent.
    """
    admin_url, normal_url, host, port, admin_user, admin_password = prepare(postgres_container)
    historical = _historical_runner(tmp_path)
    # Fresh online refusal occurs before any historical setup. Missing one
    # extension and a caller-owned marker cannot induce automatic repair.
    admin = create_engine(admin_url)
    try:
        with admin.begin() as connection:
            connection.execute(text("DROP EXTENSION pg_trgm"))
        fresh_before = None
        normal = create_engine(normal_url)
        try:
            with normal.begin() as connection:
                connection.execute(text("CREATE TABLE public.bootstrap_complete (claimed boolean)"))
                connection.execute(text("INSERT INTO public.bootstrap_complete VALUES (true)"))
            with normal.connect() as connection:
                fresh_before = tuple(
                    connection.execute(
                        text("SELECT extname FROM pg_catalog.pg_extension ORDER BY extname")
                    ).scalars()
                )
            with pytest.raises(BootstrapPrerequisiteError):
                asyncio.run(run_migrations(normal_url, chain="core", schema="fresh_probe"))
            with pytest.raises(BootstrapPrerequisiteError):
                command.upgrade(
                    _build_alembic_config(normal_url, target_schema="fresh_probe"), "core_122"
                )
            with normal.connect() as connection:
                assert (
                    tuple(
                        connection.execute(
                            text("SELECT extname FROM pg_catalog.pg_extension ORDER BY extname")
                        ).scalars()
                    )
                    == fresh_before
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace "
                            "WHERE nspname = 'fresh_probe')"
                        )
                    ).scalar_one()
                    is True
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class "
                            "WHERE relname = 'alembic_version')"
                        )
                    ).scalar_one()
                    is True
                )
            # The immutable historical entrypoint, rather than current admission,
            # installs its old ordinary extension prerequisite before old setup.
            historical["_bootstrap_extensions"](normal_url)
        finally:
            normal.dispose()
    finally:
        admin.dispose()
    old_config = historical["_build_alembic_config"](normal_url, chains=["core"])
    command.upgrade(old_config, "core_122")
    asyncio.run(
        historical["run_migrations"](normal_url, chain="relationship", schema="relationship")
    )
    command.upgrade(old_config, "core_195")
    normal = create_engine(normal_url)
    try:
        with normal.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO public.state(key, value) VALUES "
                    "('bootstrap_prerequisite_sentinel', '{\"preserved\":true}'::jsonb)"
                )
            )
        before = _readback(normal_url)
        # pinned-revision: deliberately exercise the actual historical predecessor.
        assert before["versions"] == ("core_195",)
        with pytest.raises(BootstrapPrerequisiteError, match="scripts/init-db.sql"):
            asyncio.run(run_migrations(normal_url, chain="core"))
        assert _readback(normal_url) == before
        current = _build_alembic_config(
            normal_url, chains=["core"], target_schema="bootstrap_probe"
        )
        with pytest.raises(BootstrapPrerequisiteError):
            command.upgrade(current, "core_196")
        assert _readback(normal_url) == before
        # Neutralizing only admission reaches the existing protected guard AFTER
        # the old env creates its schema. This is separate from the no-write proof.
        with patch("butlers.bootstrap_prerequisite.check_bootstrap_connection", return_value=None):
            with pytest.raises(
                Exception, match="restore-drill bootstrap installer is missing or untrusted"
            ):
                command.upgrade(current, "core_196")
        neutralized = _readback(normal_url)
        assert any(row[0] == "bootstrap_probe" for row in neutralized["schemas"])
        assert neutralized["versions"] == before["versions"]
        assert neutralized["row"] == before["row"]
        bootstrap(
            host=host,
            port=port,
            user=admin_user,
            password=admin_password,
            database=admin_url.rsplit("/", 1)[1],
            file_path=_ROOT / "scripts" / "init-db.sql",
        )
        check_bootstrap_database(normal_url)
        # Independent catalog negatives restored transactionally before a
        # separate normal-connection positive; no poisoned state reaches upgrade.
        admin = create_engine(admin_url)
        try:
            poisons = (
                "ALTER ROLE butlers CREATEDB",
                "ALTER SCHEMA restore_drill_executor_admin OWNER TO butlers",
                "ALTER FUNCTION runtime_attention_admin.finalize_interface() RENAME TO forged_finalizer",
                "ALTER FUNCTION restore_drill_executor_admin.install_interface() SECURITY INVOKER",
                "ALTER FUNCTION dnd_generation_admin.finalize_interface() SET search_path TO public",
                "REVOKE butler_general_rw FROM butlers",
                "ALTER DEFAULT PRIVILEGES FOR ROLE butlers IN SCHEMA general "
                "REVOKE INSERT ON TABLES FROM butler_general_rw",
                "ALTER TABLE runtime_attention_admin.bootstrap_configuration OWNER TO butlers",
            )
            for poison in poisons:
                with admin.connect() as connection:
                    transaction = connection.begin()
                    connection.execute(text(poison))
                    # Inspect this connection under actual SET ROLE, retaining
                    # the same uncommitted poisoned catalog for the negative.
                    connection.execute(text("SET LOCAL ROLE butlers"))
                    with pytest.raises(BootstrapPrerequisiteError):
                        check_bootstrap_connection(connection)
                    transaction.rollback()
                check_bootstrap_database(normal_url)
        finally:
            admin.dispose()
        asyncio.run(run_migrations(normal_url, chain="core"))
        after = _readback(normal_url)
        assert after["versions"] == (get_chain_head("core"),)
        assert after["row"] == before["row"]
        check_bootstrap_database(normal_url)  # consumed installer grants accepted
        asyncio.run(run_migrations(normal_url, chain="core"))
        # Genuine second-schema replay uses every canonical predecessor and all
        # revision locations, retaining the first database-global protected state.
        asyncio.run(run_migrations(normal_url, chain="core", schema="bootstrap_probe"))
        with normal.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM bootstrap_probe.alembic_version")
            ).scalar_one() == get_chain_head("core")
            assert (
                connection.execute(
                    text(
                        "SELECT NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb "
                        "FROM pg_catalog.pg_roles WHERE rolname = current_user"
                    )
                ).scalar_one()
                is True
            )
            assert (
                connection.execute(
                    text(
                        "SELECT NOT pg_catalog.pg_has_role(current_user, 'restore_drill_executor', 'MEMBER') "
                        "AND NOT pg_catalog.pg_has_role(current_user, 'restore_drill_executor_owner', 'MEMBER')"
                    )
                ).scalar_one()
                is True
            )
        assert _readback(normal_url)["row"] == before["row"]
    finally:
        normal.dispose()
