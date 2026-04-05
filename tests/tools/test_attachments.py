"""Tests for the get_attachment shared tool."""

import base64

import boto3
import pytest
from moto.server import ThreadedMotoServer

from butlers.storage import BlobNotFoundError, S3BlobStore
from butlers.tools.attachments import MAX_ATTACHMENT_SIZE_BYTES, get_attachment

TEST_BUCKET = "test-butlers-blobs"
TEST_BUTLER = "testbutler"


@pytest.fixture(scope="module")
def moto_s3_server():
    """Start a moto HTTP server for S3."""
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    endpoint = f"http://localhost:{server._server.server_address[1]}"
    client = boto3.client(
        "s3", endpoint_url=endpoint,
        aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1",
    )
    client.create_bucket(Bucket=TEST_BUCKET)
    yield endpoint
    server.stop()


@pytest.fixture
def blob_store(moto_s3_server):
    return S3BlobStore(
        bucket=TEST_BUCKET, butler_name=TEST_BUTLER, endpoint_url=moto_s3_server,
        access_key_id="testing", secret_access_key="testing", region="us-east-1",
    )


async def test_retrieve_existing_attachment(blob_store):
    """Successfully retrieve an existing attachment with correct fields."""
    data = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    storage_ref = await blob_store.put(data, content_type="image/png", filename="test.png")
    result = await get_attachment(blob_store, storage_ref)

    assert result["storage_ref"] == storage_ref
    assert result["media_type"] == "image/png"
    assert result["size_bytes"] == len(data)
    assert base64.b64decode(result["data_base64"]) == data


async def test_size_limit_enforcement(blob_store):
    """Blobs exceeding limit are rejected; blobs exactly at limit are allowed."""
    large_data = b"x" * (MAX_ATTACHMENT_SIZE_BYTES + 1)
    storage_ref = await blob_store.put(large_data, content_type="application/octet-stream")
    with pytest.raises(ValueError, match="size limit"):
        await get_attachment(blob_store, storage_ref)

    at_limit_data = b"x" * MAX_ATTACHMENT_SIZE_BYTES
    storage_ref2 = await blob_store.put(at_limit_data, content_type="application/octet-stream")
    result = await get_attachment(blob_store, storage_ref2)
    assert result["size_bytes"] == MAX_ATTACHMENT_SIZE_BYTES


async def test_blob_not_found_raises(blob_store):
    """Missing blob raises BlobNotFoundError."""
    with pytest.raises(BlobNotFoundError):
        await get_attachment(blob_store, f"s3://{TEST_BUCKET}/{TEST_BUTLER}/2026/01/01/nope.jpg")


async def test_invalid_storage_ref_raises(blob_store):
    """Invalid storage_ref format raises ValueError."""
    with pytest.raises(ValueError, match="Invalid storage_ref format"):
        await get_attachment(blob_store, "not-a-valid-ref-format")


async def test_binary_data_integrity(blob_store):
    """Binary data with full byte range is correctly base64-encoded and decoded."""
    binary_data = bytes(range(256))
    storage_ref = await blob_store.put(binary_data, content_type="application/octet-stream")
    result = await get_attachment(blob_store, storage_ref)
    assert base64.b64decode(result["data_base64"]) == binary_data
