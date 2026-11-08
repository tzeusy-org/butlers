"""Partial private-history backup foundation (bu-cew60z).

The required full backup includes DND receipts, guard and DND-only context plus
all ten canonical perception relations. Those perception sources are not yet
implemented; an absent-table guard does not prove populated coverage. The dark
producer logs unavailable DND export and does not satisfy full backup acceptance.

The controls below exercise fixed-owner authority, strict staged-row validation,
expired/superseded DND history, and the actual emitted-artifact restore consumer
with a positioned ordinary/private shared-snapshot mutation. They never widen the
dump identity. Synthetic disposable verification is not operational activation,
independent conformance approval, full-source completion or human-hold release.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migration_db,
    migration_db_name,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO_ROOT / "deploy" / "backup" / "private_history_manifest.json"
_SCRIPT = _REPO_ROOT / "deploy" / "backup" / "pg_dump.sh"

docker_available = shutil.which("docker") is not None

_EXPORT_ROLE = "butlers_backup_export"
_RESTORE_ROLE = "butlers_backup_restore"
_DND_RELATIONS = ("dnd_generation_mutations", "dnd_generation_guard", "user_context")


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text())


def test_manifest_lists_the_dnd_and_ten_perception_populations() -> None:
    populations = _manifest()["populations"]
    by_relation = {p["relation"]: p for p in populations}
    assert {r for r, p in by_relation.items() if p["interface"] == "dnd"} == {
        "public.dnd_generation_mutations",
        "public.dnd_generation_guard",
        "public.user_context",
    }
    assert all(p["status"] == "exported" for p in populations if p["interface"] == "dnd")
    perception = {r for r, p in by_relation.items() if p["interface"] == "perception"}
    assert len(perception) == 10
    assert by_relation["public.user_context"]["filter"] == "signal_type = 'dnd'"


def test_script_names_the_export_interface_and_logs_omission_until_activation() -> None:
    script = _SCRIPT.read_text()
    assert "dnd_generation_backup.export_v1()" in script
    assert "BACKUP_EXPORT_USER" in script
    assert "dnd_generation_backup.restore_v1" in script
    assert "NOT in this artifact" in script
    # The dump identity is not the export identity and is never granted into it.
    assert "SET LOCAL ROLE butlers_backup_export" in script
    assert "GRANT butlers_backup_export" not in script


def _admin_for(postgres_container, db_url: str) -> str:
    """Superuser URL for the database named in *db_url*."""
    admin = urlparse(postgres_container.get_connection_url())
    db = (urlparse(db_url).path or "").lstrip("/")
    return admin._replace(path=f"/{db}").geturl()


def _scalar(url: str, sql: str, **params):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).scalar_one()
    finally:
        engine.dispose()


def _rows(url: str, sql: str) -> list[tuple]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql))]
    finally:
        engine.dispose()


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_dump_identity_is_not_widened_and_the_dnd_owner_fence_holds(
    postgres_container, fresh_db
) -> None:
    migrated_db_url = fresh_db("core")
    admin = _admin_for(postgres_container, migrated_db_url)

    # The dump identity (the migration login) has no way into the interface.
    # Evaluated as admin: the dump login cannot even resolve the schema name.
    dump_role = urlparse(migrated_db_url).username
    for sql in (
        "SELECT has_schema_privilege(:r, 'dnd_generation_backup', 'USAGE')",
        "SELECT has_function_privilege(:r, p.oid, 'EXECUTE') FROM pg_proc p "
        "WHERE p.pronamespace = 'dnd_generation_backup'::regnamespace "
        "AND p.proname = 'export_v1'",
        "SELECT has_function_privilege(:r, p.oid, 'EXECUTE') FROM pg_proc p "
        "WHERE p.pronamespace = 'dnd_generation_backup'::regnamespace "
        "AND p.proname = 'restore_v1'",
        "SELECT has_table_privilege(:r, 'public.dnd_generation_mutations', 'SELECT')",
        "SELECT pg_has_role(:r, 'butlers_backup_export', 'MEMBER')",
        "SELECT pg_has_role(:r, 'butlers_backup_restore', 'MEMBER')",
        "SELECT pg_has_role(:r, 'dnd_generation_owner', 'MEMBER')",
    ):
        assert _scalar(admin, sql, r=dump_role) is False, sql

    # Both roles are inert and carry no membership edge in either direction;
    # dnd_generation_owner keeps the no-membership fence from init-db.sql.
    roles = _rows(
        admin,
        "SELECT rolname, rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, "
        "rolreplication, rolbypassrls FROM pg_roles WHERE rolname IN "
        "('butlers_backup_export', 'butlers_backup_restore') ORDER BY 1",
    )
    assert roles == [
        (_EXPORT_ROLE, False, False, False, False, False, False, False),
        (_RESTORE_ROLE, False, False, False, False, False, False, False),
    ]
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM pg_auth_members m WHERE m.roleid IN"
            " (SELECT oid FROM pg_roles WHERE rolname IN"
            "  ('butlers_backup_export','butlers_backup_restore','dnd_generation_owner'))"
            " OR m.member IN (SELECT oid FROM pg_roles WHERE rolname IN"
            "  ('butlers_backup_export','butlers_backup_restore','dnd_generation_owner'))",
        )
        == 0
    )

    # Exactly one fixed definer per role, owned by the DND owner, no PUBLIC EXECUTE.
    functions = _rows(
        admin,
        "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, "
        "(SELECT array_agg(g.name ORDER BY g.name) FROM ("
        "   SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC' "
        "               ELSE pg_get_userbyid(a.grantee) END AS name "
        "   FROM aclexplode(p.proacl) a WHERE a.privilege_type = 'EXECUTE') g) "
        "FROM pg_proc p WHERE p.pronamespace = 'dnd_generation_backup'::regnamespace "
        "ORDER BY 1",
    )
    assert functions == [
        (
            "export_v1",
            "dnd_generation_owner",
            True,
            ["butlers_backup_export", "dnd_generation_owner"],
        ),
        (
            "restore_v1",
            "dnd_generation_owner",
            True,
            ["butlers_backup_restore", "dnd_generation_owner"],
        ),
    ]
    # The existing DND relations keep exactly their owner ACL (no export grant).
    assert (
        _scalar(
            admin,
            "SELECT count(*) FROM pg_class c, LATERAL aclexplode(c.relacl) a "
            "WHERE c.oid = 'public.dnd_generation_mutations'::regclass "
            "AND a.grantee <> c.relowner",
        )
        == 0
    )


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_bootstrap_refuses_hostile_backup_authority_and_clean_rerun_is_exact(
    postgres_container,
    fresh_db,
) -> None:
    url = fresh_db("core")
    admin = _admin_for(postgres_container, url)
    migration_role = urlparse(url).username
    _seed_dnd(admin)
    before = _dnd_snapshot(admin)
    # Real managed bootstrap rerun preserves the clean fixed authority.
    _bootstrap_migration_prerequisites(admin, migration_role)
    assert _dnd_snapshot(admin) == before
    assert len(_export_rows(admin)) == 5
    engine = create_engine(admin)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA dnd_generation_backup CASCADE")
            conn.exec_driver_sql(f'SET LOCAL ROLE "{migration_role}"')
            conn.exec_driver_sql("CREATE SCHEMA dnd_generation_backup")
            conn.exec_driver_sql(
                "CREATE FUNCTION dnd_generation_backup.export_v1() "
                "RETURNS TABLE(ordinal integer,relation_name text,payload jsonb) "
                "LANGUAGE sql AS $$ SELECT 0,''::text,'{}'::jsonb WHERE false $$"
            )
            conn.exec_driver_sql(
                "CREATE FUNCTION dnd_generation_backup.restore_v1(p_rows jsonb) "
                "RETURNS void LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$"
            )
            conn.exec_driver_sql("GRANT USAGE ON SCHEMA dnd_generation_backup TO butler_health_rw")
            conn.exec_driver_sql(
                "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA dnd_generation_backup TO butler_health_rw"
            )
        with pytest.raises(Exception, match="DND backup schema or function authority is untrusted"):
            _bootstrap_migration_prerequisites(admin, migration_role)
        assert _scalar(
            admin,
            "SELECT pg_get_userbyid(nspowner)=:role FROM pg_namespace "
            "WHERE nspname='dnd_generation_backup'",
            role=migration_role,
        )
        with engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL ROLE butler_health_rw")
            assert (
                conn.exec_driver_sql(
                    "SELECT count(*) FROM dnd_generation_backup.export_v1()"
                ).scalar_one()
                == 0
            )
            # The untrusted inert placeholder never became an owner definer.
            assert conn.exec_driver_sql(
                "SELECT bool_and(NOT prosecdef) FROM pg_proc "
                "WHERE pronamespace='dnd_generation_backup'::regnamespace"
            ).scalar_one()
        assert _dnd_snapshot(admin) == before

        # Causal inverse: neutralize only the new adoption check in the real
        # installed installer, on this owned disposable database. PostgreSQL
        # must then demonstrate both retained runtime capabilities, rather than
        # crediting a source-string scan as a security proof.
        with engine.connect() as conn:
            definition = conn.exec_driver_sql(
                "SELECT pg_get_functiondef('dnd_generation_admin.install_backup_interface()'::regprocedure)"
            ).scalar_one()
        assert definition.count("    -- Never adopt migration/runtime-owned lookalikes:") == 1
        assert definition.count("    CREATE SCHEMA IF NOT EXISTS dnd_generation_backup") == 1
        start = definition.index("    -- Never adopt migration/runtime-owned lookalikes:")
        end = definition.index("    CREATE SCHEMA IF NOT EXISTS dnd_generation_backup", start)
        neutralized = definition[:start] + definition[end:]
        assert neutralized[:start] + definition[start:end] + neutralized[start:] == definition
        try:
            with engine.begin() as conn:
                # The driver treats bare % as a parameter marker; the installer
                # body has format() specifiers, so double them for execution.
                conn.exec_driver_sql(neutralized.replace("%", "%%"))
                conn.exec_driver_sql("SELECT dnd_generation_admin.install_backup_interface()")
                conn.exec_driver_sql("SET LOCAL ROLE butler_health_rw")
                assert (
                    conn.exec_driver_sql(
                        "SELECT count(*) FROM dnd_generation_backup.export_v1()"
                    ).scalar_one()
                    == 5
                )
            captured = _export_rows(admin)
            with engine.begin() as conn:
                conn.exec_driver_sql("DELETE FROM public.dnd_generation_mutations")
                conn.exec_driver_sql("DELETE FROM public.user_context WHERE signal_type='dnd'")
                conn.exec_driver_sql("UPDATE public.dnd_generation_guard SET generation=0")
                conn.exec_driver_sql("SET LOCAL ROLE butler_health_rw")
                conn.exec_driver_sql(
                    "SELECT dnd_generation_backup.restore_v1(%s::jsonb)", (json.dumps(captured),)
                )
            assert _dnd_snapshot(admin) == before
        finally:
            with engine.begin() as conn:
                conn.exec_driver_sql(definition.replace("%", "%%"))
                conn.exec_driver_sql("DROP SCHEMA dnd_generation_backup CASCADE")
                conn.exec_driver_sql("SELECT dnd_generation_admin.install_backup_interface()")
        with engine.begin() as conn:
            conn.exec_driver_sql("SET LOCAL ROLE butler_health_rw")
            assert not conn.exec_driver_sql(
                "SELECT has_schema_privilege(current_user,'dnd_generation_backup','USAGE')"
            ).scalar_one()
    finally:
        engine.dispose()


def _seed_dnd(admin: str) -> None:
    engine = create_engine(admin)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_general_rw")
            mutate = (
                "SELECT public.context_dnd_mutate(%s::uuid, 'general', %s, "
                "now() + interval '{h} hour', %s, 0.5::real, '{{\"k\": [1, null]}}'::jsonb)"
            )
            conn.exec_driver_sql(mutate.format(h=-1), (str(uuid.uuid4()), "set", "first"))
            conn.exec_driver_sql(
                "SELECT public.context_dnd_mutate(%s::uuid, 'general', 'clear', "
                "NULL, NULL, NULL, NULL)",
                (str(uuid.uuid4()),),
            )
            conn.exec_driver_sql(mutate.format(h=3), (str(uuid.uuid4()), "set", "second"))
            conn.exec_driver_sql("RESET ROLE")
            # A non-DND signal that must never enter the private-history artifact.
            conn.exec_driver_sql(
                "INSERT INTO public.user_context (signal_type, value, set_by_butler, expires_at)"
                " VALUES ('location', 'office', 'general', now() + interval '1 hour')"
            )
    finally:
        engine.dispose()


def _dnd_snapshot(admin: str) -> dict:
    return {
        "mutations": _rows(
            admin,
            "SELECT to_jsonb(m)::text FROM public.dnd_generation_mutations m ORDER BY generation",
        ),
        "guard": _rows(admin, "SELECT to_jsonb(g)::text FROM public.dnd_generation_guard g"),
        "context": _rows(
            admin,
            "SELECT to_jsonb(c)::text FROM public.user_context c "
            "WHERE signal_type = 'dnd' ORDER BY id",
        ),
    }


def _export_rows(admin: str) -> list[dict]:
    engine = create_engine(admin)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL ROLE {_EXPORT_ROLE}")
            rows = conn.exec_driver_sql(
                "SELECT ordinal, relation_name, payload::text "
                "FROM dnd_generation_backup.export_v1() ORDER BY 1, 3"
            ).all()
    finally:
        engine.dispose()
    return [{"ordinal": o, "relation": r, "row": json.loads(p)} for o, r, p in rows]


def _restore(admin: str, rows: list[dict]) -> None:
    engine = create_engine(admin)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(f"SET LOCAL ROLE {_RESTORE_ROLE}")
            conn.exec_driver_sql(
                "SELECT dnd_generation_backup.restore_v1(%s::jsonb)", (json.dumps(rows),)
            )
    finally:
        engine.dispose()


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_dnd_history_round_trips_and_restore_refuses_a_populated_target(
    postgres_container, fresh_db
) -> None:
    migrated_db_url = fresh_db("core")
    source_admin = _admin_for(postgres_container, migrated_db_url)
    _seed_dnd(source_admin)
    exported = _export_rows(source_admin)

    # Every included relation is present; expired/superseded rows are kept and
    # the non-DND signal is not.
    by_relation: dict[str, list[dict]] = {}
    for item in exported:
        by_relation.setdefault(item["relation"], []).append(item)
    assert set(by_relation) == set(_DND_RELATIONS)
    assert len(by_relation["dnd_generation_mutations"]) == 3
    assert (
        _scalar(
            source_admin,
            "SELECT count(*) FROM public.dnd_generation_mutations "
            "WHERE effective_expires_at < clock_timestamp()",
        )
        == 1
    )
    assert len(by_relation["dnd_generation_guard"]) == 1
    assert by_relation["dnd_generation_guard"][0]["row"]["generation"] == 3
    assert [i["row"]["signal_type"] for i in by_relation["user_context"]] == ["dnd"]

    target_url = fresh_db("core")
    target_admin = _admin_for(postgres_container, target_url)

    _restore(target_admin, exported)
    assert _dnd_snapshot(target_admin) == _dnd_snapshot(source_admin)
    assert (
        _scalar(
            target_admin,
            "SELECT count(*) FROM public.user_context WHERE signal_type = 'location'",
        )
        == 0
    )

    # A target that already holds DND history is refused, unchanged.
    before = _dnd_snapshot(target_admin)
    with pytest.raises(Exception, match="already holds DND history"):
        _restore(target_admin, exported)
    assert _dnd_snapshot(target_admin) == before


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_restore_refuses_foreign_or_malformed_rows(
    postgres_container,
    fresh_db,
) -> None:
    url = fresh_db("core")
    admin = _admin_for(postgres_container, url)
    foreign = [
        {
            "ordinal": 3,
            "relation": "user_context",
            "row": {"signal_type": "location", "set_by_butler": "x"},
        }
    ]
    valid = _export_rows(admin)
    before = _dnd_snapshot(admin)
    malformed = [
        [{}],
        [],
        [None],
        [{"ordinal": 1, "relation": "pg_authid", "row": {}}],
        [{"ordinal": 2, "relation": "dnd_generation_guard"}],
        [{"ordinal": 2, "relation": "dnd_generation_guard", "row": None}],
        [{"ordinal": None, "relation": "dnd_generation_guard", "row": {}}],
        [{"ordinal": 2, "relation": None, "row": {}}],
        [{**valid[0], "ordinal": 1}],
        [{**valid[0], "row": {}}],
        [{**valid[0], "row": {**valid[0]["row"], "guard_id": 2}}],
        [{**valid[0], "row": {**valid[0]["row"], "generation": None}}],
        foreign,
    ]
    for rows in malformed:
        with pytest.raises(Exception, match="malformed or foreign"):
            _restore(admin, rows)
        assert _dnd_snapshot(admin) == before
    _restore(admin, valid)
    assert _dnd_snapshot(admin) == before


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_every_perception_relation_present_is_covered_by_the_export_manifest(
    postgres_container, fresh_db
) -> None:
    """No silent skip: a perception relation without an exporter fails here.

    ``bu-dhloi1.9`` adds these relations. The change that does so must flip each
    manifest entry to ``exported`` and ship its fixed exporter in the same PR.
    """
    migrated_db_url = fresh_db("core")
    admin = _admin_for(postgres_container, migrated_db_url)
    manifest = _manifest()
    registered = {p["relation"]: p for p in manifest["populations"]}
    present = {
        f"{schema}.{name}"
        for schema, name in _rows(
            admin,
            "SELECT n.nspname, c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND (n.nspname LIKE 'perception%' "
            "OR c.relname LIKE 'perception\\_%')",
        )
    }
    present |= {
        relation
        for relation, entry in registered.items()
        if entry["interface"] == "perception"
        and _scalar(admin, "SELECT to_regclass(:name) IS NOT NULL", name=relation)
    }
    unexported = sorted(r for r in present if registered.get(r, {}).get("status") != "exported")
    assert not unexported, (
        "Perception relations exist but are not exported by the private-history "
        f"backup: {unexported}. Add an exporter and set status 'exported' in "
        f"{_MANIFEST.relative_to(_REPO_ROOT)}; do not skip them."
    )
    for relation, entry in registered.items():
        if entry["status"] == "exported" and entry["interface"] != "dnd":
            iface = manifest["interfaces"][entry["interface"]]
            assert _scalar(
                admin, "SELECT to_regprocedure(:fn) IS NOT NULL", fn=iface["export_function"]
            ), f"{relation} is marked exported but its exporter is missing"


def _backup_with_export_login(
    postgres_container, db_url: str, backup_dir: Path, export_user: str | None, export_pw: str
) -> subprocess.CompletedProcess[str]:
    from tests.scripts.test_pg_dump_backup import _backup_command

    extra = {"BACKUP_EXPORT_USER": export_user} if export_user else None
    cmd, env = _backup_command(
        db_url, backup_dir, str(postgres_container.get_exposed_port(5432)), extra_env=extra
    )
    if export_user:
        # Forward the generated password by name, never by value.
        image_at = cmd.index("postgres:17-alpine")
        cmd[image_at:image_at] = ["-e", "BACKUP_EXPORT_PASSWORD"]
        env["BACKUP_EXPORT_PASSWORD"] = export_pw
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_script_logs_the_omission_until_activation_then_stages_the_history(
    postgres_container, fresh_db, tmp_path: Path, request
) -> None:
    migrated_db_url = fresh_db("core")
    admin = _admin_for(postgres_container, migrated_db_url)
    _seed_dnd(admin)

    dark_dir = tmp_path / "dark"
    dark_dir.mkdir()
    dark = _backup_with_export_login(postgres_container, migrated_db_url, dark_dir, None, "")
    assert dark.returncode == 0, dark.stderr
    assert "DND mutation receipts, generation guard and DND context are NOT in this artifact" in (
        dark.stderr
    )
    (dark_artifact,) = dark_dir.glob("butlers_*.sql.gz")
    with gzip.open(dark_artifact, "rt", encoding="utf-8", errors="replace") as handle:
        assert "butlers_dnd_restore_rows" not in handle.read()

    source_before = _dnd_snapshot(admin)
    ordinary_engine = create_engine(admin)
    try:
        with ordinary_engine.begin() as conn:
            ordinary_id = conn.exec_driver_sql(
                "INSERT INTO public.entities(canonical_name,entity_type) "
                "VALUES ('Before backup snapshot','person') RETURNING id"
            ).scalar_one()
    finally:
        ordinary_engine.dispose()

    # Simulate the owner's activation step on this disposable database only.
    login = f"export_{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    db = (urlparse(migrated_db_url).path or "").lstrip("/")
    engine = create_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(f"CREATE ROLE \"{login}\" LOGIN PASSWORD '{password}'")
            conn.exec_driver_sql(f'GRANT {_EXPORT_ROLE} TO "{login}"')
            conn.exec_driver_sql(f'GRANT CONNECT ON DATABASE "{db}" TO "{login}"')
    finally:
        engine.dispose()

    def _deactivate() -> None:
        # The container is shared: leave no login or membership edge behind, so
        # the "no membership edge" fence proof stays order-independent.
        cleanup = create_engine(admin, isolation_level="AUTOCOMMIT")
        try:
            with cleanup.connect() as conn:
                conn.exec_driver_sql(f'REVOKE CONNECT ON DATABASE "{db}" FROM "{login}"')
                conn.exec_driver_sql(f'DROP ROLE "{login}"')
        finally:
            cleanup.dispose()

    request.addfinalizer(_deactivate)

    live_dir = tmp_path / "live"
    live_dir.mkdir()
    from tests.scripts.test_pg_dump_backup import _backup_command

    barrier = tmp_path / "dump-barrier"
    barrier.mkdir()
    wrapper = barrier / "pg_dump"
    wrapper.write_text(
        "#!/bin/sh\nset -eu\n: > /backups/snapshot-ready\n"
        "i=0\nwhile [ ! -e /backups/snapshot-release ]; do\n"
        'i=$((i+1)); [ "$i" -lt 3000 ] || exit 70; sleep 0.01\ndone\n'
        'exec /usr/local/bin/pg_dump "$@"\n'
    )
    wrapper.chmod(0o755)
    cmd, env = _backup_command(
        migrated_db_url,
        live_dir,
        str(postgres_container.get_exposed_port(5432)),
        extra_env={
            "BACKUP_EXPORT_USER": login,
            "PATH": "/backup-test-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        },
        extra_mounts=[(barrier, "/backup-test-bin")],
    )
    image_at = cmd.index("postgres:17-alpine")
    cmd[image_at:image_at] = ["-e", "BACKUP_EXPORT_PASSWORD"]
    env["BACKUP_EXPORT_PASSWORD"] = password
    process = subprocess.Popen(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    release = live_dir / "snapshot-release"
    try:
        deadline = time.monotonic() + 30
        while not (live_dir / "snapshot-ready").exists():
            assert process.poll() is None, "backup exited before the post-snapshot barrier"
            assert time.monotonic() < deadline, "backup did not reach post-snapshot barrier"
            time.sleep(0.05)
        # pg_dump is entered only after the real exported snapshot was opened.
        # Commit one ordinary and one DND mutation after it: neither may leak
        # into its independently connected ordinary/private export consumers.
        changed = create_engine(admin)
        try:
            with changed.begin() as conn:
                conn.exec_driver_sql(
                    "UPDATE public.entities SET canonical_name='After backup snapshot' WHERE id=%s",
                    (ordinary_id,),
                )
                conn.exec_driver_sql("SET LOCAL ROLE butler_general_rw")
                conn.exec_driver_sql(
                    "SELECT public.context_dnd_mutate(%s::uuid,'general','set',"
                    "now()+interval '1 hour','after snapshot',0.5::real,NULL)",
                    (str(uuid.uuid4()),),
                )
        finally:
            changed.dispose()
        assert _dnd_snapshot(admin) != source_before
        release.touch()
        stdout, stderr = process.communicate(timeout=120)
        assert process.returncode == 0, stderr
        live = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
    finally:
        release.touch(exist_ok=True)
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
    assert live.returncode == 0, live.stderr
    assert "NOT in this artifact" not in live.stderr
    (artifact,) = live_dir.glob("butlers_*.sql.gz")
    with gzip.open(artifact, "rt", encoding="utf-8", errors="replace") as handle:
        dump = handle.read()
    assert "-- Butlers private DND history" in dump
    assert "dnd_generation_backup.restore_v1(" in dump
    staged = [
        json.loads(bytes.fromhex(line.split("\t", 2)[2]).decode("utf-8"))
        for line in dump.splitlines()
        if line.startswith(
            ("1\tdnd_generation_mutations\t", "2\tdnd_generation_guard\t", "3\tuser_context\t")
        )
    ]
    assert len(staged) == 3 + 1 + 1
    assert all("location" not in json.dumps(row) for row in staged)

    # Reuse the supported consumer transport and its fresh persistent epoch.
    # Its --drop-existing creates an empty target; no bootstrap schema/data is
    # imported over itself. The source cluster already holds the fenced roles.
    from tests.scripts.test_pg_restore_definer_ownership import (
        _docker_client,
        _run_restore_script,
        _Target,
    )

    control_url = postgres_container.get_connection_url()
    control = urlparse(control_url)
    target = _Target(
        str(postgres_container.get_exposed_port(5432)),
        control.username or "",
        control.password or "",
        control_url,
    )
    target_db = "private_history_restore_" + uuid.uuid4().hex[:8]

    def _drop_target() -> None:
        cleanup = create_engine(control_url, isolation_level="AUTOCOMMIT")
        try:
            with cleanup.connect() as conn:
                conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{target_db}"')
        finally:
            cleanup.dispose()

    request.addfinalizer(_drop_target)
    # The DND relations are excluded from the ordinary dump, so an empty target
    # has none of them. The populated history must then refuse loudly: the
    # restore is never certified with the history silently dropped.
    assert not (artifact.parent / "epoch.json").exists()
    restore = _run_restore_script(artifact, target, target_db)
    assert restore.returncode != 0, restore.stderr + restore.stdout
    assert "incomplete data is not certified" in restore.stderr
    assert "DND relations are absent" in restore.stdout + restore.stderr

    # Into a target bootstrapped by init-db.sql and migrated, the same staged
    # block restores the exact pre-snapshot history through the fixed definer.
    prepared_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(prepared_url, chains=["core"]), "core@head")
    prepared_db = (urlparse(prepared_url).path or "").lstrip("/")
    prepared_admin = _admin_for(postgres_container, prepared_url)
    replay = _docker_client(
        artifact,
        "gunzip -c /backup.sql.gz | sed -n '/^-- Butlers private DND history/,$p' | "
        f"psql -h host.docker.internal -p {target.port} -U {target.login} --no-password "
        f"-d {prepared_db} --quiet -v ON_ERROR_STOP=1",
        password=target.password,
        mount_script=False,
    )
    assert replay.returncode == 0, replay.stderr + replay.stdout
    assert _dnd_snapshot(prepared_admin) == source_before
    assert (
        _scalar(
            prepared_admin,
            "SELECT count(*) FROM public.user_context WHERE signal_type='location'",
        )
        == 0
    )
    assert _scalar(
        prepared_admin,
        "SELECT bool_and(relforcerowsecurity) FROM pg_class "
        "WHERE oid IN ('public.dnd_generation_guard'::regclass,"
        "'public.dnd_generation_mutations'::regclass,'public.user_context'::regclass)",
    )
