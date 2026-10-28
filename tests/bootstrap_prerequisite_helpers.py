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
    engine = _observer_engine(url)
    try:
        with engine.connect() as connection:
            return {
                "function_authority": _function_catalog(connection),
                "functions": tuple(
                    connection.execute(
                        text(
                            "SELECT n.nspname, p.proname, p.proargtypes, p.proowner, p.prosecdef, "
                            "p.proconfig, p.prosrc FROM pg_catalog.pg_proc AS p JOIN pg_catalog.pg_namespace AS n "
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


def _function_catalog(connection) -> dict:
    """Independent physical catalog, including default and inherited EXECUTE."""
    functions = {}
    for row in connection.execute(
        text("""
        SELECT p.oid,n.oid,n.nspname,p.proname,p.proargtypes::text,
          p.prorettype,p.prolang,p.prokind,r.rolname,p.prosecdef,p.proconfig,p.prosrc,
          EXISTS(SELECT FROM pg_catalog.pg_depend d
            WHERE d.classid='pg_catalog.pg_proc'::regclass AND d.objid=p.oid
              AND d.refclassid='pg_catalog.pg_extension'::regclass AND d.deptype='e')
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        JOIN pg_catalog.pg_roles r ON r.oid=p.proowner
        WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
        ORDER BY p.oid
        """)
    ):
        values = tuple(row)
        functions[row[0]] = (*values[:10], tuple(row[10] or ()), *values[11:])
    oids = list(functions)
    params = {"oids": oids}
    queries = {
        "acl": """
          SELECT p.oid,a.grantor,a.grantee,a.privilege_type,a.is_grantable
          FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
          WHERE p.oid=ANY(:oids) ORDER BY 1,2,3,4,5
        """,
        "effective": """
          SELECT p.oid,r.oid,r.rolname,
            pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE'),
            pg_catalog.has_function_privilege(r.oid,p.oid,'EXECUTE WITH GRANT OPTION')
          FROM pg_catalog.pg_proc p CROSS JOIN pg_catalog.pg_roles r
          WHERE p.oid=ANY(:oids) ORDER BY 1,2
        """,
        "dependencies": """
          SELECT classid,objid,objsubid,refclassid,refobjid,refobjsubid,deptype
          FROM pg_catalog.pg_depend WHERE
            (classid='pg_catalog.pg_proc'::regclass AND objid=ANY(:oids)) OR
            (refclassid='pg_catalog.pg_proc'::regclass AND refobjid=ANY(:oids))
          ORDER BY 1,2,3,4,5,6,7
        """,
        "shared_dependencies": """
          SELECT dbid,classid,objid,objsubid,refclassid,refobjid,deptype
          FROM pg_catalog.pg_shdepend WHERE
            dbid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
            AND classid='pg_catalog.pg_proc'::regclass AND objid=ANY(:oids)
          ORDER BY 1,2,3,4,5,6,7
        """,
    }
    result = {"functions": functions}
    for name, sql in queries.items():
        result[name] = tuple(tuple(row) for row in connection.execute(text(sql), params))
    result["roles"] = tuple(
        tuple(row)
        for row in connection.execute(
            text("""
            SELECT oid,rolname,rolsuper,rolinherit,rolcanlogin,rolcreaterole,
              rolcreatedb,rolreplication,rolbypassrls FROM pg_catalog.pg_roles ORDER BY oid
            """)
        )
    )
    result["memberships"] = tuple(
        tuple(row)
        for row in connection.execute(
            text("""
            SELECT roleid,member,grantor,admin_option,inherit_option,set_option
            FROM pg_catalog.pg_auth_members ORDER BY 1,2,3
            """)
        )
    )
    return result


def _observer_engine(url):
    """Own finite read-only operations on the same disposable database."""
    return create_engine(
        url,
        isolation_level="REPEATABLE READ",
        execution_options={"postgresql_readonly": True},
        connect_args={
            "connect_timeout": 5,
            "options": "-c statement_timeout=30000 -c lock_timeout=5000",
        },
    )


def _historical_function_snapshot(admin_url: str, normal_url: str, plans: tuple) -> dict:
    """Source expectations and independent reads; never install or repair."""
    from butlers.db_authority.grants import (
        compile_function_manifest,
        compile_relation_manifest,
        compiled_security_contract,
        deployment_function_plans,
    )
    from butlers.db_authority.interfaces import (
        function_matches,
        inspect_function,
        selected_protected_functions,
    )
    from butlers.db_authority.roles import compile_deployment_profiles

    selected, administrator = make_url(normal_url), make_url(admin_url)
    engine = _observer_engine(admin_url)
    try:
        with engine.connect() as connection:
            identity = tuple(
                connection.execute(
                    text("""
                    SELECT d.oid,d.datname,r.rolname,session_user,current_user
                    FROM pg_catalog.pg_database d
                    JOIN pg_catalog.pg_roles r ON r.oid=d.datdba
                    WHERE d.datname=current_database()
                    """)
                ).one()
            )
            assert identity[1] == selected.database == administrator.database
            assert identity[2] == selected.username != administrator.username
            assert identity[3] == identity[4] == administrator.username
            for chain, schema, revision in plans:
                assert revision is None
                # Identifiers come from this fixed helper's actual producer plans.
                from butlers.db_authority.apply import quote_identifier

                stamp = connection.execute(
                    text(
                        "SELECT version_num FROM "
                        + quote_identifier(schema)
                        + ".alembic_version ORDER BY version_num"
                    )
                ).scalars()
                assert tuple(stamp) == (get_chain_head(chain),), "historical observer stamp"
            profiles = compile_deployment_profiles(identity[1], source_database="butlers")
            required = (
                *compile_function_manifest(profiles, identity[2], plans),
                *selected_protected_functions(profiles, identity[2], plans),
            )
            schemas = {row.schema for row in required}
            known = (
                *compile_function_manifest(
                    profiles,
                    identity[2],
                    (*deployment_function_plans(profiles), *plans),
                ),
                *selected_protected_functions(profiles, identity[2], plans),
            )
            definitions = {}
            for definition in (*known, *required):
                previous = definitions.setdefault(definition.identity, definition)
                assert previous == definition, "historical observer ambiguous source"
            snapshot = _function_catalog(connection)
            matched = {}
            required_ids = {row.identity for row in required}
            for key, definition in definitions.items():
                if definition.schema not in schemas:
                    continue
                actual = inspect_function(connection, definition)
                if actual is None:
                    assert key not in required_ids, "historical observer missing overload"
                    continue
                assert function_matches(connection, definition, actual), (
                    "historical source mismatch"
                )
                assert actual["body"] == definition.body, "historical body mismatch"
                assert actual["owner"] == definition.owner, "historical owner mismatch"
                assert actual["definer"] is definition.security_definer, (
                    "historical definer mismatch"
                )
                assert tuple(actual["config"] or ()) == getattr(
                    definition, "settings", ("search_path=pg_catalog, pg_temp",)
                ), "historical settings mismatch"
                # Independently evaluate every physical role, including inherited
                # rights and owner/superuser grant authority. PUBLIC is not a role.
                allowed = tuple(
                    connection.execute(
                        text("""
                        SELECT r.oid,
                          (r.rolsuper OR pg_catalog.pg_has_role(r.oid,
                            (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:owner),'USAGE')
                            OR :public OR EXISTS(SELECT FROM pg_catalog.pg_roles g
                              WHERE g.rolname=ANY(:executors)
                                AND pg_catalog.pg_has_role(r.oid,g.oid,'USAGE'))),
                          (r.rolsuper OR pg_catalog.pg_has_role(r.oid,
                            (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:owner),'USAGE'))
                        FROM pg_catalog.pg_roles r ORDER BY r.oid
                        """),
                        {
                            "owner": definition.owner,
                            "public": "PUBLIC" in definition.executors,
                            "executors": list(definition.executors),
                        },
                    )
                )
                effective = tuple(
                    (row[1], row[3], row[4])
                    for row in snapshot["effective"]
                    if row[0] == actual["oid"]
                )
                assert effective == tuple(tuple(row) for row in allowed), "historical effective ACL"
                matched[actual["oid"]] = definition
            for oid, row in snapshot["functions"].items():
                assert row[2] not in schemas or row[12] or oid in matched, (
                    "historical observer unknown managed overload"
                )
            manifest = compile_relation_manifest(
                identity[1], identity[2], identity[3], source_database="butlers"
            )
            snapshot["identity"] = identity
            snapshot["matched"] = matched
            snapshot["source_triggers"] = compiled_security_contract(manifest)["triggers"]
            snapshot["source_relations"] = {
                (row.schema, row.name): (row.owner, row.kind.value) for row in manifest.relations
            }
            snapshot["trigger_edges"] = tuple(
                dict(row)
                for row in connection.execute(
                    text("""
                    SELECT t.oid,n.nspname AS schema,c.relname AS "table",t.tgname AS name,
                      t.tgtype AS type,t.tgenabled AS enabled,t.tgfoid AS function_oid,
                      fn.nspname AS function_schema,p.proname AS function_name,
                      pg_catalog.oidvectortypes(p.proargtypes) AS signature,
                      ARRAY(SELECT a.attname FROM unnest(t.tgattr) WITH ORDINALITY x(num,pos)
                        JOIN pg_catalog.pg_attribute a ON a.attrelid=t.tgrelid
                          AND a.attnum=x.num ORDER BY x.pos) AS columns,
                      pg_catalog.pg_get_expr(t.tgqual,t.tgrelid) AS "when",
                      t.tgconstraint<>0 AS "constraint",t.tgdeferrable AS deferrable,
                      t.tginitdeferred AS deferred,t.tgisinternal AS internal,
                      t.tgnargs AS nargs,t.tgargs AS args,r.rolname AS owner,
                      c.relkind AS kind,c.relispartition AS partition
                    FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                    JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    JOIN pg_catalog.pg_roles r ON r.oid=c.relowner
                    JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                    JOIN pg_catalog.pg_namespace fn ON fn.oid=p.pronamespace
                    WHERE NOT t.tgisinternal AND n.nspname !~ '^pg_' ORDER BY t.oid
                    """)
                ).mappings()
            )
            snapshot["catalog_classes"] = tuple(
                connection.execute(
                    text("""
                    SELECT 'pg_catalog.pg_proc'::regclass::oid,
                      'pg_catalog.pg_trigger'::regclass::oid
                    """)
                ).one()
            )
            return snapshot
    finally:
        engine.dispose()


def _assert_general_function_replay(before: dict, after: dict) -> None:
    """Preserve historical functions; admit only exact source-bound trigger edges."""
    from butlers.db_authority.grants import security_expression_tokens

    assert before["identity"] == after["identity"], "historical database identity changed"
    assert before["roles"] == after["roles"], "historical roles changed"
    assert before["memberships"] == after["memberships"], "historical memberships changed"
    prior_oids = set(before["functions"])
    for oid, row in before["functions"].items():
        assert after["functions"].get(oid) == row, "historical overload changed"
    for key in ("acl", "effective", "shared_dependencies"):
        assert (
            tuple(
                row
                for row in after[key]
                if row[0 if key != "shared_dependencies" else 2] in prior_oids
            )
            == before[key]
        ), "historical function authority changed"
    proc, trigger = after["catalog_classes"]
    old_edges, new_edges = set(before["dependencies"]), set(after["dependencies"])
    assert old_edges <= new_edges, "historical dependency lost"
    for edge in new_edges - old_edges:
        # Edges unrelated to an existing overload belong to the newly installed
        # source-matched population, not its historical preservation comparison.
        if edge[3] != proc or edge[4] not in prior_oids:
            assert not (edge[0] == proc and edge[1] in prior_oids), (
                "historical outgoing dependency changed"
            )
            continue
        if edge[0] == proc:
            # A matched body does not establish header/default dependencies.
            # No exact source declaration authorizes a new historical target.
            raise AssertionError("historical unknown function dependency")
        elif edge[0] == trigger:
            actual = next((row for row in after["trigger_edges"] if row["oid"] == edge[1]), None)
            assert actual is not None and actual["schema"] == "general"
            assert actual["function_oid"] == edge[4] and edge[2] == edge[5] == 0
            assert edge[6] == "n" and edge[1] not in {row["oid"] for row in before["trigger_edges"]}
            assert actual["kind"] == "r" and actual["partition"] is False
            assert after["source_relations"].get((actual["schema"], actual["table"])) == (
                actual["owner"],
                "table",
            ), "historical unknown general trigger parent"
            assert actual["nargs"] == 0 and bytes(actual["args"]) == b""
            assert any(
                all(actual[key] == expected[key] for key in expected if key != "when")
                and security_expression_tokens(actual["when"]) == expected["when"]
                for expected in after["source_triggers"]
            ), "historical unknown general trigger edge"
        else:
            raise AssertionError("historical unknown general dependency edge")


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
        assert neutralized == before, "neutralized admission changed historical state"
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
                poison_before = _readback(normal_url)
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
                assert _readback(normal_url) == poison_before, (
                    "catalog poison rollback changed state"
                )
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
        observed_plans = (("core", "public", None), ("relationship", "relationship", None))
        finalized_functions = _historical_function_snapshot(admin_url, normal_url, observed_plans)
        after = _readback(normal_url)
        assert after["versions"] == (get_chain_head("core"),)
        assert after["row"] == before["row"]
        check_bootstrap_database(normal_url)  # no serving memberships are required
        with migration_session(admin_url, source_database="butlers") as held:
            asyncio.run(run_migrations(admin_url, chain="core", connection=held))
            finalize_authority(held, source_database="butlers", plans=(("core", "public", None),))
        repeated_functions = _historical_function_snapshot(admin_url, normal_url, observed_plans)
        assert repeated_functions == finalized_functions, "public replay changed function authority"
        assert _readback(normal_url) == after, "public replay changed historical state"
        # Genuine second-schema replay uses every canonical predecessor and all
        # revision locations, retaining the first database-global protected state.
        with migration_session(admin_url, source_database="butlers") as held:
            asyncio.run(run_migrations(admin_url, chain="core", schema="general", connection=held))
            finalize_authority(held, source_database="butlers", plans=(("core", "general", None),))
        general_functions = _historical_function_snapshot(
            admin_url, normal_url, (*observed_plans, ("core", "general", None))
        )
        _assert_general_function_replay(finalized_functions, general_functions)
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
