"""Native contracts for the shared invoker memory projection boundary.

No mocked SQL or superuser effective role counts as primitive/ACL evidence.
Spec: openspec/specs/memory-discovery-catalog/spec.md, "Atomic catalog disownment on forget, expiry, and purge".
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from alembic import command
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


@pytest.fixture(scope="module", params=["managed", "migration_only"])
def projection_database(postgres_container, request):
    name = migration_db_name()
    url = create_migrated_test_db(
        postgres_container, name, ["core"], revisions={"core": "core_313"}
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
    asyncio.run(run_migrations(url, chain="core"))
    yield engine, request.param, name
    engine.dispose()


def _seed(conn):
    source, entity = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO public.entities(id,canonical_name,entity_type) "
            "VALUES(CAST(:id AS uuid),'synthetic projection owner','person')"
        ),
        {"id": entity},
    )
    for schema, table in [
        ("chronicler_mem", "facts"),
        ("chronicler", "facts"),
        ("chronicler_mem", "rules"),
    ]:
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


def test_all_runtime_roles_and_bootstrap_replay_keep_invoker_acl(
    projection_database, postgres_container
):
    engine, topology, name = projection_database
    for role in _ROLES:
        with engine.begin() as conn:
            source = _seed(conn)
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            _assert_nonsuperuser_runtime_context(conn, role)
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
        conn.execute(text("SET LOCAL ROLE connector_writer"))
        with pytest.raises(DBAPIError), conn.begin_nested():
            _invoke(conn, str(uuid.uuid4()))
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


def test_default_redaction_argument_and_schema_local_downgrade_retain_boundary(projection_database):
    engine, _, _ = projection_database
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
    # Exercise the actual downgrade then upgrade, not direct Python downgrade().
    url = engine.url.render_as_string(hide_password=False)
    command.downgrade(_build_alembic_config(url, ["core"]), "core_313")
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
        url = create_migrated_test_db(container, name, ["core"], revisions={"core": "core_313"})
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
            asyncio.run(run_migrations(url, chain="core"))
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
