"""Genuine identity reads/locks under the runtime role; authored, NOT RUN locally.

The policy delegate is an explicit control. These cases do not establish a
verified catalog, standing rule, domain receipt or enabled production profile.
"""

import asyncio
from uuid import uuid4

import asyncpg
import pytest

from butlers.capability_broker.contracts import PolicyEvidence
from butlers.capability_broker.policy import bind_recipient_policy
from butlers.capability_broker.recipients import RecipientAuthorities
from butlers.core.capability_json import arguments_digest
from tests.capability_broker.test_recipient_authority import notify

pytest_plugins = ("tests.switchboard.goal_ledger_fixtures",)
pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class ControlledPolicy:
    async def policy(self, connection, principal, capability, args):
        assert connection.is_in_transaction()
        return PolicyEvidence("none")


async def test_runtime_identity_read_is_live_unique_and_locks_the_selected_owner(ledger):
    owner, runtime = ledger
    identifiers = [uuid4(), uuid4()]
    authority, capability = RecipientAuthorities(ControlledPolicy()), notify()
    assert (
        await owner.fetchval("SELECT count(*) FROM public.entities WHERE 'owner'=ANY(roles)") == 0
    )
    await owner.execute(
        "INSERT INTO public.entities(id,canonical_name,entity_type,roles) "
        "VALUES($1,'broker-recipient-control','person',ARRAY['owner'])",
        identifiers[0],
    )
    task = None
    try:
        async with runtime.acquire() as reader, owner.acquire() as writer:
            try:
                async with reader.transaction():
                    await reader.execute("SET LOCAL statement_timeout='3s'")
                    assert await reader.fetchval("SELECT current_user") == "butler_switchboard_rw"
                    evidence = await authority.policy(reader, None, capability, {})
                    assert evidence.recipient_binding.owner_id == identifiers[0]
                    assert (
                        bind_recipient_policy(
                            capability, {}, arguments_digest({}), evidence
                        ).recipient
                        == "owner"
                    )
                    reader_pid, writer_pid = (
                        await reader.fetchval("SELECT pg_backend_pid()"),
                        await writer.fetchval("SELECT pg_backend_pid()"),
                    )

                    async def retire():
                        async with writer.transaction():
                            await writer.execute("SET LOCAL statement_timeout='3s'")
                            return await writer.execute(
                                "UPDATE public.entities SET metadata=jsonb_build_object('deleted_at','control') WHERE id=$1",
                                identifiers[0],
                            )

                    task = asyncio.create_task(retire())
                    # Observe PostgreSQL's actual lock edge, not task-start/gather.
                    async with asyncio.timeout(2):
                        while not await owner.fetchval(
                            "SELECT $1=ANY(pg_blocking_pids($2))", reader_pid, writer_pid
                        ):
                            await asyncio.sleep(0.01)  # noqa: ASYNC110 - external DB lock observation
                    assert not task.done()
                assert await asyncio.wait_for(task, 3) == "UPDATE 1"
                # A new actual transaction observes retirement, never cached owner.
                async with reader.transaction():
                    evidence = await authority.policy(reader, None, capability, {})
                    assert evidence.recipient_binding.owner_id is None
                    assert (
                        bind_recipient_policy(
                            capability, {}, arguments_digest({}), evidence
                        ).recipient
                        == "non_owner"
                    )
            finally:
                # Settle while the owning writer acquisition is still held.
                if task is not None:
                    if not task.done():
                        task.cancel()
                    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 3)
        async with owner.acquire() as writer, writer.transaction():
            await writer.execute("SET LOCAL statement_timeout='3s'")
            await writer.execute(
                "UPDATE public.entities SET metadata='{}'::jsonb WHERE id=$1", identifiers[0]
            )
            # The migrated singleton is authoritative, including retired owners.
            # A savepoint contains the expected failure without aborting this TX.
            with pytest.raises(asyncpg.UniqueViolationError) as rejected:
                async with writer.transaction():
                    await writer.execute(
                        "INSERT INTO public.entities(id,canonical_name,entity_type,roles) "
                        "VALUES($1,'broker-recipient-control-replacement','person',ARRAY['owner'])",
                        identifiers[1],
                    )
            assert rejected.value.sqlstate == "23505"
            assert rejected.value.constraint_name == "ix_entities_owner_singleton"
            assert (
                await writer.fetchval("SELECT id FROM public.entities WHERE 'owner'=ANY(roles)")
                == identifiers[0]
            )
            assert not await writer.fetchval(
                "SELECT EXISTS(SELECT 1 FROM public.entities WHERE id=$1)", identifiers[1]
            )
        # Independent committed readback proves the failed insert changed nothing.
        async with owner.acquire() as observer:
            assert (
                await observer.fetchval("SELECT id FROM public.entities WHERE 'owner'=ANY(roles)")
                == identifiers[0]
            )
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM public.entities WHERE id=ANY($1::uuid[])", identifiers
                )
                == 1
            )
        # A real transfer revokes the former role before inserting its successor.
        async with owner.acquire() as writer, writer.transaction():
            await writer.execute("SET LOCAL statement_timeout='3s'")
            assert (
                await writer.execute(
                    "UPDATE public.entities SET roles=array_remove(roles,'owner') WHERE id=$1",
                    identifiers[0],
                )
                == "UPDATE 1"
            )
            await writer.execute(
                "INSERT INTO public.entities(id,canonical_name,entity_type,roles) "
                "VALUES($1,'broker-recipient-control-replacement','person',ARRAY['owner'])",
                identifiers[1],
            )
        async with runtime.acquire() as reader, reader.transaction():
            evidence = await authority.policy(reader, None, capability, {})
            assert evidence.recipient_binding.owner_id == identifiers[1]
            assert (
                bind_recipient_policy(capability, {}, arguments_digest({}), evidence).recipient
                == "owner"
            )
            former_owner = {"entity_id": str(identifiers[0])}
            evidence = await authority.policy(reader, None, capability, former_owner)
            assert evidence.recipient_binding.owner_id == identifiers[1]
            assert (
                bind_recipient_policy(
                    capability, former_owner, arguments_digest(former_owner), evidence
                ).recipient
                == "non_owner"
            )
        async with owner.acquire() as observer:
            assert (
                await observer.fetchval(
                    "SELECT count(*) FROM public.entities WHERE id=ANY($1::uuid[])", identifiers
                )
                == 2
            )
            assert (
                await observer.fetchval(
                    "SELECT 'owner'=ANY(roles) FROM public.entities WHERE id=$1", identifiers[0]
                )
                is False
            )
            assert (
                await observer.fetchval("SELECT id FROM public.entities WHERE 'owner'=ANY(roles)")
                == identifiers[1]
            )
    finally:
        await owner.execute("DELETE FROM public.entities WHERE id=ANY($1::uuid[])", identifiers)
