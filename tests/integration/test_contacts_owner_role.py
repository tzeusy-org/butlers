"""REQ-module-contacts-001/002: migrated owning-role positives and foreign refusal.

Registered target operations use real pools/transactions. The in-process
Switchboard peer is a deterministic test transport, not deployed admission.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from contextlib import AsyncExitStack
from types import SimpleNamespace

import asyncpg
import pytest
from fastmcp import Client, FastMCP

from butlers.db import register_jsonb_codec
from butlers.migrations import run_migrations
from butlers.modules._roster_relationship import RelationshipModule, RelationshipModuleConfig
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.contacts.owner import ContactsLookupAmbiguous, ContactsOwner
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def contacts_owning_database(postgres_container):
    url = create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "memory", "relationship", "approvals", "contacts"],
        {
            chain: "relationship"
            for chain in ["core", "memory", "relationship", "approvals", "contacts"]
        },
    )
    for schema in ["general", "education", "health", "home"]:
        for chain in ["core", "contacts"]:
            asyncio.run(run_migrations(url, chain=chain, schema=schema))
    return url


async def test_migrated_contacts_owner_denial_candidates_readback_and_rollback(
    contacts_owning_database, monkeypatch
):
    url = contacts_owning_database
    async with AsyncExitStack() as stack:
        pools = {}
        for schema in ["relationship", "general", "education", "health", "home"]:

            async def setup(connection, schema=schema):
                await connection.execute(f'SET ROLE "butler_{schema}_rw"')
                await connection.execute(f'SET search_path TO "{schema}", public')
                row = await connection.fetchrow(
                    "SELECT current_user AS role,current_schema() AS schema"
                )
                assert row["role"] == f"butler_{schema}_rw" and row["schema"] == schema

            pool = await asyncpg.create_pool(
                url,
                min_size=1,
                max_size=5,
                init=register_jsonb_codec,
                setup=setup,
            )
            stack.push_async_callback(pool.close)
            pools[schema] = pool
        relationship = pools["relationship"]
        # Prove release RESET ALL is repaired on every subsequent acquisition.
        for pool in pools.values():
            for _ in range(2):
                async with pool.acquire() as connection:
                    assert await connection.fetchval("SELECT current_user") != "butlers"
        for schema in ["general", "education", "health", "home"]:
            pool = pools[schema]
            assert not await pool.fetchval("SELECT has_schema_privilege('relationship','USAGE')")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.fetch("SELECT id FROM relationship.entity_facts")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await pool.execute(
                    "INSERT INTO relationship.entity_facts(subject) VALUES($1)", uuid.uuid4()
                )

        async def person(name, metadata=None, roles=None):
            return await relationship.fetchval(
                "INSERT INTO public.entities(canonical_name,entity_type,metadata,roles) "
                "VALUES($1,'person',$2,$3) RETURNING id",
                name,
                metadata or {},
                roles or [],
            )

        known = await person("Synthetic Contacts known")
        legacy = await person("Synthetic Contacts temporary", {"unidentified": True})
        owner_id = await person("Synthetic Contacts owner", roles=["owner"])
        module, mcp = RelationshipModule(), FastMCP("migrated-Contacts-target")
        await module.register_tools(
            mcp,
            RelationshipModuleConfig(groups=["contacts_ingress"]),
            SimpleNamespace(pool=relationship),
            "relationship",
        )
        approvals = ApprovalsModule()
        await approvals.on_startup({}, SimpleNamespace(pool=relationship))
        stack.push_async_callback(approvals.on_shutdown)
        target = await stack.enter_async_context(Client(mcp))

        async def route(name, arguments):
            assert name == "route" and arguments["target_butler"] == "relationship"
            result = await target.call_tool(
                arguments["tool_name"], arguments["args"], raise_on_error=False
            )
            return SimpleNamespace(is_error=result.is_error, data={"result": result.data})

        client = ContactsOwner(lambda: SimpleNamespace(call_tool=route), "general")
        first = await client.report(legacy, "email", "legacy@example.test", True)
        assert first["outcome"] == "inserted"
        # A separate acquisition reads the committed active result and server provenance.
        row = await relationship.fetchrow(
            "SELECT validity,src,content_authority,verified FROM relationship.entity_facts WHERE id=$1",
            uuid.UUID(first["fact_id"]),
        )
        assert dict(row) == {
            "validity": "active",
            "src": "contacts-backfill",
            "content_authority": "third_party",
            "verified": False,
        }
        assert await client.lookup("email", " LEGACY@EXAMPLE.TEST ") == legacy
        candidate = await client.report(known, "email", "candidate@example.test", False)
        assert candidate["outcome"] == "candidate"
        assert await client.lookup("email", "candidate@example.test") is None
        pending = await client.report(owner_id, "email", "owner@example.test", False)
        assert pending["outcome"] == "pending_approval" and pending["fact_id"] is None
        action = await relationship.fetchrow(
            "SELECT status,tool_name FROM pending_actions WHERE id=$1",
            uuid.UUID(pending["action_id"]),
        )
        assert dict(action) == {"status": "pending", "tool_name": "relationship_assert_fact"}
        assert not await relationship.fetchval(
            "SELECT EXISTS(SELECT 1 FROM relationship.entity_facts WHERE subject=$1 AND validity='active')",
            owner_id,
        )
        results = await asyncio.gather(
            *[client.report(legacy, "phone", "+15550001111", False) for _ in range(2)]
        )
        assert {r["outcome"] for r in results} <= {"inserted", "unchanged"}
        assert len({r["fact_id"] for r in results}) == 1
        assert (
            await relationship.fetchval(
                "SELECT count(*) FROM relationship.entity_facts WHERE subject=$1 AND predicate='has-phone' AND validity='active'",
                legacy,
            )
            == 1
        )
        duplicate = await person("Synthetic Contacts ambiguous", {"unidentified": True})
        await client.report(duplicate, "email", "legacy@example.test", False)
        with pytest.raises(ContactsLookupAmbiguous):
            await client.lookup("email", "legacy@example.test")
        assert await client.lookup("email", "truly-empty@example.test") is None
        writer = importlib.import_module("butlers.tools.relationship.relationship_assert_fact")
        original = writer.record_coverage

        async def fail_after_write(*args, **kwargs):
            raise RuntimeError("synthetic rollback witness")

        monkeypatch.setattr(writer, "record_coverage", fail_after_write)
        tool = await mcp.get_tool("contacts_report_channel")
        with pytest.raises(RuntimeError, match="synthetic rollback witness"):
            await tool.fn(
                subject=legacy, channel_type="email", channel_value="rollback@example.test"
            )
        assert not await relationship.fetchval(
            "SELECT EXISTS(SELECT 1 FROM relationship.entity_facts WHERE object='rollback@example.test')"
        )
        monkeypatch.setattr(writer, "record_coverage", original)
        restored = await client.report(legacy, "email", "rollback@example.test", False)
        assert restored["outcome"] == "inserted"


# bu-dridrs: source custody through actual sync/backfill, using migrated roles.
@pytest.fixture
async def deletion_context(contacts_owning_database):
    from butlers.modules.contacts.backfill import ContactBackfillEngine
    from butlers.modules.contacts.sync import ContactsSyncEngine, ContactsSyncStateStore

    async with AsyncExitStack() as stack:
        pools = {}
        for schema in ["general", "relationship"]:

            async def setup(connection, schema=schema):
                await connection.execute(f'SET ROLE "butler_{schema}_rw"')
                await connection.execute(f'SET search_path TO "{schema}", public')
                row = await connection.fetchrow(
                    "SELECT current_user AS role,current_schema() AS schema"
                )
                assert row["role"] == f"butler_{schema}_rw" and row["schema"] == schema

            pool = await asyncpg.create_pool(
                contacts_owning_database,
                min_size=1,
                max_size=3,
                init=register_jsonb_codec,
                setup=setup,
            )
            stack.push_async_callback(pool.close)
            pools[schema] = pool
        host, owning = pools["general"], pools["relationship"]
        module, mcp = RelationshipModule(), FastMCP("synthetic-deletion-target")
        await module.register_tools(
            mcp,
            RelationshipModuleConfig(groups=["contacts_ingress"]),
            SimpleNamespace(pool=owning),
            "relationship",
        )
        target = await stack.enter_async_context(Client(mcp))

        async def route(name, arguments):
            assert name == "route" and arguments["target_butler"] == "relationship"
            result = await target.call_tool(
                arguments["tool_name"], arguments["args"], raise_on_error=False
            )
            return SimpleNamespace(is_error=result.is_error, data={"result": result.data})

        owner = ContactsOwner(lambda: SimpleNamespace(call_tool=route), "general")
        account = "synthetic-" + uuid.uuid4().hex
        ids = {}
        for n in [10, 11, 12]:
            entity = await host.fetchval(
                "INSERT INTO public.entities(canonical_name,entity_type,metadata) "
                "VALUES($1,'person',$2) RETURNING id",
                account + str(n),
                {"unidentified": True},
            )
            ids[n] = entity
            await host.execute(
                "INSERT INTO contacts_source_links(provider,account_id,external_contact_id,local_entity_id) "
                "VALUES('telegram',$1,$2,$3)",
                account,
                f"telegram:{n}",
                entity,
            )
        await host.execute(
            "INSERT INTO contacts_source_links(provider,account_id,external_contact_id,local_entity_id) "
            "VALUES('google',$1,'people/survivor',$2),('telegram',$3,'telegram:10',$2)",
            account,
            ids[10],
            account + "-other",
        )
        await owner.report(ids[10], "telegram_user_id", "10", False)
        from butlers.modules.contacts.sync import CanonicalContact, ContactBatch

        class Provider:
            name = "telegram"
            pause = None
            failure = None
            contacts = []
            cursor = "initial"

            async def full_sync(self, **kwargs):
                if self.pause is not None:
                    entered, release = self.pause
                    entered.set()
                    await release.wait()
                if self.failure:
                    raise self.failure
                return ContactBatch(
                    contacts=self.contacts,
                    next_sync_cursor=self.cursor,
                    checkpoint={"complete_full_fetch": True},
                )

            async def incremental_sync(self, **kwargs):
                return await self.full_sync(**kwargs)

        provider = Provider()
        provider.contacts = [
            CanonicalContact(external_id=f"telegram:{n}", raw={"telegram_user_id": str(n)})
            for n in ids
        ]
        store = ContactsSyncStateStore(host)
        backfill = ContactBackfillEngine(host, provider="telegram", account_id=account, owner=owner)

        def engine(p=provider, apply=backfill):
            return ContactsSyncEngine(
                provider=p, state_store=store, apply_contact=apply, batch_finalizer=backfill
            )

        yield SimpleNamespace(
            host=host,
            owning=owning,
            owner=owner,
            account=account,
            ids=ids,
            provider=provider,
            store=store,
            backfill=backfill,
            engine=engine,
            provider_type=Provider,
        )


async def _deletion_rows(ctx):
    rows = await ctx.host.fetch(
        "SELECT provider,account_id,external_contact_id,local_entity_id,deleted_at "
        "FROM contacts_source_links WHERE account_id=ANY($1) ORDER BY provider,account_id,external_contact_id",
        [ctx.account, ctx.account + "-other"],
    )
    return {(r["provider"], r["account_id"], r["external_contact_id"]): dict(r) for r in rows}


@pytest.mark.parametrize("version_kind", ["etag", "raw"])
async def test_telegram_removal_same_version_reappearance(deletion_context, version_kind, caplog):

    ctx = deletion_context
    if version_kind == "etag":
        ctx.provider.contacts = [
            c.model_copy(update={"etag": "same"}) for c in ctx.provider.contacts
        ]
    original = list(ctx.provider.contacts)
    engine = ctx.engine()
    await engine.sync(account_id=ctx.account, mode="full")
    before = await _deletion_rows(ctx)
    facts = [
        dict(r)
        for r in await ctx.owning.fetch(
            "SELECT id,subject,predicate,object,validity FROM entity_facts WHERE subject=$1 ORDER BY id",
            ctx.ids[10],
        )
    ]
    assert facts and any(r["predicate"] == "has-handle" for r in facts)
    caplog.set_level("INFO")
    ctx.provider.contacts, ctx.provider.cursor = original[1:], "removed"
    result = await engine.sync(account_id=ctx.account, mode="full")
    after = await _deletion_rows(ctx)
    key = ("telegram", ctx.account, "telegram:10")
    assert result.deleted_contacts == 1 and after[key]["deleted_at"] is not None
    assert all(after[k] == v for k, v in before.items() if k != key)
    assert (
        await ctx.host.fetchval(
            "SELECT count(*) FROM public.entities WHERE id=ANY($1)", list(ctx.ids.values())
        )
        == 3
    )
    assert facts == [
        dict(r)
        for r in await ctx.owning.fetch(
            "SELECT id,subject,predicate,object,validity FROM entity_facts WHERE subject=$1 ORDER BY id",
            ctx.ids[10],
        )
    ]
    state = await ctx.store.load(provider="telegram", account_id=ctx.account)
    assert "telegram:10" not in state.contact_versions
    timestamp = after[key]["deleted_at"]
    assert (await engine.sync(account_id=ctx.account, mode="full")).deleted_contacts == 0
    assert (await _deletion_rows(ctx))[key]["deleted_at"] == timestamp
    transitions = [r for r in caplog.records if r.msg == "contact_sync_deleted_source"]
    assert len(transitions) == 1
    event = transitions[0]
    assert (event.entity_id, event.provider, event.account_id, event.external_id) == (
        str(ctx.ids[10]),
        "telegram",
        ctx.account,
        "telegram:10",
    )
    # The raw/etag remains byte-identical and the contact has no name or phone.
    assert original[0].display_name is None and not original[0].phones
    ctx.provider.contacts, ctx.provider.cursor = original, "reappeared"
    entity_count = await ctx.host.fetchval("SELECT count(*) FROM public.entities")
    resumed = await ctx.engine().sync(account_id=ctx.account, mode="full")
    restored = (await _deletion_rows(ctx))[key]
    assert resumed.applied_contacts == 1 and restored["deleted_at"] is None
    assert restored["local_entity_id"] == ctx.ids[10]
    assert await ctx.host.fetchval("SELECT count(*) FROM public.entities") == entity_count
    assert (
        await ctx.host.fetchval(
            "SELECT count(*) FROM public.entities WHERE id=ANY($1)", list(ctx.ids.values())
        )
        == 3
    )
    # Old live-version cache paired with an inactive anchor must also force apply.
    await ctx.host.execute(
        "UPDATE contacts_source_links SET deleted_at=now() WHERE provider='telegram' AND account_id=$1 "
        "AND external_contact_id='telegram:10'",
        ctx.account,
    )
    assert (await ctx.engine().sync(account_id=ctx.account, mode="full")).applied_contacts == 1
    assert (await _deletion_rows(ctx))[key]["deleted_at"] is None
    assert isinstance(
        (await ctx.store.load(provider="telegram", account_id=ctx.account)).contact_versions, dict
    )

    # Invalid inactive identities cannot be restored or replaced by a duplicate person.
    from butlers.modules.contacts.sync import ContactsSyncError

    await ctx.host.execute(
        "UPDATE public.entities SET metadata=metadata || $2::jsonb WHERE id=$1",
        ctx.ids[10],
        {"merged_into": str(uuid.uuid4())},
    )
    await ctx.host.execute(
        "UPDATE contacts_source_links SET deleted_at=now() WHERE provider='telegram' AND account_id=$1 "
        "AND external_contact_id='telegram:10'",
        ctx.account,
    )
    with pytest.raises(ContactsSyncError):
        await ctx.engine().sync(account_id=ctx.account, mode="full")
    assert (await _deletion_rows(ctx))[key]["deleted_at"] is not None
    assert await ctx.host.fetchval("SELECT count(*) FROM public.entities") == entity_count


async def test_telegram_absence_guard_refusals(deletion_context, caplog, monkeypatch):
    from butlers.modules.contacts.backfill import ContactBackfillEngine
    from butlers.modules.contacts.sync import ContactsSyncError

    ctx = deletion_context
    await ctx.engine().sync(account_id=ctx.account, mode="full")
    contacts = list(ctx.provider.contacts)
    baseline = await _deletion_rows(ctx)
    caplog.set_level("INFO")
    for fetched in [[], contacts[:1]]:
        ctx.provider.contacts = fetched
        assert (await ctx.engine().sync(account_id=ctx.account, mode="full")).deleted_contacts == 0
        assert await _deletion_rows(ctx) == baseline
    # A matching hash plus an actual empty FULL fetch remains suspect; incremental does no absence.
    for mode in ["full", "incremental"]:
        ctx.provider.contacts = []
        await ctx.engine().sync(account_id=ctx.account, mode=mode)
        assert await _deletion_rows(ctx) == baseline
    assert {r.reason for r in caplog.records if r.msg == "contact_sync_absence_refused"} >= {
        "empty_full",
        "sharp_shrink",
    }
    for event in caplog.records:
        if event.msg == "contact_sync_absence_refused":
            assert event.provider == "telegram" and event.account_id == ctx.account
            assert event.baseline_count == 3
            assert event.present_count == (0 if event.reason == "empty_full" else 1)
    # A late page error after a plausible nonempty first page still changes no link.
    from butlers.modules.contacts.sync import ContactBatch

    original_full = ctx.provider.full_sync

    async def partial_full(*, account_id, page_token=None):
        if page_token is None:
            return ContactBatch(
                contacts=contacts[1:],
                next_page_token="second",
                checkpoint={"complete_full_fetch": True},
            )
        raise ContactsSyncError("synthetic late page failure")

    monkeypatch.setattr(ctx.provider, "full_sync", partial_full)
    with pytest.raises(ContactsSyncError):
        await ctx.engine().sync(account_id=ctx.account, mode="full")
    assert await _deletion_rows(ctx) == baseline
    monkeypatch.setattr(ctx.provider, "full_sync", original_full)
    ctx.provider.contacts = contacts[1:]
    for invalid in [float("nan"), float("inf"), -0.1, 1.0, True]:
        with pytest.raises(ValueError):
            ContactBackfillEngine(
                ctx.host,
                provider="telegram",
                account_id=ctx.account,
                owner=ctx.owner,
                max_shrink_fraction=invalid,
            )
    # Exact equality is accepted; strictly greater shrink is refused.
    for threshold, expected in [(1 / 3 - 0.001, 0), (1 / 3 + 0.001, 1), (1 / 3, 1)]:
        ctx.provider.contacts = contacts
        await ctx.engine().sync(account_id=ctx.account, mode="full")
        ctx.provider.contacts = contacts[1:]
        backfill = ContactBackfillEngine(
            ctx.host,
            provider="telegram",
            account_id=ctx.account,
            owner=ctx.owner,
            max_shrink_fraction=threshold,
        )
        from butlers.modules.contacts.sync import ContactsSyncEngine

        engine = ContactsSyncEngine(
            provider=ctx.provider,
            state_store=ctx.store,
            apply_contact=backfill,
            batch_finalizer=backfill,
        )
        assert (await engine.sync(account_id=ctx.account, mode="full")).deleted_contacts == expected
    ctx.provider.failure = ContactsSyncError("synthetic provider failure")
    state = await ctx.store.load(provider="telegram", account_id=ctx.account)
    with pytest.raises(ContactsSyncError):
        await ctx.engine().sync(account_id=ctx.account, mode="full")
    after = await ctx.store.load(provider="telegram", account_id=ctx.account)
    assert (
        after.sync_cursor == state.sync_cursor and after.contact_versions == state.contact_versions
    )


async def test_telegram_absence_finalization_failure_and_restart(
    deletion_context, monkeypatch, caplog
):
    from butlers.modules.contacts.sync import ContactsSyncEngine

    ctx = deletion_context
    await ctx.engine().sync(account_id=ctx.account, mode="full")
    original = list(ctx.provider.contacts)
    initial = await ctx.store.load(provider="telegram", account_id=ctx.account)
    ctx.provider.contacts, ctx.provider.cursor = original[1:], "removed"
    saved = ctx.store.save
    caplog.set_level("INFO")
    for fault in ["state_save", "disconnect_before_commit"]:

        async def failing_save(*args, executor=None, fault=fault, **kwargs):
            await saved(*args, executor=executor, **kwargs)
            if executor is not None:
                if fault == "state_save":
                    raise RuntimeError("synthetic state save failure")
                executor.terminate()

        monkeypatch.setattr(ctx.store, "save", failing_save)
        with pytest.raises((RuntimeError, asyncpg.InterfaceError)):
            await ctx.engine().sync(account_id=ctx.account, mode="full")
        assert all(r["deleted_at"] is None for r in (await _deletion_rows(ctx)).values())
        after = await ctx.store.load(provider="telegram", account_id=ctx.account)
        assert (
            after.sync_cursor == initial.sync_cursor
            and after.contact_versions == initial.contact_versions
        )
        assert not any(r.msg == "contact_sync_deleted_source" for r in caplog.records)
    monkeypatch.setattr(ctx.store, "save", saved)
    # First positive contact persists, later local application fails, but no absence/cache advancement.
    ctx.provider.contacts = [c.model_copy(update={"etag": "changed"}) for c in original[1:]]
    calls = 0

    async def partial_apply(contact):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic local application failure")
        await ctx.backfill(contact)

    before_seen = await ctx.host.fetchval(
        "SELECT last_seen_at FROM contacts_source_links WHERE provider='telegram' AND account_id=$1 "
        "AND external_contact_id='telegram:11'",
        ctx.account,
    )
    with pytest.raises(RuntimeError):
        await ContactsSyncEngine(
            provider=ctx.provider,
            state_store=ctx.store,
            apply_contact=partial_apply,
            batch_finalizer=ctx.backfill,
        ).sync(account_id=ctx.account, mode="full")
    assert (
        await ctx.host.fetchval(
            "SELECT last_seen_at FROM contacts_source_links WHERE provider='telegram' AND account_id=$1 "
            "AND external_contact_id='telegram:11'",
            ctx.account,
        )
        > before_seen
    )
    assert all(r["deleted_at"] is None for r in (await _deletion_rows(ctx)).values())
    assert (
        await ctx.store.load(provider="telegram", account_id=ctx.account)
    ).contact_versions == initial.contact_versions
    assert (await ctx.engine().sync(account_id=ctx.account, mode="full")).deleted_contacts == 1


async def test_telegram_absence_persistent_fencing_and_isolation(deletion_context):
    from butlers.modules.contacts.sync import ContactsSyncError

    ctx = deletion_context
    await ctx.engine().sync(account_id=ctx.account, mode="full")
    contacts = list(ctx.provider.contacts)
    for competing_state_commit in [False, True]:
        ctx.provider.contacts = contacts
        await ctx.engine().sync(account_id=ctx.account, mode="full")
        slow = ctx.provider_type()
        slow.contacts, slow.cursor = contacts[1:], "slow"
        entered, release = asyncio.Event(), asyncio.Event()
        slow.pause = (entered, release)
        task = asyncio.create_task(ctx.engine(p=slow).sync(account_id=ctx.account, mode="full"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if competing_state_commit:
                await ctx.engine().sync(account_id=ctx.account, mode="full")
            else:
                # Real positive backfill changes the absent candidate's anchor.
                await ctx.backfill(contacts[0])
            competing_state = await ctx.store.load(provider="telegram", account_id=ctx.account)
            release.set()
            with pytest.raises(ContactsSyncError):
                await asyncio.wait_for(task, 10)
            retained_state = await ctx.store.load(provider="telegram", account_id=ctx.account)
            if competing_state_commit:
                assert retained_state == competing_state
            else:
                assert retained_state.model_dump(
                    exclude={"last_error"}
                ) == competing_state.model_dump(exclude={"last_error"})
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
        assert all(r["deleted_at"] is None for r in (await _deletion_rows(ctx)).values())
    # A separately configured account commits while the first account's fetch is paused.
    other = ctx.account + "-other"
    from butlers.modules.contacts.backfill import ContactBackfillEngine
    from butlers.modules.contacts.sync import ContactsSyncEngine

    other_backfill = ContactBackfillEngine(
        ctx.host, provider="telegram", account_id=other, owner=ctx.owner
    )
    slow = ctx.provider_type()
    slow.contacts = contacts
    slow.pause = (asyncio.Event(), asyncio.Event())
    task = asyncio.create_task(ctx.engine(p=slow).sync(account_id=ctx.account, mode="full"))
    independent_provider = ctx.provider_type()
    independent_provider.contacts, independent_provider.cursor = [contacts[0]], "other"
    independent = ContactsSyncEngine(
        provider=independent_provider,
        state_store=ctx.store,
        apply_contact=other_backfill,
        batch_finalizer=other_backfill,
    )
    try:
        await asyncio.wait_for(slow.pause[0].wait(), 5)
        await asyncio.wait_for(independent.sync(account_id=other, mode="incremental"), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        slow.pause[1].set()
        if not task.done():
            task.cancel()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)
    assert (await ctx.store.load(provider="telegram", account_id=other)).last_success_at is not None
    assert all(r["deleted_at"] is None for r in (await _deletion_rows(ctx)).values())
