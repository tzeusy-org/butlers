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
    # Create the test bucket via sync boto3
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
    """Create an S3BlobStore pointing at the moto server."""
    return S3BlobStore(
        bucket=TEST_BUCKET,
        butler_name=TEST_BUTLER,
        endpoint_url=moto_s3_server,
        access_key_id="testing",
        secret_access_key="testing",
        region="us-east-1",
    )


class TestBlobRef:
    """Test BlobRef parsing and serialization."""

    def test_parse_s3_ref(self):
        """Parse an S3 storage_ref."""
        ref = BlobRef.parse("s3://bucket/general/2026/02/16/abc123.jpg")
        assert ref.scheme == "s3"
        assert ref.key == "bucket/general/2026/02/16/abc123.jpg"

    def test_parse_invalid_ref_no_scheme(self):
        """Reject storage_ref without scheme separator."""
        with pytest.raises(ValueError, match="Invalid storage_ref format"):
            BlobRef.parse("just-a-key")

    def test_to_ref(self):
        """Convert BlobRef back to storage_ref string."""
        ref = BlobRef(scheme="s3", key="bucket/general/2026/02/16/test.jpg")
        assert ref.to_ref() == "s3://bucket/general/2026/02/16/test.jpg"

    def test_roundtrip(self):
        """Parse and serialize roundtrip."""
        original = "s3://bucket/general/2026/02/16/roundtrip.jpg"
        ref = BlobRef.parse(original)
        assert ref.to_ref() == original


class TestS3BlobStore:
    """Test S3BlobStore implementation."""

    @pytest.fixture
    def sample_data(self):
        """Sample blob data."""
        return b"This is test blob data"

    async def test_put_and_get_roundtrip(self, blob_store, sample_data):
        """Store and retrieve a blob."""
        storage_ref = await blob_store.put(
            sample_data, content_type="text/plain", filename="test.txt"
        )

        assert storage_ref.startswith(f"s3://{TEST_BUCKET}/")
        assert ".txt" in storage_ref

        retrieved = await blob_store.get(storage_ref)
        assert retrieved == sample_data

    async def test_put_with_content_type_extension(self, blob_store, sample_data):
        """Extension inferred from content_type when filename is None."""
        storage_ref = await blob_store.put(sample_data, content_type="image/jpeg")

        assert storage_ref.endswith(".jpeg") or storage_ref.endswith(".jpg")

    async def test_put_filename_extension_takes_precedence(self, blob_store, sample_data):
        """Filename extension preferred over content_type."""
        storage_ref = await blob_store.put(
            sample_data, content_type="text/plain", filename="doc.pdf"
        )

        assert storage_ref.endswith(".pdf")

    async def test_key_butler_prefix_and_date_partitioning(self, blob_store, sample_data):
        """Keys are butler-prefixed and date-partitioned."""
        storage_ref = await blob_store.put(sample_data, content_type="text/plain")

        ref = BlobRef.parse(storage_ref)
        key = ref.key
        assert key.startswith(f"{TEST_BUCKET}/{TEST_BUTLER}/")

        parts = key.split("/")
        # parts: [bucket, butler, YYYY, MM, DD, filename]
        assert len(parts) >= 6
        assert len(parts[2]) == 4 and parts[2].isdigit()  # year
        assert len(parts[3]) == 2 and parts[3].isdigit()  # month
        assert len(parts[4]) == 2 and parts[4].isdigit()  # day

    async def test_key_uniqueness(self, blob_store, sample_data):
        """Multiple puts generate unique keys."""
        ref1 = await blob_store.put(sample_data, content_type="text/plain")
        ref2 = await blob_store.put(sample_data, content_type="text/plain")

        assert ref1 != ref2

        data1 = await blob_store.get(ref1)
        data2 = await blob_store.get(ref2)
        assert data1 == sample_data
        assert data2 == sample_data

    async def test_get_missing_blob_raises_error(self, blob_store):
        """Missing blob raises BlobNotFoundError."""
        fake_ref = f"s3://{TEST_BUCKET}/{TEST_BUTLER}/2026/01/01/nonexistent.jpg"

        with pytest.raises(BlobNotFoundError, match="Blob not found"):
            await blob_store.get(fake_ref)

    async def test_get_wrong_scheme_raises_value_error(self, blob_store):
        """Storage ref with wrong scheme raises ValueError."""
        wrong_ref = "local://2026/01/01/key.jpg"

        with pytest.raises(ValueError, match="Storage scheme mismatch"):
            await blob_store.get(wrong_ref)

    async def test_delete(self, blob_store, sample_data):
        """Delete removes blob from storage."""
        storage_ref = await blob_store.put(sample_data, content_type="text/plain")

        assert await blob_store.exists(storage_ref)

        await blob_store.delete(storage_ref)

        assert not await blob_store.exists(storage_ref)

    async def test_delete_missing_blob_raises_error(self, blob_store):
        """Deleting non-existent blob raises BlobNotFoundError."""
        fake_ref = f"s3://{TEST_BUCKET}/{TEST_BUTLER}/2026/01/01/nonexistent.jpg"

        with pytest.raises(BlobNotFoundError, match="Blob not found"):
            await blob_store.delete(fake_ref)

    async def test_exists_true_for_existing_blob(self, blob_store, sample_data):
        """exists() returns True for existing blob."""
        storage_ref = await blob_store.put(sample_data, content_type="text/plain")
        assert await blob_store.exists(storage_ref) is True

    async def test_exists_false_for_missing_blob(self, blob_store):
        """exists() returns False for missing blob."""
        fake_ref = f"s3://{TEST_BUCKET}/{TEST_BUTLER}/2026/01/01/nonexistent.jpg"
        assert await blob_store.exists(fake_ref) is False

    async def test_exists_false_for_wrong_scheme(self, blob_store):
        """exists() returns False for wrong storage scheme."""
        wrong_ref = "local://2026/01/01/key.jpg"
        assert await blob_store.exists(wrong_ref) is False

    async def test_binary_data_integrity(self, blob_store):
        """Binary data stored without corruption."""
        binary_data = bytes(range(256))

        storage_ref = await blob_store.put(
            binary_data, content_type="image/png", filename="test.png"
        )

        retrieved = await blob_store.get(storage_ref)
        assert retrieved == binary_data

    async def test_large_blob(self, blob_store):
        """Handle large blobs (multi-megabyte)."""
        large_data = b"x" * (5 * 1024 * 1024)

        storage_ref = await blob_store.put(large_data, content_type="application/octet-stream")

        retrieved = await blob_store.get(storage_ref)
        assert len(retrieved) == len(large_data)
        assert retrieved == large_data

    async def test_startup_check_success(self, blob_store):
        """startup_check succeeds when bucket exists."""
        await blob_store.startup_check()

    async def test_startup_check_missing_bucket(self, moto_s3_server):
        """startup_check raises when bucket does not exist."""
        store = S3BlobStore(
            bucket="nonexistent-bucket",
            butler_name=TEST_BUTLER,
            endpoint_url=moto_s3_server,
            access_key_id="testing",
            secret_access_key="testing",
            region="us-east-1",
        )

        with pytest.raises(RuntimeError, match="does not exist"):
            await store.startup_check()
