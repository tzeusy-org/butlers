"""Native contracts for the shared invoker memory projection boundary.

No mocked SQL or superuser effective role counts as primitive/ACL evidence.
Spec: openspec/specs/memory-discovery-catalog/spec.md, "Atomic catalog disownment on forget, expiry, and purge".
"""

from __future__ import annotations

import asyncio
import runpy
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from butlers import migrations as migrations_module
from butlers.migrations import _build_alembic_config, run_migrations
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migrated_test_db,
    migration_bootstrap_db_url,
    migration_db_name,
)

pytestmark = pytest.mark.integration

_ROLES = (
    "butler_chronicler_rw",
    "butler_concierge_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_lifestyle_rw",
    "butler_messenger_rw",
    "butler_qa_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)
_SIGNATURE = "public.invalidate_memory_projection(text,text,uuid[],timestamptz,boolean,boolean)"
_WHEN = datetime(2000, 1, 1, tzinfo=UTC)


def _apply_core_315_316_bodies(engine) -> None:
    """Apply the two selected revision bodies after fixture-only ACL mutation.

    Alembic's online environment correctly refuses this intentionally
    incomplete bootstrap profile. Keep that production admission intact while
    executing the actual revision bodies and exact version transitions.
    """
    revisions = (
        ("core_315_memory_projection_invalidation.py", "core_314", "core_315"),
        ("core_316_chronicler_projection_read_boundary.py", "core_315", "core_316"),
    )
    repo_root = Path(migrations_module.__file__).resolve().parents[3]
    versions = repo_root / "alembic" / "versions" / "core"
    with engine.begin() as connection:
        current = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
        if (
            current != "core_314"
        ):  # pinned-revision: selected pre-315 disposable boundary proves missing-role refusal before applying actual 315/316 bodies
            raise AssertionError(f"expected disposable core_314 database, got {current!r}")
        migration_context = MigrationContext.configure(connection)
        with Operations.context(migration_context):
            for filename, expected, revision in revisions:
                if current != expected:
                    raise AssertionError(f"expected {expected}, got {current!r}")
                module = runpy.run_path(str(versions / filename))
                upgrade = module.get("upgrade")
                if not callable(upgrade):
                    raise AssertionError(f"{filename} has no callable upgrade body")
                upgrade()
                result = connection.execute(
                    text(
                        "UPDATE public.alembic_version SET version_num=:revision "
                        "WHERE version_num=:expected"
                    ),
                    {"revision": revision, "expected": expected},
                )
                if result.rowcount != 1:
                    raise AssertionError(
                        f"expected one {expected} version row, got {result.rowcount}"
                    )
                current = revision


@pytest.fixture(scope="module", params=["managed", "migration_only"])
def projection_database(postgres_container, request):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container, name, ["core"], revisions={"core": "core_314"}
    )
    engine = create_engine(url)
    if request.param == "migration_only":
        # Start from real migrated objects, remove the managed creator's default
        # and current grants. core314 must supply its own narrow contract.
        admin = create_engine(migration_bootstrap_db_url(postgres_container, name))
        try:
            with admin.begin() as conn:
                # public normally grants USAGE to PUBLIC. Remove that inherited path
                # as well as each bootstrap role grant so core314's USAGE grant is causal.
                conn.execute(text("REVOKE USAGE ON SCHEMA public FROM PUBLIC"))
                for role in _ROLES:
                    conn.execute(text(f"REVOKE USAGE ON SCHEMA public FROM {role}"))
                    assert not conn.scalar(
                        text("SELECT has_schema_privilege(:role,'public','USAGE')"),
                        {"role": role},
                    )
            with engine.begin() as conn:
                for role in _ROLES:
                    conn.execute(text(f"REVOKE ALL ON public.memory_catalog FROM {role}"))
                    conn.execute(text(f"REVOKE ALL ON public.entity_graph_edges FROM {role}"))
                    conn.execute(
                        text(
                            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{engine.url.username}" IN SCHEMA public '
                            f"REVOKE ALL ON TABLES FROM {role}"
                        )
                    )
                    assert not conn.scalar(
                        text("SELECT has_table_privilege(:role,'public.memory_catalog','UPDATE')"),
                        {"role": role},
                    )
        finally:
            admin.dispose()
    if request.param == "migration_only":
        # This fixture deliberately removes the bootstrap USAGE grants after
        # creating a valid core_314 database. Exercise the migration-owned ACL
        # grants directly without asking production admission to accept that
        # intentionally altered bootstrap profile.
        _apply_core_315_316_bodies(engine)
    else:
        asyncio.run(run_migrations(url, chain="core"))
    yield engine, request.param, name
    engine.dispose()


def _seed(conn):
    source, entity = str(uuid.uuid4()), str(uuid.uuid4())
    canonical_name = f"synthetic projection owner {entity}"
    conn.execute(
        text(
            "INSERT INTO public.entities(id,canonical_name,entity_type) "
            "VALUES(CAST(:id AS uuid),:canonical_name,'person')"
        ),
        {"id": entity, "canonical_name": canonical_name},
    )

    # The identity guard requires the actual Chronicler runtime role for this
    # exact source tuple. Migration-only fixtures remove creator defaults, so
    # temporarily grant only the insert privileges needed for faithful fixture
    # seeding and revoke only privileges this helper added.
    temporary_inserts: list[str] = []
    for relation in ("memory_catalog", "entity_graph_edges"):
        allowed = conn.scalar(
            text("SELECT has_table_privilege(:role, :relation, 'INSERT')"),
            {"role": "butler_chronicler_rw", "relation": f"public.{relation}"},
        )
        if not allowed:
            conn.execute(text(f"GRANT INSERT ON public.{relation} TO butler_chronicler_rw"))
            temporary_inserts.append(relation)

    conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
    conn.execute(
        text(
            "INSERT INTO public.memory_catalog(source_schema,source_table,source_id,summary,title,"
            "embedding,search_vector,confidence) VALUES('chronicler_mem','facts',CAST(:id AS uuid),"
            "'synthetic copied detail','synthetic copied title',CAST(:vector AS public.vector),"
            "to_tsvector('simple','synthetic copied detail'),0.75)"
        ),
        {"id": source, "vector": "[" + ",".join(["0.1"] * 384) + "]"},
    )
    conn.execute(
        text(
            "INSERT INTO public.entity_graph_edges(source_schema,source_table,source_id,"
            "subject_entity_id,predicate,object_entity_id) "
            "VALUES('chronicler_mem','facts',CAST(:id AS uuid),CAST(:entity AS uuid),"
            "'knows',CAST(:entity AS uuid))"
        ),
        {"id": source, "entity": entity},
    )
    conn.execute(text("RESET ROLE"))
    for relation in temporary_inserts:
        conn.execute(text(f"REVOKE INSERT ON public.{relation} FROM butler_chronicler_rw"))
        assert not conn.scalar(
            text("SELECT has_table_privilege('butler_chronicler_rw', :relation, 'INSERT')"),
            {"relation": f"public.{relation}"},
        )

    for schema, table in [("chronicler", "facts"), ("chronicler_mem", "rules")]:
        conn.execute(
            text(
                "INSERT INTO public.memory_catalog(source_schema,source_table,source_id,summary,title,"
                "embedding,search_vector,confidence) VALUES(:schema,:table,CAST(:id AS uuid),"
                "'synthetic copied detail','synthetic copied title',CAST(:vector AS public.vector),"
                "to_tsvector('simple','synthetic copied detail'),0.75)"
            ),
            {
                "schema": schema,
                "table": table,
                "id": source,
                "vector": "[" + ",".join(["0.1"] * 384) + "]",
            },
        )
        conn.execute(
            text(
                "INSERT INTO public.entity_graph_edges(source_schema,source_table,source_id,"
                "subject_entity_id,predicate,object_entity_id) VALUES(:schema,:table,CAST(:id AS uuid),"
                "CAST(:entity AS uuid),'knows',CAST(:entity AS uuid))"
            ),
            {"schema": schema, "table": table, "id": source, "entity": entity},
        )
    return source


def _rows(conn, relation, source):
    assert relation in {"memory_catalog", "entity_graph_edges"}
    return (
        conn.execute(
            text(
                f"SELECT * FROM public.{relation} WHERE source_id=CAST(:id AS uuid) "
                "ORDER BY source_schema,source_table"
            ),
            {"id": source},
        )
        .mappings()
        .all()
    )


def _visibility(conn, signature, row_id):
    function = signature.split("(", 1)[0]
    return conn.scalar(text(f"SELECT {function}(CAST(:id AS uuid))"), {"id": row_id})


def _invoke(conn, source, graph=True, redact=False):
    conn.execute(
        text(
            "SELECT public.invalidate_memory_projection('chronicler_mem','facts',"
            "ARRAY[CAST(:id AS uuid)],:when,:graph,:redact)"
        ),
        {"id": source, "when": _WHEN, "graph": graph, "redact": redact},
    )


def _assert_nonsuperuser_runtime_context(conn, expected_role):
    assert conn.scalar(text("SELECT current_user")) == expected_role
    assert not conn.scalar(
        text(
            "SELECT bool_or(rolsuper OR rolcreatedb OR rolcreaterole OR "
            "rolreplication OR rolbypassrls) FROM pg_roles "
            "WHERE rolname IN (session_user,current_user)"
        )
    ), "runtime role or session login has elevated cluster authority"


@pytest.mark.parametrize(("graph", "redact"), [(False, False), (True, False), (True, True)])
def test_exact_provenance_modes_and_privacy_positive_copies(projection_database, graph, redact):
    engine, _, _ = projection_database
    with engine.begin() as conn:
        source = _seed(conn)
        before = _rows(conn, "memory_catalog", source)
        edges_before = _rows(conn, "entity_graph_edges", source)
        assert len(before) == len(edges_before) == 3
        copied = next(
            r
            for r in before
            if r["source_schema"] == "chronicler_mem" and r["source_table"] == "facts"
        )
        assert copied["summary"] and copied["title"] and copied["embedding"] is not None
        assert copied["search_vector"] is not None
        conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
        assert not conn.scalar(text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"))
        _invoke(conn, source, graph, redact)
        _invoke(conn, source, graph, redact)  # same supplied time remains idempotent
        after = _rows(conn, "memory_catalog", source)
        for old, new in zip(before, after, strict=True):
            if old is not copied:
                assert old == new
            else:
                assert new["confidence"] == 0 and new["invalid_at"] == _WHEN
                assert new["updated_at"] >= old["updated_at"]
                for column, cleared in [
                    ("summary", ""),
                    ("title", None),
                    ("embedding", None),
                    ("search_vector", None),
                ]:
                    assert new[column] == (cleared if redact else old[column])
        expected = [
            r
            for r in edges_before
            if not (
                graph and r["source_schema"] == "chronicler_mem" and r["source_table"] == "facts"
            )
        ]
        assert _rows(conn, "entity_graph_edges", source) == expected


def test_empty_unmatched_and_invalid_modes_do_not_change_positive_rows(projection_database):
    engine, _, _ = projection_database
    with engine.begin() as conn:
        source = _seed(conn)
        before = _rows(conn, "memory_catalog", source)
        graph_before = _rows(conn, "entity_graph_edges", source)
        conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
        conn.execute(
            text(
                "SELECT public.invalidate_memory_projection('chronicler_mem','facts','{}'::uuid[],"
                "now(),TRUE,FALSE)"
            )
        )
        _invoke(conn, str(uuid.uuid4()))
        for argument in [
            "NULL::text,'facts','{}'::uuid[],now(),TRUE,FALSE",
            "'','facts','{}'::uuid[],now(),TRUE,FALSE",
            "'bad.schema','facts','{}'::uuid[],now(),TRUE,FALSE",
            "'chronicler_mem',NULL::text,'{}'::uuid[],now(),TRUE,FALSE",
            "'chronicler_mem','episodes','{}'::uuid[],now(),TRUE,FALSE",
            "'chronicler_mem','facts',NULL::uuid[],now(),TRUE,FALSE",
            "'chronicler_mem','facts',ARRAY[NULL::uuid],now(),TRUE,FALSE",
            "'chronicler_mem','facts','{}'::uuid[],NULL,TRUE,FALSE",
            "'chronicler_mem','facts','{}'::uuid[],now(),NULL,FALSE",
            "'chronicler_mem','facts','{}'::uuid[],now(),TRUE,NULL",
            "'chronicler_mem','facts','{}'::uuid[],now(),FALSE,TRUE",
        ]:
            with pytest.raises(DBAPIError) as error, conn.begin_nested():
                conn.execute(text(f"SELECT public.invalidate_memory_projection({argument})"))
            assert error.value.orig.pgcode == "22023"
        assert _rows(conn, "memory_catalog", source) == before
        assert _rows(conn, "entity_graph_edges", source) == graph_before


_VISIBILITY_SIGNATURES = (
    "public.chronicler_catalog_row_visible(uuid)",
    "public.chronicler_graph_row_visible(uuid)",
)


def test_row_id_helpers_preserve_foreign_rows_and_fail_closed_without_private_chain(
    projection_database,
):
    engine, _, _ = projection_database
    with engine.begin() as conn:
        source = _seed(conn)
        assert not conn.scalar(text("SELECT to_regclass('chronicler_mem.facts') IS NOT NULL"))
        catalog_rows = (
            conn.execute(
                text(
                    "SELECT id, source_schema, source_table FROM public.memory_catalog "
                    "WHERE source_id=CAST(:id AS uuid)"
                ),
                {"id": source},
            )
            .mappings()
            .all()
        )
        graph_rows = (
            conn.execute(
                text(
                    "SELECT id, source_schema, source_table FROM public.entity_graph_edges "
                    "WHERE source_id=CAST(:id AS uuid)"
                ),
                {"id": source},
            )
            .mappings()
            .all()
        )
        assert len(catalog_rows) == len(graph_rows) == 3
        conn.execute(text("SET LOCAL ROLE butler_concierge_rw"))
        for rows, signature in [
            (catalog_rows, _VISIBILITY_SIGNATURES[0]),
            (graph_rows, _VISIBILITY_SIGNATURES[1]),
        ]:
            for row in rows:
                visible = _visibility(conn, signature, row["id"])
                expected = not (
                    row["source_schema"] == "chronicler_mem" and row["source_table"] == "facts"
                )
                assert visible is expected, (
                    signature,
                    row["source_schema"],
                    row["source_table"],
                )
            assert _visibility(conn, signature, str(uuid.uuid4())) is False


def test_non_chronicler_cannot_mint_or_retarget_private_projection_identity(projection_database):
    engine, _, _ = projection_database
    role = "butler_concierge_rw"
    with engine.begin() as conn:
        source = _seed(conn)
        assert not conn.scalar(
            text("SELECT pg_has_role(:role, 'butler_chronicler_rw', 'MEMBER')"),
            {"role": role},
        )
        catalog_ids = (
            conn.execute(
                text(
                    "SELECT id, source_schema FROM public.memory_catalog "
                    "WHERE source_id=CAST(:id AS uuid)"
                ),
                {"id": source},
            )
            .mappings()
            .all()
        )
        graph_rows = (
            conn.execute(
                text(
                    "SELECT id, source_schema, subject_entity_id FROM public.entity_graph_edges "
                    "WHERE source_id=CAST(:id AS uuid)"
                ),
                {"id": source},
            )
            .mappings()
            .all()
        )
        private_catalog = next(
            row for row in catalog_ids if row["source_schema"] == "chronicler_mem"
        )
        foreign_catalog = next(row for row in catalog_ids if row["source_schema"] == "chronicler")
        private_graph = next(row for row in graph_rows if row["source_schema"] == "chronicler_mem")
        foreign_graph = next(row for row in graph_rows if row["source_schema"] == "chronicler")

        temporary: list[tuple[str, str]] = []
        required = [
            ("memory_catalog", "INSERT"),
            ("memory_catalog", "UPDATE"),
            ("entity_graph_edges", "INSERT"),
            ("entity_graph_edges", "UPDATE"),
        ]
        for relation, privilege in required:
            if not conn.scalar(
                text("SELECT has_table_privilege(:role, :relation, :privilege)"),
                {"role": role, "relation": f"public.{relation}", "privilege": privilege},
            ):
                conn.execute(text(f"GRANT {privilege} ON public.{relation} TO {role}"))
                temporary.append((relation, privilege))
        conn.execute(text(f"SET LOCAL ROLE {role}"))

        attempts = [
            (
                "INSERT INTO public.memory_catalog(source_schema,source_table,source_id) "
                "VALUES('chronicler_mem','facts',CAST(:new_id AS uuid))",
                {"new_id": str(uuid.uuid4())},
            ),
            (
                "UPDATE public.memory_catalog SET source_id=CAST(:new_id AS uuid) "
                "WHERE id=CAST(:row_id AS uuid)",
                {"new_id": str(uuid.uuid4()), "row_id": private_catalog["id"]},
            ),
            (
                "UPDATE public.memory_catalog SET source_schema='chronicler_mem', "
                "source_table='facts', source_id=CAST(:new_id AS uuid) "
                "WHERE id=CAST(:row_id AS uuid)",
                {"new_id": str(uuid.uuid4()), "row_id": foreign_catalog["id"]},
            ),
            (
                "INSERT INTO public.entity_graph_edges(source_schema,source_table,source_id,"
                "subject_entity_id,predicate,object_entity_id) "
                "VALUES('chronicler_mem','facts',CAST(:new_id AS uuid),"
                "CAST(:entity_id AS uuid),'knows',CAST(:entity_id AS uuid))",
                {"new_id": str(uuid.uuid4()), "entity_id": foreign_graph["subject_entity_id"]},
            ),
            (
                "UPDATE public.entity_graph_edges SET source_id=CAST(:new_id AS uuid) "
                "WHERE id=CAST(:row_id AS uuid)",
                {"new_id": str(uuid.uuid4()), "row_id": private_graph["id"]},
            ),
            (
                "UPDATE public.entity_graph_edges SET source_schema='chronicler_mem', "
                "source_table='facts', source_id=CAST(:new_id AS uuid) "
                "WHERE id=CAST(:row_id AS uuid)",
                {"new_id": str(uuid.uuid4()), "row_id": foreign_graph["id"]},
            ),
        ]
        for statement, params in attempts:
            with pytest.raises(DBAPIError) as error, conn.begin_nested():
                conn.execute(text(statement), params)
            assert error.value.orig.pgcode == "42501"
        conn.execute(text("RESET ROLE"))
        for relation, privilege in temporary:
            conn.execute(text(f"REVOKE {privilege} ON public.{relation} FROM {role}"))
            assert not conn.scalar(
                text("SELECT has_table_privilege(:role, :relation, :privilege)"),
                {"role": role, "relation": f"public.{relation}", "privilege": privilege},
            )


def test_private_memory_chain_finalizes_canonical_projection_reader(postgres_container):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container,
        name,
        ["core", "memory"],
        schemas={"memory": "chronicler_mem"},
    )
    engine = create_engine(url)
    owner = "chronicler_memory_owner"
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT to_regclass('chronicler_mem.facts')")) is not None
            assert conn.scalar(
                text("SELECT has_schema_privilege(:role, 'chronicler_mem', 'USAGE')"),
                {"role": owner},
            )
            for column in (
                "id",
                "source_butler",
                "predicate",
                "content_authority",
                "validity",
                "invalid_at",
                "subject",
                "tags",
                "metadata",
            ):
                assert conn.scalar(
                    text(
                        "SELECT has_column_privilege(:role, 'chronicler_mem.facts', "
                        ":column, 'SELECT')"
                    ),
                    {"role": owner, "column": column},
                )
            for column in ("content", "embedding", "search_vector", "confidence"):
                assert not conn.scalar(
                    text(
                        "SELECT has_column_privilege(:role, 'chronicler_mem.facts', "
                        ":column, 'SELECT')"
                    ),
                    {"role": owner, "column": column},
                )
            assert not conn.scalar(
                text("SELECT has_table_privilege(:role, 'chronicler_mem.facts', 'UPDATE')"),
                {"role": owner},
            )
            assert not conn.scalar(
                text("SELECT has_schema_privilege(:role, 'chronicler_mem', 'CREATE')"),
                {"role": owner},
            )
        asyncio.run(run_migrations(url, chain="chronicler", schema="chronicler"))
        with engine.connect() as conn:
            for column in (
                "singleton",
                "timezone",
                "closed_through",
                "replay_epoch",
                "replay_ack",
                "blocked_reason",
            ):
                assert conn.scalar(
                    text(
                        "SELECT has_column_privilege(:role, 'chronicler.daily_repair_control', "
                        ":column, 'SELECT')"
                    ),
                    {"role": owner, "column": column},
                )
            assert conn.scalar(
                text(
                    "SELECT has_column_privilege(:role, "
                    "'chronicler.daily_repair_fact_dispositions', 'local_date', 'SELECT')"
                ),
                {"role": owner},
            )
            assert conn.scalar(
                text(
                    "SELECT has_column_privilege(:role, "
                    "'chronicler.daily_repair_retention_spans', 'cursor_date', 'SELECT')"
                ),
                {"role": owner},
            )
            assert not conn.scalar(
                text(
                    "SELECT has_column_privilege(:role, 'chronicler.daily_repair_control', "
                    "'created_at', 'SELECT')"
                ),
                {"role": owner},
            )
    finally:
        engine.dispose()


def test_all_runtime_roles_and_bootstrap_replay_keep_invoker_acl(
    projection_database, postgres_container
):
    engine, topology, name = projection_database
    for role in _ROLES:
        with engine.begin() as conn:
            source = _seed(conn)
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            for relation, trigger in (
                ("memory_catalog", "chronicler_catalog_identity_guard"),
                ("entity_graph_edges", "chronicler_graph_identity_guard"),
            ):
                with pytest.raises(DBAPIError) as error, conn.begin_nested():
                    conn.execute(text(f"ALTER TABLE public.{relation} DISABLE TRIGGER {trigger}"))
                assert error.value.orig.pgcode == "42501"
            _assert_nonsuperuser_runtime_context(conn, role)
            for signature in _VISIBILITY_SIGNATURES:
                assert conn.scalar(
                    text("SELECT has_function_privilege(current_user,:sig,'EXECUTE')"),
                    {"sig": signature},
                )
            assert conn.scalar(text("SELECT has_schema_privilege('public','USAGE')"))
            for relation, privileges in [
                ("memory_catalog", ["SELECT", "UPDATE"]),
                ("entity_graph_edges", ["SELECT", "DELETE"]),
            ]:
                for privilege in privileges:
                    assert conn.scalar(
                        text("SELECT has_table_privilege(:relation,:privilege)"),
                        {"relation": "public." + relation, "privilege": privilege},
                    )
            _invoke(conn, source)
            assert (
                next(
                    r
                    for r in _rows(conn, "memory_catalog", source)
                    if r["source_schema"] == "chronicler_mem" and r["source_table"] == "facts"
                )["invalid_at"]
                == _WHEN
            )
            if topology == "migration_only":
                assert not conn.scalar(
                    text("SELECT has_table_privilege('public.memory_catalog','DELETE')")
                )
                assert not conn.scalar(
                    text("SELECT has_table_privilege('public.entity_graph_edges','INSERT')")
                )
    with engine.begin() as conn:
        assert not conn.scalar(
            text("SELECT prosecdef FROM pg_proc WHERE oid=to_regprocedure(:sig)"),
            {"sig": _SIGNATURE},
        )
        assert conn.scalar(
            text("SELECT proconfig FROM pg_proc WHERE oid=to_regprocedure(:sig)"),
            {"sig": _SIGNATURE},
        ) == ["search_path=pg_catalog"]
        assert not conn.scalar(
            text(
                "SELECT EXISTS(SELECT 1 FROM pg_proc AS p, "
                "LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) AS a "
                "WHERE p.oid=to_regprocedure(:sig) AND a.grantee=0 AND a.privilege_type='EXECUTE')"
            ),
            {"sig": _SIGNATURE},
        )
        for role in ["connector_writer"]:
            assert not conn.scalar(
                text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),
                {"role": role, "sig": _SIGNATURE},
            )
            for visibility_signature in _VISIBILITY_SIGNATURES:
                assert not conn.scalar(
                    text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),
                    {"role": role, "sig": visibility_signature},
                )
        conn.execute(text("SET LOCAL ROLE connector_writer"))
        with pytest.raises(DBAPIError), conn.begin_nested():
            _invoke(conn, str(uuid.uuid4()))
        for visibility_signature in _VISIBILITY_SIGNATURES:
            with pytest.raises(DBAPIError), conn.begin_nested():
                _visibility(conn, visibility_signature, str(uuid.uuid4()))
    with engine.begin() as conn:
        conn.execute(text(f"REVOKE EXECUTE ON FUNCTION {_SIGNATURE} FROM butler_chronicler_rw"))
        assert not conn.scalar(
            text("SELECT has_function_privilege('butler_chronicler_rw',:sig,'EXECUTE')"),
            {"sig": _SIGNATURE},
        )
    # Replay the actual managed bootstrap, not a fixture mirror of grants.
    # It intentionally restores its preexisting broader public-table topology.
    _bootstrap_migration_prerequisites(
        migration_bootstrap_db_url(postgres_container, name),
        engine.url.username,
    )
    with engine.begin() as conn:
        source = _seed(conn)
        conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
        _invoke(conn, source)
        assert (
            next(
                r
                for r in _rows(conn, "memory_catalog", source)
                if r["source_schema"] == "chronicler_mem" and r["source_table"] == "facts"
            )["invalid_at"]
            == _WHEN
        )
        assert not conn.scalar(
            text("SELECT has_function_privilege(:role,:sig,'EXECUTE')"),
            {"role": "connector_writer", "sig": _SIGNATURE},
        )


def test_default_redaction_argument_and_schema_local_downgrade_retain_boundary(
    projection_database, postgres_container
):
    engine, topology, name = projection_database
    with engine.begin() as conn:
        source = _seed(conn)
        before = _rows(conn, "memory_catalog", source)
        conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
        # Existing five-argument callers resolve the one six-argument signature.
        conn.execute(
            text(
                "SELECT public.invalidate_memory_projection('chronicler_mem','facts',"
                "ARRAY[CAST(:id AS uuid)],:when,TRUE)"
            ),
            {"id": source, "when": _WHEN},
        )
        after = _rows(conn, "memory_catalog", source)
        for old, new in zip(before, after, strict=True):
            if old["source_schema"] == "chronicler_mem" and old["source_table"] == "facts":
                assert new["invalid_at"] == _WHEN
                for field in ("summary", "title", "embedding", "search_vector"):
                    assert new[field] == old[field]
            else:
                assert new == old
    if topology == "migration_only":
        # The direct revision-body seam intentionally left bootstrap defaults
        # restricted. Restore the actual managed bootstrap before exercising
        # Alembic's guarded downgrade/upgrade entry point.
        _bootstrap_migration_prerequisites(
            migration_bootstrap_db_url(postgres_container, name), engine.url.username
        )
    # Exercise the actual downgrade then upgrade, not direct Python downgrade().
    url = engine.url.render_as_string(hide_password=False)
    command.downgrade(_build_alembic_config(url, ["core"]), "core_314")
    with engine.begin() as conn:
        source = _seed(conn)
        conn.execute(text("SET LOCAL ROLE butler_chronicler_rw"))
        _invoke(conn, source)
        assert (
            next(
                r
                for r in _rows(conn, "memory_catalog", source)
                if r["source_schema"] == "chronicler_mem" and r["source_table"] == "facts"
            )["invalid_at"]
            == _WHEN
        )
    asyncio.run(run_migrations(url, chain="core"))


def test_late_created_allowlist_role_receives_actual_bootstrap_replay():
    # Roles are cluster-global. A separate owned container prevents DROP ROLE
    # from interfering with databases using the shared session container.
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17").with_kwargs(shm_size="256m") as container:
        name = migration_db_name()
        # This test mutates a cluster-global role. Its single database must not
        # leave template-database grants behind when DROP ROLE is exercised.
        with pytest.MonkeyPatch.context() as migration_subject:
            migration_subject.setenv("BUTLERS_TEST_DB_TEMPLATES", "0")
            url = create_migrated_test_db(container, name, ["core"], revisions={"core": "core_314"})
        engine = create_engine(url)
        admin = create_engine(migration_bootstrap_db_url(container, name))
        role = "butler_travel_rw"
        try:
            with admin.begin() as conn:
                conn.execute(text(f"DROP OWNED BY {role} CASCADE"))
                conn.execute(text(f"DROP ROLE {role}"))
                assert not conn.scalar(
                    text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"),
                    {"role": role},
                )
            # The deliberately missing required role invalidates normal bootstrap
            # admission. Keep that production refusal, then exercise the selected
            # revision bodies as in the migration_only ACL fixture above.
            from butlers.bootstrap_prerequisite import BootstrapPrerequisiteError

            with pytest.raises(BootstrapPrerequisiteError):
                asyncio.run(run_migrations(url, chain="core"))
            with engine.connect() as conn:
                assert (  # pinned-revision: selected pre-315 disposable boundary proves missing-role refusal before applying actual 315/316 bodies
                    conn.scalar(text("SELECT version_num FROM public.alembic_version"))
                    == "core_314"
                )
                assert conn.scalar(
                    text("SELECT to_regprocedure(:sig) IS NULL"), {"sig": _SIGNATURE}
                )
            _apply_core_315_316_bodies(engine)
            with engine.begin() as conn:
                assert conn.scalar(
                    text("SELECT to_regprocedure(:sig) IS NOT NULL"), {"sig": _SIGNATURE}
                )
                assert not conn.scalar(
                    text("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=:role)"),
                    {"role": role},
                )
            _bootstrap_migration_prerequisites(
                migration_bootstrap_db_url(container, name), engine.url.username
            )
            # Normal online admission is earned only after the real bootstrap
            # replay restores the required role/profile, never by direct bodies.
            asyncio.run(run_migrations(url, chain="core"))
            with engine.begin() as conn:
                source = _seed(conn)
                conn.execute(text(f"SET LOCAL ROLE {role}"))
                _assert_nonsuperuser_runtime_context(conn, role)
                for relation, privilege in [
                    ("memory_catalog", "SELECT"),
                    ("memory_catalog", "UPDATE"),
                    ("entity_graph_edges", "SELECT"),
                    ("entity_graph_edges", "DELETE"),
                ]:
                    assert conn.scalar(
                        text("SELECT has_table_privilege(:relation,:privilege)"),
                        {"relation": "public." + relation, "privilege": privilege},
                    )
                assert conn.scalar(
                    text("SELECT has_function_privilege(:sig,'EXECUTE')"), {"sig": _SIGNATURE}
                )
                _invoke(conn, source)
                assert (
                    next(
                        r
                        for r in _rows(conn, "memory_catalog", source)
                        if r["source_schema"] == "chronicler_mem" and r["source_table"] == "facts"
                    )["invalid_at"]
                    == _WHEN
                )
        finally:
            admin.dispose()
            engine.dispose()


def test_unrelated_role_cannot_execute_even_with_public_schema_access(
    projection_database, postgres_container
):
    engine, _, name = projection_database
    role = "projection_unrelated_" + uuid.uuid4().hex
    admin = create_engine(migration_bootstrap_db_url(postgres_container, name))
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEROLE NOBYPASSRLS'))
            conn.execute(text(f'GRANT "{role}" TO "{engine.url.username}"'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
        with engine.begin() as conn:
            conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            assert conn.scalar(text("SELECT current_user")) == role
            assert not conn.scalar(
                text("SELECT has_function_privilege(:sig,'EXECUTE')"), {"sig": _SIGNATURE}
            )
            with pytest.raises(DBAPIError) as error, conn.begin_nested():
                _invoke(conn, str(uuid.uuid4()))
            assert error.value.orig.pgcode == "42501"
    finally:
        with admin.begin() as conn:
            conn.execute(text(f'DROP OWNED BY "{role}"'))
            conn.execute(text(f'DROP ROLE "{role}"'))
        admin.dispose()


def _chronicler_admin_function_state(conn):
    rows = (
        conn.execute(
            text(
                "SELECT p.oid, p.proname, p.proowner, pg_get_functiondef(p.oid) AS body "
                "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='chronicler_memory_admin' "
                "AND p.proname IN ('install_interface','finalize_interface') "
                "AND p.pronargs=0 ORDER BY p.proname"
            )
        )
        .mappings()
        .all()
    )
    assert len(rows) == 2
    result = []
    for row in rows:
        acl = conn.execute(
            text(
                "SELECT a.grantor,a.grantee,a.privilege_type,a.is_grantable "
                "FROM pg_proc p CROSS JOIN LATERAL "
                "aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
                "WHERE p.oid=:oid ORDER BY 1,2,3,4"
            ),
            {"oid": row["oid"]},
        ).all()
        result.append((row["proname"], row["proowner"], row["body"], tuple(acl)))
    return result


@pytest.mark.parametrize("function", ["install_interface", "finalize_interface"])
def test_chronicler_admin_bootstrap_refuses_wrong_function_owner(postgres_container, function):
    # Function ownership is database-local; no cluster role is created or changed.
    import psycopg2.errors

    name = migration_db_name()
    url = create_migrated_test_db(postgres_container, name, ["core"])
    engine = create_engine(url)
    admin_url = migration_bootstrap_db_url(postgres_container, name)
    admin = create_engine(admin_url)
    signature = f"chronicler_memory_admin.{function}()"
    original_owner = None
    try:
        with admin.connect() as conn:
            before = _chronicler_admin_function_state(conn)
        _bootstrap_migration_prerequisites(admin_url, engine.url.username)
        with admin.connect() as conn:
            assert _chronicler_admin_function_state(conn) == before
        with admin.begin() as conn:
            original_owner = conn.scalar(
                text(
                    "SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE oid=to_regprocedure(:sig)"
                ),
                {"sig": signature},
            )
            had_create = conn.scalar(
                text("SELECT has_schema_privilege(:role,'chronicler_memory_admin','CREATE')"),
                {"role": engine.url.username},
            )
            conn.execute(
                text(f'GRANT CREATE ON SCHEMA chronicler_memory_admin TO "{engine.url.username}"')
            )
            conn.execute(text(f'ALTER FUNCTION {signature} OWNER TO "{engine.url.username}"'))
            if not had_create:
                conn.execute(
                    text(
                        f'REVOKE CREATE ON SCHEMA chronicler_memory_admin FROM "{engine.url.username}"'
                    )
                )
            tampered = _chronicler_admin_function_state(conn)
            assert next(row[1] for row in tampered if row[0] == function) != next(
                row[1] for row in before if row[0] == function
            )
        with pytest.raises(
            psycopg2.errors.RaiseException,
            match="Chronicler memory admin interface function is not owned by the bootstrap role",
        ):
            _bootstrap_migration_prerequisites(admin_url, engine.url.username)
        with admin.connect() as conn:
            assert _chronicler_admin_function_state(conn) == tampered
    finally:
        try:
            if original_owner is not None:
                with admin.begin() as conn:
                    conn.execute(text(f'ALTER FUNCTION {signature} OWNER TO "{original_owner}"'))
                _bootstrap_migration_prerequisites(admin_url, engine.url.username)
                with admin.connect() as conn:
                    assert _chronicler_admin_function_state(conn) == before
        finally:
            try:
                engine.dispose()
            finally:
                admin.dispose()
