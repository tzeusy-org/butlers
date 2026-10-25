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
from sqlalchemy.engine import make_url

from alembic import command
from butlers.bootstrap_prerequisite import (
    BootstrapPrerequisiteError,
    check_bootstrap_connection,
    check_bootstrap_database,
    prepare_bootstrap_connection,
)
from butlers.db_authority.roles import AuthorityError
from butlers.migrations import (
    _build_alembic_config,
    finalize_authority,
    get_chain_head,
    migration_session,
    run_migrations,
)

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
                "relation_acl": tuple(
                    connection.execute(
                        text(
                            "SELECT n.nspname, c.relname, c.relacl::text FROM pg_catalog.pg_class AS c "
                            "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
                            "WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' ORDER BY 1,2"
                        )
                    )
                ),
                "default_acl": tuple(
                    connection.execute(
                        text(
                            "SELECT defaclrole, defaclnamespace, defaclobjtype, defaclacl::text "
                            "FROM pg_catalog.pg_default_acl ORDER BY 1,2,3"
                        )
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
                    "('bootstrap_prerequisite_sentinel', '{\"preserved\": true}'::jsonb)"
                )
            )
        before = _readback(normal_url)
        # pinned-revision: deliberately exercise the actual historical predecessor.
        assert before["versions"] == ("core_195",)
        with pytest.raises(BootstrapPrerequisiteError):
            asyncio.run(run_migrations(normal_url, chain="core"))
        assert _readback(normal_url) == before
        current = _build_alembic_config(
            normal_url, chains=["core"], target_schema="bootstrap_probe"
        )
        with pytest.raises(BootstrapPrerequisiteError):
            command.upgrade(current, "core_196")
        assert _readback(normal_url) == before
        # Neutralizing the read-only preparation predicate still cannot grant
        # maintenance identity. The independently checked real actor refuses
        # BEFORE even the former schema side effect. This executing successor
        # tightens the old admission-neutralized partial-write observation.
        with patch("butlers.bootstrap_prerequisite.check_bootstrap_connection", return_value=None):
            with pytest.raises(AuthorityError, match="maintenance/ownership prerequisite"):
                command.upgrade(current, "core_196")
        neutralized = _readback(normal_url)
        assert not any(row[0] == "bootstrap_probe" for row in neutralized["schemas"])
        assert neutralized["versions"] == before["versions"]
        assert neutralized["row"] == before["row"]
        # Current preparation requires the actual source compiler on the owned
        # privileged connection. A raw psql callback cannot supply authority.
        # Retain the public helper signature for its existing real caller.
        selected = make_url(normal_url)
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        raw = None
        try:
            raw = admin.raw_connection()
            prepare_bootstrap_connection(
                raw, selected.database, selected.username, source_database="butlers"
            )
            raw.commit()
        finally:
            if raw is not None:
                raw.close()
            admin.dispose()
        check_bootstrap_database(normal_url)
        # Independent catalog negatives restored transactionally before a
        # separate normal-connection positive; no poisoned state reaches upgrade.
        admin = create_engine(admin_url)
        try:
            from butlers.db_authority.apply import quote_identifier

            owner = quote_identifier(selected.username)
            # These eight actual catalog poisons replace the retired bunker,
            # shared-membership and broad-default checks. Each targets a current
            # read-only admission requirement, not an intentionally absent API.
            poisons = (
                f"ALTER ROLE {owner} CREATEDB",
                "ALTER SCHEMA general OWNER TO butler_general_rw",
                "DROP EXTENSION pg_trgm",
                "ALTER ROLE dnd_generation_owner LOGIN",
                f"GRANT dnd_generation_owner TO {owner}",
                "ALTER ROLE dashboard_api RENAME TO poisoned_dashboard_capability",
                "ALTER SCHEMA chronicler_mem RENAME TO poisoned_private_namespace",
                f"ALTER ROLE {owner} CREATEROLE",
            )
            for poison in poisons:
                with admin.connect() as connection:
                    transaction = connection.begin()
                    connection.execute(text(poison))
                    # Real session identity sees this connection's uncommitted
                    # catalog change; a caller-controlled GUC is never used.
                    connection.exec_driver_sql(f"SET SESSION AUTHORIZATION {owner}")
                    with pytest.raises(BootstrapPrerequisiteError):
                        check_bootstrap_connection(connection)
                    transaction.rollback()
                check_bootstrap_database(normal_url)
        finally:
            admin.dispose()
        # An ordinary connection can now pass preparation admission, but it
        # cannot acquire the separate maintenance identity or repair protected
        # functions. Prove refusal and a complete unchanged observer readback.
        prepared_before = _readback(normal_url)
        with pytest.raises(AuthorityError, match="maintenance/ownership prerequisite"):
            asyncio.run(run_migrations(normal_url, chain="core"))
        assert _readback(normal_url) == prepared_before
        with migration_session(admin_url, source_database="butlers") as held:
            asyncio.run(run_migrations(admin_url, chain="core", connection=held))
            finalize_authority(held, source_database="butlers", plans=(("core", "public", None),))
        after = _readback(normal_url)
        assert after["versions"] == (get_chain_head("core"),)
        assert after["row"] == before["row"]
        check_bootstrap_database(normal_url)  # no serving memberships are required
        with migration_session(admin_url, source_database="butlers") as held:
            asyncio.run(run_migrations(admin_url, chain="core", connection=held))
            finalize_authority(held, source_database="butlers", plans=(("core", "public", None),))
        # Genuine second-schema replay uses every canonical predecessor and all
        # revision locations, retaining the first database-global protected state.
        with migration_session(admin_url, source_database="butlers") as held:
            asyncio.run(run_migrations(admin_url, chain="core", schema="general", connection=held))
            finalize_authority(held, source_database="butlers", plans=(("core", "general", None),))
        with normal.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM general.alembic_version")
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
