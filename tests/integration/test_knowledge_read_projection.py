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
from sqlalchemy.exc import DBAPIError

from butlers.knowledge_read.chronology import time_use, timeline
from butlers.knowledge_read.core import (
    ReadContext,
    ReadError,
    projection_disabled_domains,
    read_context,
)
from butlers.knowledge_read.describe import describe
from butlers.knowledge_read.entities import entity_lookup, entity_profile
from butlers.knowledge_read.finance import finance_ledger
from butlers.knowledge_read.graph import graph_neighbors, graph_path
from butlers.knowledge_read.manifest import load_manifest
from butlers.knowledge_read.search import fetch, search, search_filtered
from butlers.knowledge_read.travel import trips
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
        # Facts that name Avery only as their object belong to Avery's profile too.
        befriended = await primary.fetchval(
            "INSERT INTO relationship.entity_facts(subject,predicate,object,object_kind,src) "
            "VALUES ($1,'friend-of',$2,'entity','reader-test') RETURNING id",
            uuid.UUID(other),
            person,
        )
        gift = await primary.fetchval(
            "INSERT INTO relationship.facts(subject,predicate,content,entity_id,"
            "object_entity_id,sensitivity,validity,valid_at) VALUES ('Blake','gift',"
            "'Blake gave Avery a book',$1,$2,'normal','active',now()) RETURNING id",
            uuid.UUID(other),
            uuid.UUID(person),
        )
        created += [
            ("relationship.entity_facts", works_at),
            ("relationship.entity_facts", knows),
            ("relationship.entity_facts", email),
            ("relationship.entity_facts", befriended),
            ("relationship.facts", interaction),
            ("relationship.facts", contact),
            ("relationship.facts", gift),
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
        assert set(by_id) == {
            str(works_at),
            str(knows),
            str(interaction),
            str(befriended),
            str(gift),
        }
        assert by_id[str(befriended)]["subject_entity_id"] == other
        assert by_id[str(gift)]["object_entity_id"] == person
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
        assert len(dropped["facts"]) == 5

        await primary.execute(
            "UPDATE relationship.entity_facts SET validity='retracted' WHERE id=$1", works_at
        )
        # A memory fact reclassified to pii leaves the profile too.
        await primary.execute(
            "UPDATE relationship.facts SET sensitivity='pii' WHERE id=$1", interaction
        )
        _barrier(projected)
        after = await entity_profile(reader, await _context(reader), {"entity_id": person})
        assert {f["id"] for f in after["facts"]} == {str(knows), str(befriended), str(gift)}

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


async def test_entity_profile_fits_the_response_cap(projected, primary, reader):
    """REQ-public-mcp-surface-012: 200 long facts are cut to 256 KiB and marked, newest kept."""
    from butlers.knowledge_read.core import MAX_RESPONSE_BYTES

    person = await _entity(primary, "Profile Cap Subject")
    try:
        await primary.execute(
            "INSERT INTO relationship.facts(subject,predicate,content,entity_id,sensitivity,"
            "validity,valid_at) SELECT 'cap-' || n, 'note', repeat('x', 1400) || n, $1, "
            "'normal','active', now() - n * interval '1 minute' "
            "FROM generate_series(1, 200) AS n",
            uuid.UUID(person),
        )
        _barrier(projected)
        profile = await entity_profile(reader, await _context(reader), {"entity_id": person})
        assert len(json.dumps(profile, separators=(",", ":")).encode()) <= MAX_RESPONSE_BYTES
        assert profile["truncated"] is True
        assert profile["coverage"]["facts_complete"] is False
        assert 100 < len(profile["facts"]) < 200
        # The cut drops the oldest facts: what remains is the newest prefix.
        kept = [f["content"].lstrip("x") for f in profile["facts"]]
        assert kept == [str(n) for n in range(1, len(kept) + 1)]
        small = await entity_profile(
            reader, await _context(reader), {"entity_id": person, "sections": ["edges"]}
        )
        assert "truncated" not in small and small["coverage"]["facts_complete"] is True
    finally:
        await primary.execute(
            "DELETE FROM relationship.facts WHERE entity_id=$1", uuid.UUID(person)
        )
        await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(person))
        _barrier(projected)


async def test_entity_lookup_drops_merges_into_hidden_survivors_and_types_before_the_cut(
    projected, primary, reader, monkeypatch
):
    """A merge into a deleted or placeholder survivor publishes neither (REQ-...-007)."""
    from butlers.knowledge_read import entities

    deleted_survivor = await _entity(primary, "Hidden Survivor Ottoline")
    deleted_merged = await _entity(
        primary, "Hidden Merged Ottilie", metadata={"merged_into": deleted_survivor}
    )
    placeholder_survivor = await _entity(primary, "Hidden Survivor Peregrine")
    placeholder_merged = await _entity(
        primary, "Hidden Merged Peregryn", metadata={"merged_into": placeholder_survivor}
    )
    # Three organizations match by alias, which outranks the person's trigram match.
    organizations = [
        await _entity(
            primary,
            f"Typecut Holdings {n}",
            aliases=["Typecut Corvidae"],
            entity_type="organization",
        )
        for n in range(3)
    ]
    person = await _entity(primary, "Typecut Corvidae Fan")
    hidden = (deleted_survivor, deleted_merged, placeholder_survivor, placeholder_merged)
    names = (
        "Hidden Survivor Ottoline",
        "Hidden Merged Ottilie",
        "Hidden Survivor Peregrine",
        "Hidden Merged Peregryn",
    )
    try:
        _barrier(projected)
        context = await _context(reader)
        # Positive control: both merged names resolve to their published survivors.
        for merged_name, survivor in (
            ("Hidden Merged Ottilie", deleted_survivor),
            ("Hidden Merged Peregryn", placeholder_survivor),
        ):
            found = await entity_lookup(reader, context, {"name": merged_name})
            assert found["matches"][0]["entity_id"] == survivor

        await primary.execute(
            "UPDATE public.entities SET metadata=metadata||jsonb_build_object('deleted_at',now()) "
            "WHERE id=$1",
            uuid.UUID(deleted_survivor),
        )
        await primary.execute(
            "UPDATE public.entities SET metadata=metadata||jsonb_build_object("
            "'source_channel','email','source_value','peregrine@example.test'), "
            "canonical_name='Unknown (email peregrine@example.test)' WHERE id=$1",
            uuid.UUID(placeholder_survivor),
        )
        _barrier(projected)
        context = await _context(reader)
        for name in names:
            result = await entity_lookup(reader, context, {"name": name})
            assert not set(hidden) & {m["entity_id"] for m in result["matches"]}, name

        # The type filter runs before the candidate cut, so other-type matches cannot crowd out.
        monkeypatch.setattr(entities, "CANDIDATE_LIMIT", 2)
        typed = await entity_lookup(reader, context, {"name": "Typecut Corvidae", "type": "person"})
        assert [m["entity_id"] for m in typed["matches"]] == [person]
        assert typed["matches"][0]["match"] == "trigram"
    finally:
        for entity in (*hidden, *organizations, person):
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
            await search(reader, context, {"query": "Purity coffee"})
            await search_filtered(reader, context, {"query": "Purity", "limit": 5})
            await fetch(reader, context, {"id": f"butlers://relationship/fact/{fact}"})
            await graph_neighbors(reader, context, {"entity_id": person, "hops": 2})
            await graph_path(reader, context, {"from_id": person, "to_id": person})
            await trips(reader, context, {})
            await describe(reader, context, {})
        assert versions(projection, projected_tables) == before
        with topology.engine.connect() as conn:
            assert conn.execute(text(counters_sql), {"id": fact}).one() == counters

        # Of the trigram extension the reader may execute similarity(text, text) alone.
        assert await reader.fetchval("SELECT mcp_ext.similarity('abc', 'abd')") > 0
        grants = await reader.fetch(
            "SELECT p.oid::regprocedure::text AS name, has_function_privilege(p.oid, 'EXECUTE') "
            "AS allowed FROM pg_proc p WHERE p.pronamespace = 'mcp_ext'::regnamespace"
        )
        assert len(grants) > 20
        assert {g["name"] for g in grants if g["allowed"]} == {"mcp_ext.similarity(text,text)"}
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.fetchval("SELECT mcp_ext.show_trgm('abc')")

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


async def test_owner_planted_trigram_extension_is_refused(projected):
    """The projection owner cannot pre-create pg_trgm, plant a member and have it adopted.

    pg_trgm is a trusted extension, so the database owner can create it and add its own
    functions to it. The owner step must refuse that state before mutating anything, and
    the owner must not be able to create functions in the superuser-owned ``mcp_ext``.
    """
    topology, projection = projected
    manifest = load_manifest()
    program = render_owner_program(manifest)

    def superuser(sql):
        with projection.begin() as conn:
            conn.exec_driver_sql(sql)

    def as_owner(*statements):
        with projection.begin() as conn:
            conn.exec_driver_sql("SET LOCAL ROLE mcp_projection_owner")
            for statement in statements:
                conn.exec_driver_sql(statement)

    applied = _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied")
    try:
        # With the step applied, the owner cannot add a function to mcp_ext.
        with pytest.raises(DBAPIError):
            as_owner(
                "CREATE FUNCTION mcp_ext.planted(text) RETURNS text LANGUAGE sql AS 'SELECT $1'"
            )
        # An interrupted first step: no extension yet; the owner plants its own copy.
        superuser("DROP EXTENSION pg_trgm")
        superuser("DROP SCHEMA mcp_ext")
        as_owner(
            "CREATE SCHEMA mcp_ext",
            "CREATE EXTENSION pg_trgm WITH SCHEMA mcp_ext",
            "CREATE FUNCTION mcp_ext.planted(text) RETURNS text LANGUAGE sql AS 'SELECT $1'",
            "ALTER EXTENSION pg_trgm ADD FUNCTION mcp_ext.planted(text)",
        )
        refused = topology.execute(program)
        assert refused.exit_code != 0, "the owner step must refuse an owner-planted pg_trgm"
        assert b"pg_trgm" in bytes(refused.output)
        assert _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied") == applied
        # The same extension in another owner schema is refused too.
        as_owner(
            "DROP EXTENSION pg_trgm",
            "DROP SCHEMA mcp_ext",
            "CREATE EXTENSION pg_trgm SCHEMA mcp_read",
        )
        refused = topology.execute(program)
        assert refused.exit_code != 0 and b"pg_trgm" in bytes(refused.output)
    finally:
        superuser("DROP EXTENSION IF EXISTS pg_trgm")
        superuser("DROP SCHEMA IF EXISTS mcp_ext CASCADE")
        healed = topology.execute(program)
        assert healed.exit_code == 0, repr(healed.output)[-4000:]


async def test_owner_shadowed_catalog_names_cannot_admit_a_planted_extension(projected):
    """A database-level search_path and a shadowing ``public.pg_roles`` do not fool the step.

    The projection owner owns ``butlers_public``, so it can set the database's search_path and
    create objects in ``public``. The step pins its own search_path, qualifies catalog names
    and refuses both footholds before it trusts any catalog read.
    """
    topology, projection = projected
    program = render_owner_program(load_manifest())

    def superuser(sql):
        with projection.begin() as conn:
            conn.exec_driver_sql(sql)

    def as_owner(*statements):
        with projection.begin() as conn:
            conn.exec_driver_sql("SET LOCAL ROLE mcp_projection_owner")
            for statement in statements:
                conn.exec_driver_sql(statement)

    applied = _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied")
    try:
        superuser("DROP EXTENSION pg_trgm")
        superuser("DROP SCHEMA mcp_ext")
        as_owner(
            "CREATE SCHEMA mcp_ext",
            "CREATE EXTENSION pg_trgm WITH SCHEMA mcp_ext",
            "CREATE FUNCTION mcp_ext.planted(text) RETURNS text LANGUAGE sql AS 'SELECT $1'",
            "ALTER EXTENSION pg_trgm ADD FUNCTION mcp_ext.planted(text)",
            "ALTER DATABASE butlers_public SET search_path = public, pg_catalog",
            "CREATE VIEW public.pg_roles AS SELECT oid, rolname, true AS rolsuper"
            " FROM pg_catalog.pg_roles",
        )
        refused = topology.execute(program)
        assert refused.exit_code != 0, "the step must not trust a shadowed pg_roles"
        assert _scalar(projection, "SELECT applied_at FROM mcp_local.manifest_applied") == applied
        owner = _scalar(
            projection,
            "SELECT x.extowner::regrole::text FROM pg_catalog.pg_extension x"
            " WHERE x.extname='pg_trgm'",
        )
        assert owner == "mcp_projection_owner", "the planted extension must not be adopted"

        # Each foothold is refused on its own.
        superuser("DROP VIEW public.pg_roles")
        refused = topology.execute(program)
        assert refused.exit_code != 0 and b"database setting" in bytes(refused.output)
        superuser("ALTER DATABASE butlers_public RESET search_path")
        superuser("DROP EXTENSION pg_trgm")
        superuser("DROP SCHEMA mcp_ext CASCADE")
        as_owner("CREATE FUNCTION public.shadow() RETURNS int LANGUAGE sql AS 'SELECT 1'")
        refused = topology.execute(program)
        assert refused.exit_code != 0 and b"public schema object" in bytes(refused.output)
    finally:
        superuser("ALTER DATABASE butlers_public RESET search_path")
        superuser("DROP VIEW IF EXISTS public.pg_roles")
        superuser("DROP FUNCTION IF EXISTS public.shadow()")
        superuser("DROP EXTENSION IF EXISTS pg_trgm")
        superuser("DROP SCHEMA IF EXISTS mcp_ext CASCADE")
        healed = topology.execute(program)
        assert healed.exit_code == 0, repr(healed.output)[-4000:]


async def _general_edge(conn, subject, obj, predicate="knows", *, withheld=False):
    """A ``general`` memory fact and its graph edge (or withheld stub), as the writer does."""
    from butlers.core.entity_graph_edges import (
        project_entity_graph_edge,
        withhold_entity_graph_edge,
    )

    fact = await conn.fetchval(
        "INSERT INTO general.facts(subject,predicate,content,sensitivity,validity) "
        "VALUES ($1,$2,$3,$4,'active') RETURNING id",
        f"{subject}:{obj}:{uuid.uuid4()}",
        predicate,
        f"{predicate} edge",
        "pii" if withheld else "normal",
    )
    if withheld:
        await withhold_entity_graph_edge(
            conn,
            source_schema="general",
            source_table="facts",
            source_id=fact,
            subject_entity_id=uuid.UUID(subject),
            sensitivity="pii",
        )
    else:
        await project_entity_graph_edge(
            conn,
            source_schema="general",
            source_table="facts",
            source_id=fact,
            subject_entity_id=uuid.UUID(subject),
            predicate=predicate,
            object_entity_id=uuid.UUID(obj),
        )
    return fact


async def test_search_and_fetch_keep_the_chatgpt_shape_and_never_reach_finance(
    projected, primary, reader
):
    """REQ-public-mcp-surface-003/004: exact shapes, no finance item, a 16 KiB cut, proposals."""
    from butlers.knowledge_read.search import FETCH_TEXT_BYTES, fetch, search

    person = await _entity(primary, "Quillonar Vargastead", aliases=["Quill V"])
    long_fact = await primary.fetchval(
        "INSERT INTO relationship.facts(subject,predicate,content,entity_id,sensitivity,"
        "validity,valid_at) VALUES ('Quillonar','note',$1,$2,'normal','active',now()) "
        "RETURNING id",
        "Quillonar Vargastead ramen notes " + "é" * 12000,
        uuid.UUID(person),
    )
    transaction = await primary.fetchval(
        "INSERT INTO finance.transactions(posted_at,merchant,amount,currency,direction,category)"
        " VALUES (now(),'Zephyrinequa Outfitters',9.99,'SGD','debit','shopping') RETURNING id"
    )
    trip = await primary.fetchval(
        "INSERT INTO travel.trips(name,destination,start_date,end_date,status) VALUES "
        "('Quillonar Lisbon trip','Lisbon','2026-05-01','2026-05-04','planned') RETURNING id"
    )
    proposal = uuid.uuid4()
    await primary.execute(
        "INSERT INTO relationship.external_fact_proposals(proposal_id,state,decided_at,note,"
        "evidence_url) VALUES ($1,'applied',now(),'proposal-note-marker',"
        "'https://evidence.invalid/marker')",
        proposal,
    )
    try:
        _barrier(projected)
        context = await _context(reader)
        found = await search(reader, context, {"query": "Quillonar Vargastead"})
        assert set(found) == {"results"}
        assert 0 < len(found["results"]) <= 20
        assert all(set(r) == {"id", "title", "url"} for r in found["results"])
        ids = [r["id"] for r in found["results"]]
        assert f"butlers://relationship/entity/{person}" in ids
        assert f"butlers://relationship/fact/{long_fact}" in ids
        assert f"butlers://travel/trip/{trip}" in ids
        assert all("?" not in r["url"] for r in found["results"])

        assert await search(reader, context, {"query": "Zephyrinequa Outfitters"}) == {
            "results": []
        }
        travel_off = await _context(reader, disabled=frozenset({"travel"}))
        quiet = await search(reader, travel_off, {"query": "Quillonar Vargastead"})
        assert f"butlers://travel/trip/{trip}" not in [r["id"] for r in quiet["results"]]
        assert f"butlers://relationship/entity/{person}" in [r["id"] for r in quiet["results"]]
        for bad in ("", "x" * 257):
            assert (await _refused(search(reader, context, {"query": bad}))).code == (
                "invalid_argument"
            )

        entity = await fetch(reader, context, {"id": f"butlers://relationship/entity/{person}"})
        assert set(entity) == {"id", "title", "text", "url", "metadata"}
        assert set(entity["metadata"]) == {"domain", "kind", "source", "valid_at", "truncated"}
        assert entity["title"] == "Quillonar Vargastead" and "Quill V" in entity["text"]
        assert entity["metadata"]["source"]["uri"] == f"butlers://relationship/entity/{person}"
        assert entity["metadata"]["truncated"] is False

        long = await fetch(reader, context, {"id": f"butlers://relationship/fact/{long_fact}"})
        assert len(long["text"].encode()) <= FETCH_TEXT_BYTES
        assert long["metadata"]["truncated"] is True
        assert long["text"].startswith("Quillonar Vargastead ramen notes")

        finance_uri = f"butlers://finance/transactions/{transaction}"
        assert (await _refused(fetch(reader, context, {"id": finance_uri}))).code == "not_found"
        unknown = f"butlers://relationship/entity/{uuid.uuid4()}"
        assert (await _refused(fetch(reader, context, {"id": unknown}))).code == "not_found"
        disabled = await _refused(
            fetch(reader, travel_off, {"id": f"butlers://travel/trip/{trip}"})
        )
        assert (disabled.code, disabled.domain) == ("domain_disabled", "travel")

        state = await fetch(reader, context, {"id": f"proposal:{proposal}"})
        assert set(state) == {"id", "title", "text", "url", "metadata"}
        assert (state["title"], state["text"]) == ("Proposal", "applied")
        assert state["metadata"]["state"] == "applied"
        assert state["metadata"]["decided_at"] is not None
        assert "marker" not in json.dumps(state)
        gone = await _refused(fetch(reader, context, {"id": f"proposal:{uuid.uuid4()}"}))
        assert gone.code == "not_found"
    finally:
        await primary.execute(
            "DELETE FROM relationship.external_fact_proposals WHERE proposal_id=$1", proposal
        )
        await primary.execute("DELETE FROM travel.trips WHERE id=$1", trip)
        await primary.execute("DELETE FROM finance.transactions WHERE id=$1", transaction)
        await primary.execute("DELETE FROM relationship.facts WHERE id=$1", long_fact)
        await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(person))
        _barrier(projected)


async def test_search_filtered_filters_pages_and_names_degraded_domains(projected, primary, reader):
    """REQ-public-mcp-surface-006 and -005 (lagging) over the published index."""
    from butlers.knowledge_read.search import search_filtered

    facts = [
        await primary.fetchval(
            "INSERT INTO general.facts(subject,predicate,content,sensitivity,validity,valid_at)"
            " VALUES ($1,'note',$2,'normal','active',$3) RETURNING id",
            f"filtered-{n}",
            f"Marmorkatze filtered note {n}",
            datetime(2026, 6, n + 1, tzinfo=UTC),
        )
        for n in range(3)
    ]
    trip = await primary.fetchval(
        "INSERT INTO travel.trips(name,destination,start_date,end_date,status) VALUES "
        "('Marmorkatze trip','Bergen','2026-06-02','2026-06-03','planned') RETURNING id"
    )
    try:
        _barrier(projected)
        context = await _context(reader)
        everything = await search_filtered(reader, context, {"query": "Marmorkatze"})
        assert {i["kind"] for i in everything["items"]} == {"fact", "trip"}
        assert everything["sources_degraded"] == []
        item = everything["items"][0]
        assert set(item) == {"id", "title", "snippet", "domain", "kind", "url", "source"}

        knowledge = await search_filtered(
            reader, context, {"query": "Marmorkatze", "domains": ["knowledge"], "limit": 2}
        )
        assert [i["domain"] for i in knowledge["items"]] == ["knowledge", "knowledge"]
        assert knowledge["next_cursor"] is not None
        rest = await search_filtered(
            reader,
            context,
            {
                "query": "Marmorkatze",
                "domains": ["knowledge"],
                "limit": 2,
                "cursor": knowledge["next_cursor"],
            },
        )
        paged = {i["id"] for i in knowledge["items"] + rest["items"]}
        assert paged == {f"butlers://knowledge/fact/{f}" for f in facts}
        assert rest["next_cursor"] is None
        rebound = await _refused(
            search_filtered(
                reader,
                context,
                {"query": "other", "domains": ["knowledge"], "cursor": knowledge["next_cursor"]},
            )
        )
        assert rebound.code == "invalid_argument"

        window = await search_filtered(
            reader,
            context,
            {"query": "Marmorkatze", "since": "2026-06-02", "until": "2026-06-03"},
        )
        assert {i["id"] for i in window["items"]} == {
            f"butlers://knowledge/fact/{facts[1]}",
            f"butlers://travel/trip/{trip}",
        }
        only_trips = await search_filtered(
            reader, context, {"query": "Marmorkatze", "kinds": ["trip"]}
        )
        assert [i["id"] for i in only_trips["items"]] == [f"butlers://travel/trip/{trip}"]

        travel_off = await _context(reader, disabled=frozenset({"travel"}))
        named = await search_filtered(
            reader, travel_off, {"query": "Marmorkatze", "domains": ["travel"]}
        )
        assert named["items"] == []
        assert named["sources_degraded"] == [{"domain": "travel", "reason": "disabled"}]

        lagging = await read_context(
            reader, sub="github|1", cursor_key=KEY, now=context.as_of + timedelta(minutes=6)
        )
        late = await search_filtered(reader, lagging, {"query": "Marmorkatze"})
        assert late["items"] and all(i["source"]["as_of"] for i in late["items"])
        assert late["sources_degraded"] == [
            {"domain": d, "reason": "lagging"}
            for d in ("knowledge", "relationship", "time", "travel")
        ]
    finally:
        await primary.execute("DELETE FROM travel.trips WHERE id=$1", trip)
        await primary.execute("DELETE FROM general.facts WHERE id = ANY($1)", facts)
        _barrier(projected)


async def test_graph_neighbors_and_path_match_the_walk_contract(projected, primary, reader):
    """REQ-public-mcp-surface-007 graph clause: REQ-entity-graph-003/005/006/009 parity."""
    names = ("Graph Alder", "Graph Birch", "Graph Cedar", "Graph Dogwood", "Graph Elm")
    alder, birch, cedar, dogwood, elm = [await _entity(primary, n) for n in names]
    facts = []
    try:
        async with primary.acquire() as conn, conn.transaction():
            facts.append(await _general_edge(conn, alder, birch))
            facts.append(await _general_edge(conn, cedar, birch, "works_at"))
            facts.append(await _general_edge(conn, cedar, dogwood))
            facts.append(await _general_edge(conn, dogwood, alder, "knows"))  # a cycle
            for _ in range(3):
                facts.append(await _general_edge(conn, alder, None, withheld=True))
        _barrier(projected)
        context = await _context(reader)
        one = await graph_neighbors(reader, context, {"entity_id": alder})
        assert {n["entity_id"]: n["hop"] for n in one["nodes"]} == {birch: 1, dogwood: 1}
        assert one["withheld"] == 3
        receipt = next(e for e in one["edges"] if birch in (e["object_entity_id"],))
        assert set(receipt) == {
            "id",
            "subject_entity_id",
            "predicate",
            "object_entity_id",
            "source",
        }
        assert receipt["source"]["uri"] == f"butlers://relationship/edge/{receipt['id']}"
        assert "withheld edge" not in json.dumps(one)

        two = await graph_neighbors(reader, context, {"entity_id": alder, "hops": 2})
        assert {n["entity_id"]: n["hop"] for n in two["nodes"]} == {
            birch: 1,
            dogwood: 1,
            cedar: 2,
        }
        assert alder not in {n["entity_id"] for n in two["nodes"]}
        knows = await graph_neighbors(
            reader, context, {"entity_id": alder, "hops": 2, "predicates": ["knows"]}
        )
        assert {n["entity_id"] for n in knows["nodes"]} == {birch, dogwood, cedar}
        assert {e["predicate"] for e in knows["edges"]} == {"knows"}
        capped = await graph_neighbors(reader, context, {"entity_id": alder, "hops": 2, "limit": 2})
        assert [n["hop"] for n in capped["nodes"]] == [1, 1] and capped["truncated"] is True

        knowledge_off = await _context(reader, disabled=frozenset({"knowledge"}))
        dropped = await graph_neighbors(reader, knowledge_off, {"entity_id": alder})
        assert dropped["nodes"] == [] and dropped["edges"] == [] and dropped["withheld"] == 0

        path = await graph_path(reader, context, {"from_id": birch, "to_id": dogwood})
        assert len(path["path"]) == 2
        steps = path["path"]
        assert birch in (steps[0]["subject_entity_id"], steps[0]["object_entity_id"])
        assert dogwood in (steps[-1]["subject_entity_id"], steps[-1]["object_entity_id"])
        assert await graph_path(reader, context, {"from_id": birch, "to_id": birch}) == {"path": []}
        assert await graph_path(reader, context, {"from_id": birch, "to_id": elm}) == {"path": None}
        short = await graph_path(
            reader, context, {"from_id": birch, "to_id": dogwood, "max_hops": 1}
        )
        assert short == {"path": None}
        assert await graph_path(reader, knowledge_off, {"from_id": birch, "to_id": dogwood}) == {
            "path": None
        }
        missing = await _refused(
            graph_path(reader, context, {"from_id": birch, "to_id": str(uuid.uuid4())})
        )
        assert missing.code == "not_found"
        too_far = await _refused(
            graph_path(reader, context, {"from_id": birch, "to_id": elm, "max_hops": 5})
        )
        assert too_far.code == "invalid_argument"
    finally:
        await primary.execute("DELETE FROM general.facts WHERE id = ANY($1)", facts)
        for entity in (alder, birch, cedar, dogwood, elm):
            await primary.execute(
                "DELETE FROM public.entity_graph_edges WHERE subject_entity_id=$1 "
                "OR object_entity_id=$1",
                uuid.UUID(entity),
            )
            await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(entity))
        _barrier(projected)


async def test_trips_expose_expiry_not_document_numbers(projected, primary, reader):
    """REQ-public-mcp-surface-008 trips clause."""
    trip = await primary.fetchval(
        "INSERT INTO travel.trips(name,destination,start_date,end_date,status) VALUES "
        "('Trips reader trip','Kyoto','2026-07-01','2026-07-05','active') RETURNING id"
    )
    other = await primary.fetchval(
        "INSERT INTO travel.trips(name,destination,start_date,end_date,status) VALUES "
        "('Trips reader old','Porto','2025-07-01','2025-07-05','completed') RETURNING id"
    )
    try:
        leg = await primary.fetchval(
            "INSERT INTO travel.legs(trip_id,type,carrier,departure_city,departure_at,"
            "arrival_city,arrival_at,confirmation_number) VALUES ($1,'flight','JL','Tokyo',"
            "'2026-07-01T01:00:00+00:00','Kyoto','2026-07-01T03:00:00+00:00','CONF-TRIP-77')"
            " RETURNING id",
            trip,
        )
        document = await primary.fetchval(
            "INSERT INTO travel.documents(trip_id,type,blob_ref,expiry_date,metadata) VALUES "
            "($1,'visa','blob://doc-marker','2027-01-31','{\"number\":\"DOCNO-MARKER-5\"}')"
            " RETURNING id",
            trip,
        )
        _barrier(projected)
        context = await _context(reader)
        result = await trips(reader, context, {"trip_id": str(trip)})
        (only,) = result["trips"]
        assert (only["name"], only["status"], only["destination"]) == (
            "Trips reader trip",
            "active",
            "Kyoto",
        )
        assert only["source"]["uri"] == f"butlers://travel/trip/{trip}"
        assert [leg_["id"] for leg_ in only["legs"]] == [str(leg)]
        assert only["legs"][0]["carrier"] == "JL"
        assert only["documents"] == [
            {
                "id": str(document),
                "type": "visa",
                "expiry_date": "2027-01-31",
                "source": context.source("document", f"butlers://travel/document/{document}"),
            }
        ]
        text_ = json.dumps(result)
        assert "CONF-TRIP-77" not in text_ and "MARKER" not in text_
        active = await trips(reader, context, {"status": "active"})
        assert str(trip) in {t["id"] for t in active["trips"]}
        assert str(other) not in {t["id"] for t in active["trips"]}
        assert (await _refused(trips(reader, context, {"status": "booked"}))).code == (
            "invalid_argument"
        )
        gone = await _refused(trips(reader, context, {"trip_id": str(uuid.uuid4())}))
        assert gone.code == "not_found"
        off = await _context(reader, disabled=frozenset({"travel"}))
        assert (await _refused(trips(reader, off, {}))).code == "domain_disabled"
    finally:
        await primary.execute("DELETE FROM travel.trips WHERE id = ANY($1)", [trip, other])
        _barrier(projected)


async def test_describe_reports_switches_freshness_and_the_replicated_digest(
    projected, primary, reader
):
    """REQ-public-mcp-surface-006 describe clause."""
    from butlers.knowledge_read.describe import TOOLS, describe

    context = await _context(reader, disabled=frozenset({"health"}))
    described = await describe(reader, context, {})
    assert set(described) == {
        "server_version",
        "manifest_digest",
        "domains",
        "as_of",
        "tools",
        "columns",
    }
    assert described["manifest_digest"] == load_manifest().digest
    assert described["domains"]["health"] == "disabled"
    assert {d: s for d, s in described["domains"].items() if d != "health"} == {
        d: "enabled" for d in described["domains"] if d != "health"
    }
    assert described["tools"] == list(TOOLS) and len(TOOLS) == 19
    assert "publish_key" not in described["columns"]["public.entity_graph_edges"]
    assert "deleted" not in described["columns"]["public.entities"]
    assert (await _refused(describe(reader, context, {"x": 1}))).code == "invalid_argument"

    stale = await read_context(
        reader,
        sub="github|1",
        cursor_key=KEY,
        disabled_domains=frozenset({"finance"}),
        now=context.as_of + timedelta(minutes=20),
    )
    late = await describe(reader, stale, {})
    assert late["domains"]["finance"] == "disabled"
    assert {s for d, s in late["domains"].items() if d != "finance"} == {"stale"}
    assert (await _refused(trips(reader, stale, {}))).code == "projection_stale"

    # The replicated owner switch is a layer describe reads for itself.
    try:
        with projected[0].engine.begin() as conn:
            conn.execute(
                text("UPDATE mcp_surface.controls SET state='disabled' WHERE domain='travel'")
            )
        _barrier(projected)
        switched = await describe(reader, await _context(reader), {})
        assert switched["domains"]["travel"] == "disabled"
    finally:
        with projected[0].engine.begin() as conn:
            conn.execute(text("UPDATE mcp_surface.controls SET state='enabled'"))
        _barrier(projected)


async def test_graph_tools_start_from_the_published_survivor_of_a_merge(projected, primary, reader):
    """A merged start or endpoint walks from its survivor; a hidden survivor is not_found."""
    survivor = await _entity(primary, "Graph Merge Survivor Fenwick")
    merged = await _entity(
        primary, "Graph Merge Merged Fenwyck", metadata={"merged_into": survivor}
    )
    neighbour = await _entity(primary, "Graph Merge Neighbour Garnet")
    facts = []
    try:
        async with primary.acquire() as conn, conn.transaction():
            facts.append(await _general_edge(conn, survivor, neighbour))
        _barrier(projected)
        context = await _context(reader)
        walked = await graph_neighbors(reader, context, {"entity_id": merged})
        assert walked["entity"]["entity_id"] == survivor
        assert [n["entity_id"] for n in walked["nodes"]] == [neighbour]
        path = await graph_path(reader, context, {"from_id": merged, "to_id": neighbour})
        assert [(e["subject_entity_id"], e["object_entity_id"]) for e in path["path"]] == [
            (survivor, neighbour)
        ]
        reverse = await graph_path(reader, context, {"from_id": neighbour, "to_id": merged})
        assert len(reverse["path"]) == 1
        assert await graph_path(reader, context, {"from_id": merged, "to_id": survivor}) == {
            "path": []
        }

        await primary.execute(
            "UPDATE public.entities SET metadata=metadata||jsonb_build_object('deleted_at',now()) "
            "WHERE id=$1",
            uuid.UUID(survivor),
        )
        _barrier(projected)
        context = await _context(reader)
        for call in (
            graph_neighbors(reader, context, {"entity_id": merged}),
            graph_path(reader, context, {"from_id": merged, "to_id": neighbour}),
            graph_path(reader, context, {"from_id": neighbour, "to_id": merged}),
        ):
            assert (await _refused(call)).code == "not_found"
        # Positive control: the neighbour itself is still published and walkable.
        alone = await graph_neighbors(reader, context, {"entity_id": neighbour})
        assert alone["entity"]["entity_id"] == neighbour and alone["nodes"] == []
    finally:
        await primary.execute("DELETE FROM general.facts WHERE id = ANY($1)", facts)
        for entity in (merged, survivor, neighbour):
            await primary.execute(
                "DELETE FROM public.entity_graph_edges WHERE subject_entity_id=$1 "
                "OR object_entity_id=$1",
                uuid.UUID(entity),
            )
            await primary.execute("DELETE FROM public.entities WHERE id=$1", uuid.UUID(entity))
        _barrier(projected)
