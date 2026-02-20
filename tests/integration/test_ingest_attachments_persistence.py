"""Integration tests for attachments persistence in message_inbox table."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh Switchboard database and return a pool."""
    from butlers.db import Database
    from butlers.migrations import run_migrations

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    db_url = f"postgresql://{db.user}:{db.password}@{db.host}:{db.port}/{db.db_name}"
    await run_migrations(db_url, chain="core")
    await run_migrations(db_url, chain="switchboard")

    yield p

    await p.close()
    await db.close()


def _build_ingest_envelope(
    *,
    text: str = "Test message",
    attachments: list[dict] | None = None,
) -> dict:
    """Build a minimal IngestEnvelopeV1 dict for testing."""
    envelope = {
        "schema_version": "ingest.v1",
        "source": {
            "channel": "telegram",
            "provider": "telegram",
            "endpoint_identity": "bot_test",
        },
        "event": {
            "external_event_id": f"event-{uuid.uuid4()}",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "sender": {
            "identity": "user123",
        },
        "payload": {
            "raw": {"text": text},
            "normalized_text": text,
        },
    }

    if attachments is not None:
        envelope["payload"]["attachments"] = attachments

    return envelope


@pytest.mark.integration
async def test_ingest_persists_null_for_missing_attachments(pool):
    """When attachments field is missing, NULL is persisted to message_inbox."""
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    envelope = _build_ingest_envelope(text="Message without attachments")
    response = await ingest_v1(pool, envelope)

    assert response.status == "accepted"
    assert not response.duplicate

    # Verify database row has NULL attachments
    row = await pool.fetchrow(
        "SELECT attachments FROM message_inbox WHERE id = $1",
        response.request_id,
    )

    assert row is not None
    assert row["attachments"] is None


@pytest.mark.integration
async def test_ingest_persists_null_for_empty_attachments(pool):
    """When attachments is an empty list, NULL is persisted (not empty array)."""
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    envelope = _build_ingest_envelope(
        text="Message with empty attachments",
        attachments=[],
    )
    response = await ingest_v1(pool, envelope)

    assert response.status == "accepted"

    # Verify database row has NULL (not [])
    row = await pool.fetchrow(
        "SELECT attachments FROM message_inbox WHERE id = $1",
        response.request_id,
    )

    assert row is not None
    # Empty tuple becomes NULL, not []
    assert row["attachments"] is None


@pytest.mark.integration
async def test_ingest_persists_valid_attachments(pool):
    """When attachments are provided, they are persisted as JSONB array."""
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    envelope = _build_ingest_envelope(
        text="Check out this photo",
        attachments=[
            {
                "media_type": "image/jpeg",
                "storage_ref": "s3://bucket/photo123.jpg",
                "size_bytes": 1024000,
                "filename": "vacation.jpg",
                "width": 1920,
                "height": 1080,
            }
        ],
    )
    response = await ingest_v1(pool, envelope)

    assert response.status == "accepted"

    # Verify database row has attachments as JSONB
    row = await pool.fetchrow(
        "SELECT attachments FROM message_inbox WHERE id = $1",
        response.request_id,
    )

    assert row is not None
    assert row["attachments"] is not None

    # Parse JSONB back to Python
    attachments = (
        json.loads(row["attachments"])
        if isinstance(row["attachments"], str)
        else row["attachments"]
    )
    assert isinstance(attachments, list)
    assert len(attachments) == 1

    attachment = attachments[0]
    assert attachment["media_type"] == "image/jpeg"
    assert attachment["storage_ref"] == "s3://bucket/photo123.jpg"
    assert attachment["size_bytes"] == 1024000
    assert attachment["filename"] == "vacation.jpg"
    assert attachment["width"] == 1920
    assert attachment["height"] == 1080


@pytest.mark.integration
async def test_ingest_persists_multiple_attachments(pool):
    """Multiple attachments are persisted as JSONB array."""
    from butlers.tools.switchboard.ingestion.ingest import ingest_v1

    envelope = _build_ingest_envelope(
        text="Multiple files attached",
        attachments=[
            {
                "media_type": "image/png",
                "storage_ref": "s3://bucket/image1.png",
                "size_bytes": 500000,
                "filename": "screenshot.png",
                "width": 1280,
                "height": 720,
            },
            {
                "media_type": "application/pdf",
                "storage_ref": "s3://bucket/doc.pdf",
                "size_bytes": 2048000,
                "filename": "report.pdf",
            },
        ],
    )
    response = await ingest_v1(pool, envelope)

    assert response.status == "accepted"

    # Verify database row
    row = await pool.fetchrow(
        "SELECT attachments FROM message_inbox WHERE id = $1",
        response.request_id,
    )

    assert row is not None
    assert row["attachments"] is not None

    attachments = (
        json.loads(row["attachments"])
        if isinstance(row["attachments"], str)
        else row["attachments"]
    )
    assert isinstance(attachments, list)
    assert len(attachments) == 2

    # Verify first attachment
    assert attachments[0]["media_type"] == "image/png"
    assert attachments[0]["storage_ref"] == "s3://bucket/image1.png"
    assert attachments[0]["filename"] == "screenshot.png"

    # Verify second attachment
    assert attachments[1]["media_type"] == "application/pdf"
    assert attachments[1]["storage_ref"] == "s3://bucket/doc.pdf"
    assert attachments[1]["filename"] == "report.pdf"
    # PDF has no width/height
    assert attachments[1]["width"] is None
    assert attachments[1]["height"] is None
