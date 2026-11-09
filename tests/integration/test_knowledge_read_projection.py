"""The public read functions over a genuinely replicated projection (RFC 0039 D10, D12).

Source rows are written on the primary by their real writers and triggers, reach
``butlers_public`` through the real ``butlers_public_v1`` publication and subscription, and are
read back through the production readers as ``mcp_public_ro`` over TCP. Each case positions its
absence checks against a row it first saw published, and keeps a healthy neighbour.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text

from butlers.knowledge_read.chronology import time_use, timeline
from butlers.knowledge_read.core import (
    ReadContext,
    ReadError,
    projection_disabled_domains,
    read_context,
)
from butlers.knowledge_read.entities import entity_lookup, entity_profile
from butlers.knowledge_read.finance import finance_ledger
from butlers.knowledge_read.manifest import load_manifest
from butlers.migrations.public_mcp import reconcile_connection
from butlers.migrations.public_mcp_owner import render_owner_program
from butlers.testing.public_mcp import public_mcp_topology

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]

KEY = b"process-lifetime-test-key-000000"


def _wait(predicate, *, seconds=40):
    deadline = time.monotonic() + seconds
    while not (value := predicate()):
        assert time.monotonic() < deadline, "the subscriber did not reach the observation"
        time.sleep(0.05)
    return value


def _scalar(engine, sql, params=None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar_one()


@pytest.fixture(scope="module")
def projected():
    with public_mcp_topology() as topology:
        projection = topology.projection_engine()
        manifest = load_manifest()
        try:
            with topology.engine.begin() as conn:
                reconcile_connection(conn, manifest)
            result = topology.execute(render_owner_program(manifest))
            assert result.exit_code == 0, repr(result.output)[-4000:]
            _wait(
                lambda: _scalar(
                    projection,
                    "SELECT COALESCE(bool_and(copy_complete AND digest=:d), false) "
                    "FROM mcp_read.manifest_applied",
                    {"d": manifest.digest},
                )
            )
            yield topology, projection
        finally:
            projection.dispose()


def _barrier(projected):
    """Wait until a committed write to an already-published control row has replicated."""
    topology, projection = projected
    with topology.engine.begin() as conn:
        changed = conn.execute(
            text(
                "UPDATE mcp_surface.controls SET changed_at=GREATEST(clock_timestamp(),"
                "changed_at+interval '1 microsecond') WHERE domain='all' RETURNING changed_at"
            )
        ).scalar_one()
    _wait(
        lambda: _scalar(
            projection,
            "SELECT changed_at=:c FROM mcp_surface.controls WHERE domain='all'",
            {"c": changed},
        )
    )


@pytest.fixture
async def primary(projected):
    topology, _ = projected
    url = topology.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=2,
        command_timeout=15,
        server_settings={"search_path": "chronicler,public"},
    )
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def reader(projected):
    topology, _ = projected
    conn = await topology.reader_connection()
    try:
        assert await conn.fetchval("SELECT current_user") == "mcp_public_ro"
        yield conn
    finally:
        await conn.close()


async def _context(reader, *, sub="github|1", disabled=frozenset()) -> ReadContext:
    context = await read_context(reader, sub=sub, cursor_key=KEY, disabled_domains=disabled)
    assert context.freshness == "fresh"
    return context


async def _refused(call) -> ReadError:
    with pytest.raises(ReadError) as caught:
        await call
    return caught.value


async def _entity(pool, name, *, metadata=None, aliases=(), entity_type="person"):
    return str(
        await pool.fetchval(
            "INSERT INTO public.entities(canonical_name,entity_type,aliases,metadata) "
            "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
            name,
            entity_type,
            list(aliases),
            json.dumps(metadata or {}),
        )
    )


async def test_entity_profile_serves_facts_and_edges_and_drops_a_retraction(
    projected, primary, reader
):
    """REQ-public-mcp-projection-005 (retraction) and REQ-public-mcp-surface-005/007."""
    from butlers.core.entity_graph_edges import (
        delete_entity_graph_edge,
        project_entity_graph_edge,
        withhold_entity_graph_edge,
    )

    person = await _entity(primary, "Profile Subject Avery")
    other = await _entity(primary, "Profile Object Blake")
    created = []
    try:
        works_at = await primary.fetchval(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES ($1,'works-at',$2,'entity','reader-test') RETURNING id",
            uuid.UUID(person),
            other,
        )
        knows = await primary.fetchval(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES ($1,'knows',$2,'entity','reader-test') RETURNING id",
            uuid.UUID(person),
            other,
        )
        email = await primary.fetchval(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES ($1,'has-email','avery@example.test','literal','reader-test') RETURNING id",
            uuid.UUID(person),
        )
        interaction = await primary.fetchval(
            "INSERT INTO relationship.facts(subject,predicate,content,entity_id,sensitivity,"
            "validity,valid_at) VALUES ('Avery','interaction','Lunch with Avery',$1,'normal',"
            "'active',now()) "
            "RETURNING id",
            uuid.UUID(person),
        )
        contact = await primary.fetchval(
            "INSERT INTO relationship.facts(subject,predicate,content,entity_id,sensitivity,"
            "validity) VALUES ('Avery','contact_email','avery@example.test',$1,'normal',"
            "'active') RETURNING id",
            uuid.UUID(person),
        )
        created += [
            ("relationship.entity_facts", works_at),
            ("relationship.entity_facts", knows),
            ("relationship.entity_facts", email),
            ("relationship.facts", interaction),
            ("relationship.facts", contact),
        ]
        graph_facts = []
        async with primary.acquire() as conn, conn.transaction():
            for content in ("Avery knows Blake", "Avery met Blake"):
                fact = await conn.fetchval(
                    "INSERT INTO general.facts(subject,predicate,content,sensitivity,validity) "
                    "VALUES ($1,'knows',$2,'normal','active') RETURNING id",
                    f"{person}:{content}",
                    content,
                )
                graph_facts.append(fact)
                await project_entity_graph_edge(
                    conn,
                    source_schema="general",
                    source_table="facts",
                    source_id=fact,
                    subject_entity_id=uuid.UUID(person),
                    predicate="knows",
                    object_entity_id=uuid.UUID(other),
                )
            await conn.execute(
                "UPDATE general.facts SET sensitivity='pii' WHERE id=$1", graph_facts[1]
            )
            await withhold_entity_graph_edge(
                conn,
                source_schema="general",
                source_table="facts",
                source_id=graph_facts[1],
                subject_entity_id=uuid.UUID(person),
                sensitivity="pii",
            )
        created += [("general.facts", f) for f in graph_facts]
        _barrier(projected)

        context = await _context(reader)
        profile = await entity_profile(reader, context, {"entity_id": person})
        assert profile["entity"]["entity_id"] == person
        assert profile["entity"]["name"] == "Profile Subject Avery"
        by_id = {f["id"]: f for f in profile["facts"]}
        assert set(by_id) == {str(works_at), str(knows), str(interaction)}
        assert by_id[str(works_at)]["predicate"] == "works-at"
        assert by_id[str(works_at)]["object_entity_id"] == other
        assert by_id[str(interaction)]["content"] == "Lunch with Avery"
        assert by_id[str(interaction)]["valid_at"] is not None
        assert by_id[str(interaction)]["source"] == {
            "kind": "fact",
            "uri": f"butlers://relationship/fact/{interaction}",
            "as_of": context.source("", "")["as_of"],
            "content_authority": None,
        }
        assert "avery@example.test" not in json.dumps(profile)
        assert profile["edges"] == {"known": 1, "withheld": 1}
        assert profile["coverage"]["facts_complete"] is True

        narrowed = await entity_profile(
            reader, context, {"entity_id": person, "sections": ["edges"]}
        )
        assert narrowed["facts"] == [] and narrowed["edges"] == {"known": 1, "withheld": 1}
        # The edge and stub come from a knowledge-domain source; disabling it drops both.
        knowledge_off = await _context(reader, disabled=frozenset({"knowledge"}))
        dropped = await entity_profile(reader, knowledge_off, {"entity_id": person})
        assert dropped["edges"] == {"known": 0, "withheld": 0}
        assert dropped["coverage"]["domains_omitted"] == ["knowledge"]
        assert len(dropped["facts"]) == 3

        await primary.execute(
            "UPDATE relationship.entity_facts SET validity='retracted' WHERE id=$1", works_at
        )
        _barrier(projected)
        after = await entity_profile(reader, await _context(reader), {"entity_id": person})
        assert {f["id"] for f in after["facts"]} == {str(knows), str(interaction)}

        missing = await _refused(entity_profile(reader, context, {"entity_id": str(uuid.uuid4())}))
        assert missing.code == "not_found"
        relationship_off = await _context(reader, disabled=frozenset({"relationship"}))
        off = await _refused(entity_profile(reader, relationship_off, {"entity_id": person}))
        assert (off.code, off.domain) == ("domain_disabled", "relationship")
        bad = await _refused(entity_profile(reader, context, {"entity_id": person, "x": 1}))
        assert bad.code == "invalid_argument"
        async with primary.acquire() as conn, conn.transaction():
            for fact in graph_facts:
                await delete_entity_graph_edge(
                    conn, source_schema="general", source_table="facts", source_id=fact
                )
    finally:
        for table, row in created:
            await primary.execute(f"DELETE FROM {table} WHERE id=$1", row)
        for entity in (person, other):
            await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(entity))
        _barrier(projected)


async def test_entity_lookup_matches_resolves_merges_and_follows_flags(projected, primary, reader):
    """REQ-public-mcp-projection-007 consumer clauses and REQ-public-mcp-surface-007 lookup."""
    survivor = await _entity(primary, "Lookup Quinlan Harper", aliases=["Q. Harper"])
    merged = await _entity(primary, "Lookup Quinlan Harpur", metadata={"merged_into": survivor})
    healthy = await _entity(primary, "Lookup Rosalind Vance")
    doomed = await _entity(primary, "Lookup Thaddeus Okafor")
    placeholder = await _entity(
        primary,
        "Unknown (email lookup-sender@example.test)",
        metadata={"source_channel": "email", "source_value": "lookup-sender@example.test"},
    )
    try:
        _barrier(projected)
        context = await _context(reader)
        exact = await entity_lookup(reader, context, {"name": "lookup quinlan harper"})
        first = exact["matches"][0]
        assert (first["entity_id"], first["match"], first["score"]) == (survivor, "exact", 1.0)
        # The merged entity resolves to its survivor, never appears itself.
        ids = [m["entity_id"] for m in exact["matches"]]
        assert merged not in ids and ids.count(survivor) == 1
        via_merged = await entity_lookup(reader, context, {"name": "Lookup Quinlan Harpur"})
        assert via_merged["matches"][0]["entity_id"] == survivor
        assert via_merged["matches"][0]["name"] == "Lookup Quinlan Harper"
        alias = await entity_lookup(reader, context, {"name": "q. harper"})
        assert (alias["matches"][0]["entity_id"], alias["matches"][0]["match"]) == (
            survivor,
            "alias",
        )
        fuzzy = await entity_lookup(reader, context, {"name": "Rosalind Vanse"})
        top = fuzzy["matches"][0]
        assert top["entity_id"] == healthy and top["match"] == "trigram"
        assert 0.3 <= top["score"] < 1.0
        assert fuzzy["matches"][0]["source"]["uri"] == f"butlers://relationship/entity/{healthy}"
        typed = await entity_lookup(
            reader, context, {"name": "Rosalind Vance", "type": "organization"}
        )
        assert typed["matches"] == []
        sender = await entity_lookup(reader, context, {"name": "lookup-sender@example.test"})
        assert placeholder not in [m["entity_id"] for m in sender["matches"]]

        assert (await entity_lookup(reader, context, {"name": "Thaddeus Okafor"}))["matches"][0][
            "entity_id"
        ] == doomed
        await primary.execute(
            "UPDATE public.entities SET metadata=metadata||jsonb_build_object('deleted_at',now()) "
            "WHERE id=$1",
            uuid.UUID(doomed),
        )
        # Promotion clears unidentified; renaming the placeholder publishes it.
        await primary.execute(
            "UPDATE public.entities SET canonical_name='Lookup Promoted Sender' WHERE id=$1",
            uuid.UUID(placeholder),
        )
        _barrier(projected)
        context = await _context(reader)
        gone = await entity_lookup(reader, context, {"name": "Lookup Thaddeus Okafor"})
        assert doomed not in [m["entity_id"] for m in gone["matches"]]
        promoted = await entity_lookup(reader, context, {"name": "Lookup Promoted Sender"})
        assert promoted["matches"][0]["entity_id"] == placeholder
        assert "lookup-sender@example.test" not in json.dumps(promoted)
        stillthere = await entity_lookup(reader, context, {"name": "Lookup Rosalind Vance"})
        assert stillthere["matches"][0]["entity_id"] == healthy

        long_name = await _refused(entity_lookup(reader, context, {"name": "x" * 129}))
        assert long_name.code == "invalid_argument"
    finally:
        for entity in (merged, survivor, healthy, doomed, placeholder):
            await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(entity))
        _barrier(projected)


async def test_timeline_and_time_use_follow_corrected_chronicler_visibility(
    projected, primary, reader
):
    """REQ-public-mcp-projection-006 consumer clauses and REQ-public-mcp-surface-008."""
    from butlers.chronicler.public_projection import sweep_public_projection

    day = datetime(2026, 3, 10, 2, 0, tzinfo=UTC)
    adapter = bool(
        await primary.fetchval(
            "INSERT INTO chronicler.source_adapter_state(source_name,chronicler_compatibility) "
            "VALUES ('google_health.measurements','supported') ON CONFLICT DO NOTHING "
            "RETURNING true"
        )
    )
    episodes = []
    trip = None
    try:
        for offset, title, privacy in (
            (0, "Timeline sleep", "sensitive"),
            (4, "Timeline nap", "normal"),
        ):
            episodes.append(
                await primary.fetchval(
                    "INSERT INTO chronicler.episodes(source_name,source_ref,episode_type,"
                    "start_at,end_at,title,privacy,layer,payload) VALUES "
                    "('google_health.measurements',$1,'sleep_episode',$2,$3,$4,$5,'activity',"
                    '\'{"heart_rate":"payload-marker-71"}\'::jsonb) RETURNING id',
                    str(uuid.uuid4()),
                    day + timedelta(hours=offset),
                    day + timedelta(hours=offset + 1),
                    title,
                    privacy,
                )
            )
        sleep, nap = (str(e) for e in episodes)
        trip = await primary.fetchval(
            "INSERT INTO travel.trips(name,destination,start_date,end_date,status) VALUES "
            "('Timeline trip','Osaka','2026-03-10','2026-03-12','planned') RETURNING id"
        )
        leg = str(
            await primary.fetchval(
                "INSERT INTO travel.legs(trip_id,type,carrier,departure_city,departure_at,"
                "arrival_city,arrival_at,confirmation_number) VALUES ($1,'flight','SQ',"
                "'Singapore',$2,'Osaka',$3,'CONF-MARKER-9') RETURNING id",
                trip,
                day + timedelta(hours=2),
                day + timedelta(hours=8),
            )
        )
        await sweep_public_projection(primary)
        _barrier(projected)

        window = {"start": "2026-03-10", "end": "2026-03-11"}
        context = await _context(reader)
        page = await timeline(reader, context, window)
        items = {i["source"]["uri"].rsplit("/", 1)[1]: i for i in page["items"]}
        assert set(items) == {sleep, nap, leg}
        assert items[sleep] == {
            "at": items[sleep]["at"],
            "end": items[sleep]["end"],
            "kind": "sleep_episode",
            "title": "Timeline sleep",
            "domain": "time",
            "source": context.source("episode", f"butlers://time/episode/{sleep}"),
        }
        assert datetime.fromisoformat(items[sleep]["at"]) == day
        assert items[leg]["domain"] == "travel" and items[leg]["kind"] == "trip_leg"
        assert items[leg]["title"] == "flight SQ Singapore to Osaka"
        assert [i["source"]["uri"] for i in page["items"]] == [
            f"butlers://time/episode/{sleep}",
            f"butlers://travel/leg/{leg}",
            f"butlers://time/episode/{nap}",
        ]
        rendered = json.dumps(page)
        assert "payload-marker-71" not in rendered and "CONF-MARKER-9" not in rendered
        assert page["next_cursor"] is None and page["sources_degraded"] == []

        # Paging: two pages, no overlap or gap, cursor bound to its caller and arguments.
        first = await timeline(reader, context, {**window, "limit": 2})
        second = await timeline(
            reader, context, {**window, "limit": 2, "cursor": first["next_cursor"]}
        )
        assert first["items"] + second["items"] == page["items"]
        assert second["next_cursor"] is None
        other_sub = await _context(reader, sub="github|2")
        stolen = await _refused(
            timeline(reader, other_sub, {**window, "limit": 2, "cursor": first["next_cursor"]})
        )
        assert stolen.code == "invalid_argument"

        travel_off = await timeline(
            reader, await _context(reader, disabled=frozenset({"travel"})), window
        )
        assert {i["domain"] for i in travel_off["items"]} == {"time"}
        assert travel_off["sources_degraded"] == [{"domain": "travel", "reason": "disabled"}]
        kinds = await timeline(reader, context, {**window, "kinds": ["trip_leg"]})
        assert [i["domain"] for i in kinds["items"]] == ["travel"]

        usage = {"start": "2026-03-10", "end": "2026-03-10", "bucket": "day", "group_by": "kind"}
        series = await time_use(reader, context, usage)
        assert series["series"] == [
            {"bucket_start": "2026-03-10", "group": "sleep_episode", "minutes": 120}
        ]
        assert series["source"] == {
            "kind": "time_use",
            "uri": "butlers://time/time_use",
            "as_of": context.source("", "")["as_of"],
        }

        # Owner restriction leaves; a model correction cannot republish; the owner can lift it.
        await primary.execute(
            "INSERT INTO chronicler.overrides(target_kind,target_id,corrected_privacy,"
            "submitted_by) VALUES ('episode',$1,'restricted','owner')",
            uuid.UUID(sleep),
        )
        await primary.execute(
            "INSERT INTO chronicler.overrides(target_kind,target_id,corrected_privacy,"
            "submitted_by) VALUES ('episode',$1,'normal','user')",
            uuid.UUID(sleep),
        )
        _barrier(projected)
        context = await _context(reader)
        restricted = await timeline(reader, context, window)
        assert {i["source"]["uri"].rsplit("/", 1)[1] for i in restricted["items"]} == {nap, leg}
        assert (await time_use(reader, context, usage))["series"][0]["minutes"] == 60
        await primary.execute(
            "INSERT INTO chronicler.overrides(target_kind,target_id,corrected_privacy,"
            "submitted_by) VALUES ('episode',$1,'normal','owner')",
            uuid.UUID(sleep),
        )
        _barrier(projected)
        context = await _context(reader)
        lifted = await timeline(reader, context, window)
        assert sleep in {i["source"]["uri"].rsplit("/", 1)[1] for i in lifted["items"]}
        assert (await time_use(reader, context, usage))["series"][0]["minutes"] == 120

        # A tombstone removes the episode from both the timeline and time use.
        await primary.execute(
            "INSERT INTO chronicler.overrides(target_kind,target_id,corrected_tombstone_at,"
            "submitted_by) VALUES ('episode',$1,now(),'owner')",
            uuid.UUID(sleep),
        )
        _barrier(projected)
        context = await _context(reader)
        tombstoned = await timeline(reader, context, window)
        assert {i["source"]["uri"].rsplit("/", 1)[1] for i in tombstoned["items"]} == {nap, leg}
        assert (await time_use(reader, context, usage))["series"] == [
            {"bucket_start": "2026-03-10", "group": "sleep_episode", "minutes": 60}
        ]

        too_long = await _refused(
            timeline(reader, context, {"start": "2026-01-01", "end": "2026-05-01"})
        )
        assert too_long.code == "invalid_argument"
        time_off = await _context(reader, disabled=frozenset({"time"}))
        assert (await _refused(time_use(reader, time_off, usage))).code == "domain_disabled"
    finally:
        for episode in episodes:
            await primary.execute("DELETE FROM chronicler.overrides WHERE target_id=$1", episode)
            await primary.execute("DELETE FROM chronicler.episodes WHERE id=$1", episode)
        if trip:
            await primary.execute("DELETE FROM travel.trips WHERE id=$1", trip)
        await sweep_public_projection(primary)
        if adapter:
            await primary.execute(
                "DELETE FROM chronicler.source_adapter_state "
                "WHERE source_name='google_health.measurements'"
            )
        _barrier(projected)


async def test_finance_ledger_serves_display_overlays_exact_amounts_and_pages(
    projected, primary, reader
):
    """REQ-public-mcp-projection-013 ledger clause and REQ-public-mcp-surface-009/012."""
    account = await primary.fetchval(
        "INSERT INTO finance.accounts(institution,type,name,last_four,currency) VALUES "
        "('Ledger Bank','credit','Ledger Card','4242','SGD') RETURNING id"
    )
    rows = []
    try:
        for day, merchant, amount in (
            ("2026-04-01T09:00:00+00:00", "ACME*1234 SG", "12.30"),
            ("2026-04-02T09:00:00+00:00", "Corner Grocer", "8.05"),
            ("2026-04-03T23:59:00+00:00", "Night Market", "100.00"),
        ):
            rows.append(
                await primary.fetchval(
                    "INSERT INTO finance.transactions(account_id,posted_at,merchant,amount,"
                    "currency,direction,category,notes,tags) VALUES ($1,$2,$3,"
                    "$4::numeric,'SGD','debit','shopping','ledger note',ARRAY['t1']) "
                    "RETURNING id",
                    account,
                    datetime.fromisoformat(day),
                    merchant,
                    amount,
                )
            )
        hidden = await primary.fetchval(
            "INSERT INTO finance.transactions(posted_at,merchant,amount,currency,direction,"
            "category,deleted_at) VALUES ('2026-04-02T09:00:00+00:00','Deleted Shop',1.00,'SGD','debit',"
            "'shopping',now()) RETURNING id"
        )
        rows.append(hidden)
        _barrier(projected)
        context = await _context(reader)
        window = {"start": "2026-04-01", "end": "2026-04-03"}
        ledger = await finance_ledger(reader, context, window)
        assert [i["id"] for i in ledger["items"]] == [str(r) for r in reversed(rows[:3])]
        acme = ledger["items"][2]
        assert acme["merchant"] == "ACME*1234 SG" and acme["amount"] == "12.30"
        assert acme["category"] == "shopping" and acme["currency"] == "SGD"
        assert acme["notes"] == "ledger note" and acme["tags"] == ["t1"]
        assert acme["account_label"] == "Ledger Bank credit"
        assert acme["account"] == {
            "id": str(account),
            "institution": "Ledger Bank",
            "type": "credit",
            "name": "Ledger Card",
            "last_four": "4242",
        }
        assert acme["source"]["uri"] == f"butlers://finance/transactions/{rows[0]}"
        assert "Deleted Shop" not in json.dumps(ledger)

        first = await finance_ledger(reader, context, {**window, "limit": 2})
        second = await finance_ledger(
            reader, context, {**window, "limit": 2, "cursor": first["next_cursor"]}
        )
        assert first["items"] + second["items"] == ledger["items"]
        changed = await _refused(
            finance_ledger(reader, context, {**window, "limit": 3, "cursor": first["next_cursor"]})
        )
        assert changed.code == "invalid_argument"

        await primary.execute(
            "UPDATE finance.transactions SET metadata=metadata||"
            '\'{"normalized_merchant":"Acme","inferred_category":"groceries"}\'::jsonb '
            "WHERE id=$1",
            rows[0],
        )
        _barrier(projected)
        context = await _context(reader)
        overlay = await finance_ledger(reader, context, {**window, "merchant": "acme"})
        assert [(i["id"], i["merchant"], i["category"]) for i in overlay["items"]] == [
            (str(rows[0]), "Acme", "groceries")
        ]
        groceries = await finance_ledger(reader, context, {**window, "category": "Groceries"})
        assert [i["id"] for i in groceries["items"]] == [str(rows[0])]

        finance_off = await _context(reader, disabled=frozenset({"finance"}))
        off = await _refused(finance_ledger(reader, finance_off, window))
        assert (off.code, off.domain) == ("domain_disabled", "finance")
        wide = await _refused(
            finance_ledger(reader, context, {"start": "2025-01-01", "end": "2026-04-03"})
        )
        assert wide.code == "invalid_argument"
    finally:
        for row in rows:
            await primary.execute("DELETE FROM finance.transactions WHERE id=$1", row)
        await primary.execute("DELETE FROM finance.accounts WHERE id=$1", account)
        _barrier(projected)


async def test_replicated_controls_and_reads_are_pure(projected, primary, reader):
    """REQ-public-mcp-surface-011: reads change no row version on either database."""
    topology, projection = projected
    person = await _entity(primary, "Purity Subject Morgan")
    fact = await primary.fetchval(
        "INSERT INTO relationship.facts(subject,predicate,content,entity_id,sensitivity,"
        "validity) VALUES ('Morgan','interaction','Purity coffee',$1,'normal','active') "
        "RETURNING id",
        uuid.UUID(person),
    )
    try:
        _barrier(projected)

        def versions(engine, tables_sql):
            with engine.connect() as conn:
                tables = conn.execute(text(tables_sql)).scalars().all()
                return {
                    t: conn.execute(
                        text(
                            f"SELECT count(*)||':'||COALESCE(md5(string_agg(xmin::text||ctid::text,"
                            f"',' ORDER BY ctid)),'') FROM {t}"
                        )
                    ).scalar_one()
                    for t in tables
                }

        projected_tables = (
            "SELECT format('%I.%I',schemaname,tablename) FROM pg_tables "
            "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1"
        )
        before = versions(projection, projected_tables)
        counters_sql = (
            "SELECT reference_count, last_referenced_at FROM relationship.facts WHERE id=:id"
        )
        with topology.engine.connect() as conn:
            counters = conn.execute(text(counters_sql), {"id": fact}).one()
        assert len(before) > 40
        for _ in range(5):
            context = await _context(reader)
            await entity_lookup(reader, context, {"name": "Purity Subject Morgan"})
            await entity_profile(reader, context, {"entity_id": person})
            await timeline(reader, context, {"start": "2026-01-01", "end": "2026-03-01"})
            await time_use(
                reader,
                context,
                {"start": "2026-01-01", "end": "2026-12-31", "bucket": "month", "group_by": "kind"},
            )
            await finance_ledger(reader, context, {"start": "2026-01-01", "end": "2026-12-31"})
        assert versions(projection, projected_tables) == before
        with topology.engine.connect() as conn:
            assert conn.execute(text(counters_sql), {"id": fact}).one() == counters

        # Of the trigram extension the reader may execute similarity() alone.
        assert await reader.fetchval("SELECT mcp_ext.similarity('abc', 'abd')") > 0
        for denied in (
            "SELECT mcp_ext.show_trgm('abc')",
            "SELECT mcp_ext.word_similarity('a','b')",
        ):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await reader.fetchval(denied)

        # The replicated owner switch is a readable layer the host composes.
        assert await projection_disabled_domains(reader) == frozenset()
        with topology.engine.begin() as conn:
            conn.execute(
                text("UPDATE mcp_surface.controls SET state='disabled' WHERE domain='finance'")
            )
        _barrier(projected)
        assert await projection_disabled_domains(reader) == frozenset({"finance"})
    finally:
        with topology.engine.begin() as conn:
            conn.execute(text("UPDATE mcp_surface.controls SET state='enabled'"))
        await primary.execute("DELETE FROM relationship.facts WHERE id=$1", fact)
        await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(person))
        _barrier(projected)
