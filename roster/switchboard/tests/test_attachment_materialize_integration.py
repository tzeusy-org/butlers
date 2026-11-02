"""Lazy Gmail attachment refs are fetched into blob storage on request (bu-jv1moa.48).

Switchboard's ``attachment_materialize`` marks a ``switchboard.attachment_refs``
row requested and waits; the Gmail connector that owns the row claims it,
downloads the bytes and records the blob ref. Both sides run against a real
migrated database: the Switchboard pool on its production search path and the
connector pool with none, as the connector's cursor pool connects.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime
from butlers.tools.attachment_materialize import attachment_materialize

pytestmark = pytest.mark.integration
session_loop = pytest.mark.asyncio(loop_scope="session")

_OWNER = "gmail:user:owner@example.com"
_OTHER = "gmail:user:other@example.com"


class _Blobs:
    def __init__(self) -> None:
        self.puts: list[tuple[bytes, str, str | None]] = []
        self.deleted: list[str] = []

    async def put(self, data: bytes, *, content_type: str, filename: str | None = None) -> str:
        self.puts.append((data, content_type, filename))
        return f"s3://butlers/connectors/2026/11/02/{len(self.puts)}.pdf"

    async def get(self, storage_ref: str) -> bytes:
        raise NotImplementedError

    async def delete(self, storage_ref: str) -> None:
        self.deleted.append(storage_ref)

    async def exists(self, storage_ref: str) -> bool:
        raise NotImplementedError


def _runtime(pool: Any, blobs: _Blobs, identity: str, download: AsyncMock) -> GmailConnectorRuntime:
    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_endpoint_identity=identity,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
    )
    runtime = GmailConnectorRuntime(config, blob_store=blobs, db_pool=pool)
    runtime._download_gmail_attachment = download  # type: ignore[method-assign]
    return runtime


async def _pools(db: Any, fresh_db: Any) -> tuple[Any, Any]:
    url = fresh_db("core", "switchboard", schemas={"switchboard": "switchboard"})
    return await db.open(url, schema="switchboard"), await db.open(url)


async def _materialize_while_connector_runs(
    switchboard: Any, runtime: GmailConnectorRuntime, message_id: str, attachment_id: str
) -> dict[str, Any]:
    call = asyncio.create_task(
        attachment_materialize(switchboard, message_id, attachment_id, wait_s=10, poll_s=0.05)
    )
    while not call.done():
        await runtime.materialize_requested_once()
        await asyncio.sleep(0.05)
    return call.result()


@session_loop
async def test_a_lazy_ref_is_fetched_once_and_survives_ingest_replay(db, fresh_db) -> None:
    switchboard, connector = await _pools(db, fresh_db)
    blobs = _Blobs()
    download = AsyncMock(return_value=b"%PDF-1.7 bill")
    owner = _runtime(connector, blobs, _OWNER, download)
    await owner._write_attachment_ref("m1", "a1", "bill.pdf", "application/pdf", 1234)

    first = await _materialize_while_connector_runs(switchboard, owner, "m1", "a1")
    assert first == {
        "status": "ready",
        "storage_ref": "s3://butlers/connectors/2026/11/02/1.pdf",
        "media_type": "application/pdf",
        "filename": "bill.pdf",
        "size_bytes": 1234,
        "content_trust": "untrusted",
    }
    assert blobs.puts == [(b"%PDF-1.7 bill", "application/pdf", "bill.pdf")]

    # A replayed ingest rewrites the lazy ref; it must not forget the blob.
    await owner._write_attachment_ref("m1", "a1", "bill.pdf", "application/pdf", 1234)
    again = await attachment_materialize(switchboard, "m1", "a1", wait_s=0, poll_s=0.05)
    assert again == first
    assert download.await_count == 1
    assert await owner.materialize_requested_once() == 0


@session_loop
async def test_materialize_refuses_unknown_and_failed_refs_and_waits_for_the_owner(
    db, fresh_db
) -> None:
    switchboard, connector = await _pools(db, fresh_db)
    other = _runtime(connector, _Blobs(), _OTHER, AsyncMock(side_effect=RuntimeError("gone")))
    owner = _runtime(connector, _Blobs(), _OWNER, AsyncMock(return_value=b"%PDF"))
    await other._write_attachment_ref("m2", "a2", None, "application/pdf", 10)

    assert await attachment_materialize(switchboard, "nope", "nope", wait_s=0, poll_s=0.05) == {
        "status": "refused",
        "reason": "attachment_not_found",
        "message_id": "nope",
        "attachment_id": "nope",
    }

    # Only the account that ingested the message may fetch it.
    pending = await attachment_materialize(switchboard, "m2", "a2", wait_s=0.2, poll_s=0.05)
    assert pending["status"] == "pending"
    assert await owner.materialize_requested_once() == 0

    failed = await _materialize_while_connector_runs(switchboard, other, "m2", "a2")
    assert failed["status"] == "refused"
    assert failed["reason"] == "fetch_failed"
    # Asking again retries rather than replaying the old failure.
    retried = await attachment_materialize(switchboard, "m2", "a2", wait_s=0.2, poll_s=0.05)
    assert retried["status"] == "pending"
    assert await other.materialize_requested_once() == 1


@session_loop
async def test_a_failed_ref_write_is_a_fetch_error_and_drops_the_blob(db, fresh_db) -> None:
    switchboard, connector = await _pools(db, fresh_db)
    blobs = _Blobs()
    owner = _runtime(connector, blobs, _OWNER, AsyncMock(return_value=b"%PDF"))
    owner._record_fetched_blob = AsyncMock(  # type: ignore[method-assign]
        side_effect=ConnectionError("db went away")
    )
    await owner._write_attachment_ref("m3", "a3", "x.pdf", "application/pdf", 4)

    failed = await _materialize_while_connector_runs(switchboard, owner, "m3", "a3")
    assert failed["reason"] == "fetch_failed"
    # The stored bytes are deleted, not orphaned; a retry downloads again.
    assert blobs.deleted == ["s3://butlers/connectors/2026/11/02/1.pdf"]


@session_loop
async def test_an_interrupted_batch_releases_its_unfinished_claims(db, fresh_db) -> None:
    switchboard, connector = await _pools(db, fresh_db)
    stopping = _runtime(connector, _Blobs(), _OWNER, AsyncMock(side_effect=asyncio.CancelledError))
    for attachment_id in ("a4", "a5"):
        await stopping._write_attachment_ref("m4", attachment_id, None, "application/pdf", 4)
        pending = await attachment_materialize(
            switchboard, "m4", attachment_id, wait_s=0, poll_s=0.05
        )
        assert pending["status"] == "pending"

    with pytest.raises(asyncio.CancelledError):
        await stopping.materialize_requested_once()

    # Both claims are released at once, not left for the stale window.
    restarted = _runtime(connector, _Blobs(), _OWNER, AsyncMock(return_value=b"%PDF"))
    assert await restarted.materialize_requested_once() == 2
    for attachment_id in ("a4", "a5"):
        ready = await attachment_materialize(
            switchboard, "m4", attachment_id, wait_s=0, poll_s=0.05
        )
        assert ready["status"] == "ready"


@session_loop
async def test_a_blob_the_row_may_name_is_kept_when_recording_fails_late(db, fresh_db) -> None:
    switchboard, connector = await _pools(db, fresh_db)
    blobs = _Blobs()
    owner = _runtime(connector, blobs, _OWNER, AsyncMock(return_value=b"%PDF"))
    record = owner._record_fetched_blob

    async def commit_then_lose_the_reply(*args: Any) -> None:
        await record(*args)
        raise ConnectionError("reply lost after commit")

    owner._record_fetched_blob = commit_then_lose_the_reply  # type: ignore[method-assign]
    await owner._write_attachment_ref("m6", "a6", "x.pdf", "application/pdf", 4)

    ready = await _materialize_while_connector_runs(switchboard, owner, "m6", "a6")
    assert ready["status"] == "ready"
    assert ready["storage_ref"] == "s3://butlers/connectors/2026/11/02/1.pdf"
    assert blobs.deleted == []


@session_loop
async def test_a_runtime_never_fails_or_releases_a_claim_taken_over(db, fresh_db) -> None:
    switchboard, connector = await _pools(db, fresh_db)
    taken = datetime(2030, 1, 1, tzinfo=UTC)

    def taken_over_then(error: BaseException) -> AsyncMock:
        async def download(message_id: str, attachment_id: str) -> bytes:
            # Another runtime reclaims the claim while this one downloads.
            await switchboard.execute(
                "UPDATE attachment_refs SET fetch_claimed_at = $3"
                " WHERE message_id = $1 AND attachment_id = $2",
                message_id,
                attachment_id,
                taken,
            )
            raise error

        return AsyncMock(side_effect=download)

    for attachment_id, error in (("a7", RuntimeError("gone")), ("a8", asyncio.CancelledError())):
        slow = _runtime(connector, _Blobs(), _OWNER, taken_over_then(error))
        await slow._write_attachment_ref("m7", attachment_id, None, "application/pdf", 4)
        await attachment_materialize(switchboard, "m7", attachment_id, wait_s=0, poll_s=0.05)
        try:
            await slow.materialize_requested_once()
        except asyncio.CancelledError:
            pass
        row = await switchboard.fetchrow(
            "SELECT fetch_error, fetch_claimed_at = $3 AS still_taken"
            " FROM attachment_refs WHERE message_id = $1 AND attachment_id = $2",
            "m7",
            attachment_id,
            taken,
        )
        assert row["fetch_error"] is None
        assert row["still_taken"] is True
