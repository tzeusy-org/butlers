"""Tests for the get_attachment shared tool."""

import base64

import pytest

from butlers.storage import BlobNotFoundError, LocalBlobStore
from butlers.tools.attachments import MAX_ATTACHMENT_SIZE_BYTES, get_attachment


@pytest.fixture
def blob_store(tmp_path):
    """Create a LocalBlobStore with temp directory."""
    return LocalBlobStore(base_dir=tmp_path)


@pytest.fixture
def sample_image_data():
    """Sample binary image data."""
    # Simulate a small PNG-like binary blob
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"x" * 100)


@pytest.fixture
def sample_pdf_data():
    """Sample PDF data."""
    return b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + (b"y" * 200)


class TestGetAttachment:
    """Test get_attachment function."""

    async def test_retrieve_existing_attachment(self, blob_store, sample_image_data):
        """Successfully retrieve an existing attachment."""
        # Store a blob
        storage_ref = await blob_store.put(
            sample_image_data, content_type="image/png", filename="test.png"
        )

        # Retrieve via get_attachment
        result = await get_attachment(blob_store, storage_ref)

        # Verify structure
        assert "storage_ref" in result
        assert "media_type" in result
        assert "data_base64" in result
        assert "size_bytes" in result

        # Verify data
        assert result["storage_ref"] == storage_ref
        assert result["media_type"] == "image/png"
        assert result["size_bytes"] == len(sample_image_data)

        # Verify base64 decoding works
        decoded = base64.b64decode(result["data_base64"])
        assert decoded == sample_image_data

    async def test_media_type_inference_from_extension(self, blob_store, sample_pdf_data):
        """Media type correctly inferred from file extension."""
        storage_ref = await blob_store.put(
            sample_pdf_data, content_type="application/pdf", filename="doc.pdf"
        )

        result = await get_attachment(blob_store, storage_ref)

        assert result["media_type"] == "application/pdf"

    async def test_media_type_jpeg(self, blob_store):
        """JPEG media type correctly identified."""
        jpeg_data = b"\xff\xd8\xff\xe0" + (b"j" * 100)

        storage_ref = await blob_store.put(
            jpeg_data, content_type="image/jpeg", filename="photo.jpg"
        )

        result = await get_attachment(blob_store, storage_ref)

        assert result["media_type"] == "image/jpeg"

    async def test_media_type_fallback_for_unknown_extension(self, blob_store):
        """Unknown extensions fall back to application/octet-stream."""
        data = b"random data"

        storage_ref = await blob_store.put(
            data, content_type="application/octet-stream", filename="unknown.unknownext123"
        )

        result = await get_attachment(blob_store, storage_ref)

        assert result["media_type"] == "application/octet-stream"

    async def test_size_limit_enforcement(self, blob_store):
        """Blobs exceeding 5MB size limit are rejected."""
        # Create blob just over 5MB
        large_data = b"x" * (MAX_ATTACHMENT_SIZE_BYTES + 1)

        storage_ref = await blob_store.put(large_data, content_type="application/octet-stream")

        # Should raise ValueError with clear message about size limit
        with pytest.raises(ValueError, match="Attachment exceeds size limit"):
            await get_attachment(blob_store, storage_ref)

    async def test_exactly_at_size_limit_allowed(self, blob_store):
        """Blobs exactly at 5MB limit are allowed."""
        # Create blob exactly at limit
        data_at_limit = b"x" * MAX_ATTACHMENT_SIZE_BYTES

        storage_ref = await blob_store.put(data_at_limit, content_type="application/octet-stream")

        # Should succeed
        result = await get_attachment(blob_store, storage_ref)
        assert result["size_bytes"] == MAX_ATTACHMENT_SIZE_BYTES

    async def test_blob_not_found_raises_clear_error(self, blob_store):
        """Missing blob raises BlobNotFoundError."""
        fake_ref = "local://2026/01/01/nonexistent.jpg"

        with pytest.raises(BlobNotFoundError, match="Blob not found"):
            await get_attachment(blob_store, fake_ref)

    async def test_invalid_storage_ref_format_raises_value_error(self, blob_store):
        """Invalid storage_ref format raises ValueError."""
        invalid_ref = "not-a-valid-ref-format"

        with pytest.raises(ValueError, match="Invalid storage_ref format"):
            await get_attachment(blob_store, invalid_ref)

    async def test_wrong_scheme_raises_value_error(self, blob_store):
        """Storage ref with wrong scheme raises ValueError during retrieval."""
        wrong_ref = "s3://bucket/key.jpg"

        # ValueError will be raised by blob_store.get() due to scheme mismatch
        with pytest.raises(ValueError, match="Storage scheme mismatch"):
            await get_attachment(blob_store, wrong_ref)

    async def test_base64_encoding_correctness(self, blob_store):
        """Base64 encoding is valid ASCII and correctly encodes data."""
        data = b"Hello, World! \x00\xff\x42"

        storage_ref = await blob_store.put(data, content_type="text/plain")

        result = await get_attachment(blob_store, storage_ref)

        # Verify it's ASCII
        assert result["data_base64"].isascii()

        # Verify roundtrip
        decoded = base64.b64decode(result["data_base64"])
        assert decoded == data

    async def test_binary_data_integrity(self, blob_store):
        """Binary data with full byte range is correctly encoded."""
        # All possible byte values
        binary_data = bytes(range(256))

        storage_ref = await blob_store.put(binary_data, content_type="application/octet-stream")

        result = await get_attachment(blob_store, storage_ref)

        # Decode and verify
        decoded = base64.b64decode(result["data_base64"])
        assert decoded == binary_data

    async def test_empty_blob(self, blob_store):
        """Empty blobs are handled correctly."""
        empty_data = b""

        storage_ref = await blob_store.put(empty_data, content_type="text/plain")

        result = await get_attachment(blob_store, storage_ref)

        assert result["size_bytes"] == 0
        assert result["data_base64"] == ""

    async def test_concurrent_retrievals(self, blob_store, sample_image_data):
        """Multiple concurrent retrievals of the same blob work correctly."""
        storage_ref = await blob_store.put(sample_image_data, content_type="image/png")

        # Retrieve same blob multiple times concurrently
        import asyncio

        results = await asyncio.gather(*[get_attachment(blob_store, storage_ref) for _ in range(5)])

        # All results should be identical
        for result in results:
            assert result["storage_ref"] == storage_ref
            assert result["size_bytes"] == len(sample_image_data)
            decoded = base64.b64decode(result["data_base64"])
            assert decoded == sample_image_data
