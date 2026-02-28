"""Tests for blob storage abstraction."""

import pytest

from butlers.storage.blobs import BlobNotFoundError, BlobRef, LocalBlobStore

pytestmark = pytest.mark.unit


class TestBlobRef:
    """Test BlobRef parsing and serialization."""

    def test_parse_valid_ref(self):
        """Parse a valid storage_ref string."""
        ref = BlobRef.parse("local://2026/02/16/abc123.jpg")
        assert ref.scheme == "local"
        assert ref.key == "2026/02/16/abc123.jpg"

    def test_parse_s3_ref(self):
        """Parse a future S3 storage_ref."""
        ref = BlobRef.parse("s3://bucket/path/to/blob.png")
        assert ref.scheme == "s3"
        assert ref.key == "bucket/path/to/blob.png"

    def test_parse_invalid_ref_no_scheme(self):
        """Reject storage_ref without scheme separator."""
        with pytest.raises(ValueError, match="Invalid storage_ref format"):
            BlobRef.parse("just-a-key")

    def test_to_ref(self):
        """Convert BlobRef back to storage_ref string."""
        ref = BlobRef(scheme="local", key="2026/02/16/test.jpg")
        assert ref.to_ref() == "local://2026/02/16/test.jpg"

    def test_roundtrip(self):
        """Parse and serialize roundtrip."""
        original = "local://2026/02/16/roundtrip.jpg"
        ref = BlobRef.parse(original)
        assert ref.to_ref() == original


class TestLocalBlobStore:
    """Test LocalBlobStore implementation."""

    @pytest.fixture
    def blob_store(self, tmp_path):
        """Create a LocalBlobStore with temp directory."""
        return LocalBlobStore(base_dir=tmp_path)

    @pytest.fixture
    def sample_data(self):
        """Sample blob data."""
        return b"This is test blob data"

    async def test_put_and_get_roundtrip(self, blob_store, sample_data):
        """Store and retrieve a blob."""
        storage_ref = await blob_store.put(
            sample_data, content_type="text/plain", filename="test.txt"
        )

        # Verify storage_ref format
        assert storage_ref.startswith("local://")
        assert ".txt" in storage_ref

        # Retrieve blob
        retrieved = await blob_store.get(storage_ref)
        assert retrieved == sample_data

    async def test_put_creates_parent_directories(self, blob_store, sample_data):
        """Parent directories are created automatically on put."""
        storage_ref = await blob_store.put(
            sample_data, content_type="image/jpeg", filename="photo.jpg"
        )

        # Parse ref and verify directory structure exists
        ref = BlobRef.parse(storage_ref)
        file_path = blob_store.base_dir / ref.key
        assert file_path.exists()
        assert file_path.parent.exists()

    async def test_put_with_content_type_extension(self, blob_store, sample_data):
        """Extension inferred from content_type when filename is None."""
        storage_ref = await blob_store.put(sample_data, content_type="image/jpeg")

        # Should have .jpeg or .jpg extension
        assert storage_ref.endswith(".jpeg") or storage_ref.endswith(".jpg")

    async def test_put_filename_extension_takes_precedence(self, blob_store, sample_data):
        """Filename extension preferred over content_type."""
        storage_ref = await blob_store.put(
            sample_data, content_type="text/plain", filename="doc.pdf"
        )

        # Should have .pdf extension from filename
        assert storage_ref.endswith(".pdf")

    async def test_key_date_partitioning(self, blob_store, sample_data):
        """Keys are date-partitioned (YYYY/MM/DD)."""
        storage_ref = await blob_store.put(sample_data, content_type="text/plain")

        ref = BlobRef.parse(storage_ref)
        parts = ref.key.split("/")

        # Should have at least 4 parts: YYYY, MM, DD, filename
        assert len(parts) >= 4
        # Year should be 4 digits
        assert len(parts[0]) == 4
        assert parts[0].isdigit()
        # Month should be 2 digits
        assert len(parts[1]) == 2
        assert parts[1].isdigit()
        # Day should be 2 digits
        assert len(parts[2]) == 2
        assert parts[2].isdigit()

    async def test_key_uniqueness(self, blob_store, sample_data):
        """Multiple puts generate unique keys (no collisions)."""
        ref1 = await blob_store.put(sample_data, content_type="text/plain")
        ref2 = await blob_store.put(sample_data, content_type="text/plain")

        assert ref1 != ref2

        # Both should exist independently
        data1 = await blob_store.get(ref1)
        data2 = await blob_store.get(ref2)
        assert data1 == sample_data
        assert data2 == sample_data

    async def test_get_missing_blob_raises_clear_error(self, blob_store):
        """Missing blob raises BlobNotFoundError, not generic FileNotFoundError."""
        fake_ref = "local://2026/01/01/nonexistent.jpg"

        with pytest.raises(BlobNotFoundError, match="Blob not found"):
            await blob_store.get(fake_ref)

    async def test_get_wrong_scheme_raises_value_error(self, blob_store):
        """Storage ref with wrong scheme raises ValueError."""
        wrong_ref = "s3://bucket/key.jpg"

        with pytest.raises(ValueError, match="Storage scheme mismatch"):
            await blob_store.get(wrong_ref)

    async def test_path_traversal_blocked(self, blob_store):
        """Path traversal attempts are blocked with ValueError."""
        # Try various path traversal techniques
        traversal_attempts = [
            "local://../../../etc/passwd",
            "local://../../sensitive.txt",
            "local://../outside.jpg",
            "local://subdir/../../outside.jpg",
        ]

        for malicious_ref in traversal_attempts:
            with pytest.raises(ValueError, match="Path traversal attempt detected"):
                await blob_store.get(malicious_ref)

    async def test_delete(self, blob_store, sample_data):
        """Delete removes blob from storage."""
        storage_ref = await blob_store.put(sample_data, content_type="text/plain")

        # Verify it exists
        assert await blob_store.exists(storage_ref)

        # Delete it
        await blob_store.delete(storage_ref)

        # Verify it's gone
        assert not await blob_store.exists(storage_ref)

    async def test_delete_missing_blob_raises_error(self, blob_store):
        """Deleting non-existent blob raises BlobNotFoundError."""
        fake_ref = "local://2026/01/01/nonexistent.jpg"

        with pytest.raises(BlobNotFoundError, match="Blob not found"):
            await blob_store.delete(fake_ref)

    async def test_exists_true_for_existing_blob(self, blob_store, sample_data):
        """exists() returns True for existing blob."""
        storage_ref = await blob_store.put(sample_data, content_type="text/plain")
        assert await blob_store.exists(storage_ref) is True

    async def test_exists_false_for_missing_blob(self, blob_store):
        """exists() returns False for missing blob."""
        fake_ref = "local://2026/01/01/nonexistent.jpg"
        assert await blob_store.exists(fake_ref) is False

    async def test_exists_false_for_wrong_scheme(self, blob_store):
        """exists() returns False for wrong storage scheme."""
        wrong_ref = "s3://bucket/key.jpg"
        assert await blob_store.exists(wrong_ref) is False

    async def test_exists_false_for_path_traversal(self, blob_store):
        """exists() returns False for path traversal attempts."""
        traversal_ref = "local://../../../etc/passwd"
        assert await blob_store.exists(traversal_ref) is False

    async def test_binary_data_integrity(self, blob_store):
        """Binary data (like images) stored without corruption."""
        # Simulate binary image data with various byte values
        binary_data = bytes(range(256))

        storage_ref = await blob_store.put(
            binary_data, content_type="image/png", filename="test.png"
        )

        retrieved = await blob_store.get(storage_ref)
        assert retrieved == binary_data

    async def test_large_blob(self, blob_store):
        """Handle large blobs (multi-megabyte)."""
        # Create 5MB blob
        large_data = b"x" * (5 * 1024 * 1024)

        storage_ref = await blob_store.put(large_data, content_type="application/octet-stream")

        retrieved = await blob_store.get(storage_ref)
        assert len(retrieved) == len(large_data)
        assert retrieved == large_data
