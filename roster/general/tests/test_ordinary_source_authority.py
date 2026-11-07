"""Real-PostgreSQL proof of General's ordinary-only boundary and source versions.

Every privacy-absence assertion is paired with a positive ordinary control, so
an empty result cannot pass for the wrong reason.  "Private" collections here
are synthetic stand-ins for a future custody release: the tests classify them
with a direct UPDATE because General itself never classifies anything.

Issue: bu-2jtfw.9.3 (RFC 0037; REQ-general-capture-003/-005)
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.deps import get_db
from butlers.testing.db_manager import db_manager

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

SENTINEL = "vault-sentinel-7f3c"


@pytest.fixture
async def general_pool(db):
    """A private clone migrated with the real General chain; room for concurrent writers."""
    return await db("general", schemas={"general": "general"}, schema="general", max_size=12)


async def _private_collection(pool: asyncpg.Pool, name: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a collection holding a sentinel item, then classify it private."""
    from butlers.tools.general import collection_create, item_create

    collection_id = await collection_create(pool, name, description=SENTINEL)
    item_id = await item_create(pool, name, {"secret": SENTINEL}, tags=[SENTINEL])
    await pool.execute("UPDATE collections SET custody_private = true WHERE id = $1", collection_id)
    return collection_id, item_id


async def _cut_over(pool: asyncpg.Pool) -> None:
    """Synthetic post-cutover schema: ordinary-only uniqueness, no global constraint."""
    await pool.execute("ALTER TABLE collections DROP CONSTRAINT collections_name_key")


async def _private_state(pool: asyncpg.Pool) -> list[tuple]:
    rows = await pool.fetch(
        """
        SELECT c.id, c.name, c.description, c.custody_private, c.eligibility_generation,
               i.id AS item_id, i.data::text, i.tags::text
        FROM collections AS c LEFT JOIN collection_items AS i ON i.collection_id = c.id
        WHERE c.custody_private
        ORDER BY c.id, i.id
        """
    )
    return [tuple(row) for row in rows]


def _leaks(value: object) -> bool:
    return SENTINEL in json.dumps(value, default=str)


async def _blocked_on_lock(pool: asyncpg.Pool, expected: int = 1) -> None:
    for _ in range(200):
        waiting = await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        if waiting >= expected:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("the concurrent writer never blocked on the classification lock")


def _router_app(pool: asyncpg.Pool) -> FastAPI:
    from butlers.api.router_discovery import discover_butler_routers

    discover_butler_routers()
    router_module = sys.modules["general_api_router"]

    manager = db_manager({"general": pool})
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_db] = lambda: manager
    return app


async def test_generic_reads_and_id_writes_treat_private_parents_as_absent(general_pool) -> None:
    """Lists, search, counts, exact reads and id-based writes never expose a private parent."""
    from butlers.tools.general import (
        collection_create,
        collection_delete,
        collection_list,
        item_create,
        item_delete,
        item_get,
        item_search,
        item_update,
    )

    control_id = await collection_create(general_pool, "pantry")
    control_item = await item_create(general_pool, "pantry", {"food": "rice"}, tags=["staple"])
    # An ordinary-flagged parent holding a reserved profile is inconsistent:
    # it refuses generic exposure instead of falling back to ordinary.
    drawer_id = await collection_create(general_pool, "drawer")
    drawer_item = await general_pool.fetchval(
        "INSERT INTO collection_items (collection_id, data) VALUES ($1, $2) RETURNING id",
        drawer_id,
        {"possession_profile": {"revision": 1}, "note": SENTINEL},
    )
    # Seeded last: once a private row exists, name-based creation refuses.
    private_id, private_item = await _private_collection(general_pool, "vault")
    before = await _private_state(general_pool)

    listed = await collection_list(general_pool)
    assert [c["name"] for c in listed] == ["pantry"]
    everything = await item_search(general_pool)
    assert [r["id"] for r in everything] == [control_item]
    assert await item_search(general_pool, tags=[SENTINEL]) == []
    assert [r["id"] for r in await item_search(general_pool, tags=["staple"])] == [control_item]
    assert (await item_get(general_pool, control_item))["data"] == {"food": "rice"}
    for hidden in (private_item, drawer_item):
        assert await item_get(general_pool, hidden) is None

    # Id-based writes refuse exactly as for a nonexistent id, with no write.
    missing = uuid.uuid4()
    for hidden in (private_item, drawer_item):
        for write in (
            lambda target: item_update(general_pool, target, {"x": 1}),
            lambda target: item_update(general_pool, target, {}, tags=["retagged"]),
            lambda target: item_delete(general_pool, target),
        ):
            with pytest.raises(ValueError) as refused:
                await write(hidden)
            with pytest.raises(ValueError) as absent:
                await write(missing)
            assert str(refused.value).replace(str(hidden), "<id>") == str(absent.value).replace(
                str(missing), "<id>"
            )
    for hidden_collection in (private_id, drawer_id):
        with pytest.raises(ValueError) as refused:
            await collection_delete(general_pool, hidden_collection)
        with pytest.raises(ValueError) as absent:
            await collection_delete(general_pool, missing)
        assert str(refused.value).replace(str(hidden_collection), "<id>") == str(
            absent.value
        ).replace(str(missing), "<id>")

    # Positive control: the ordinary item still mutates.
    await item_update(general_pool, control_item, {"brand": "jasmine"})
    assert (await item_get(general_pool, control_item))["data"] == {
        "food": "rice",
        "brand": "jasmine",
    }
    assert await _private_state(general_pool) == before
    assert await general_pool.fetchval(
        "SELECT count(*) FROM collection_items WHERE id = $1", drawer_item
    )
    assert not _leaks([listed, everything])

    # The dashboard API applies the same predicate before counting.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_router_app(general_pool)), base_url="http://test"
    ) as client:
        stats = (await client.get("/api/general/stats")).json()
        collections = (await client.get("/api/general/collections")).json()
        entities = (await client.get("/api/general/entities")).json()
        by_text = (await client.get("/api/general/entities", params={"q": SENTINEL})).json()
        private_members = (
            await client.get(f"/api/general/collections/{private_id}/entities")
        ).json()
        control_members = (
            await client.get(f"/api/general/collections/{control_id}/entities")
        ).json()
        hidden_detail = await client.get(f"/api/general/entities/{private_item}")
        missing_detail = await client.get(f"/api/general/entities/{missing}")
        control_detail = await client.get(f"/api/general/entities/{control_item}")

    assert (stats["total_collections"], stats["total_entities"]) == (1, 1)
    assert stats["last_modified_collection"] == "pantry"
    assert [c["name"] for c in collections["data"]] == ["pantry"]
    assert collections["meta"]["total"] == 1
    assert [e["id"] for e in entities["data"]] == [str(control_item)]
    assert entities["meta"]["total"] == 1
    assert (by_text["data"], by_text["meta"]["total"]) == ([], 0)
    assert (private_members["data"], private_members["meta"]["total"]) == ([], 0)
    assert control_members["meta"]["total"] == 1
    assert (hidden_detail.status_code, hidden_detail.json()) == (
        missing_detail.status_code,
        missing_detail.json(),
    )
    assert control_detail.status_code == 200
    assert not _leaks([stats, collections, entities, private_members])


async def test_all_names_refuse_uniformly_while_legacy_uniqueness_meets_a_private_row(
    general_pool,
) -> None:
    """Before cutover, any private row makes EVERY name-based admission unavailable."""
    from butlers.tools.general import (
        GeneralSourceUnavailable,
        collection_create,
        collection_declare,
        collection_export,
        collection_resolve,
        item_create,
        item_search,
    )

    name_calls = {
        "item_create": lambda name: item_create(general_pool, name, {"k": 1}),
        "collection_create": lambda name: collection_create(general_pool, name),
        "collection_export": lambda name: collection_export(general_pool, name),
        "item_search": lambda name: item_search(general_pool, collection_name=name),
        "collection_declare": lambda name: collection_declare(general_pool, name, "shape"),
        "collection_resolve": lambda name: collection_resolve(general_pool, name),
    }
    # Positive control: every name path works while no private row exists.
    for label, call in name_calls.items():
        await call(f"control-{label}")

    await _private_collection(general_pool, "vault")
    before = await _private_state(general_pool)
    ordinary_before = await general_pool.fetchval("SELECT count(*) FROM collections")
    for label, call in name_calls.items():
        for name in ("vault", "pantry-unused", f"control-{label}"):
            with pytest.raises(GeneralSourceUnavailable) as refused:
                await call(name)
            assert str(refused.value) == GeneralSourceUnavailable.MESSAGE
    assert await _private_state(general_pool) == before
    assert await general_pool.fetchval("SELECT count(*) FROM collections") == ordinary_before

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_router_app(general_pool)), base_url="http://test"
    ) as client:
        refused = await client.get("/api/general/entities", params={"collection": "vault"})
        unused = await client.get("/api/general/entities", params={"collection": "unused"})
    assert refused.status_code == unused.status_code == 503
    assert refused.json() == unused.json()


async def test_private_only_names_behave_as_absent_after_cutover(general_pool) -> None:
    """Post-cutover, a private-only name creates, declares and resolves like an unused one."""
    from butlers.tools.general import (
        collection_create,
        collection_declare,
        collection_export,
        collection_resolve,
        item_create,
        item_search,
    )

    await _cut_over(general_pool)
    private_id, private_item = await _private_collection(general_pool, "keepsakes")
    await collection_declare(general_pool, "journal", "Dated personal entries", aliases=["diary"])
    before = await _private_state(general_pool)

    outcomes: dict[str, dict[str, object]] = {}
    for name in ("keepsakes", "never-used"):
        resolved = await collection_resolve(general_pool, name)
        exported = await collection_export(general_pool, name)
        searched = await item_search(general_pool, collection_name=name)
        created = await collection_create(general_pool, name)
        item_id = await item_create(general_pool, name, {"k": 1})
        items = await item_search(general_pool, collection_name=name)
        outcomes[name] = {
            "resolved": resolved,
            "exported": exported,
            "searched": searched,
            "new_collection": created not in (private_id, None),
            "items": [r["id"] for r in items] == [item_id],
        }
        with pytest.raises(asyncpg.UniqueViolationError):
            await collection_create(general_pool, name)
    assert (
        outcomes["keepsakes"]
        == outcomes["never-used"]
        == {
            "resolved": {"collection": None, "match": None, "suggestions": []},
            "exported": [],
            "searched": [],
            "new_collection": True,
            "items": True,
        }
    )
    # A private-only name declares a fresh ordinary vocabulary entry too.
    declared = await collection_declare(general_pool, "keepsakes", "Ordinary keepsake notes")
    assert declared["declared"] is True and declared["collection_id"] != private_id
    # Ordinary control resolves through its declared alias.
    assert (await collection_resolve(general_pool, "diary"))["match"] == "alias"
    assert await _private_state(general_pool) == before
    assert not await general_pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM collection_items WHERE id = $1 AND data->>'k' = '1')",
        private_item,
    )


async def test_ordinary_vocabulary_is_explicit_exact_and_private_blind(general_pool) -> None:
    """Declares need a shape; exact aliases resolve; near matches only suggest."""
    from butlers.tools.general import (
        collection_declare,
        collection_resolve,
        item_create,
        item_search,
    )

    with pytest.raises(ValueError, match="shape_description"):
        await collection_declare(general_pool, "books", "   ")
    assert await general_pool.fetchval("SELECT count(*) FROM collections") == 0

    books = await collection_declare(
        general_pool, "books", "Books read or wanted", aliases=["reading list"]
    )
    assert books["declared"] is True and books["aliases"] == ["reading list"]

    alias = await collection_resolve(general_pool, "reading list")
    assert (alias["collection"]["id"], alias["match"]) == (books["collection_id"], "alias")
    item_id = await item_create(general_pool, "reading list", {"title": "Dune"})
    assert [r["id"] for r in await item_search(general_pool, collection_name="books")] == [item_id]

    for near in ("Books", "READING-list", "bokos"):
        suggested = await collection_resolve(general_pool, near)
        assert suggested["collection"] is None
        assert [s["collection_id"] for s in suggested["suggestions"]] == [books["collection_id"]]

    # A normalized variant converges instead of creating a twin.
    variant = await collection_declare(general_pool, "  BOOKS ", "ignored shape")
    assert (variant["collection_id"], variant["declared"]) == (books["collection_id"], False)
    # An alias held by another ordinary collection refuses the whole declare.
    with pytest.raises(ValueError, match="another collection"):
        await collection_declare(general_pool, "films", "Films", aliases=["Reading_List"])
    assert await general_pool.fetchval("SELECT count(*) FROM collections WHERE name = 'films'") == 0
    # Uniqueness is the database's, not only the tool's.
    with pytest.raises(asyncpg.UniqueViolationError):
        await general_pool.execute(
            "INSERT INTO collection_vocabulary_keys (collection_id, spelling, kind) "
            "VALUES ($1, 'Reading--List!', 'alias')",
            (await collection_declare(general_pool, "films", "Films"))["collection_id"],
        )

    # Classifying a declared parent removes it from resolution and suggestions.
    await _cut_over(general_pool)
    await general_pool.execute(
        "UPDATE collections SET custody_private = true WHERE id = $1", books["collection_id"]
    )
    assert await general_pool.fetchval(
        "SELECT bool_and(custody_private) FROM collection_vocabulary_keys WHERE collection_id = $1",
        books["collection_id"],
    )
    for name in ("reading list", "Books", "bokos"):
        assert await collection_resolve(general_pool, name) == {
            "collection": None,
            "match": None,
            "suggestions": [],
        }
    fresh = await collection_declare(general_pool, "reading list", "Now an ordinary list")
    assert fresh["declared"] is True and fresh["collection_id"] != books["collection_id"]


async def test_source_versions_are_atomic_immutable_and_gated_by_current_eligibility(
    general_pool,
) -> None:
    from butlers.tools.general import (
        collection_create,
        item_create,
        item_create_versioned,
        item_delete,
        item_update,
        read_source_version,
    )
    from butlers.tools.general.source_authority import source_digest

    item_id, created = await item_create_versioned(general_pool, "notes", {"text": "a"}, tags=["t"])
    assert (created.version, created.operation, created.eligibility_generation) == (
        1,
        "create",
        0,
    )
    assert created.digest == source_digest(item_id, "create", created.content)
    assert created.content["data"] == {"text": "a"}

    await item_update(general_pool, item_id, {"text": "b"})
    latest = await read_source_version(general_pool, item_id)
    first = await read_source_version(general_pool, item_id, 1)
    assert (latest.version, latest.current, latest.content["data"]) == (2, True, {"text": "b"})
    assert (first.version, first.current, first.digest) == (1, False, created.digest)

    # Atomic with the caller's transaction: a rolled-back composition leaves
    # neither the item nor its version.
    class _Abort(Exception):
        pass

    with pytest.raises(_Abort):
        async with general_pool.acquire() as conn, conn.transaction():
            doomed, _ = await item_create_versioned(conn, "notes", {"text": "doomed"})
            raise _Abort
    assert (
        await general_pool.fetchval("SELECT count(*) FROM collection_items WHERE id = $1", doomed)
        == 0
    )
    assert (
        await general_pool.fetchval(
            "SELECT count(*) FROM source_versions WHERE item_id = $1", doomed
        )
        == 0
    )

    # Stored history is immutable, even for the table owner.
    for statement in (
        "UPDATE source_versions SET digest = digest",
        "DELETE FROM source_versions",
        "TRUNCATE source_versions",
    ):
        with pytest.raises(asyncpg.PostgresError, match="immutable"):
            await general_pool.execute(statement)

    # Delete records a tombstone and the stored versions stop being readable.
    await item_delete(general_pool, item_id)
    assert [
        (r["version"], r["operation"], r["content"] is None)
        for r in await general_pool.fetch(
            "SELECT version, operation, content FROM source_versions "
            "WHERE item_id = $1 ORDER BY version",
            item_id,
        )
    ] == [(1, "create", False), (2, "update", False), (3, "delete", True)]
    assert await read_source_version(general_pool, item_id) is None
    assert await read_source_version(general_pool, item_id, 1) is None

    # Classification advances the parent's generation: old versions stop
    # being readable while the ordinary control stays readable.
    control_id = await item_create(general_pool, "control", {"text": "c"})
    private_parent = await collection_create(general_pool, "soon-private")
    hidden = await item_create(general_pool, "soon-private", {"text": SENTINEL})
    assert (await read_source_version(general_pool, hidden)).content["data"] == {"text": SENTINEL}
    await general_pool.execute(
        "UPDATE collections SET custody_private = true WHERE id = $1", private_parent
    )
    assert (
        await general_pool.fetchval(
            "SELECT eligibility_generation FROM collections WHERE id = $1", private_parent
        )
        == 1
    )
    assert await read_source_version(general_pool, hidden) is None
    assert (await read_source_version(general_pool, control_id)).content["data"] == {"text": "c"}
    assert await general_pool.fetchval(
        "SELECT count(*) FROM source_versions WHERE item_id = $1", hidden
    )
    # A classification can never be cleared, and generations never move alone.
    with pytest.raises(asyncpg.PostgresError, match="cannot be cleared"):
        await general_pool.execute(
            "UPDATE collections SET custody_private = false WHERE id = $1", private_parent
        )
    with pytest.raises(asyncpg.PostgresError, match="only advances"):
        await general_pool.execute(
            "UPDATE collections SET eligibility_generation = 9 WHERE name = 'control'"
        )


@pytest.mark.parametrize("stage", ["legacy-global", "cut-over"])
async def test_concurrent_writers_serialize_with_classification(general_pool, stage: str) -> None:
    """Concurrent creates, declares and mutations keep uniqueness and privacy."""
    from butlers.tools.general import (
        GeneralSourceUnavailable,
        collection_declare,
        item_create,
        item_get,
        item_update,
    )

    if stage == "cut-over":
        await _cut_over(general_pool)

    # Concurrent ordinary creates of one name converge on one collection.
    await asyncio.gather(*(item_create(general_pool, "shared", {"n": n}) for n in range(8)))
    assert (
        await general_pool.fetchval("SELECT count(*) FROM collections WHERE name = 'shared'") == 1
    )
    assert (
        await general_pool.fetchval(
            "SELECT count(*) FROM collection_items AS i JOIN collections AS c "
            "ON c.id = i.collection_id WHERE c.name = 'shared'"
        )
        == 8
    )

    # Concurrent case/punctuation variants converge on one declaration.
    variants = ["Email Records", "email_records", "email-records", "EMAIL  records"]
    declared = await asyncio.gather(
        *(collection_declare(general_pool, v, "Mail") for v in variants)
    )
    assert len({d["collection_id"] for d in declared}) == 1
    assert sum(d["declared"] for d in declared) == 1
    assert await general_pool.fetchval("SELECT count(*) FROM collection_vocabulary") == 1
    assert (
        await general_pool.fetchval("SELECT count(*) FROM collections WHERE name ILIKE 'email%'")
        == 1
    )

    # A create and an update racing classification of their parent either
    # finish first or are refused; nothing lands in the now-private parent.
    parent_id = await general_pool.fetchval("SELECT id FROM collections WHERE name = 'shared'")
    member = await general_pool.fetchval(
        "SELECT id FROM collection_items WHERE collection_id = $1 LIMIT 1", parent_id
    )
    async with general_pool.acquire() as enrolling:
        tx = enrolling.transaction()
        await tx.start()
        await enrolling.execute("SELECT id FROM collections WHERE id = $1 FOR UPDATE", parent_id)
        create = asyncio.create_task(item_create(general_pool, "shared", {"late": True}))
        update = asyncio.create_task(item_update(general_pool, member, {"late": True}))
        await _blocked_on_lock(general_pool, expected=2)
        await enrolling.execute(
            "UPDATE collections SET custody_private = true WHERE id = $1", parent_id
        )
        await tx.commit()
    create_outcome, update_outcome = await asyncio.gather(create, update, return_exceptions=True)

    assert isinstance(update_outcome, ValueError) and "not found" in str(update_outcome)
    if stage == "legacy-global":
        assert isinstance(create_outcome, GeneralSourceUnavailable)
    else:
        assert isinstance(create_outcome, uuid.UUID)
        created = await item_get(general_pool, create_outcome)
        assert created["collection_id"] != parent_id
        assert (
            await general_pool.fetchval(
                "SELECT custody_private FROM collections WHERE id = $1", created["collection_id"]
            )
            is False
        )
    assert (
        await general_pool.fetchval(
            "SELECT count(*) FROM collection_items WHERE collection_id = $1 AND data ? 'late'",
            parent_id,
        )
        == 0
    )


@pytest.mark.parametrize("stage", ["legacy-global", "cut-over"])
async def test_declare_racing_classification_never_returns_or_extends_a_private_parent(
    general_pool, stage: str
) -> None:
    """A variant declare that found a declaration just before it became private
    re-resolves as an absent name: no private shape, alias or id is returned or added."""
    from butlers.tools.general import GeneralSourceUnavailable, collection_declare

    if stage == "cut-over":
        await _cut_over(general_pool)
    books = await collection_declare(general_pool, "books", SENTINEL, aliases=["tomes"])
    with pytest.raises(ValueError, match="letter or digit"):
        await collection_declare(general_pool, "!!!", "punctuation only")

    async with general_pool.acquire() as enrolling:
        tx = enrolling.transaction()
        await tx.start()
        await enrolling.execute(
            "UPDATE collections SET custody_private = true WHERE id = $1", books["collection_id"]
        )
        declare = asyncio.create_task(
            collection_declare(general_pool, "Books", "ordinary shelf", aliases=["volumes"])
        )
        await _blocked_on_lock(general_pool)
        await tx.commit()
    outcome = (await asyncio.gather(declare, return_exceptions=True))[0]

    assert not _leaks(outcome if not isinstance(outcome, Exception) else str(outcome))
    assert await general_pool.fetchval(
        "SELECT array_agg(spelling ORDER BY spelling) FROM collection_vocabulary_keys "
        "WHERE collection_id = $1",
        books["collection_id"],
    ) == ["books", "tomes"]
    if stage == "legacy-global":
        # A private row now meets legacy global uniqueness: refuse uniformly.
        assert isinstance(outcome, GeneralSourceUnavailable)
    else:
        assert outcome["declared"] is True
        assert outcome["collection_id"] != books["collection_id"]
        assert (outcome["name"], outcome["shape_description"], outcome["aliases"]) == (
            "Books",
            "ordinary shelf",
            ["volumes"],
        )
