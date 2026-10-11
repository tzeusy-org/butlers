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
