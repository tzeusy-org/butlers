"""Full PRIMARY manifest over actual migrated PostgreSQL 17 sources; no subscriber claim.

One module topology amortizes the nine-schema migration cost. Each control owns
and rolls back its changes, so malformed catalog cases do not rebuild 479 tables.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, text

from butlers.knowledge_read.manifest import Manifest, ManifestError, load_manifest
from butlers.migrations import run_migrations
from butlers.migrations.public_mcp import (
    publication_state,
    reconcile_connection,
    validate_catalog,
)
from butlers.testing.migration import (
    _bootstrap_migration_prerequisites,
    create_migration_db,
    migration_bootstrap_db_url,
    migration_db_name,
)
from tests.migrations.test_public_projection_source import logical_postgres  # noqa: F401

pytestmark = pytest.mark.integration
_MANAGED = (
    "butlers_projection_publisher",
    "mcp_projection_owner",
    "mcp_public_ro",
    "mcp_proposals_reader",
    "mcp_subscription_owner",
)


@pytest.fixture(scope="module")
def manifest_primary(logical_postgres):  # noqa: F811
    name = migration_db_name()
    url = create_migration_db(logical_postgres, name)
    engine = create_engine(url)
    admin_url = migration_bootstrap_db_url(logical_postgres, name)
    admin = create_engine(admin_url)
    with engine.connect() as conn:
        migration_role = conn.execute(text("SELECT current_user")).scalar_one()
        assert conn.execute(text("SELECT to_regclass('mcp_surface.controls')")).scalar_one() is None
    control = create_engine(logical_postgres.get_connection_url(), isolation_level="AUTOCOMMIT")
    try:
        with control.connect() as conn:
            for role in _MANAGED:
                if not conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}
                ).scalar():
                    conn.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOREPLICATION')
            if not conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname='butlers_public'")
            ).scalar():
                conn.exec_driver_sql("CREATE DATABASE butlers_public")
        _bootstrap_migration_prerequisites(admin_url, migration_role)

        async def install():
            for domain in (
                "general",
                "lifestyle",
                "education",
                "travel",
                "relationship",
                "finance",
                "health",
            ):
                await run_migrations(url, chain="core", schema=domain)
                if domain in ("travel", "relationship", "finance", "health"):
                    await run_migrations(url, chain=domain, schema=domain)
                await run_migrations(url, chain="memory", schema=domain)
            await run_migrations(url, chain="core", schema="chronicler")
            await run_migrations(url, chain="core", schema="chronicler_mem")
            await run_migrations(url, chain="memory", schema="chronicler_mem")
            await run_migrations(url, chain="chronicler", schema="chronicler")

        asyncio.run(install())
        yield engine, admin, migration_role
    finally:
        engine.dispose()
        admin.dispose()
        control.dispose()


@pytest.fixture
def primary(manifest_primary):
    engine, _, _ = manifest_primary
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def test_complete_manifest_reconcile_is_real_noop_and_primary_only(primary):
    manifest = load_manifest()
    role = primary.execute(
        text("SELECT rolsuper,rolreplication FROM pg_roles WHERE rolname=current_user")
    ).one()
    assert role == (False, False)
    validate_catalog(primary, manifest)
    primary.exec_driver_sql(
        "CREATE PUBLICATION unrelated_projection FOR TABLE mcp_surface.controls"
    )
    unrelated = primary.execute(
        text("SELECT oid,xmin::text FROM pg_publication WHERE pubname='unrelated_projection'")
    ).one()
    assert reconcile_connection(primary, manifest) is True
    assert (
        primary.execute(
            text("SELECT oid,xmin::text FROM pg_publication WHERE pubname='unrelated_projection'")
        ).one()
        == unrelated
    )
    assert publication_state(primary, manifest).exact
    before = primary.execute(
        text("SELECT oid,xmin::text FROM pg_publication WHERE pubname='butlers_public_v1'")
    ).one()
    state = primary.execute(text("SELECT digest,xmin::text FROM mcp_surface.manifest_state")).one()
    assert state[0] == manifest.digest
    assert reconcile_connection(primary, manifest) is False
    assert (
        primary.execute(
            text("SELECT oid,xmin::text FROM pg_publication WHERE pubname='butlers_public_v1'")
        ).one()
        == before
    )
    assert (
        primary.execute(text("SELECT digest,xmin::text FROM mcp_surface.manifest_state")).one()
        == state
    )
    assert primary.execute(text("SELECT count(*) FROM pg_subscription")).scalar_one() == 0
    assert primary.execute(text("SELECT count(*) FROM pg_replication_slots")).scalar_one() == 0
    primary.exec_driver_sql(
        "ALTER TABLE finance.transactions ADD COLUMN private_after_manifest text"
    )
    assert reconcile_connection(primary, manifest) is False
    actual = primary.execute(
        text(
            "SELECT attnames FROM pg_publication_tables WHERE pubname='butlers_public_v1' AND schemaname='finance' AND tablename='transactions'"
        )
    ).scalar_one()
    assert "private_after_manifest" not in actual
    assert len(
        primary.execute(
            text("SELECT * FROM pg_publication_tables WHERE pubname='butlers_public_v1'")
        ).all()
    ) == len(manifest.tables)


def test_publication_action_explicit_columns_filters_and_wildcards(primary):
    manifest = load_manifest()
    reconcile_connection(primary, manifest)
    primary.exec_driver_sql("ALTER PUBLICATION butlers_public_v1 SET (publish='insert')")
    assert publication_state(primary, manifest).reason == "publication action flags"
    assert reconcile_connection(primary, manifest)
    assert publication_state(primary, manifest).exact
    # Every current metadata column is listed, so attnames alone cannot expose this drift.
    primary.exec_driver_sql("ALTER PUBLICATION butlers_public_v1 DROP TABLE mcp_surface.controls")
    primary.exec_driver_sql("ALTER PUBLICATION butlers_public_v1 ADD TABLE mcp_surface.controls")
    assert primary.execute(
        text(
            "SELECT attnames FROM pg_publication_tables WHERE pubname='butlers_public_v1' AND schemaname='mcp_surface' AND tablename='controls'"
        )
    ).scalar_one() == ["domain", "state", "changed_at"]
    assert "explicit column" in publication_state(primary, manifest).reason
    assert reconcile_connection(primary, manifest)
    primary.exec_driver_sql("ALTER TABLE mcp_surface.controls ADD COLUMN private_actor text")
    assert publication_state(primary, manifest).exact
    actual = primary.execute(
        text(
            "SELECT attnames FROM pg_publication_tables WHERE pubname='butlers_public_v1' AND schemaname='mcp_surface' AND tablename='controls'"
        )
    ).scalar_one()
    assert "private_actor" not in actual
    primary.exec_driver_sql("ALTER PUBLICATION butlers_public_v1 DROP TABLE public.entities")
    primary.exec_driver_sql(
        "ALTER PUBLICATION butlers_public_v1 ADD TABLE public.entities (id,canonical_name,entity_type,aliases,roles,merged_into,deleted,unidentified,placeholder_name)"
    )
    assert "row filter" in publication_state(primary, manifest).reason
    assert reconcile_connection(primary, manifest)


def test_wildcard_provenance_refuses_even_when_table_rows_look_finite(manifest_primary):
    _, admin, migration_role = manifest_primary
    manifest = load_manifest()
    with admin.connect() as conn, conn.begin():
        conn.exec_driver_sql(
            "CREATE PUBLICATION butlers_public_v1 FOR TABLES IN SCHEMA mcp_surface"
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM pg_publication_tables WHERE pubname='butlers_public_v1'")
            ).scalar_one()
            == 2
        )
        with pytest.raises(ManifestError, match="wildcard"):
            publication_state(conn, manifest)
        conn.exec_driver_sql(f'SET LOCAL ROLE "{migration_role}"')
        with pytest.raises(ManifestError, match="unsupported owner"):
            reconcile_connection(conn, manifest)
        conn.exec_driver_sql("RESET ROLE")
        assert (
            conn.execute(text("SELECT count(*) FROM mcp_surface.manifest_state")).scalar_one() == 0
        )
        conn.exec_driver_sql("DROP PUBLICATION butlers_public_v1")
        conn.exec_driver_sql("CREATE PUBLICATION butlers_public_v1 FOR ALL TABLES")
        with pytest.raises(ManifestError, match="wildcard"):
            publication_state(conn, manifest)
        conn.rollback()


def test_catalog_guards_precede_ddl_with_healthy_companions(primary):
    manifest = load_manifest()
    first = next(t for t in manifest.tables if t.source == "finance.transactions")
    cases = [
        (
            replace(
                first, types=tuple((c, "integer" if c == "id" else typ) for c, typ in first.types)
            ),
            "type/generated",
        ),
        (
            replace(
                first,
                columns=first.columns + ("missing",),
                types=first.types + (("missing", "text"),),
            ),
            "missing column",
        ),
        (replace(first, key=("description",)), "primary key"),
        (replace(first, identity=("description",)), "nullable identity"),
        (replace(first, json_identifiers=("id.external_ref",)), "JSON source"),
    ]
    for broken, reason in cases:
        bad = Manifest(tuple(broken if t.source == first.source else t for t in manifest.tables))
        with pytest.raises(ManifestError, match=reason):
            reconcile_connection(primary, bad)
        assert not publication_state(primary, manifest).exists
        validate_catalog(primary, manifest)
    with primary.begin_nested() as savepoint:
        primary.exec_driver_sql("ALTER TABLE finance.transactions REPLICA IDENTITY FULL")
        with pytest.raises(ManifestError, match="replica identity"):
            reconcile_connection(primary, manifest)
        savepoint.rollback()
    with primary.begin_nested() as savepoint:
        table = next(t for t in manifest.tables if t.source == "relationship.entity_facts")
        bad = replace(
            table,
            filters=tuple((k, ("has-email",) if k == "predicate" else v) for k, v in table.filters),
        )
        with pytest.raises(ManifestError, match="contact/override/unknown"):
            validate_catalog(primary, Manifest((bad,)))
        savepoint.rollback()
    assert reconcile_connection(primary, manifest)


def test_publication_and_digest_roll_back_together(manifest_primary, monkeypatch):
    from butlers.migrations import public_mcp as projection

    engine, _, _ = manifest_primary
    manifest = load_manifest()
    real = projection.publication_state
    observations = 0

    def refused_readback(conn, plan):
        nonlocal observations
        observations += 1
        value = real(conn, plan)
        if observations == 2:
            assert value.exact
            return replace(value, exact=False, reason="positioned readback failure")
        return value

    monkeypatch.setattr(projection, "publication_state", refused_readback)
    with pytest.raises(ManifestError, match="readback differs"):
        with engine.begin() as conn:
            reconcile_connection(conn, manifest)
    with engine.connect() as conn:
        assert not real(conn, manifest).exists
        assert (
            conn.execute(text("SELECT count(*) FROM mcp_surface.manifest_state")).scalar_one() == 0
        )
    monkeypatch.setattr(projection, "publication_state", real)
    with engine.connect() as conn, conn.begin():
        assert reconcile_connection(conn, manifest)
        conn.rollback()


def test_fact_and_entity_filter_iud_uses_actual_publication_expressions(primary):
    """Primary SQL/RI and stored filters, without claiming subscriber propagation."""
    from butlers.knowledge_read.manifest import qualified

    manifest = load_manifest()
    reconcile_connection(primary, manifest)
    expressions = dict(
        primary.execute(
            text("""SELECT n.nspname||'.'||c.relname,
        pg_get_expr(r.prqual,r.prrelid) FROM pg_publication_rel r
        JOIN pg_class c ON c.oid=r.prrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_publication p ON p.oid=r.prpubid WHERE p.pubname='butlers_public_v1'
    """)
        ).all()
    )
    for table in manifest.tables:
        if not table.source.endswith(".facts"):
            continue
        relation = qualified(table.source)
        predicate = "knows" if table.domain not in ("finance", "health") else "preference"
        row_id = primary.execute(
            text(f"""INSERT INTO {relation}
            (subject,predicate,content,sensitivity,validity)
            VALUES ('manifest-control',:predicate,'allowed synthetic content','normal','active')
            RETURNING id"""),
            {"predicate": predicate},
        ).scalar_one()

        def visible(relation=relation, row_id=row_id, source=table.source):
            return primary.execute(
                text(
                    f"SELECT EXISTS(SELECT 1 FROM {relation} WHERE id=:id AND ({expressions[source]}))"
                ),
                {"id": row_id},
            ).scalar_one()

        assert visible()
        primary.execute(
            text(f"UPDATE {relation} SET sensitivity='sensitive' WHERE id=:id"), {"id": row_id}
        )
        assert visible() is (table.domain in ("finance", "health"))
        primary.execute(
            text(f"UPDATE {relation} SET sensitivity='normal',validity='retracted' WHERE id=:id"),
            {"id": row_id},
        )
        assert not visible()
        primary.execute(
            text(f"UPDATE {relation} SET validity='active' WHERE id=:id"), {"id": row_id}
        )
        assert visible()
        primary.execute(text(f"DELETE FROM {relation} WHERE id=:id"), {"id": row_id})
        assert not visible()
    entity_id = primary.execute(
        text(
            "INSERT INTO public.entities(canonical_name,entity_type) VALUES ('Manifest control','person') RETURNING id"
        )
    ).scalar_one()

    def entity_visible():
        return primary.execute(
            text(
                f"SELECT EXISTS(SELECT 1 FROM public.entities WHERE id=:id AND ({expressions['public.entities']}))"
            ),
            {"id": entity_id},
        ).scalar_one()

    assert entity_visible()
    # Exercise the real source fields. The installed BEFORE trigger deliberately
    # overwrites direct edits of the public boolean shadow columns.
    changes = (
        ('{"tombstone":true}', "Manifest control", "deleted"),
        ('{"unidentified":true}', "Manifest control", "unidentified"),
        (
            '{"source_channel":"test","source_value":"synthetic"}',
            "Unknown (test synthetic)",
            "placeholder_name",
        ),
    )
    for metadata, name, column in changes:
        primary.execute(
            text(
                "UPDATE public.entities SET canonical_name=:name,metadata=CAST(:metadata AS jsonb) WHERE id=:id"
            ),
            {"id": entity_id, "name": name, "metadata": metadata},
        )
        assert (
            primary.execute(
                text(f"SELECT {column} FROM public.entities WHERE id=:id"), {"id": entity_id}
            ).scalar_one()
            is True
        )
        assert not entity_visible()
        primary.execute(
            text(
                "UPDATE public.entities SET canonical_name='Manifest control',metadata='{}'::jsonb WHERE id=:id"
            ),
            {"id": entity_id},
        )
        assert entity_visible()
    primary.execute(text("DELETE FROM public.entities WHERE id=:id"), {"id": entity_id})
    assert not entity_visible()


def test_reconcile_serialization_is_bounded_and_rollback_releases_it(manifest_primary):
    from sqlalchemy.exc import OperationalError

    from butlers.migrations import public_mcp

    engine, admin, _ = manifest_primary
    manifest = load_manifest()
    with admin.connect() as blocker, blocker.begin():
        blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": public_mcp._LOCK})
        with pytest.raises(OperationalError) as refused:
            with engine.begin() as conn:
                reconcile_connection(conn, manifest)
        assert refused.value.orig.pgcode == "55P03"
    with engine.connect() as conn, conn.begin():
        assert not publication_state(conn, manifest).exists
        assert reconcile_connection(conn, manifest)
        conn.rollback()


def test_proposal_status_source_roundtrip_and_payload_free_catalog(manifest_primary):
    """Real rel_041, not a fixture-only table or an implementation of task 5.2."""
    import uuid

    from sqlalchemy.exc import IntegrityError

    from alembic import command
    from butlers.migrations import _build_alembic_config, get_chain_head

    engine, _, _ = manifest_primary
    manifest = load_manifest()
    proposal = next(t for t in manifest.tables if t.domain == "proposals")
    assert proposal.source == "relationship.external_fact_proposals"
    with engine.connect() as conn, conn.begin():
        validate_catalog(conn, manifest)
        assert (
            conn.execute(
                text("SELECT state FROM mcp_surface.controls WHERE domain='proposals'")
            ).scalar_one()
            == "enabled"
        )
        row_id = uuid.uuid4()
        row = conn.execute(
            text("""INSERT INTO relationship.external_fact_proposals
            (proposal_id,note,evidence_url) VALUES (:id,'private synthetic note','https://example.invalid/proof')
            RETURNING proposal_id,state,decided_at,created_at"""),
            {"id": row_id},
        ).one()
        assert row[0] == row_id and row[1] == "imported" and row[2] is None and row[3] is not None
        terminal = (
            "invalid",
            "duplicate",
            "conflict",
            "rate_limited",
            "applied",
            "owner_rejected",
            "expired",
            "apply_failed",
        )
        for state in terminal:
            conn.execute(
                text("""UPDATE relationship.external_fact_proposals
                SET state=:state,decided_at=now() WHERE proposal_id=:id"""),
                {"state": state, "id": row_id},
            )
            assert (
                conn.execute(
                    text(
                        "SELECT decided_at FROM relationship.external_fact_proposals WHERE proposal_id=:id"
                    ),
                    {"id": row_id},
                ).scalar_one()
                is not None
            )
        # No intake/apply action is performed: this only proves the persisted lifecycle shape.
        for statement, params in (
            ("INSERT INTO relationship.external_fact_proposals DEFAULT VALUES", {}),
            (
                "INSERT INTO relationship.external_fact_proposals (proposal_id) VALUES (:id)",
                {"id": row_id},
            ),
            (
                "UPDATE relationship.external_fact_proposals SET state='received' WHERE proposal_id=:id",
                {"id": row_id},
            ),
            (
                "UPDATE relationship.external_fact_proposals SET decided_at=NULL WHERE proposal_id=:id",
                {"id": row_id},
            ),
            (
                "UPDATE relationship.external_fact_proposals SET state='parked' WHERE proposal_id=:id",
                {"id": row_id},
            ),
            (
                "UPDATE relationship.external_fact_proposals SET note=:note WHERE proposal_id=:id",
                {"id": row_id, "note": "x" * 501},
            ),
        ):
            with conn.begin_nested() as savepoint:
                with pytest.raises(IntegrityError):
                    conn.execute(text(statement), params)
                savepoint.rollback()
        before = conn.execute(
            text(
                "SELECT proposal_id,state,decided_at FROM relationship.external_fact_proposals WHERE proposal_id=:id"
            ),
            {"id": row_id},
        ).one()
        conn.execute(
            text(
                "UPDATE relationship.external_fact_proposals SET note=NULL,evidence_url=NULL WHERE proposal_id=:id"
            ),
            {"id": row_id},
        )
        assert (
            conn.execute(
                text(
                    "SELECT proposal_id,state,decided_at FROM relationship.external_fact_proposals WHERE proposal_id=:id"
                ),
                {"id": row_id},
            ).one()
            == before
        )
        reconcile_connection(conn, manifest)
        columns = conn.execute(
            text(
                "SELECT attnames FROM pg_publication_tables WHERE pubname='butlers_public_v1' AND schemaname='relationship' AND tablename='external_fact_proposals'"
            )
        ).scalar_one()
        assert columns == ["proposal_id", "state", "decided_at"]
        assert not {"note", "evidence_url", "created_at"} & set(columns)
        conn.rollback()
    # The real source chain downgrades only its additive source, and ordinary
    # head installation recreates it. Credentials flow internally, never to output.
    cfg = _build_alembic_config(
        engine.url.render_as_string(hide_password=False),
        chains=["relationship"],
        target_schema="relationship",
    )
    try:
        command.downgrade(cfg, "rel_040")
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT to_regclass('relationship.external_fact_proposals')")
                ).scalar_one()
                is None
            )
            assert (
                conn.execute(text("SELECT to_regclass('relationship.entity_facts')")).scalar_one()
                is not None
            )
            with pytest.raises(
                ManifestError, match="external_fact_proposals.proposal_id: missing column"
            ):
                validate_catalog(conn, manifest)
    finally:
        command.upgrade(cfg, "relationship@head")
    head = get_chain_head("relationship")
    with engine.connect() as conn:
        validate_catalog(conn, manifest)
        assert (
            conn.execute(
                text(
                    "SELECT version_num FROM relationship.alembic_version WHERE version_num=:head"
                ),
                {"head": head},
            ).scalar_one()
            == head
        )


def test_remaining_filtered_sources_iud_through_owning_writers(manifest_primary):
    """The four R2 families under this canonical publication, with actual source APIs."""
    import uuid

    import asyncpg

    from butlers.chronicler.public_projection import sweep_public_projection
    from butlers.core.entity_graph_edges import (
        delete_entity_graph_edge,
        project_entity_graph_edge,
        withhold_entity_graph_edge,
    )

    engine, _, _ = manifest_primary
    manifest = load_manifest()
    with engine.begin() as conn:
        assert not publication_state(conn, manifest).exists
        reconcile_connection(conn, manifest)
    url = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)

    async def exercise():
        pool = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=1,
            command_timeout=15,
            server_settings={"search_path": "chronicler,public"},
        )
        episode = None
        adapter_inserted = False
        try:
            expressions = dict(
                await pool.fetch("""SELECT n.nspname||'.'||c.relname,
                pg_get_expr(r.prqual,r.prrelid) FROM pg_publication_rel r
                JOIN pg_class c ON c.oid=r.prrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_publication p ON p.oid=r.prpubid WHERE p.pubname='butlers_public_v1'""")
            )
            adapter_inserted = bool(
                await pool.fetchval("""INSERT INTO chronicler.source_adapter_state
                (source_name,chronicler_compatibility) VALUES ('google_health.measurements','supported')
                ON CONFLICT DO NOTHING RETURNING true""")
            )
            # True source input -> real sweep -> override BEFORE/AFTER triggers.
            episode = await pool.fetchval(
                """INSERT INTO chronicler.episodes
                (source_name,source_ref,episode_type,start_at,end_at,title,privacy,layer)
                VALUES ('google_health.measurements',$1,'sleep_episode',now()-interval '1 hour',now(),
                        'Synthetic manifest sleep','normal','activity') RETURNING id""",
                str(uuid.uuid4()),
            )
            await sweep_public_projection(pool)

            async def episode_state():
                row = await pool.fetchrow(
                    f"""SELECT effective_privacy,tombstoned,
                    ({expressions["chronicler.episodes_public"]}) AS visible
                    FROM chronicler.episodes_public WHERE id=$1""",
                    episode,
                )
                assert row is not None
                return tuple(row)

            assert await episode_state() == ("normal", False, True)
            await pool.execute(
                """INSERT INTO chronicler.overrides
                (target_kind,target_id,corrected_privacy,submitted_by)
                VALUES ('episode',$1,'restricted','user')""",
                episode,
            )
            assert await episode_state() == ("restricted", False, False)
            await pool.execute(
                """INSERT INTO chronicler.overrides
                (target_kind,target_id,corrected_privacy,submitted_by)
                VALUES ('episode',$1,'normal','user')""",
                episode,
            )
            assert await episode_state() == ("restricted", False, False)
            # Source policy permits only the owner to lower an earlier restriction.
            await pool.execute(
                """INSERT INTO chronicler.overrides
                (target_kind,target_id,corrected_privacy,submitted_by)
                VALUES ('episode',$1,'normal','owner')""",
                episode,
            )
            assert await episode_state() == ("normal", False, True)
            await pool.execute(
                """INSERT INTO chronicler.overrides
                (target_kind,target_id,corrected_tombstone_at,submitted_by)
                VALUES ('episode',$1,now(),'user')""",
                episode,
            )
            assert await episode_state() == ("normal", True, False)
            await pool.execute("DELETE FROM chronicler.episodes WHERE id=$1", episode)
            assert not await pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM chronicler.episodes_public WHERE id=$1)", episode
            )
            async with pool.acquire() as conn:
                transaction = conn.transaction()
                await transaction.start()
                try:
                    person = await conn.fetchval(
                        "INSERT INTO public.entities(canonical_name,entity_type) VALUES ('Manifest graph subject','person') RETURNING id"
                    )
                    other = await conn.fetchval(
                        "INSERT INTO public.entities(canonical_name,entity_type) VALUES ('Manifest graph object','person') RETURNING id"
                    )
                    row = await conn.fetchval(
                        """INSERT INTO relationship.entity_facts
                        (subject,predicate,object,object_kind,src)
                        VALUES ($1,'works-at',$2,'entity','manifest-control') RETURNING id""",
                        person,
                        str(other),
                    )

                    async def fact_visible():
                        return await conn.fetchval(
                            f"SELECT EXISTS(SELECT 1 FROM relationship.entity_facts WHERE id=$1 AND ({expressions['relationship.entity_facts']}))",
                            row,
                        )

                    assert await fact_visible()
                    await conn.execute(
                        "UPDATE relationship.entity_facts SET validity='retracted' WHERE id=$1", row
                    )
                    assert not await fact_visible()
                    await conn.execute(
                        "UPDATE relationship.entity_facts SET validity='active',predicate='has-email',object_kind='literal' WHERE id=$1",
                        row,
                    )
                    assert not await fact_visible()
                    await conn.execute(
                        "UPDATE relationship.entity_facts SET predicate='works-at',object_kind='entity' WHERE id=$1",
                        row,
                    )
                    assert await fact_visible()
                    await conn.execute("DELETE FROM relationship.entity_facts WHERE id=$1", row)
                    assert not await fact_visible()

                    sources = []
                    for predicate in ("knows", "works_at"):
                        source = await conn.fetchval(
                            """INSERT INTO general.facts
                            (subject,predicate,content,sensitivity,validity)
                            VALUES ($1,$2,'synthetic content','normal','active') RETURNING id""",
                            str(person),
                            predicate,
                        )
                        sources.append(source)
                        await project_entity_graph_edge(
                            conn,
                            source_schema="general",
                            source_table="facts",
                            source_id=source,
                            subject_entity_id=person,
                            predicate=predicate,
                            object_entity_id=other,
                        )
                        edge = await conn.fetchrow(
                            f"""SELECT publish_key,({expressions["public.entity_graph_edges"]}) AS visible
                            FROM public.entity_graph_edges WHERE source_schema='general' AND source_table='facts' AND source_id=$1""",
                            source,
                        )
                        assert tuple(edge) == (f"general.facts:{predicate}", True)
                        assert await conn.fetchval(
                            f"SELECT EXISTS(SELECT 1 FROM general.facts WHERE id=$1 AND ({expressions['general.facts']}))",
                            source,
                        )
                    for i, source in enumerate(sources, 1):
                        await conn.execute(
                            "UPDATE general.facts SET sensitivity='pii' WHERE id=$1", source
                        )
                        assert not await conn.fetchval(
                            f"SELECT EXISTS(SELECT 1 FROM general.facts WHERE id=$1 AND ({expressions['general.facts']}))",
                            source,
                        )
                        await withhold_entity_graph_edge(
                            conn,
                            source_schema="general",
                            source_table="facts",
                            source_id=source,
                            subject_entity_id=person,
                            sensitivity="pii",
                        )
                        # The real core319 count trigger is deferred. Flush it; do
                        # not infer a missing row before the owning trigger runs.
                        await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        edge = await conn.fetchrow(
                            f"""SELECT predicate,object_entity_id,publish_key,
                            ({expressions["public.entity_graph_edges"]}) AS visible
                            FROM public.entity_graph_edges WHERE source_schema='general' AND source_table='facts' AND source_id=$1""",
                            source,
                        )
                        assert tuple(edge) == (None, None, "", False)
                        counts = await conn.fetchrow(
                            f"""SELECT withheld,
                            ({expressions["public.entity_graph_withheld_counts"]}) AS visible
                            FROM public.entity_graph_withheld_counts WHERE entity_id=$1 AND source_key='general.facts'""",
                            person,
                        )
                        assert tuple(counts) == (i, True)
                    # A real excluded fact source also makes an actual count, but
                    # the stored canonical filter withholds it (healthy inverse).
                    hidden = await conn.fetchval(
                        """INSERT INTO health.facts(subject,predicate,content,sensitivity,validity)
                        VALUES ($1,'knows','synthetic health','pii','active') RETURNING id""",
                        str(person),
                    )
                    await withhold_entity_graph_edge(
                        conn,
                        source_schema="health",
                        source_table="facts",
                        source_id=hidden,
                        subject_entity_id=person,
                        sensitivity="pii",
                    )
                    counts = await conn.fetchrow(
                        f"""SELECT withheld,
                        ({expressions["public.entity_graph_withheld_counts"]}) AS visible
                        FROM public.entity_graph_withheld_counts WHERE entity_id=$1 AND source_key='health.facts'""",
                        person,
                    )
                    assert tuple(counts) == (1, False)
                    for i, source in enumerate(sources):
                        await delete_entity_graph_edge(
                            conn, source_schema="general", source_table="facts", source_id=source
                        )
                        assert not await conn.fetchval(
                            "SELECT EXISTS(SELECT 1 FROM public.entity_graph_edges WHERE source_schema='general' AND source_table='facts' AND source_id=$1)",
                            source,
                        )
                        count = await conn.fetchval(
                            "SELECT withheld FROM public.entity_graph_withheld_counts WHERE entity_id=$1 AND source_key='general.facts'",
                            person,
                        )
                        assert count == (1 if i == 0 else None)
                finally:
                    await transaction.rollback()
        finally:
            try:
                if episode is not None:
                    await pool.execute(
                        "DELETE FROM chronicler.overrides WHERE target_kind='episode' AND target_id=$1",
                        episode,
                    )
                    await pool.execute("DELETE FROM chronicler.episodes WHERE id=$1", episode)
                    await sweep_public_projection(pool)
                if adapter_inserted:
                    await pool.execute(
                        "DELETE FROM chronicler.source_adapter_state WHERE source_name='google_health.measurements'"
                    )
            finally:
                await pool.close()

    try:
        asyncio.run(exercise())
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql("DROP PUBLICATION butlers_public_v1")
            conn.exec_driver_sql("DELETE FROM mcp_surface.manifest_state")
