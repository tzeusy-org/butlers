"""Real local capture transaction/source proof: REQ-general-capture-001/-002/-005."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from butlers.testing.migration import create_migrated_test_db, migration_db_name
from butlers.tools.general import capture_service as implementation
from butlers.tools.general.items import item_delete, item_get, item_update
from butlers.tools.general.vocabulary import collection_declare
from tests.integration import test_general_capture_ledger as capture_fixtures

capture_pool = capture_fixtures.capture_pool
service = capture_fixtures.service
authority = capture_fixtures.authority


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def capture_db_url(postgres_container):
    # Each service case needs a fresh namespace: irreversible private custody
    # makes all names unavailable while legacy global uniqueness remains.
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "general"],
        schemas={"general": "general"},
        revisions={"core": "core_259"},
    )


async def test_local_operation_race_and_live_source_verification(service, capture_pool):
    svc, epoch = service
    await collection_declare(capture_pool, "notes", "Ordinary notes")
    held = await svc.admit(authority(epoch), "Synthetic capture")
    answers = await asyncio.gather(
        *[svc.process_one(held.capture_id, owner="general", kind="note") for _ in range(2)]
    )
    assert answers[0] == answers[1] and answers[0].disposition == "routed"
    verified = await svc.verify(answers[0].operation_id)
    assert verified["content"]["data"]["text"] == "Synthetic capture"
    assert (
        await capture_pool.fetchval(
            "SELECT count(*) FROM public.capture_operations WHERE capture_id=$1", held.capture_id
        )
        == 1
    )
    item_id = uuid.UUID(verified["item_id"])
    assert (
        await capture_pool.fetchval(
            "SELECT count(*) FROM source_versions WHERE item_id=$1", item_id
        )
        == 1
    )
    async with capture_pool.acquire() as conn:
        for sql in [
            "UPDATE public.captures SET category='pending' WHERE id=$1",
            "DELETE FROM public.captures WHERE id=$1",
        ]:
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(sql, held.capture_id)
    # Direct legacy mutation leaves source_versions.current true, so only live
    # digest recomputation can refuse it. Restore value then protect the parent.
    await capture_pool.execute(
        "UPDATE collection_items SET data=$2 WHERE id=$1", item_id, {"text": "Legacy mutation"}
    )
    assert await svc.verify(answers[0].operation_id) is None
    await capture_pool.execute(
        "UPDATE collection_items SET data=$2 WHERE id=$1", item_id, verified["content"]["data"]
    )
    assert await svc.verify(answers[0].operation_id) is not None
    assert await svc.verify(uuid.uuid4()) is None
    unknown = await svc.admit(authority(epoch), "Synthetic capture")
    unknown = await svc.claim(unknown.capture_id, owner="general", kind="note")
    assert await svc.verify(unknown.operation_id) is None

    # Each negative starts from a genuine service receipt that verifies. Newer
    # versions and deletion must retire capture authority as well as source reads.
    for mutation in ("new_version", "delete"):
        admitted = await svc.admit(authority(epoch), "Synthetic capture")
        routed = await svc.process_one(admitted.capture_id, owner="general", kind="note")
        positive = await svc.verify(routed.operation_id)
        assert positive is not None
        target_id = uuid.UUID(positive["item_id"])
        if mutation == "new_version":
            await item_update(capture_pool, target_id, {"text": "New synthetic version"})
            assert (
                await capture_pool.fetchval(
                    "SELECT max(version) FROM source_versions WHERE item_id=$1", target_id
                )
                == 2
            )
        else:
            await item_delete(capture_pool, target_id)
            assert (
                await capture_pool.fetchval(
                    "SELECT count(*) FROM collection_items WHERE id=$1", target_id
                )
                == 0
            )
        assert await svc.verify(routed.operation_id) is None

    # Plant a stale generation while the live target is still ordinary and its
    # projection is unchanged. A legacy delete/reinsert can reuse UUIDs without
    # recording a source version; it must not revive the older receipt. This
    # uses migrated tables and INSERTs, never disables classification guards or
    # clears a private parent.
    await collection_declare(capture_pool, "preferences", "Ordinary preferences")
    stale = await svc.admit(authority(epoch), "Synthetic capture")
    stale = await svc.process_one(stale.capture_id, owner="general", kind="preference")
    stale_positive = await svc.verify(stale.operation_id)
    assert stale_positive is not None
    stale_parent = uuid.UUID(stale_positive["content"]["collection_id"])
    async with capture_pool.acquire() as conn, conn.transaction():
        await conn.execute("DELETE FROM collections WHERE id=$1", stale_parent)
        await conn.execute(
            "INSERT INTO collections(id,name,eligibility_generation) VALUES ($1,$2,$3)",
            stale_parent,
            "preferences",
            stale_positive["generation"] + 1,
        )
        await conn.execute(
            "INSERT INTO collection_items(id,collection_id,data,tags) VALUES ($1,$2,$3,$4)",
            stale.operation_id,
            stale_parent,
            stale_positive["content"]["data"],
            stale_positive["content"]["tags"],
        )
    await collection_declare(capture_pool, "preferences", "Ordinary preferences")
    assert (await item_get(capture_pool, stale.operation_id))["data"] == stale_positive["content"][
        "data"
    ]
    assert (
        await capture_pool.fetchval(
            "SELECT max(version) FROM source_versions WHERE item_id=$1", stale.operation_id
        )
        == 1
    )
    assert await svc.verify(stale.operation_id) is None

    collection_id = uuid.UUID(verified["content"]["collection_id"])
    await capture_pool.execute(
        "UPDATE collections SET custody_private=true WHERE id=$1", collection_id
    )
    assert (
        await capture_pool.fetchval(
            "SELECT eligibility_generation FROM collections WHERE id=$1", collection_id
        )
        == verified["generation"] + 1
    )
    assert await svc.verify(answers[0].operation_id) is None
    # This parent stays private. The production guard is irreversible, and the
    # other consolidated service gate uses its own migrated database.
    with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
        await capture_pool.execute(
            "UPDATE collections SET custody_private=false WHERE id=$1", collection_id
        )


async def test_rollback_unknown_lineage_and_unsupported_owner(service, capture_pool, monkeypatch):
    svc, epoch = service
    await collection_declare(capture_pool, "facts", "Ordinary facts")
    held = await svc.admit(authority(epoch), "Synthetic capture")
    before_items = await capture_pool.fetchval("SELECT count(*) FROM collection_items")
    before_versions = await capture_pool.fetchval("SELECT count(*) FROM source_versions")
    original = implementation.item_create_versioned

    async def fail_after_write(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("synthetic post-write interruption")

    monkeypatch.setattr(implementation, "item_create_versioned", fail_after_write)
    with pytest.raises(RuntimeError):
        await svc.process_one(held.capture_id, owner="general", kind="fact")
    # Separate acquisition proves both the target and version rolled back.
    assert await capture_pool.fetchval("SELECT count(*) FROM collection_items") == before_items
    assert await capture_pool.fetchval("SELECT count(*) FROM source_versions") == before_versions
    op = await capture_pool.fetchrow(
        "SELECT * FROM public.capture_operations WHERE capture_id=$1", held.capture_id
    )
    assert op["stage"] == "claimed" and op["receipt"] is None
    # Exercise the production handler after a real write in the outer effect
    # transaction, including task cancellation. No manual mark_unknown installs
    # the tested result. Separate acquisitions read its durable commit.
    for error_type in (
        ConnectionError,
        OSError,
        asyncpg.PostgresConnectionError,
        asyncio.CancelledError,
    ):
        interrupted = await svc.admit(authority(epoch), "Synthetic capture")

        async def interrupt_after_write(*args, **kwargs):
            await original(*args, **kwargs)
            if error_type is asyncio.CancelledError:
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            raise error_type("synthetic post-write interruption")

        monkeypatch.setattr(implementation, "item_create_versioned", interrupt_after_write)
        with pytest.raises(error_type):
            await asyncio.create_task(
                svc.process_one(interrupted.capture_id, owner="general", kind="fact")
            )
        recorded = await capture_pool.fetchrow(
            "SELECT disposition,category,operation_id FROM public.captures WHERE id=$1",
            interrupted.capture_id,
        )
        operation_id = recorded["operation_id"]
        assert recorded["disposition"] == "held"
        assert recorded["category"] == "target_outcome_unknown" and operation_id is not None
        recorded_op = await capture_pool.fetchrow(
            "SELECT stage,receipt FROM public.capture_operations WHERE id=$1", operation_id
        )
        assert recorded_op["stage"] == "in_doubt" and recorded_op["receipt"] is None

        async def never_resend(*args, **kwargs):
            raise AssertionError("unknown lineage must not resend a target")

        monkeypatch.setattr(implementation, "item_create_versioned", never_resend)
        restarted = implementation.CaptureService(capture_pool, svc.epoch_path)
        for _ in range(2):
            result = await restarted.process_one(
                interrupted.capture_id, owner="general", kind="fact"
            )
            assert result.category == "target_outcome_unknown"
            assert result.operation_id == operation_id
        assert await svc.verify(operation_id) is None
        assert (
            await capture_pool.fetchval(
                "SELECT count(*) FROM public.capture_operations WHERE capture_id=$1",
                interrupted.capture_id,
            )
            == 1
        )
        assert await capture_pool.fetchval("SELECT count(*) FROM collection_items") == before_items
        assert (
            await capture_pool.fetchval("SELECT count(*) FROM source_versions") == before_versions
        )
    monkeypatch.setattr(implementation, "item_create_versioned", original)
    # A separately admitted positive control proves the writer is wired.
    positive = await svc.admit(authority(epoch), "Synthetic capture")
    assert (
        await svc.process_one(positive.capture_id, owner="general", kind="fact")
    ).disposition == "routed"
    from butlers.core.capture import VerifiedAuthority, canonical_intake

    refs = [{"source_id": str(uuid.uuid4()), "attachment_id": str(uuid.uuid4())}]
    ref_auth = authority(epoch)
    ref_auth = VerifiedAuthority(
        ref_auth.principal_id,
        ref_auth.source_occurrence,
        ref_auth.source_occurred_at,
        epoch.generation,
        canonical_intake("Synthetic capture", refs)[1],
    )
    ref_capture = await svc.admit(ref_auth, "Synthetic capture", references=refs)
    ref_result = await svc.process_one(ref_capture.capture_id, owner="general", kind="fact")
    assert ref_result.disposition == "held" and ref_result.category == "source_unavailable"
    assert await svc.verify(ref_result.operation_id) is None
    unsupported = await svc.admit(authority(epoch), "Synthetic capture")

    async def forbidden(*args, **kwargs):
        raise AssertionError("unsupported ownership must never invoke a writer")

    monkeypatch.setattr(implementation, "item_create_versioned", forbidden)
    refused = await svc.process_one(unsupported.capture_id, owner="finance", kind="fact")
    assert refused.disposition == "refused"
    assert await svc.process_one(unsupported.capture_id, owner="general", kind="fact") == refused
