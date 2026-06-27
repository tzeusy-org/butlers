"""Tests for S3-compatible blob storage."""

import boto3
import pytest
from moto.server import ThreadedMotoServer

from butlers.storage.blobs import BlobNotFoundError, BlobRef, S3BlobStore

pytestmark = pytest.mark.unit

TEST_BUCKET = "test-butlers-blobs"
TEST_BUTLER = "testbutler"


@pytest.fixture(autouse=True)
def _mock_s3_startup_check():
    """Override the global autouse fixture to allow real startup_check in blob tests."""
    yield


@pytest.fixture(scope="module")
def moto_s3_server():
    """Start a moto HTTP server for S3 (module-scoped for speed)."""
    server = ThreadedMotoServer(port=0, verbose=False)
    server.start()
    endpoint = f"http://localhost:{server._server.server_address[1]}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="us-east-1",
    )
    client.create_bucket(Bucket=TEST_BUCKET)
    yield endpoint
    server.stop()


@pytest.fixture
def blob_store(moto_s3_server):
    return S3BlobStore(
        bucket=TEST_BUCKET,
        butler_name=TEST_BUTLER,
        endpoint_url=moto_s3_server,
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
    )


def test_s3_blob_store_configures_explicit_network_timeouts(moto_s3_server):
    """S3 startup checks should fail fast when the endpoint does not answer."""
    store = S3BlobStore(
        bucket=TEST_BUCKET,
        butler_name=TEST_BUTLER,
        endpoint_url=moto_s3_server,
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
        request_timeout_s=1.25,
    )

    assert store._boto_config.connect_timeout == 1.25
    assert store._boto_config.read_timeout == 1.25


def test_blob_ref_contract():
    """BlobRef parse/serialize roundtrip; invalid format raises; to_ref produces correct string."""
    ref = BlobRef.parse("s3://bucket/general/2026/02/16/abc123.jpg")
    assert ref.scheme == "s3" and ref.key == "bucket/general/2026/02/16/abc123.jpg"
    assert ref.to_ref() == "s3://bucket/general/2026/02/16/abc123.jpg"

    original = "s3://bucket/general/2026/02/16/roundtrip.jpg"
    assert BlobRef.parse(original).to_ref() == original

    with pytest.raises(ValueError, match="Invalid storage_ref format"):
        BlobRef.parse("just-a-key")


async def test_s3_blob_store_put_get_delete(blob_store):
    """put/get roundtrip; key is butler-prefixed and date-partitioned; unique keys per put;
    delete removes blob."""
    data = b"This is test blob data"
    storage_ref = await blob_store.put(data, content_type="text/plain", filename="test.txt")
    assert storage_ref.startswith(f"s3://{TEST_BUCKET}/") and ".txt" in storage_ref
    assert await blob_store.get(storage_ref) == data

    # Key structure: bucket/butler/YYYY/MM/DD/filename
    key = BlobRef.parse(storage_ref).key
    parts = key.split("/")
    assert parts[0] == TEST_BUCKET and parts[1] == TEST_BUTLER
    assert len(parts) >= 6 and len(parts[2]) == 4 and parts[2].isdigit()

    # Unique keys
    ref2 = await blob_store.put(data, content_type="text/plain")
    assert storage_ref != ref2

    # Extension from content_type when no filename
    jpeg_ref = await blob_store.put(data, content_type="image/jpeg")
    assert jpeg_ref.endswith(".jpeg") or jpeg_ref.endswith(".jpg")

    # Filename extension takes precedence
    pdf_ref = await blob_store.put(data, content_type="text/plain", filename="doc.pdf")
    assert pdf_ref.endswith(".pdf")

    # Delete removes blob
    assert await blob_store.exists(storage_ref) is True
    await blob_store.delete(storage_ref)
    assert await blob_store.exists(storage_ref) is False


async def test_s3_blob_store_errors(blob_store):
    """Missing blob raises BlobNotFoundError; wrong scheme raises ValueError; delete missing
    raises; exists returns False for wrong scheme."""
    fake_ref = f"s3://{TEST_BUCKET}/{TEST_BUTLER}/2026/01/01/nonexistent.jpg"

    with pytest.raises(BlobNotFoundError, match="Blob not found"):
        await blob_store.get(fake_ref)

    with pytest.raises(ValueError, match="Storage scheme mismatch"):
        await blob_store.get("local://2026/01/01/key.jpg")

    with pytest.raises(BlobNotFoundError, match="Blob not found"):
        await blob_store.delete(fake_ref)

    assert await blob_store.exists(fake_ref) is False
    assert await blob_store.exists("local://2026/01/01/key.jpg") is False


async def test_s3_blob_store_binary_and_startup(blob_store, moto_s3_server):
    """Binary data stored without corruption; startup_check succeeds; missing bucket raises."""
    binary_data = bytes(range(256))
    ref = await blob_store.put(binary_data, content_type="image/png", filename="test.png")
    assert await blob_store.get(ref) == binary_data

    await blob_store.startup_check()

    bad_store = S3BlobStore(
        bucket="nonexistent-bucket",
        butler_name=TEST_BUTLER,
        endpoint_url=moto_s3_server,
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
    )
    with pytest.raises(RuntimeError, match="does not exist"):
        await bad_store.startup_check()
