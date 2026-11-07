"""Entity metadata writers patch their own keys (bu-dhloi1.43).

Owner law walks ``metadata.merged_into`` to a merge's survivor, and ``deleted_at``
and ``unidentified`` decide who an entity is. A writer that reads the metadata and
writes the whole document back restores whatever the row held at its read, so a
write committing in between is lost. Each writer here runs against real Postgres
with that write committing between its read and its UPDATE.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from butlers.modules.contacts.backfill import ContactBackfillWriter
from butlers.modules.contacts.sync import CanonicalContact
from butlers.roster_imports import roster_module
from butlers.testing.db_manager import db_manager

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class _WriteAfterRead:
    """The real pool, with a write committing right after the writer's metadata read."""

    def __init__(self, pool: asyncpg.Pool, write: Callable[[UUID], Awaitable[Any]]):
        self._pool, self._write = pool, write
        self.wrote = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)

    async def fetchrow(self, query: str, *args: Any) -> Any:
        row = await self._pool.fetchrow(query, *args)
        read_by_id = query.startswith("SELECT") and query.endswith("WHERE id = $1")
        if not self.wrote and read_by_id and "metadata" in query:
            self.wrote = True
            await self._write(args[0])
        return row


async def _database(db) -> asyncpg.Pool:
    return await db("core", "relationship", "approvals")


def _contact() -> CanonicalContact:
    return CanonicalContact(
        external_id="people/c123",
        etag="etag-1",
        display_name=f"Zhi {uuid4().hex}",
        first_name="Zhi",
        last_name=uuid4().hex,
    )


async def _person(pool: asyncpg.Pool, metadata: dict[str, Any] | None = None) -> UUID:
    return await pool.fetchval(
        "INSERT INTO public.entities (canonical_name, metadata) VALUES ($1, $2) RETURNING id",
        f"Person {uuid4()}",
        metadata or {},
    )


async def _metadata(pool: asyncpg.Pool, entity: UUID) -> dict[str, Any]:
    return await pool.fetchval("SELECT metadata FROM public.entities WHERE id = $1", entity)


async def _merge(pool: asyncpg.Pool, source: UUID, target: UUID) -> None:
    """The real merge authority."""
    merge = roster_module("relationship", "tools/entity_merge").merge_entity_pair
    await merge(pool, source_entity_id=source, target_entity_id=target, target_schemas=())


def _writer(pool: Any) -> ContactBackfillWriter:
    return ContactBackfillWriter(pool, provider="google", account_id="default")


async def test_create_contact_keeps_a_merge_committed_after_its_read(db):
    pool = await _database(db)
    survivor = await _person(pool)

    async def merge(entity: UUID) -> None:
        await _merge(pool, entity, survivor)

    racing = _WriteAfterRead(pool, merge)
    entity = await _writer(racing).create_contact(_contact())
    assert racing.wrote
    metadata = await _metadata(pool, entity)
    assert metadata["merged_into"] == str(survivor)
    assert metadata["profile"]["first_name"] == "Zhi"


async def test_update_contact_keeps_a_merge_committed_after_its_read(db):
    pool = await _database(db)
    source, survivor = await _person(pool), await _person(pool)

    async def merge(entity: UUID) -> None:
        await _merge(pool, entity, survivor)

    racing = _WriteAfterRead(pool, merge)
    await _writer(racing).update_contact(source, _contact(), match_strategy="source_link")
    assert racing.wrote
    metadata = await _metadata(pool, source)
    assert metadata["merged_into"] == str(survivor)
    assert metadata["profile"]["first_name"] == "Zhi"


@pytest.mark.parametrize("key", ["unidentified", "duplicate_candidate"])
async def test_update_contact_does_not_restore_a_key_removed_after_its_read(db, key):
    """A promote removes both flags; a sync that read them must not bring them back."""
    pool = await _database(db)
    entity = await _person(pool, {key: True})

    async def promote(target: UUID) -> None:
        await pool.execute(
            "UPDATE public.entities SET metadata = metadata - $2::text WHERE id = $1", target, key
        )

    racing = _WriteAfterRead(pool, promote)
    await _writer(racing).update_contact(entity, _contact(), match_strategy="source_link")
    assert racing.wrote
    metadata = await _metadata(pool, entity)
    assert key not in metadata
    assert metadata["profile"]["first_name"] == "Zhi"


async def test_create_contact_does_not_restore_a_flag_removed_after_its_read(db):
    """On a recovered entity: only the ``duplicate_candidate`` argument sets the flag."""
    pool = await _database(db)
    contact = _contact()
    entity = await _writer(pool).create_contact(contact)
    await pool.execute(
        "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
        entity,
        {"duplicate_candidate": True},
    )

    async def promote(target: UUID) -> None:
        await pool.execute(
            "UPDATE public.entities SET metadata = metadata - 'duplicate_candidate' WHERE id = $1",
            target,
        )

    racing = _WriteAfterRead(pool, promote)
    assert await _writer(racing).create_contact(contact) == entity
    assert racing.wrote
    assert "duplicate_candidate" not in await _metadata(pool, entity)


async def test_a_duplicate_candidate_is_still_flagged(db):
    pool = await _database(db)
    async with pool.acquire() as conn, conn.transaction():
        entity = await _writer(pool).create_contact(
            _contact(), executor=conn, duplicate_candidate=True
        )
    metadata = await _metadata(pool, entity)
    assert metadata["duplicate_candidate"] is True
    assert metadata["profile"]["first_name"] == "Zhi"


async def _promote(pool: asyncpg.Pool, entity: UUID) -> Any:
    router = roster_module("relationship", "api/router")
    body = router.PromoteEntityRequest(canonical_name=f"Promoted {uuid4()}", entity_id=entity)
    return await router.promote_entity(body, db=db_manager({"relationship": pool}))


async def _wait_for_row_lock_wait(pool: asyncpg.Pool) -> None:
    for _ in range(1000):
        if await pool.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
            "AND datname = current_database()"
        ):
            return
        await asyncio.sleep(0.01)
    pytest.fail("the promote never waited on the row lock")


@pytest.mark.parametrize(
    ("key", "value"),
    [("merged_into", "survivor"), ("deleted_at", "2026-11-06T00:00:00Z")],
)
async def test_a_promote_keeps_a_write_it_waited_on(db, key, value):
    """Its batch lock is ``FOR KEY SHARE``, which lets a plain metadata UPDATE commit past it.

    The real merge takes ``FOR UPDATE`` and so cannot interleave; ``delete_entity``
    is a plain UPDATE and can. Both keys are written the plain way here.
    """
    pool = await _database(db)
    await pool.execute(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner'])"
    )
    entity = await _person(
        pool, {"unidentified": True, "duplicate_candidate": True, "note": "kept"}
    )
    written = str(await _person(pool)) if value == "survivor" else value
    async with pool.acquire() as writing, writing.transaction():
        await writing.execute(
            "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
            entity,
            {key: written},
        )
        promote = asyncio.create_task(_promote(pool, entity))
        await _wait_for_row_lock_wait(pool)
    await promote
    assert await _metadata(pool, entity) == {"note": "kept", key: written}


async def test_update_contact_keeps_a_profile_key_written_after_its_read(db):
    """``contact_merge``'s reconcile and an owner edit write ``profile`` keys the sync never reads."""
    pool = await _database(db)
    entity = await _person(pool)

    async def write_company(target: UUID) -> None:
        await pool.execute(
            "UPDATE public.entities SET metadata = metadata || $2::jsonb WHERE id = $1",
            target,
            {"profile": {"company": "Acme"}},
        )

    racing = _WriteAfterRead(pool, write_company)
    results = await _writer(racing).update_contact(entity, _contact(), match_strategy="source_link")
    assert racing.wrote
    assert results["first_name"] == "updated"
    profile = (await _metadata(pool, entity))["profile"]
    assert profile["company"] == "Acme"
    assert profile["first_name"] == "Zhi"


async def test_a_promote_without_roles_keeps_roles_written_after_its_read(db):
    """With no roles in the request, the UPDATE leaves them as the row holds them then."""
    pool = await _database(db)
    await pool.execute(
        "INSERT INTO public.entities (canonical_name, entity_type, roles) "
        "VALUES ('Owner', 'person', ARRAY['owner'])"
    )
    entity = await _person(pool, {"unidentified": True})
    async with pool.acquire() as writing, writing.transaction():
        await writing.execute(
            "UPDATE public.entities SET roles = ARRAY['family'] WHERE id = $1", entity
        )
        promote = asyncio.create_task(_promote(pool, entity))
        await _wait_for_row_lock_wait(pool)
    await promote
    assert await pool.fetchval("SELECT roles FROM public.entities WHERE id = $1", entity) == [
        "family"
    ]
