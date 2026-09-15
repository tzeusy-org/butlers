"""Shared tools for serving media attachments to runtime instances.

Provides:
- ``get_attachment()`` — small non-image blobs (PDFs, text, ...) as base64
  JSON, capped well under Claude's context so a tool result never smuggles a
  large payload into the model's readable text.
- ``attachment_view()`` — images as a real MCP image content block (bytes
  travel as protocol-level vision input, never as a JSON string field), for
  everything up to the API's vision size limit.

Neither function ever returns a base64 string over ``MAX_INLINE_BASE64_BYTES``
inside a JSON/dict tool result — see ``tests/core/test_attachment_view.py``'s
contract test.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
from typing import Any

from fastmcp.utilities.types import Image

from butlers.storage import BlobNotFoundError, BlobRef, BlobStore

logger = logging.getLogger(__name__)

# Claude API limit for a single vision/PDF attachment.
MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024  # 5MB

# get_attachment() embeds the blob as a base64 string inside a JSON tool
# result (readable model-context text, not a protocol-level content block).
# Capped far below MAX_ATTACHMENT_SIZE_BYTES: a multi-MB base64 string dumped
# into a tool result is exactly the anti-pattern bu-2jtfw.7 fixes. Images
# belong in attachment_view()'s real image content block instead, which has
# no such cap (up to MAX_ATTACHMENT_SIZE_BYTES).
MAX_INLINE_BASE64_BYTES = 64 * 1024  # 64KB


async def get_attachment(blob_store: BlobStore | None, storage_ref: str) -> dict[str, Any]:
    """Retrieve a small, non-image ingested attachment as inline base64 JSON.

    Returns base64-encoded data for PDFs/text attachments small enough to
    embed directly in a tool result. Images should use ``attachment_view()``
    instead, which returns a real MCP image content block with no inline-size
    cap (only the absolute ``MAX_ATTACHMENT_SIZE_BYTES`` ceiling).

    Args:
        blob_store: The BlobStore instance to retrieve from
        storage_ref: Storage reference string (e.g., 's3://bucket/general/2026/02/16/abc123.jpg')

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
    if blob_store is None:
        raise ValueError(
            "Blob storage is not configured or currently unavailable. "
            "Check /api/settings/blob-storage/test and the BLOB_S3_* secrets."
        )

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

    # Check absolute size limit
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

    media_type = _infer_media_type(blob_ref.key)

    # Refuse rather than embed a base64 string over MAX_INLINE_BASE64_BYTES —
    # a large blob (in particular any image, which should go through
    # attachment_view() anyway) must never be dumped into a tool result.
    b64_size_estimate = (size_bytes + 2) // 3 * 4  # base64 inflation, no encode needed yet
    if b64_size_estimate > MAX_INLINE_BASE64_BYTES:
        logger.info(
            "Refusing inline base64 for %s: %d bytes would encode to ~%d base64 bytes > %d cap",
            storage_ref,
            size_bytes,
            b64_size_estimate,
            MAX_INLINE_BASE64_BYTES,
        )
        return {
            "status": "refused",
            "reason": "inline_size_cap_exceeded",
            "storage_ref": storage_ref,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "inline_limit_bytes": MAX_INLINE_BASE64_BYTES,
            "hint": (
                "This attachment is too large to embed inline. If it is an image, "
                "call attachment_view(storage_ref=...) instead."
            ),
        }

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


async def attachment_view(blob_store: BlobStore | None, storage_ref: str) -> Image | dict[str, Any]:
    """Retrieve an image attachment as a real MCP image content block.

    Unlike ``get_attachment()``, the image bytes never pass through this
    function's return value as a JSON string field — ``Image.to_image_content()``
    (invoked by FastMCP when a tool returns an ``Image``) is a protocol-level
    ``ImageContent`` block, the correct channel for vision input.

    Args:
        blob_store: The BlobStore instance to retrieve from.
        storage_ref: Storage reference string (e.g. 's3://bucket/general/2026/02/16/abc123.jpg').

    Returns:
        An ``Image`` for a fetchable, in-cap, image/* blob. A typed refusal
        dict (``status: "refused"``) for anything over the size cap or not an
        image media type. Never raises for an ordinary not-found/oversized
        case — see the ``status`` field of the returned dict.
    """
    if blob_store is None:
        return {
            "status": "refused",
            "reason": "blob_store_unavailable",
            "storage_ref": storage_ref,
            "hint": "Blob storage is not configured. Check /api/settings/blob-storage/test.",
        }

    try:
        blob_ref = BlobRef.parse(storage_ref)
    except ValueError as exc:
        logger.warning("Invalid storage_ref format: %s", storage_ref)
        return {
            "status": "refused",
            "reason": "invalid_storage_ref",
            "storage_ref": storage_ref,
            "hint": str(exc),
        }

    media_type = _infer_media_type(blob_ref.key)
    if not media_type.startswith("image/"):
        return {
            "status": "refused",
            "reason": "unsupported_media_type",
            "storage_ref": storage_ref,
            "media_type": media_type,
            "hint": "attachment_view only serves image/* attachments; use get_attachment instead.",
        }

    try:
        data = await blob_store.get(storage_ref)
    except BlobNotFoundError:
        logger.warning("Blob not found: %s", storage_ref)
        return {"status": "refused", "reason": "not_found", "storage_ref": storage_ref}

    size_bytes = len(data)
    if size_bytes > MAX_ATTACHMENT_SIZE_BYTES:
        logger.warning(
            "Image exceeds vision size limit: %s (%.2f MB > %.2f MB)",
            storage_ref,
            size_bytes / (1024 * 1024),
            MAX_ATTACHMENT_SIZE_BYTES / (1024 * 1024),
        )
        return {
            "status": "refused",
            "reason": "attachment_too_large",
            "storage_ref": storage_ref,
            "media_type": media_type,
            "size_bytes": size_bytes,
            "limit_bytes": MAX_ATTACHMENT_SIZE_BYTES,
        }

    image_format = media_type.split("/", 1)[1] or "png"
    logger.info(
        "Serving image attachment: %s (%.2f KB, %s)", storage_ref, size_bytes / 1024, media_type
    )
    return Image(data=data, format=image_format)


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
