"""Same-instance PG17 execution of the unexposed production owner program.

One genuine topology amortizes full nine-schema migration. Every case restores
its own state; these tests never replace the renderer, publication, peer path,
apply worker, source migrations or owning source triggers with stand-ins.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import replace

import pytest
from sqlalchemy import Column, Integer, MetaData, text
from sqlalchemy import Table as SqlTable
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateTable

from butlers.knowledge_read.manifest import (
    Manifest,
    ManifestError,
    decode_applied_manifest,
    load_manifest,
    manifest_transition,
    qualified,
)
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program, subscriber_plan
from butlers.testing.public_mcp import public_mcp_topology

pytestmark = pytest.mark.integration


def _wait(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value:
            return value
        assert time.monotonic() < deadline, "owned subscriber did not reach the actual observation"
        time.sleep(0.05)


def _scalar(engine, sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


def _run(topology, program, *, success=True):
    result = topology.execute(program)
    if success:
        # Output has no source rows or credentials: only psql status and closed errors.
        assert result.exit_code == 0, result.output.decode(errors="replace")[-4000:]
    else:
        assert result.exit_code != 0, "the real psql program must refuse"
    return result


def _reconcile(topology, manifest):
    with topology.engine.begin() as conn:
        reconcile_connection(conn, manifest)


def _ready(projection, digest):
    return _scalar(
        projection,
        "SELECT COALESCE(bool_and(copy_complete AND digest=:digest),false) FROM mcp_read.manifest_applied",
        {"digest": digest},
    )


def _apply(topology, projection, manifest):
    _reconcile(topology, manifest)
    _run(topology, render_owner_program(manifest))
    _wait(lambda: _ready(projection, manifest.digest))


@pytest.fixture(scope="module")
def topology():
    with public_mcp_topology() as actual:
        yield actual


@pytest.fixture
def copied(topology):
    projection = topology.projection_engine()
    target = load_manifest()
    try:
        _apply(topology, projection, target)
        yield topology, projection, target
    finally:
        projection.dispose()


def _barrier(topology, projection):
    # This is an actual already-published source row, never an extra publication
    # member or a synthetic transport barrier. Its committed value names this write.
    with topology.engine.begin() as conn:
        changed = conn.execute(
            text(
                "UPDATE mcp_surface.controls SET changed_at=GREATEST(clock_timestamp(),changed_at+interval '1 microsecond') WHERE domain='all' RETURNING changed_at"
            )
        ).scalar_one()
    _wait(
        lambda: _scalar(
            projection,
            "SELECT changed_at=:changed FROM mcp_surface.controls WHERE domain='all'",
            {"changed": changed},
        )
    )


def test_actual_full_copy_catalog_views_freshness_and_noop(copied):
    topology, projection, manifest = copied
    with projection.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT n.nspname||'.'||c.relname,r.srsubstate FROM pg_subscription_rel r JOIN pg_subscription s ON s.oid=r.srsubid JOIN pg_class c ON c.oid=r.srrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE s.subname='butlers_public_sub' ORDER BY 1"
            )
        ).all()
        assert rows == [(t.source, "r") for t in manifest.tables]
        assert conn.execute(
            text(
                "SELECT pg_get_userbyid(subowner),subpasswordrequired,subslotname,subpublications FROM pg_subscription WHERE subname='butlers_public_sub'"
            )
        ).one() == ("mcp_subscription_owner", False, "butlers_public_sub", ["butlers_public_v1"])
        views = dict(
            conn.execute(
                text(
                    "SELECT relname,oid FROM pg_class WHERE relnamespace='mcp_read'::regnamespace AND relkind='v'"
                )
            ).all()
        )
        assert set(views) == {p.view for p in subscriber_plan(manifest)} | {"manifest_applied"}
        before = conn.execute(
            text("SELECT digest,canonical,applied_at,xmin::text FROM mcp_local.manifest_applied")
        ).one()
        sub_oid = conn.execute(
            text("SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub'")
        ).scalar_one()
    _barrier(topology, projection)
    # Positive timestamp is required before freshness absence/permission assertions.
    _wait(lambda: _scalar(projection, "SELECT mcp_read.replication_as_of() IS NOT NULL"))
    _run(topology, render_owner_program(manifest))
    with projection.connect() as conn:
        assert (
            dict(
                conn.execute(
                    text(
                        "SELECT relname,oid FROM pg_class WHERE relnamespace='mcp_read'::regnamespace AND relkind='v'"
                    )
                ).all()
            )
            == views
        )
        assert (
            conn.execute(
                text(
                    "SELECT digest,canonical,applied_at,xmin::text FROM mcp_local.manifest_applied"
                )
            ).one()
            == before
        )
        assert (
            conn.execute(
                text("SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub'")
            ).scalar_one()
            == sub_oid
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.prosecdef AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema'"
                )
            ).scalar_one()
            == 2
        )
        assert conn.execute(
            text(
                "SELECT bool_and(proconfig=ARRAY['search_path=pg_catalog, pg_temp']) FROM pg_proc WHERE oid IN ('mcp_read.replication_as_of()'::regprocedure,'mcp_inbox.cap_proposals()'::regprocedure)"
            )
        ).scalar_one()
        assert not conn.execute(
            text("SELECT pg_has_role('mcp_projection_owner','pg_read_all_stats','MEMBER')")
        ).scalar_one()
        assert (
            conn.execute(
                text(
                    "SELECT has_database_privilege('mcp_public_ro',current_database(),'TEMPORARY')"
                )
            ).scalar_one()
            is False
        )


def _states(kind):
    full = load_manifest()
    account = next(t for t in full.tables if t.source == "finance.accounts")
    small = replace(
        account,
        columns=tuple(c for c in account.columns if c != "last_four"),
        types=tuple(p for p in account.types if p[0] != "last_four"),
        identifiers=tuple(c for c in account.identifiers if c != "last_four"),
    )
    narrow = Manifest(tuple(small if t == account else t for t in full.tables))
    fewer = Manifest(tuple(t for t in full.tables if t != account))
    if kind == "column-narrow":
        return full, narrow
    if kind == "column-widen":
        return narrow, full
    if kind == "table-add":
        return fewer, full
    if kind == "table-remove":
        return full, fewer
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["column-narrow", "column-widen", "table-add", "table-remove"])
def test_runtime_and_pure_transition_agree_and_views_keep_oid(copied, kind):
    topology, projection, full = copied
    old, target = _states(kind)
    expected = manifest_transition(
        decode_applied_manifest(old.canonical), decode_applied_manifest(target.canonical)
    )
    try:
        _apply(topology, projection, old)
        with projection.connect() as conn:
            old_views = dict(
                conn.execute(
                    text(
                        "SELECT relname,oid FROM pg_class WHERE relnamespace='mcp_read'::regnamespace AND relkind='v'"
                    )
                ).all()
            )
        _reconcile(topology, target)
        # Execute exactly the production validation/classification prefix, before
        # its first persistent mutation; compare the shared canonical cases.
        prefix = render_owner_program(target).split("\\if :e_rebuild\n", 1)[0]
        observed = _run(
            topology, prefix + "\\echo TRANSITION :e_rebuild :e_narrowing\n"
        ).output.decode()
        assert (
            f"TRANSITION {'t' if expected.rebuild else 'f'} {'t' if expected.narrowing else 'f'}"
            in observed
        )
        _run(topology, render_owner_program(target))
        _wait(lambda: _ready(projection, target.digest))
        with projection.connect() as conn:
            now = dict(
                conn.execute(
                    text(
                        "SELECT relname,oid FROM pg_class WHERE relnamespace='mcp_read'::regnamespace AND relkind='v'"
                    )
                ).all()
            )
            for name in set(old_views) & set(now):
                assert now[name] == old_views[name]
            columns = (
                conn.execute(
                    text(
                        "SELECT attname FROM pg_attribute WHERE attrelid=to_regclass('finance.accounts') AND attnum>0 AND NOT attisdropped ORDER BY attnum"
                    )
                )
                .scalars()
                .all()
            )
            assert ("last_four" in columns) == any(
                "last_four" in t.columns for t in target.tables if t.source == "finance.accounts"
            )
    finally:
        _apply(topology, projection, full)


@pytest.mark.parametrize("kind", ["type-change", "generated-dependency-removal"])
def test_view_oid_survives_real_type_and_generated_dependency_changes(copied, kind):
    topology, projection, full = copied
    source = "finance.accounts" if kind == "type-change" else "relationship.entity_facts"
    original = next(table for table in full.tables if table.source == source)
    if kind == "type-change":
        changed = replace(
            original,
            types=tuple(
                (name, "text" if name == "last_four" else type_) for name, type_ in original.types
            ),
        )
    else:
        changed = replace(
            original,
            columns=tuple(name for name in original.columns if name != "object_kind"),
            types=tuple(pair for pair in original.types if pair[0] != "object_kind"),
        )
    target = Manifest(tuple(changed if table == original else table for table in full.tables))
    decode_applied_manifest(target.canonical)
    view = "mcp_read." + source.replace(".", "__")
    before = _scalar(projection, "SELECT to_regclass(:view)::oid", {"view": view})
    try:
        if kind == "type-change":
            with topology.engine.begin() as conn:
                # PostgreSQL cannot alter a published column's type. This is
                # the real source migration boundary, before subscriber repair:
                # remove only this owned membership, change the source type and
                # restore the exact target publication atomically.
                conn.exec_driver_sql(
                    "ALTER PUBLICATION butlers_public_v1 DROP TABLE finance.accounts"
                )
                conn.exec_driver_sql(
                    "ALTER TABLE finance.accounts ALTER COLUMN last_four TYPE text"
                )
                reconcile_connection(conn, target)
            assert (
                _scalar(
                    topology.engine,
                    "SELECT format_type(atttypid,atttypmod) FROM pg_attribute "
                    "WHERE attrelid='finance.accounts'::regclass AND attname='last_four'",
                )
                == "text"
            )
        _apply(topology, projection, target)
        assert _scalar(projection, "SELECT to_regclass(:view)::oid", {"view": view}) == before
        with projection.connect() as conn:
            attrs = dict(
                conn.execute(
                    text(
                        "SELECT attname,format_type(atttypid,atttypmod) FROM pg_attribute "
                        "WHERE attrelid=to_regclass(:source) AND attnum>0 AND NOT attisdropped"
                    ),
                    {"source": source},
                ).all()
            )
        if kind == "type-change":
            assert attrs["last_four"] == "text"
        else:
            assert "object_kind" not in attrs and "object_entity_id" not in attrs
    finally:
        if kind == "type-change":
            with topology.engine.begin() as conn:
                # A failed apply may leave this membership absent. Inspect it
                # before restoring, so cleanup handles either actual catalog.
                if conn.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_publication_rel r "
                        "JOIN pg_publication p ON p.oid=r.prpubid "
                        "WHERE p.pubname='butlers_public_v1' "
                        "AND r.prrelid='finance.accounts'::regclass)"
                    )
                ).scalar_one():
                    conn.exec_driver_sql(
                        "ALTER PUBLICATION butlers_public_v1 DROP TABLE finance.accounts"
                    )
                conn.exec_driver_sql(
                    "ALTER TABLE finance.accounts ALTER COLUMN last_four TYPE character(4)"
                )
                reconcile_connection(conn, full)
        _apply(topology, projection, full)
    assert _scalar(projection, "SELECT to_regclass(:view)::oid", {"view": view}) == before
    if kind == "generated-dependency-removal":
        assert (
            _scalar(
                projection,
                "SELECT count(*) FROM pg_attribute WHERE attrelid='relationship.entity_facts'::regclass AND attname='object_entity_id' AND attgenerated='s'",
            )
            == 1
        )


def test_malformed_or_inconsistent_applied_state_refuses_before_mutation(copied):
    topology, projection, manifest = copied
    with projection.connect() as conn:
        sub_oid = conn.execute(
            text("SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub'")
        ).scalar_one()
    mutations = []
    for change in ("extra", "private", "filter", "type", "graph", "marks"):
        obj = json.loads(manifest.canonical)
        if change == "extra":
            obj["tables"][0]["ignored"] = True
        elif change == "private":
            obj["tables"][0]["source"] = "chronicler.episodes"
        elif change == "filter":
            next(t for t in obj["tables"] if t["source"] == "public.entities")["filters"] = []
        elif change == "type":
            obj["tables"][0]["types"][0][1] = "jsonb"
        elif change == "graph":
            next(t for t in obj["tables"] if t["source"] == "public.entity_graph_edges")[
                "filters"
            ] = []
        elif change == "marks":
            obj["tables"][0]["json_identifiers"] = ["metadata.password"]
        mutations.append(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    mutations += [
        " " + manifest.canonical,
        manifest.canonical.replace('"version":1', '"version":1,"version":1'),
    ]
    try:
        for canonical in mutations:
            with pytest.raises(ManifestError):
                decode_applied_manifest(canonical)
            with projection.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE mcp_local.manifest_applied SET canonical=:canonical,digest=encode(sha256(convert_to(:canonical,'UTF8')),'hex')"
                    ),
                    {"canonical": canonical},
                )
            refused = _run(topology, render_owner_program(manifest), success=False)
            assert b"applied" in refused.output
            assert (
                _scalar(
                    projection, "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub'"
                )
                == sub_oid
            )
        # A valid declaration with a false digest is independently refused.
        with projection.begin() as conn:
            conn.execute(
                text(
                    "UPDATE mcp_local.manifest_applied SET canonical=:canonical,digest=repeat('0',64)"
                ),
                {"canonical": manifest.canonical},
            )
        refused = _run(topology, render_owner_program(manifest), success=False)
        assert b"digest inconsistent" in refused.output
    finally:
        with projection.begin() as conn:
            conn.execute(
                text("UPDATE mcp_local.manifest_applied SET canonical=:canonical,digest=:digest"),
                {"canonical": manifest.canonical, "digest": manifest.digest},
            )
        _apply(topology, projection, manifest)


def test_primary_publication_drift_refuses_before_subscriber_mutation(copied):
    topology, projection, manifest = copied
    before = _scalar(
        projection, "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub'"
    )
    mutations = (
        "ALTER PUBLICATION butlers_public_v1 SET (publish='insert')",
        "ALTER PUBLICATION butlers_public_v1 SET TABLE mcp_surface.controls",
        "ALTER TABLE finance.accounts REPLICA IDENTITY FULL",
    )
    for mutation in mutations:
        try:
            with topology.engine.begin() as conn:
                conn.exec_driver_sql(mutation)
            refused = _run(topology, render_owner_program(manifest), success=False)
            assert b"public MCP" in refused.output or b"primary source" in refused.output
            assert (
                _scalar(
                    projection, "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub'"
                )
                == before
            )
        finally:
            with topology.engine.begin() as conn:
                conn.exec_driver_sql("ALTER TABLE finance.accounts REPLICA IDENTITY DEFAULT")
                reconcile_connection(conn, manifest)
    _run(topology, render_owner_program(manifest))


def test_view_fields_replicate_exact_values_and_hide_private_neighbors(copied):
    topology, projection, manifest = copied
    ids = {}
    try:
        with topology.engine.begin() as conn:
            ids["entity"] = conn.execute(
                text(
                    "INSERT INTO public.entities(canonical_name,entity_type) VALUES ('Subscriber allowed person','person') RETURNING id"
                )
            ).scalar_one()
            ids["account"] = conn.execute(
                text(
                    "INSERT INTO finance.accounts(institution,name,type,currency,last_four) VALUES ('Synthetic institution','Subscriber account','checking','USD','1234') RETURNING id"
                )
            ).scalar_one()
            ids["balance"] = conn.execute(
                text(
                    "INSERT INTO finance.balance_snapshots(account_id,balance,currency,as_of_date,source) VALUES (:account,123456789012.34,'USD',CURRENT_DATE,'synthetic') RETURNING id"
                ),
                ids,
            ).scalar_one()
            ids["category_name"] = "subscriber-" + uuid.uuid4().hex
            ids["category"] = conn.execute(
                text(
                    "INSERT INTO finance.categories(name,display_name) "
                    "VALUES (:category_name,'Subscriber category') RETURNING id"
                ),
                ids,
            ).scalar_one()
            ids["transaction"] = conn.execute(
                text(
                    "INSERT INTO finance.transactions(account_id,posted_at,merchant,amount,currency,direction,category) "
                    "VALUES (:account,clock_timestamp(),'Synthetic merchant',12.34,'USD','debit',:category_name) RETURNING id"
                ),
                ids,
            ).scalar_one()
            ids["fact"] = conn.execute(
                text(
                    "INSERT INTO general.facts(subject,predicate,content,sensitivity,validity) VALUES ('Subscriber subject','note','allowed neighbor','normal','active') RETURNING id"
                )
            ).scalar_one()
            ids["hidden"] = conn.execute(
                text(
                    "INSERT INTO general.facts(subject,predicate,content,sensitivity,validity) VALUES ('Subscriber hidden subject','note','synthetic private canary','pii','active') RETURNING id"
                )
            ).scalar_one()
        _barrier(topology, projection)
        assert (
            _scalar(
                projection,
                "SELECT fields->>'balance' FROM mcp_read.finance__balance_snapshots WHERE source_id=:id",
                {"id": str(ids["balance"])},
            )
            == "123456789012.34"
        )
        assert (
            _scalar(
                projection,
                "SELECT fields->>'canonical_name' FROM mcp_read.public__entities WHERE source_id=:id",
                {"id": str(ids["entity"])},
            )
            == "Subscriber allowed person"
        )
        assert not _scalar(
            projection,
            "SELECT fields ?| ARRAY['deleted','unidentified','placeholder_name'] FROM mcp_read.public__entities WHERE source_id=:id",
            {"id": str(ids["entity"])},
        )
        assert (
            _scalar(
                projection, "SELECT count(*) FROM general.facts WHERE id=:id", {"id": ids["fact"]}
            )
            == 1
        )
        assert (
            _scalar(
                projection, "SELECT count(*) FROM general.facts WHERE id=:id", {"id": ids["hidden"]}
            )
            == 0
        )
        assert (
            _scalar(
                projection,
                "SELECT fields->>'amount' FROM mcp_read.finance__transactions WHERE source_id=:id",
                {"id": str(ids["transaction"])},
            )
            == "12.34"
        )
        with topology.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE finance.transactions SET deleted_at=clock_timestamp() WHERE id=:transaction"
                ),
                ids,
            )
        _barrier(topology, projection)
        # The mirrored source still contains this soft-deleted row, while its
        # supported read view hides it. This positions the view predicate itself.
        assert (
            _scalar(
                projection, "SELECT count(*) FROM finance.transactions WHERE id=:transaction", ids
            )
            == 1
        )
        assert (
            _scalar(
                projection,
                "SELECT count(*) FROM mcp_read.finance__transactions WHERE source_id=:id",
                {"id": str(ids["transaction"])},
            )
            == 0
        )
        with topology.engine.begin() as conn:
            conn.execute(text("UPDATE general.facts SET sensitivity='pii' WHERE id=:fact"), ids)
            conn.execute(
                text(
                    "UPDATE public.entities SET metadata='{\"tombstone\":true}'::jsonb WHERE id=:entity"
                ),
                ids,
            )
        _barrier(topology, projection)
        assert _scalar(projection, "SELECT count(*) FROM general.facts WHERE id=:fact", ids) == 0
        assert (
            _scalar(projection, "SELECT count(*) FROM public.entities WHERE id=:entity", ids) == 0
        )
        with topology.engine.begin() as conn:
            conn.execute(text("DELETE FROM finance.balance_snapshots WHERE id=:balance"), ids)
        _barrier(topology, projection)
        assert (
            _scalar(
                projection, "SELECT count(*) FROM finance.balance_snapshots WHERE id=:balance", ids
            )
            == 0
        )
    finally:
        with topology.engine.begin() as conn:
            if "transaction" in ids:
                conn.execute(text("DELETE FROM finance.transactions WHERE id=:transaction"), ids)
            if "category" in ids:
                conn.execute(text("DELETE FROM finance.categories WHERE id=:category"), ids)
            if "balance" in ids:
                conn.execute(text("DELETE FROM finance.balance_snapshots WHERE id=:balance"), ids)
            if "account" in ids:
                conn.execute(text("DELETE FROM finance.accounts WHERE id=:account"), ids)
            for key in ("fact", "hidden"):
                if key in ids:
                    conn.execute(text("DELETE FROM general.facts WHERE id=:id"), {"id": ids[key]})
            if "entity" in ids:
                conn.execute(text("DELETE FROM public.entities WHERE id=:entity"), ids)
        _barrier(topology, projection)


def test_real_privileges_inbox_cap_and_definer_search_path(copied):
    topology, projection, manifest = copied
    with projection.connect() as conn:
        with conn.begin():
            conn.exec_driver_sql("SET LOCAL ROLE mcp_public_ro")
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mcp_read.mcp_surface__controls")
                ).scalar_one()
                == 8
            )
            assert conn.execute(
                text("SELECT mcp_read.replication_as_of() IS NOT NULL")
            ).scalar_one()
            assert conn.execute(
                text("SELECT copy_complete FROM mcp_read.manifest_applied")
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO mcp_inbox.proposals(proposal_id,sub,cid,idempotency_key,subject_entity_id,predicate,object_entity_id) VALUES (:id,'synthetic-sub','synthetic-cid','key',:subject,'knows',:object)"
                ),
                {"id": uuid.uuid4(), "subject": uuid.uuid4(), "object": uuid.uuid4()},
            )
            conn.rollback()
        for statement in (
            "SELECT * FROM general.facts",
            "SELECT * FROM mcp_local.manifest_applied",
            "SELECT * FROM mcp_inbox.proposals",
            "DELETE FROM mcp_inbox.proposals",
            "UPDATE mcp_inbox.proposals SET note='changed'",
            "SELECT mcp_inbox.cap_proposals()",
        ):
            transaction = conn.begin()
            conn.exec_driver_sql("SET LOCAL ROLE mcp_public_ro")
            with pytest.raises(DBAPIError):
                conn.exec_driver_sql(statement)
            transaction.rollback()
        # Typed privilege canary, never a stand-in database or replicated table.
        transaction = conn.begin()
        conn.exec_driver_sql("SET LOCAL ROLE mcp_public_ro")
        denied = SqlTable("denied", MetaData(), Column("id", Integer), prefixes=["TEMPORARY"])
        with pytest.raises(DBAPIError):
            conn.execute(CreateTable(denied))
        transaction.rollback()
        with conn.begin():
            conn.exec_driver_sql(
                "INSERT INTO mcp_inbox.proposals(proposal_id,sub,cid,idempotency_key,subject_entity_id,predicate,object_entity_id) SELECT gen_random_uuid(),'synthetic-sub','synthetic-cid',n::text,gen_random_uuid(),'knows',gen_random_uuid() FROM generate_series(1,1000) n"
            )
            assert (
                conn.execute(text("SELECT count(*) FROM mcp_inbox.proposals")).scalar_one() == 1000
            )
            transaction = conn.begin_nested()
            with pytest.raises(DBAPIError, match="inbox full"):
                conn.exec_driver_sql(
                    "INSERT INTO mcp_inbox.proposals(proposal_id,sub,cid,idempotency_key,subject_entity_id,predicate,object_entity_id) VALUES (gen_random_uuid(),'sub','cid','overflow',gen_random_uuid(),'knows',gen_random_uuid())"
                )
            transaction.rollback()
            conn.exec_driver_sql("SET LOCAL ROLE mcp_proposals_reader")
            assert (
                conn.execute(text("SELECT count(*) FROM mcp_inbox.proposals")).scalar_one() == 1000
            )
            assert conn.exec_driver_sql("DELETE FROM mcp_inbox.proposals").rowcount == 1000
            conn.rollback()


def test_filtered_families_propagate_real_iud_with_healthy_neighbors(copied):
    import asyncio

    import asyncpg

    from butlers.chronicler.public_projection import sweep_public_projection
    from butlers.core.entity_graph_edges import (
        delete_entity_graph_edge,
        project_entity_graph_edge,
        withhold_entity_graph_edge,
    )

    topology, projection, manifest = copied

    async def exercise():
        url = topology.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        actual = await asyncpg.create_pool(
            url,
            min_size=1,
            max_size=1,
            command_timeout=15,
            server_settings={"search_path": "chronicler,public"},
        )
        episode = person = other = relation = graph_source = None
        facts = {}
        adapter_inserted = False
        try:
            person = await actual.fetchval(
                "INSERT INTO public.entities(canonical_name,entity_type) VALUES ('Subscriber graph subject','person') RETURNING id"
            )
            other = await actual.fetchval(
                "INSERT INTO public.entities(canonical_name,entity_type) VALUES ('Subscriber graph object','person') RETURNING id"
            )
            # All seven published fact sources, including broad finance/health,
            # use their actual migrated constraints/shadow triggers.
            for table in (t for t in manifest.tables if t.source.endswith(".facts")):
                metadata = (
                    '{"value":{"pin":1234,"safe":70},"password":"synthetic-private-leaf","allowed":"neighbor"}'
                    if table.source == "health.facts"
                    else "{}"
                )
                predicate = "measurement_weight" if table.source == "health.facts" else "note"
                facts[table.source] = await actual.fetchval(
                    f"INSERT INTO {qualified(table.source)}(subject,predicate,content,sensitivity,validity,metadata) VALUES ($1,$2,'Subscriber fact','normal','active',$3::jsonb) RETURNING id",
                    str(uuid.uuid4()),
                    predicate,
                    metadata,
                )
            relation = await actual.fetchval(
                "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) VALUES ($1,'works-at',$2,'entity','subscriber-control') RETURNING id",
                person,
                str(other),
            )
            async with actual.acquire() as conn, conn.transaction():
                graph_source = await conn.fetchval(
                    "INSERT INTO general.facts(subject,predicate,content,sensitivity,validity) VALUES ($1,'knows','Subscriber graph fact','normal','active') RETURNING id",
                    str(person),
                )
                await project_entity_graph_edge(
                    conn,
                    source_schema="general",
                    source_table="facts",
                    source_id=graph_source,
                    subject_entity_id=person,
                    predicate="knows",
                    object_entity_id=other,
                )
            adapter_inserted = bool(
                await actual.fetchval(
                    "INSERT INTO chronicler.source_adapter_state(source_name,chronicler_compatibility) VALUES ('google_health.measurements','supported') ON CONFLICT DO NOTHING RETURNING true"
                )
            )
            episode = await actual.fetchval(
                "INSERT INTO chronicler.episodes(source_name,source_ref,episode_type,start_at,end_at,title,privacy,layer) VALUES ('google_health.measurements',$1,'sleep_episode',now()-interval '1 hour',now(),'Subscriber sleep','normal','activity') RETURNING id",
                str(uuid.uuid4()),
            )
            await sweep_public_projection(actual)
            _barrier(topology, projection)
            for source, row in facts.items():
                assert (
                    _scalar(
                        projection,
                        f"SELECT count(*) FROM {qualified(source)} WHERE id=:id",
                        {"id": row},
                    )
                    == 1
                )
            assert _scalar(
                projection,
                "SELECT fields->>'object_entity_id' FROM mcp_read.relationship__entity_facts WHERE source_id=:id",
                {"id": str(relation)},
            ) == str(other)
            graph = _scalar(
                projection,
                "SELECT fields FROM mcp_read.public__entity_graph_edges WHERE fields->>'subject_entity_id'=:id",
                {"id": str(person)},
            )
            assert graph["predicate"] == "knows" and graph["source_domain"] == "knowledge"
            assert "publish_key" not in graph
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM chronicler.episodes_public WHERE id=:id",
                    {"id": episode},
                )
                == 1
            )
            assert _scalar(projection, "SELECT count(*) FROM chronicler.rollups_public") > 0
            health_id = str(facts["health.facts"])
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM health.measurements_public WHERE id LIKE :prefix AND metric='measurement_weight.safe'",
                    {"prefix": health_id + "%"},
                )
                == 1
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM health.measurements_public WHERE id LIKE :prefix AND metric='measurement_weight.pin'",
                    {"prefix": health_id + "%"},
                )
                == 0
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM health.json_fields_public WHERE row_id=:id AND path='metadata.value.safe'",
                    {"id": health_id},
                )
                == 1
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM health.json_fields_public WHERE row_id=:id AND (path LIKE '%pin%' OR path LIKE '%password%')",
                    {"id": health_id},
                )
                == 0
            )
            # Eligibility changes become real subscriber DELETEs; each assertion
            # follows the committed published control row, not a blind sleep.
            for source, row in facts.items():
                await actual.execute(
                    f"UPDATE {qualified(source)} SET validity='retracted' WHERE id=$1", row
                )
            await actual.execute(
                "UPDATE relationship.entity_facts SET validity='retracted' WHERE id=$1", relation
            )
            async with actual.acquire() as conn, conn.transaction():
                await conn.execute(
                    "UPDATE general.facts SET sensitivity='pii' WHERE id=$1", graph_source
                )
                await withhold_entity_graph_edge(
                    conn,
                    source_schema="general",
                    source_table="facts",
                    source_id=graph_source,
                    subject_entity_id=person,
                    sensitivity="pii",
                )
            await actual.execute(
                "INSERT INTO chronicler.overrides(target_kind,target_id,corrected_privacy,submitted_by) VALUES ('episode',$1,'restricted','user')",
                episode,
            )
            _barrier(topology, projection)
            for source, row in facts.items():
                assert (
                    _scalar(
                        projection,
                        f"SELECT count(*) FROM {qualified(source)} WHERE id=:id",
                        {"id": row},
                    )
                    == 0
                )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM relationship.entity_facts WHERE id=:id",
                    {"id": relation},
                )
                == 0
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM public.entity_graph_edges WHERE subject_entity_id=:id",
                    {"id": person},
                )
                == 0
            )
            counts = _scalar(
                projection,
                "SELECT fields FROM mcp_read.public__entity_graph_withheld_counts WHERE fields->>'entity_id'=:id AND fields->>'source_key'='general.facts'",
                {"id": str(person)},
            )
            assert counts["withheld"] == 1
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM chronicler.episodes_public WHERE id=:id",
                    {"id": episode},
                )
                == 0
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM health.measurements_public WHERE id LIKE :prefix",
                    {"prefix": health_id + "%"},
                )
                == 0
            )
            # Restore eligibility before DELETE so each deletion is positioned
            # against a currently present subscriber row, not an already hidden one.
            for source, row in facts.items():
                await actual.execute(
                    f"UPDATE {qualified(source)} SET validity='active' WHERE id=$1", row
                )
            await actual.execute(
                "UPDATE relationship.entity_facts SET validity='active' WHERE id=$1", relation
            )
            await actual.execute(
                "INSERT INTO chronicler.overrides(target_kind,target_id,corrected_privacy,submitted_by) VALUES ('episode',$1,'normal','owner')",
                episode,
            )
            _barrier(topology, projection)
            for source, row in facts.items():
                assert (
                    _scalar(
                        projection,
                        f"SELECT count(*) FROM {qualified(source)} WHERE id=:id",
                        {"id": row},
                    )
                    == 1
                )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM relationship.entity_facts WHERE id=:id",
                    {"id": relation},
                )
                == 1
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM chronicler.episodes_public WHERE id=:id",
                    {"id": episode},
                )
                == 1
            )
            for source, row in facts.items():
                await actual.execute(f"DELETE FROM {qualified(source)} WHERE id=$1", row)
            await actual.execute("DELETE FROM relationship.entity_facts WHERE id=$1", relation)
            await actual.execute("DELETE FROM chronicler.episodes WHERE id=$1", episode)
            # Actual source deletion also removes the still-visible count row.
            async with actual.acquire() as conn, conn.transaction():
                await delete_entity_graph_edge(
                    conn, source_schema="general", source_table="facts", source_id=graph_source
                )
            _barrier(topology, projection)
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM public.entity_graph_withheld_counts WHERE entity_id=:id AND source_key='general.facts'",
                    {"id": person},
                )
                == 0
            )
            for source, row in facts.items():
                assert (
                    _scalar(
                        projection,
                        f"SELECT count(*) FROM {qualified(source)} WHERE id=:id",
                        {"id": row},
                    )
                    == 0
                )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM relationship.entity_facts WHERE id=:id",
                    {"id": relation},
                )
                == 0
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM chronicler.episodes_public WHERE id=:id",
                    {"id": episode},
                )
                == 0
            )
        finally:
            if episode:
                await actual.execute("DELETE FROM chronicler.episodes WHERE id=$1", episode)
            if relation:
                await actual.execute("DELETE FROM relationship.entity_facts WHERE id=$1", relation)
            if graph_source:
                await actual.execute("DELETE FROM general.facts WHERE id=$1", graph_source)
            for source, row in facts.items():
                await actual.execute(f"DELETE FROM {qualified(source)} WHERE id=$1", row)
            for person_id in (person, other):
                if person_id:
                    await actual.execute("DELETE FROM public.entities WHERE id=$1", person_id)
            if adapter_inserted:
                await actual.execute(
                    "DELETE FROM chronicler.source_adapter_state WHERE source_name='google_health.measurements'"
                )
            await actual.close()
            _barrier(topology, projection)

    asyncio.run(exercise())


def test_actual_tcp_principals_and_no_credential_guards(copied):
    from psycopg2 import OperationalError

    from butlers.testing.public_mcp import _HBA, _IDENT

    topology, projection, manifest = copied
    reader = topology.reader_engine("mcp_public_ro")
    importer = topology.reader_engine("mcp_proposals_reader")
    try:
        assert _scalar(reader, "SELECT count(*) FROM mcp_read.mcp_surface__controls") == 8
        assert _scalar(importer, "SELECT count(*) FROM mcp_inbox.proposals") == 0
        for allowed, role in ((reader, "mcp_public_ro"), (importer, "mcp_proposals_reader")):
            with allowed.connect() as conn:
                assert conn.execute(text("SELECT current_user,current_database()")).one() == (
                    role,
                    "butlers_public",
                )
        for role in ("mcp_public_ro", "mcp_proposals_reader", "butlers_projection_publisher"):
            for database in ("butlers", "postgres", "template1"):
                denied = topology.reader_engine(role, database)
                try:
                    with pytest.raises(DBAPIError) as refusal:
                        with denied.connect():
                            pytest.fail("foreign database connection must be refused")
                    original = refusal.value.orig
                    # libpq startup failures can lack a structured SQLSTATE.
                    # Match only the server's explicit reject for this principal
                    # and database; wrong passwords/unreachable endpoints and
                    # unrelated OperationalErrors cannot satisfy this predicate.
                    # Keep the raw diagnostic in RAM; assertion output is closed.
                    hba_reject = re.search(
                        r"(?:^|failed: )FATAL:\s+pg_hba\.conf rejects connection for host "
                        r'"[^"\r\n]+", user "'
                        + re.escape(role)
                        + r'", database "'
                        + re.escape(database)
                        + r'", (?:no encryption|SSL encryption)\s*$',
                        str(original),
                        re.MULTILINE,
                    )
                    facts = {
                        "same_healthy_tcp_endpoint": (denied.url.host, denied.url.port)
                        == (reader.url.host, reader.url.port),
                        "expected_principal": denied.url.username == role,
                        "expected_database": denied.url.database == database,
                        "startup_driver_error": isinstance(original, OperationalError),
                        "sqlstate_available_or_startup_unavailable": original.pgcode
                        in (None, "28000"),
                        "explicit_server_hba_reject": hba_reject is not None,
                    }
                    assert all(facts.values()), facts
                finally:
                    denied.dispose()
        before = _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied")
        for mutation, restore in (
            (
                "ALTER ROLE butlers_projection_publisher PASSWORD 'synthetic-negative'",
                "ALTER ROLE butlers_projection_publisher PASSWORD NULL",
            ),
            (
                "GRANT butlers_projection_publisher TO mcp_public_ro",
                "REVOKE butlers_projection_publisher FROM mcp_public_ro",
            ),
        ):
            try:
                with topology.admin.begin() as conn:
                    conn.exec_driver_sql(mutation)
                refused = _run(topology, render_owner_program(manifest), success=False)
                assert b"publisher" in refused.output
                assert (
                    _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied")
                    == before
                )
            finally:
                with topology.admin.begin() as conn:
                    conn.exec_driver_sql(restore)
        for hba, ident in (
            (_HBA.replace("peer map=local", "trust"), _IDENT),
            (_HBA, _IDENT + "local postgres mcp_public_ro\n"),
            (_HBA.replace("host all butlers_projection_publisher all reject\n", ""), _IDENT),
        ):
            with topology.authentication(hba=hba, ident=ident):
                refused = _run(topology, render_owner_program(manifest), success=False)
                assert b"public MCP" in refused.output
                assert (
                    _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied")
                    == before
                )
        _run(topology, render_owner_program(manifest))
        assert _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied") == before
    finally:
        reader.dispose()
        importer.dispose()


@pytest.mark.parametrize("fault", ["missing", "physical", "missing-subscription"])
def test_missing_slot_rebuilds_stale_data_and_invalid_slot_refuses(copied, fault):
    topology, projection, manifest = copied
    stale = uuid.uuid4()
    unrelated = None
    unrelated_slot = "mcp_e_survivor_" + uuid.uuid4().hex[:12]
    unrelated_pid = None
    with projection.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        before = conn.execute(
            text(
                "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())"
            )
        ).scalar_one()
        conn.exec_driver_sql("ALTER SUBSCRIPTION butlers_public_sub DISABLE")
    _wait(
        lambda: _scalar(
            topology.admin,
            "SELECT active_pid IS NULL FROM pg_replication_slots WHERE slot_name='butlers_public_sub'",
        )
    )
    try:
        if fault == "missing-subscription":
            unrelated = topology.admin.connect().execution_options(isolation_level="AUTOCOMMIT")
            unrelated_pid = unrelated.execute(text("SELECT pg_backend_pid()")).scalar_one()
            unrelated.execute(
                text("SELECT pg_create_physical_replication_slot(:slot)"),
                {"slot": unrelated_slot},
            )
            with projection.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql("ALTER SUBSCRIPTION butlers_public_sub SET (slot_name=NONE)")
                conn.exec_driver_sql("DROP SUBSCRIPTION butlers_public_sub")
            assert _scalar(
                topology.admin,
                "SELECT slot_type='logical' AND plugin='pgoutput' AND database='butlers' AND wal_status<>'lost' AND active_pid IS NULL FROM pg_replication_slots WHERE slot_name='butlers_public_sub'",
            )
            assert (
                _scalar(projection, "SELECT digest FROM mcp_local.manifest_applied")
                == manifest.digest
            )
            assert (
                _scalar(
                    projection,
                    "SELECT count(*) FROM pg_subscription WHERE subname='butlers_public_sub' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())",
                )
                == 0
            )
        else:
            with topology.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql("SELECT pg_drop_replication_slot('butlers_public_sub')")
                if fault == "physical":
                    conn.exec_driver_sql(
                        "SELECT pg_create_physical_replication_slot('butlers_public_sub')"
                    )
        with projection.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO finance.accounts(id,institution,type,currency,name) VALUES (:id,'Synthetic stale','checking','USD','must be purged')"
                ),
                {"id": stale},
            )
        assert (
            _scalar(
                topology.engine, "SELECT count(*) FROM finance.accounts WHERE id=:id", {"id": stale}
            )
            == 0
        )
        assert (
            _scalar(projection, "SELECT count(*) FROM finance.accounts WHERE id=:id", {"id": stale})
            == 1
        )
        if fault == "physical":
            refused = _run(topology, render_owner_program(manifest), success=False)
            assert b"slot identity refused" in refused.output
            assert (
                _scalar(
                    projection, "SELECT count(*) FROM finance.accounts WHERE id=:id", {"id": stale}
                )
                == 1
            )
            with topology.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql("SELECT pg_drop_replication_slot('butlers_public_sub')")
        if fault == "missing-subscription":
            # Observe the real renderer's decision before destructive statements:
            # applied/catalog/slot are healthy, but no copy owner remains.
            prefix = render_owner_program(manifest).split("\\if :e_rebuild\n", 1)[0]
            classified = _run(topology, prefix + "\\echo RECOVERY :e_rebuild\n")
            assert b"RECOVERY t" in classified.output
        _run(topology, render_owner_program(manifest))
        _wait(lambda: _ready(projection, manifest.digest))
        assert (
            _scalar(projection, "SELECT count(*) FROM finance.accounts WHERE id=:id", {"id": stale})
            == 0
        )
        if fault == "missing-subscription":
            assert (
                _scalar(
                    topology.admin,
                    "SELECT count(*) FROM pg_replication_slots WHERE slot_name=:slot AND slot_type='physical'",
                    {"slot": unrelated_slot},
                )
                == 1
            )
            assert (
                _scalar(
                    topology.admin,
                    "SELECT count(*) FROM pg_stat_activity WHERE pid=:pid",
                    {"pid": unrelated_pid},
                )
                == 1
            )
            assert _scalar(projection, "SELECT count(*) FROM mcp_local.rebuild_state") == 0
            assert (
                _scalar(projection, "SELECT digest FROM mcp_local.manifest_applied")
                == manifest.digest
            )
        assert (
            _scalar(
                projection,
                "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())",
            )
            != before
        )
    finally:
        if unrelated is not None:
            try:
                unrelated.execute(
                    text("SELECT pg_drop_replication_slot(:slot)"), {"slot": unrelated_slot}
                )
            finally:
                unrelated.close()
        _apply(topology, projection, manifest)


@pytest.mark.parametrize(
    "boundary", ["capture", "deletion", "drop", "cleanup", "ddl", "before-applied"]
)
def test_actual_program_interruption_resumes_with_exact_owned_journal(
    copied, boundary, record_property
):
    topology, projection, full = copied
    _, target = _states("column-narrow")
    # A real unrelated primary slot/session must survive every cleanup wave.
    unrelated = topology.admin.connect().execution_options(isolation_level="AUTOCOMMIT")
    slot = "mcp_e_unrelated_" + uuid.uuid4().hex[:12]
    pid = unrelated.execute(text("SELECT pg_backend_pid()")).scalar_one()
    unrelated.execute(text("SELECT pg_create_physical_replication_slot(:slot)"), {"slot": slot})
    try:
        _reconcile(topology, target)
        program = render_owner_program(target)
        if boundary == "capture":
            cut = program.rindex(
                "DO $mcp$", 0, program.index("DELETE FROM mcp_local.manifest_applied")
            )
        elif boundary == "deletion":
            cut = program.index(
                "SELECT EXISTS(SELECT 1 FROM pg_subscription",
                program.index("DELETE FROM mcp_local.manifest_applied"),
            )
        elif boundary == "drop":
            cut = program.index(
                "SELECT set_config('butlers.e_cleanup_sources'", program.index("DROP SUBSCRIPTION")
            )
        elif boundary == "cleanup":
            cut = program.index(
                "\\endif\n\\connect butlers\n", program.index("PERFORM pg_drop_replication_slot")
            )
        elif boundary == "ddl":
            cut = program.index("-- PUBLIC_MCP_SUBSCRIBER_DDL_COMPLETE\n")
        else:
            cut = program.rindex("SET ROLE mcp_projection_owner;\nBEGIN;")
        prefix = program[:cut]
        # Finish an open psql conditional after a positioned deliberate SQL error;
        # ON_ERROR_STOP aborts before that terminator can execute anything else.
        interrupted = _run(
            topology,
            prefix + "DO $$ BEGIN RAISE EXCEPTION 'positioned owner interruption'; END $$;\n",
            success=False,
        )
        assert b"positioned owner interruption" in interrupted.output
        if boundary == "capture":
            assert _scalar(projection, "SELECT count(*) FROM mcp_local.manifest_applied") == 1
            assert _scalar(projection, "SELECT count(*) FROM mcp_local.rebuild_state") == 0
            assert (
                _scalar(projection, "SELECT digest FROM mcp_local.manifest_applied") == full.digest
            )
            return
        assert _scalar(projection, "SELECT count(*) FROM mcp_local.manifest_applied") == 0
        assert _scalar(projection, "SELECT count(*) FROM mcp_local.rebuild_state") == 1
        with projection.connect() as conn:
            journal = conn.execute(
                text(
                    "SELECT database_oid,subscription_name,attempt_id,target_digest,manifests,subscription_oids FROM mcp_local.rebuild_state"
                )
            ).one()
            assert journal.subscription_name == "butlers_public_sub" and journal.attempt_id
            assert journal.target_digest == target.digest
            assert target.canonical in journal.manifests and full.canonical in journal.manifests
            assert journal.subscription_oids
            assert not conn.execute(
                text(
                    "SELECT has_table_privilege('mcp_public_ro','mcp_local.rebuild_state','SELECT') OR has_table_privilege('mcp_proposals_reader','mcp_local.rebuild_state','SELECT')"
                )
            ).scalar_one()
        with projection.connect() as conn:
            current_oid = conn.execute(
                text(
                    "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())"
                )
            ).scalar_one_or_none()
        candidates = list(journal.subscription_oids) + ([current_oid] if current_oid else [])
        with topology.admin.connect() as conn:
            real_sync = (
                conn.execute(
                    text(
                        "SELECT slot_name FROM pg_replication_slots WHERE EXISTS(SELECT 1 FROM unnest(CAST(:oids AS oid[])) o WHERE slot_name ~ ('^pg_'||o::text||'_sync_[0-9]+_[0-9]+$'))"
                    ),
                    {"oids": candidates},
                )
                .scalars()
                .all()
            )
        record_property("actual_owned_sync_slots_before_resume", len(real_sync))
        # A changed-target retry retains all retired declarations and OIDs; it
        # must not silently forget the interrupted narrowing attempt.
        _apply(topology, projection, full)
        assert _scalar(projection, "SELECT count(*) FROM mcp_local.rebuild_state") == 0
        for observed_slot in real_sync:
            assert (
                _scalar(
                    topology.admin,
                    "SELECT count(*) FROM pg_replication_slots WHERE slot_name=:slot",
                    {"slot": observed_slot},
                )
                == 0
            )
        assert (
            _scalar(
                topology.admin,
                "SELECT count(*) FROM pg_replication_slots WHERE slot_name=:slot",
                {"slot": slot},
            )
            == 1
        )
        assert unrelated.execute(text("SELECT pg_backend_pid()")).scalar_one() == pid
    finally:
        unrelated.execute(text("SELECT pg_drop_replication_slot(:slot)"), {"slot": slot})
        unrelated.close()
        _apply(topology, projection, full)


def test_malformed_foreign_or_conflicting_journal_refuses_without_cleanup(copied):
    import hashlib

    topology, projection, manifest = copied
    _, narrow = _states("column-narrow")
    current_oid = _scalar(
        projection,
        "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())",
    )
    _reconcile(topology, narrow)
    program = render_owner_program(narrow)
    cut = program.index(
        "SELECT EXISTS(SELECT 1 FROM pg_subscription",
        program.index("DELETE FROM mcp_local.manifest_applied"),
    )
    interrupted = _run(
        topology,
        program[:cut] + "DO $$ BEGIN RAISE EXCEPTION 'journal control interruption'; END $$;",
        success=False,
    )
    assert b"journal control interruption" in interrupted.output
    assert _scalar(projection, "SELECT count(*) FROM mcp_local.manifest_applied") == 0
    with projection.connect() as conn:
        original = conn.execute(
            text(
                "SELECT database_oid,target_digest,manifests,subscription_oids FROM mcp_local.rebuild_state"
            )
        ).one()
    with topology.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql(
            "CREATE SUBSCRIPTION mcp_e_foreign CONNECTION 'host=/controller/run dbname=butlers user=postgres' PUBLICATION butlers_public_v1 WITH(connect=false,create_slot=false,enabled=false,slot_name=NONE)"
        )
        foreign_oid = conn.execute(
            text(
                "SELECT oid FROM pg_subscription WHERE subname='mcp_e_foreign' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())"
            )
        ).scalar_one()
    try:
        for fault in (
            "database",
            "digest",
            "canonical",
            "foreign-oid",
            "acl",
            "shape",
            "undeclared-local",
            "completion",
        ):
            try:
                with projection.begin() as conn:
                    if fault == "database":
                        conn.exec_driver_sql(
                            "UPDATE mcp_local.rebuild_state SET database_oid=(SELECT oid FROM pg_database WHERE datname='postgres')"
                        )
                    elif fault == "digest":
                        conn.exec_driver_sql(
                            "UPDATE mcp_local.rebuild_state SET target_digest=repeat('0',64)"
                        )
                    elif fault == "canonical":
                        malformed = '{"tables":[],"version":1}'
                        conn.execute(
                            text(
                                "UPDATE mcp_local.rebuild_state SET manifests=ARRAY[CAST(:canonical AS text)],target_digest=:digest"
                            ),
                            {
                                "canonical": malformed,
                                "digest": hashlib.sha256(malformed.encode()).hexdigest(),
                            },
                        )
                    elif fault == "foreign-oid":
                        conn.execute(
                            text(
                                "UPDATE mcp_local.rebuild_state SET subscription_oids=array_append(subscription_oids,CAST(:oid AS oid))"
                            ),
                            {"oid": foreign_oid},
                        )
                    elif fault == "acl":
                        conn.exec_driver_sql(
                            "GRANT SELECT ON mcp_local.rebuild_state TO mcp_public_ro"
                        )
                    elif fault == "undeclared-local":
                        conn.exec_driver_sql("SET LOCAL ROLE mcp_projection_owner")
                        conn.exec_driver_sql(
                            "CREATE VIEW mcp_local.unexpected AS SELECT 1 AS value"
                        )
                    elif fault == "shape":
                        conn.exec_driver_sql(
                            "ALTER TABLE mcp_local.rebuild_state ADD COLUMN unexpected text"
                        )
                    else:
                        conn.execute(
                            text(
                                "INSERT INTO mcp_local.manifest_applied(digest,canonical) VALUES (:digest,:canonical)"
                            ),
                            {"digest": manifest.digest, "canonical": manifest.canonical},
                        )
                refused = _run(topology, render_owner_program(narrow), success=False)
                assert any(
                    label in refused.output
                    for label in (
                        b"journal",
                        b"managed table",
                        b"applied manifest",
                        b"undeclared local catalog",
                    )
                )
                assert _scalar(projection, "SELECT count(*) FROM mcp_local.manifest_applied") == (
                    1 if fault == "completion" else 0
                )
                assert (
                    _scalar(
                        projection,
                        "SELECT oid FROM pg_subscription WHERE subname='butlers_public_sub' AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())",
                    )
                    == current_oid
                )
                assert (
                    _scalar(
                        topology.admin,
                        "SELECT count(*) FROM pg_subscription WHERE oid=:oid",
                        {"oid": foreign_oid},
                    )
                    == 1
                )
            finally:
                with projection.begin() as conn:
                    if fault == "acl":
                        conn.exec_driver_sql(
                            "REVOKE SELECT ON mcp_local.rebuild_state FROM mcp_public_ro"
                        )
                    elif fault == "undeclared-local":
                        conn.exec_driver_sql("SET LOCAL ROLE mcp_projection_owner")
                        conn.exec_driver_sql("DROP VIEW mcp_local.unexpected")
                    elif fault == "shape":
                        conn.exec_driver_sql(
                            "ALTER TABLE mcp_local.rebuild_state DROP COLUMN unexpected"
                        )
                    elif fault == "completion":
                        conn.exec_driver_sql("DELETE FROM mcp_local.manifest_applied")
                    conn.execute(
                        text(
                            "UPDATE mcp_local.rebuild_state SET database_oid=:db,target_digest=:digest,manifests=CAST(:manifests AS text[]),subscription_oids=CAST(:oids AS oid[])"
                        ),
                        {
                            "db": original.database_oid,
                            "digest": original.target_digest,
                            "manifests": original.manifests,
                            "oids": original.subscription_oids,
                        },
                    )
        _apply(topology, projection, manifest)
        assert _ready(projection, manifest.digest)
    finally:
        with topology.admin.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("DROP SUBSCRIPTION mcp_e_foreign")
        _apply(topology, projection, manifest)
