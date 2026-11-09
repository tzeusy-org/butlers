"""A merged-away entity id aliases its survivor under owner law (bu-dhloi1.34).

An entity merge tombstones the source with ``metadata.merged_into``, so an owner
term written on the source would miss the survivor that now holds that person.
Entity params therefore narrow only through the broker's ``entity`` resolver,
which matches both the call and the terms on the live survivor. It never rewrites
the call: a merge tool must keep naming its source, and a grant on the source
must not widen to everything the survivor already held. Merges here run through
the real ``merge_entity_pair`` against a migrated database.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastmcp import FastMCP

from butlers.api.routers.memory import list_entities, promote_entity
from butlers.capability_broker.contracts import BrokerRefused, Capability, Catalog, Reason
from butlers.core.owner_policy import declared_resource_params
from butlers.core.tool_catalog import RecipientParameters
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_INVENTORY
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from butlers.roster_imports import roster_module
from butlers.testing.db_manager import db_manager
from butlers.tools.relationship.entity_merge import merge_entity_pair
from tests.capability_broker.helpers import capability
from tests.capability_broker.test_broker_ledger import (
    CATALOG,
    DOMAIN,
    StaticCatalog,
    _grant,
    _law,
    _refused,
    world,
)
from tests.switchboard.goal_ledger_fixtures import codec

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _from_inventory(name: str) -> Capability:
    declared = next(p for p in TOOL_PRESENTATION_INVENTORY if p.canonical_name == name)
    return capability(
        name,
        declared.effect_class,
        DOMAIN,
        resource_params=tuple(declared.resource_params or ()),
        alias_free_params=frozenset(declared.alias_free_params or ()),
        alias_resolvers=tuple(sorted(dict(declared.alias_resolvers or {}).items())),
    )


EPISODES = _from_inventory("chronicler_list_episodes")
ENTITY_MERGE = _from_inventory("memory_entity_merge")
SET_POSTURE = _from_inventory("entity_set_posture")
ASSERT_SENDER = _from_inventory("identity_assert_sender_channel")
NOTES = capability(
    "entity_note",
    "record",
    DOMAIN,
    resource_params=("entity_ids",),
    alias_resolvers=(("entity_ids", "entity"),),
    recipient_params=RecipientParameters(),
)
ALIASED = Catalog(
    "catalog-v1",
    (*CATALOG.capabilities, EPISODES, ENTITY_MERGE, NOTES, SET_POSTURE, ASSERT_SENDER),
)
PARAM = "participant_entity_id"


@asynccontextmanager
async def _world(url, *, resolver=True):
    async with world(url, catalog=StaticCatalog(ALIASED)) as w:
        if resolver:
            from butlers.capability_broker.aliases import EntityAliases

            # The broker's own runtime role, as production would construct it.
            w.broker.aliases = EntityAliases(w.broker.pool)
        yield w


async def _person(w, name="Person") -> UUID:
    return await w.owner.fetchval(
        "INSERT INTO public.entities (canonical_name) VALUES ($1) RETURNING id",
        f"{name} {uuid4()}",
    )


async def _merge(url, source: UUID, target: UUID) -> None:
    """The real merge authority, on a relationship-scoped pool."""
    async with asyncpg.create_pool(
        url,
        server_settings={"search_path": "relationship,public"},
        init=codec,
        min_size=1,
        max_size=2,
    ) as pool:
        await merge_entity_pair(
            pool, source_entity_id=source, target_entity_id=target, target_schemas=()
        )


async def _never(w, cap: Capability, param: str, ids: list[UUID]):
    declared = declared_resource_params([cap], "capability", cap.identity)
    return await _law(
        w,
        "capability",
        cap.identity,
        "never",
        resources={param: [str(item) for item in ids]},
        resource_params=tuple(declared.params),
        alias_free_params=tuple(declared.alias_free),
    )


async def _episodes_call(w, entity: UUID | str):
    goal = await w.goal(_grant(EPISODES))
    _, token = await w.invocation(goal, _grant(EPISODES))
    return await w.broker.call(goal, token, EPISODES.identity, {PARAM: str(entity)})


@pytest.mark.parametrize("resolver", [False, True])
async def test_a_term_on_a_merged_away_id_holds_its_survivor(broker_db_url, resolver):
    """The gap: written on the source, the term must not be released by the survivor."""
    async with _world(broker_db_url, resolver=resolver) as w:
        source, survivor = await _person(w), await _person(w)
        await _never(w, EPISODES, PARAM, [source])
        await _merge(broker_db_url, source, survivor)
        held = await _refused(_episodes_call(w, survivor))
        assert held.reason is Reason.POLICY


@pytest.mark.parametrize(
    ("cap", "other_args"),
    [(SET_POSTURE, {"posture": "quiet"}), (ASSERT_SENDER, {"channel_type": "telegram"})],
    ids=["entity_set_posture", "identity_assert_sender_channel"],
)
async def test_person_id_tools_resolve_a_merged_away_term_to_its_survivor(
    broker_db_url, cap, other_args
):
    """bu-dhloi1.46: a term written on a merged-away person must hold these tools' survivor."""
    assert dict(cap.alias_resolvers) == {"entity_id": "entity"}
    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        await _never(w, cap, "entity_id", [source])
        await _merge(broker_db_url, source, survivor)
        goal = await w.goal(_grant(cap))
        _, token = await w.invocation(goal, _grant(cap))
        held = await _refused(
            w.broker.call(goal, token, cap.identity, {"entity_id": str(survivor), **other_args})
        )
        assert held.reason is Reason.POLICY


async def test_a_term_on_a_survivor_holds_the_merged_away_id(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        await _merge(broker_db_url, source, survivor)
        await _never(w, EPISODES, PARAM, [survivor])
        held = await _refused(_episodes_call(w, source))
        assert held.reason is Reason.POLICY


async def test_a_term_follows_a_merge_chain_and_releases_other_people(broker_db_url):
    async with _world(broker_db_url) as w:
        first, second, last = await _person(w), await _person(w), await _person(w)
        other = await _person(w)
        await _never(w, EPISODES, PARAM, [first])
        await _merge(broker_db_url, first, second)
        await _merge(broker_db_url, second, last)
        held = await _refused(_episodes_call(w, last))
        assert held.reason is Reason.POLICY
        result = await _episodes_call(w, other)
        assert result.status == "returned"
        assert w.forwarder.calls[-1][1] == {PARAM: str(other)}


async def test_matching_never_rewrites_what_the_domain_receives(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor, other = await _person(w), await _person(w), await _person(w)
        await _merge(broker_db_url, source, survivor)
        await _never(w, EPISODES, PARAM, [other])
        await _episodes_call(w, source)
        _, args, assertion, _ = w.forwarder.calls[-1]
        assert args == {PARAM: str(source)}
        assert w.verify(assertion, EPISODES.name, {PARAM: str(source)})
        with pytest.raises(BrokerRefused):
            w.verify(assertion, EPISODES.name, {PARAM: str(survivor)})


async def test_a_merge_tool_keeps_naming_its_tombstoned_source(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor, target = await _person(w), await _person(w), await _person(w)
        await _merge(broker_db_url, source, survivor)
        goal = await w.goal(_grant(ENTITY_MERGE))
        _, token = await w.invocation(goal, _grant(ENTITY_MERGE))
        args = {"source_entity_id": str(source), "target_entity_id": str(target)}
        await w.broker.call(goal, token, ENTITY_MERGE.identity, args)
        assert w.forwarder.calls[-1][1] == args


async def test_every_listed_id_is_matched_on_its_survivor(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor, other = await _person(w), await _person(w), await _person(w)
        await _never(w, NOTES, "entity_ids", [source])
        await _merge(broker_db_url, source, survivor)
        goal = await w.goal(_grant(NOTES))
        _, token = await w.invocation(goal, _grant(NOTES))
        held = await _refused(
            w.broker.call(
                goal, token, NOTES.identity, {"entity_ids": [str(other), str(survivor).upper()]}
            )
        )
        assert held.reason is Reason.POLICY
        result = await w.broker.call(goal, token, NOTES.identity, {"entity_ids": [str(other)]})
        assert result.status == "committed"
        assert w.forwarder.calls[-1][1] == {"entity_ids": [str(other)]}


@pytest.mark.parametrize("break_kind", ["missing", "cycle", "malformed", "unknown", "not_a_uuid"])
async def test_an_unwalkable_id_keeps_the_term_selected(broker_db_url, break_kind):
    async with _world(broker_db_url) as w:
        entity, partner, other = await _person(w), await _person(w), await _person(w)
        await _never(w, EPISODES, PARAM, [other])
        tombstone = "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1"
        spelled: UUID | str = entity
        if break_kind == "missing":
            await w.owner.execute(tombstone, entity, {"merged_into": str(uuid4())})
        elif break_kind == "cycle":
            await w.owner.execute(tombstone, entity, {"merged_into": str(partner)})
            await w.owner.execute(tombstone, partner, {"merged_into": str(entity)})
        elif break_kind == "malformed":
            await w.owner.execute(tombstone, entity, {"merged_into": "not-a-uuid"})
        elif break_kind == "unknown":
            spelled = uuid4()
        else:
            spelled = "Alice"
        held = await _refused(_episodes_call(w, spelled))
        assert held.reason is Reason.POLICY


async def test_a_deleted_entity_stays_its_own_identity(broker_db_url):
    """Deletion aliases nothing, so it drops no term's narrowing and releases nobody."""
    async with _world(broker_db_url) as w:
        deleted, other = await _person(w), await _person(w)
        await _never(w, EPISODES, PARAM, [deleted])
        await w.owner.execute(
            "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
            deleted,
            {"deleted_at": "2026-11-06T00:00:00Z"},
        )
        held = await _refused(_episodes_call(w, deleted))
        assert held.reason is Reason.POLICY
        assert (await _episodes_call(w, other)).status == "returned"


async def test_the_approval_fence_sees_a_merge_after_the_park(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        goal = await w.goal(_grant(NOTES))
        _, token = await w.invocation(goal, _grant(NOTES))
        await _law(w, "capability", NOTES.identity, "always_ask")
        parked = await w.broker.call(goal, token, NOTES.identity, {"entity_ids": [str(source)]})
        assert parked.status == "parked"
        await _merge(broker_db_url, source, survivor)
        await _never(w, NOTES, "entity_ids", [survivor])
        approved = {"entity_ids": [str(source)]}
        held = await _refused(
            w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, approved)
        )
        assert held.reason is Reason.POLICY
        assert all(call[1] != approved for call in w.forwarder.calls)


class _MergesBetweenReads:
    """The real resolver, with a real merge committing between the call's read and the term's."""

    def __init__(self, resolver, url, source: UUID, target: UUID):
        self._resolver, self._url, self._merge = resolver, url, (source, target)
        self._reads = 0

    async def resolve(self, domain, kind, values):
        self._reads += 1
        if self._reads == 3:  # The call's read is an answer and its fixed-point check.
            await _merge(self._url, *self._merge)
        return await self._resolver.resolve(domain, kind, values)


def _merge_between_reads(w, url, source: UUID, target: UUID) -> None:
    w.broker.aliases = _MergesBetweenReads(w.broker.aliases, url, source, target)
    # A merge past the deadline would hold the call by failing closed, proving nothing.
    w.broker.alias_timeout = 30


async def test_a_merge_between_the_reads_cannot_release_the_term_on_the_call(broker_db_url):
    """The call reads its own id, then the term reads the survivor: the same raw id still holds."""
    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        await _never(w, EPISODES, PARAM, [source])
        _merge_between_reads(w, broker_db_url, source, survivor)
        held = await _refused(_episodes_call(w, source))
        assert held.reason is Reason.POLICY
        assert await _survivor_of(w, source) == str(survivor)


async def test_a_chain_growing_between_the_reads_cannot_release_it_either(broker_db_url):
    """The call reads the middle of the chain and the term its new end; only raw ids still meet."""
    async with _world(broker_db_url) as w:
        first, middle, last = await _person(w), await _person(w), await _person(w)
        await _merge(broker_db_url, first, middle)
        await _never(w, EPISODES, PARAM, [first])
        _merge_between_reads(w, broker_db_url, middle, last)
        held = await _refused(_episodes_call(w, first))
        assert held.reason is Reason.POLICY
        assert await _survivor_of(w, middle) == str(last)


async def test_a_merge_between_the_reads_still_releases_other_people(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor, other = await _person(w), await _person(w), await _person(w)
        await _never(w, EPISODES, PARAM, [source])
        _merge_between_reads(w, broker_db_url, source, survivor)
        spelled = str(other).upper()
        assert (await _episodes_call(w, spelled)).status == "returned"
        assert w.forwarder.calls[-1][1] == {PARAM: spelled}


async def test_a_merge_between_the_reads_at_the_approval_fence_holds_the_call(broker_db_url):
    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        goal = await w.goal(_grant(NOTES))
        _, token = await w.invocation(goal, _grant(NOTES))
        await _law(w, "capability", NOTES.identity, "always_ask")
        approved = {"entity_ids": [str(source)]}
        parked = await w.broker.call(goal, token, NOTES.identity, approved)
        assert parked.status == "parked"
        await _never(w, NOTES, "entity_ids", [source])
        _merge_between_reads(w, broker_db_url, source, survivor)
        held = await _refused(
            w.broker.execute_approved(goal, parked.effect_id, parked.approval_ref, approved)
        )
        assert held.reason is Reason.POLICY
        assert await _survivor_of(w, source) == str(survivor)
        assert all(call[1] != approved for call in w.forwarder.calls)


async def _survivor_of(w, entity: UUID) -> str | None:
    return await w.owner.fetchval(
        "SELECT metadata ->> 'merged_into' FROM public.entities WHERE id = $1", entity
    )


async def _memory_tool(pool, name: str):
    module = MemoryModule()
    mcp = FastMCP("merge-tombstone")
    await module.register_tools(
        mcp, MemoryModuleConfig(), SimpleNamespace(pool=pool, schema="public"), "memory"
    )
    return (await mcp.get_tool(name)).fn


async def _relationship_entity_update(pool):
    relationship = roster_module("relationship", "modules/__init__")
    module = relationship.RelationshipModule()
    mcp = FastMCP("merge-tombstone")
    config = relationship.RelationshipModuleConfig(groups=["entity_write"])
    await module.register_tools(
        mcp, config, SimpleNamespace(pool=pool, schema="relationship"), "relationship"
    )
    return (await mcp.get_tool("entity_update")).fn


@pytest.mark.parametrize("tool", ["memory_entity_update", "relationship_entity_update"])
@pytest.mark.parametrize("forged", [None, "other"])
async def test_a_tool_cannot_unmerge_a_tombstone_and_release_its_term(broker_db_url, tool, forged):
    """Both tools reach one writer; the survivor stays held, the caller's other keys land."""
    async with _world(broker_db_url) as w:
        source, survivor, other = await _person(w), await _person(w), await _person(w)
        await _never(w, EPISODES, PARAM, [source])
        await _merge(broker_db_url, source, survivor)
        update = (
            await _memory_tool(w.owner, tool)
            if tool == "memory_entity_update"
            else await _relationship_entity_update(w.owner)
        )
        result = await update(
            str(source),
            metadata={
                "merged_into": str(other) if forged else None,
                "deleted_at": "2026-11-06T00:00:00Z",
                "unidentified": True,
                "note": "kept",
            },
        )
        assert result["metadata"]["note"] == "kept"
        assert result["metadata"]["merged_into"] == str(survivor)
        assert not {"deleted_at", "unidentified"} & set(result["metadata"])
        held = await _refused(_episodes_call(w, survivor))
        assert held.reason is Reason.POLICY


async def test_a_metadata_write_does_not_lose_a_merge_it_raced(broker_db_url):
    """The writer waits on the merge's row lock, then must keep what the merge committed."""
    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        update = await _memory_tool(w.owner, "memory_entity_update")
        async with w.owner.acquire() as merging, merging.transaction():
            await merging.execute(
                "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
                source,
                {"merged_into": str(survivor)},
            )
            write = asyncio.create_task(update(str(source), metadata={"note": "kept"}))
            for _ in range(1000):
                if await w.owner.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
                    "AND datname = current_database()"
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("the metadata write never waited on the merge's row lock")
        result = await write
        assert result["metadata"] == {"merged_into": str(survivor), "note": "kept"}


async def test_a_created_entity_cannot_be_born_a_tombstone(broker_db_url):
    async with _world(broker_db_url) as w:
        survivor = await _person(w)
        create = await _memory_tool(w.owner, "memory_entity_create")
        created = await create(
            f"Forged {uuid4()}",
            "person",
            metadata={
                "merged_into": str(survivor),
                "deleted_at": "2026-11-06T00:00:00Z",
                "note": "kept",
            },
        )
        metadata = await w.owner.fetchval(
            "SELECT metadata FROM public.entities WHERE id = $1", UUID(created["entity_id"])
        )
        assert metadata == {"note": "kept"}


async def test_an_agent_created_unidentified_entity_reaches_owner_review(broker_db_url):
    """The butler skills create a guessed identity with the flag; the owner must see it."""
    async with _world(broker_db_url) as w:
        create = await _memory_tool(w.owner, "memory_entity_create")
        created = await create(f"Sam {uuid4()}", "person", metadata={"unidentified": True})
        entity_id = created["entity_id"]
        db = db_manager({"relationship": w.owner})

        async def listed(unidentified: bool) -> set[str]:
            page = await list_entities(
                q=None,
                entity_type=None,
                unidentified=unidentified,
                archived=False,
                offset=0,
                limit=200,
                db=db,
            )
            return {entity.id for entity in page.data}

        assert entity_id in await listed(True)
        assert entity_id not in await listed(False)
        promoted = await promote_entity(entity_id, db=db)
        assert promoted.data.unidentified is False
        assert entity_id in await listed(False)


async def test_an_update_cannot_mark_an_entity_unidentified(broker_db_url):
    """The flag is set at create and cleared by a promote; an update changes neither."""
    async with _world(broker_db_url) as w:
        create = await _memory_tool(w.owner, "memory_entity_create")
        update = await _memory_tool(w.owner, "memory_entity_update")
        flagged = await create(f"Sam {uuid4()}", "person", metadata={"unidentified": True})
        result = await update(flagged["entity_id"], metadata={"unidentified": False, "note": "n"})
        assert result["metadata"] == {"unidentified": True, "note": "n"}


async def test_a_contact_merge_leaves_no_survivor_pointer_to_resolve(broker_db_url):
    """Why contact params stop narrowing: the merged-away contact id simply disappears."""
    from butlers.capability_broker.aliases import EntityAliases
    from butlers.tools.relationship.contacts import contact_merge

    async with _world(broker_db_url) as w:
        source_entity, target_entity = await _person(w), await _person(w)
        source, target = uuid4(), uuid4()
        async with asyncpg.create_pool(
            broker_db_url,
            server_settings={"search_path": "relationship,public"},
            init=codec,
            min_size=1,
            max_size=4,
        ) as pool:
            for contact, entity in ((source, source_entity), (target, target_entity)):
                await pool.execute(
                    "INSERT INTO contact_entity_map (contact_id, entity_id) VALUES ($1, $2)",
                    contact,
                    entity,
                )
            await contact_merge(pool, source, target)
            assert not await pool.fetchval(
                "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", source
            )
            assert await pool.fetchval(
                "SELECT count(*) FROM contact_entity_map WHERE contact_id = $1", target
            )
            # The linked entities do merge, so an entity term still follows it.
            assert await EntityAliases(pool).resolve(DOMAIN, "entity", {str(source_entity)}) == {
                str(source_entity): str(target_entity)
            }


async def test_aliases_route_each_kind_to_its_resolver(broker_db_url):
    from butlers.capability_broker.aliases import AliasesByKind, CalendarAliases, EntityAliases

    async with _world(broker_db_url) as w:
        source, survivor = await _person(w), await _person(w)
        await _merge(broker_db_url, source, survivor)
        await w.domain.execute("DELETE FROM calendar_sources")
        await w.domain.execute(
            "INSERT INTO calendar_sources (source_key, source_kind, provider, calendar_id, "
            "metadata) VALUES ('provider:google:a@example.com', 'provider_event', 'google', "
            "'a@example.com', $1::jsonb)",
            {"projection": "provider_sync", "account_email": "a@example.com"},
        )
        router = AliasesByKind(EntityAliases(w.broker.pool), CalendarAliases({DOMAIN: w.domain}))
        assert await router.resolve(DOMAIN, "entity", {str(source)}) == {str(source): str(survivor)}
        assert await router.resolve(DOMAIN, "calendar", {"primary"}) == {"primary": "a@example.com"}
        assert await router.resolve(DOMAIN, "unknown", {str(source)}) == {}
