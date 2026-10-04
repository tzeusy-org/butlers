"""Real local capture transaction/source proof: REQ-general-capture-001/-002/-005."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from butlers.tools.general import capture_service as implementation
from butlers.tools.general.vocabulary import collection_declare
from tests.integration import test_general_capture_ledger as capture_fixtures

capture_db_url = capture_fixtures.capture_db_url
capture_pool = capture_fixtures.capture_pool
service = capture_fixtures.service
authority = capture_fixtures.authority


pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


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
    collection_id = uuid.UUID(verified["content"]["collection_id"])
    await capture_pool.execute(
        "UPDATE collections SET custody_private=true WHERE id=$1", collection_id
    )
    try:
        assert await svc.verify(answers[0].operation_id) is None
    finally:
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
    monkeypatch.setattr(implementation, "item_create_versioned", original)
    await svc.mark_unknown(op["id"])
    restarted = implementation.CaptureService(capture_pool, svc.epoch_path)
    for _ in range(2):
        result = await restarted.process_one(held.capture_id, owner="general", kind="fact")
        assert result.category == "target_outcome_unknown" and result.operation_id == op["id"]
    assert await svc.verify(op["id"]) is None
    assert await capture_pool.fetchval("SELECT count(*) FROM collection_items") == before_items
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
