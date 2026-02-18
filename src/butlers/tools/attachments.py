"""Shared tool for serving media attachments to runtime instances.

Provides get_attachment() for retrieving ingested blobs (images, PDFs, etc.)
as base64-encoded data suitable for Claude vision/PDF input.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any

from butlers.storage import BlobNotFoundError, BlobRef, BlobStore

logger = logging.getLogger(__name__)

# Claude API limit for attachments
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


async def get_attachment(blob_store: BlobStore, storage_ref: str) -> dict[str, Any]:
    """Retrieve an ingested media attachment for analysis.

    Returns base64-encoded data suitable for Claude vision/PDF input.

    Args:
        blob_store: The BlobStore instance to retrieve from
        storage_ref: Storage reference string (e.g., 'local://2026/02/16/abc123.jpg')

    Returns:
        Dictionary with:
        - storage_ref: The storage reference
        - media_type: Inferred MIME type
        - data_base64: Base64-encoded blob data
        - size_bytes: Size of the blob in bytes

    Raises:
        ValueError: If storage_ref is invalid or blob exceeds size limit
        BlobNotFoundError: If blob does not exist
    """
    # Validate storage_ref format
    try:
        blob_ref = BlobRef.parse(storage_ref)
    except ValueError as e:
        logger.warning("Invalid storage_ref format: %s", storage_ref)
        raise ValueError(f"Invalid storage_ref format: {e}") from e

    # Retrieve blob
    try:
        data = await blob_store.get(storage_ref)
    except BlobNotFoundError:
        logger.warning("Blob not found: %s", storage_ref)
        raise

    # Check size limit
    size_bytes = len(data)
    if size_bytes > MAX_ATTACHMENT_SIZE_BYTES:
        logger.warning(
            "Blob exceeds size limit: %s (%.2f MB > %.2f MB)",
            storage_ref,
            size_bytes / (1024 * 1024),
            MAX_ATTACHMENT_SIZE_BYTES / (1024 * 1024),
        )
        raise ValueError(
            f"Attachment exceeds size limit: {size_bytes / (1024 * 1024):.2f} MB > "
            f"{MAX_ATTACHMENT_SIZE_BYTES / (1024 * 1024):.2f} MB"
        )

    # Infer media type from storage_ref key (file extension)
    media_type = _infer_media_type(blob_ref.key)

    # Base64 encode
    b64_data = base64.b64encode(data).decode("ascii")

    logger.info(
        "Retrieved attachment: %s (%.2f KB, %s)",
        storage_ref,
        size_bytes / 1024,
        media_type,
    )

    return {
        "storage_ref": storage_ref,
        "media_type": media_type,
        "data_base64": b64_data,
        "size_bytes": size_bytes,
    }


def _infer_media_type(key: str) -> str:
    """Infer MIME type from blob key (file extension).

    Args:
        key: Blob key like '2026/02/16/abc123.jpg'

    Returns:
        MIME type string, or 'application/octet-stream' if unknown
    """
    # Try to guess from extension
    guessed_type, _ = mimetypes.guess_type(key)
    if guessed_type:
        return guessed_type

    # Fallback to generic binary
    return "application/octet-stream"
