"""Private-history backup contract (bu-cew60z).

The full backup includes the DND mutation receipts, the generation guard and the
DND-only ``user_context`` rows, plus (once ``bu-dhloi1.9`` lands them) the ten
perception relations. The dump identity is never widened: rows travel only
through fixed ``SECURITY DEFINER`` functions owned by ``dnd_generation_owner``
that are executable solely by two inert ``NOLOGIN`` roles. Activating a login
for those roles is an owner step; until then ``deploy/backup/pg_dump.sh`` logs
the omission instead of silently narrowing the artifact.

Proofs here:

* the dump identity gained no grant, membership or EXECUTE, and the
  ``dnd_generation_owner`` no-membership fence still holds;
* an export/restore round trip on a migrated database reproduces every row of
  every included relation, and restore refuses a non-fresh target;
* every relation registered as perception must be covered by the export
  manifest, so adding the perception tables without exporting them fails loudly.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

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


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    db_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(db_url, chains=["core"]), "core@head")
    return db_url


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
    postgres_container, migrated_db_url: str
) -> None:
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


def _seed_dnd(admin: str) -> None:
    engine = create_engine(admin)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("SET ROLE butler_general_rw")
            mutate = (
                "SELECT public.context_dnd_mutate(%s::uuid, 'general', %s, "
                "now() + interval '{h} hour', %s, 0.5::real, '{{\"k\": [1, null]}}'::jsonb)"
            )
            conn.exec_driver_sql(mutate.format(h=1), (str(uuid.uuid4()), "set", "first"))
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
    postgres_container, migrated_db_url: str
) -> None:
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
    assert len(by_relation["dnd_generation_guard"]) == 1
    assert by_relation["dnd_generation_guard"][0]["row"]["generation"] == 3
    assert [i["row"]["signal_type"] for i in by_relation["user_context"]] == ["dnd"]

    target_url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(target_url, chains=["core"]), "core@head")
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
) -> None:
    url = create_migration_db(postgres_container, migration_db_name())
    command.upgrade(_build_alembic_config(url, chains=["core"]), "core@head")
    admin = _admin_for(postgres_container, url)
    foreign = [
        {
            "ordinal": 3,
            "relation": "user_context",
            "row": {"signal_type": "location", "set_by_butler": "x"},
        }
    ]
    with pytest.raises(Exception, match="malformed or foreign"):
        _restore(admin, foreign)
    with pytest.raises(Exception, match="malformed or foreign"):
        _restore(admin, [{"ordinal": 1, "relation": "pg_authid", "row": {}}])
    assert _dnd_snapshot(admin)["mutations"] == []


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
def test_every_perception_relation_present_is_covered_by_the_export_manifest(
    postgres_container, migrated_db_url: str
) -> None:
    """No silent skip: a perception relation without an exporter fails here.

    ``bu-dhloi1.9`` adds these relations. The change that does so must flip each
    manifest entry to ``exported`` and ship its fixed exporter in the same PR.
    """
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
    postgres_container, migrated_db_url: str, tmp_path: Path, request
) -> None:
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
    live = _backup_with_export_login(postgres_container, migrated_db_url, live_dir, login, password)
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
