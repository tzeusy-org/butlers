"""File attachments for owner notifications (``notify()`` / ``notify.v1``).

A notify attachment is either an existing blob, named by its RFC 0016
``storage_ref``, or a small generated text file passed inline as
``content_text``.  This module owns the limits and the two checks every hop
shares:

* :func:`validate_attachment_specs` — pure shape validation before any I/O;
* :func:`resolve_attachments` — fetch every blob, enforce size limits and
  verify pinned sha256 digests, all before the first provider call.

Bytes and text never leave this module in logs: :func:`attachment_audit_summary`
and :func:`redact_attachment_content` carry only media type, size and digest.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from butlers.storage.blobs import BlobNotFoundError, BlobRef

MAX_ATTACHMENTS = 10
MAX_INLINE_BYTES = 256 * 1024
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_FILENAME_LENGTH = 255
CAPTION_LIMIT = 1024
INLINE_MEDIA_TYPES = frozenset(
    {"text/plain", "text/csv", "text/markdown", "text/calendar", "application/json"}
)
PHOTO_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})

_SPEC_KEYS = frozenset(
    {"filename", "media_type", "storage_ref", "content_text", "caption", "sha256", "size_bytes"}
)
_MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FILENAME_CHARS = re.compile(r"[/\\\x00-\x1f\x7f]")


class AttachmentError(ValueError):
    """An attachment cannot be delivered; nothing was sent.

    ``code`` is a fixed, content-free category safe for logs and receipts.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedAttachment:
    """One attachment with its bytes in hand, ready for upload."""

    filename: str
    media_type: str
    data: bytes
    sha256: str
    caption: str | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def is_photo(self) -> bool:
        return self.media_type in PHOTO_MEDIA_TYPES and self.size_bytes <= MAX_PHOTO_BYTES


def _invalid(message: str) -> AttachmentError:
    return AttachmentError("attachment_invalid", message)


def validate_filename(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid("filename must be a non-empty string")
    if len(value) > MAX_FILENAME_LENGTH or _FORBIDDEN_FILENAME_CHARS.search(value):
        raise _invalid("filename must be at most 255 characters without path separators")
    if value.strip() in {".", ".."}:
        raise _invalid("filename must name a file")
    return value


def validate_media_type(value: Any) -> str:
    if not isinstance(value, str) or not _MEDIA_TYPE_RE.fullmatch(value):
        raise _invalid("media_type must be a lowercase type/subtype")
    return value


def validate_inline_content(media_type: str, content_text: Any) -> str:
    if not isinstance(content_text, str):
        raise _invalid("content_text must be a string")
    if media_type not in INLINE_MEDIA_TYPES:
        raise _invalid("inline content_text supports only " + ", ".join(sorted(INLINE_MEDIA_TYPES)))
    if len(content_text.encode("utf-8")) > MAX_INLINE_BYTES:
        raise _invalid("inline content_text must be at most 256 KB")
    return content_text


def validate_attachment_specs(raw: Any) -> list[dict[str, Any]]:
    """Validate attachment shapes before any I/O; return normalized dicts.

    Each item needs ``filename``, ``media_type`` and exactly one of
    ``storage_ref`` or ``content_text``; ``caption`` and a pinned ``sha256``
    are optional.  Raises :class:`AttachmentError` (``attachment_invalid``).
    """
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise _invalid("attachments must be a list")
    if not raw:
        raise _invalid("attachments must not be empty when provided")
    if len(raw) > MAX_ATTACHMENTS:
        raise _invalid(f"at most {MAX_ATTACHMENTS} attachments per notification")
    specs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise _invalid("each attachment must be an object")
        unknown = set(item) - _SPEC_KEYS
        if unknown:
            raise _invalid("unknown attachment fields: " + ", ".join(sorted(unknown)))
        spec: dict[str, Any] = {
            "filename": validate_filename(item.get("filename")),
            "media_type": validate_media_type(item.get("media_type")),
        }
        storage_ref = item.get("storage_ref")
        content_text = item.get("content_text")
        if (storage_ref is None) == (content_text is None):
            raise _invalid("each attachment needs exactly one of storage_ref or content_text")
        if storage_ref is not None:
            if not isinstance(storage_ref, str) or not storage_ref.strip():
                raise _invalid("storage_ref must be a non-empty string")
            spec["storage_ref"] = storage_ref
        else:
            spec["content_text"] = validate_inline_content(spec["media_type"], content_text)
        caption = item.get("caption")
        if caption is not None:
            if not isinstance(caption, str) or len(caption) > CAPTION_LIMIT:
                raise _invalid(f"caption must be a string of at most {CAPTION_LIMIT} characters")
            spec["caption"] = caption
        sha256 = item.get("sha256")
        if sha256 is not None:
            if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
                raise _invalid("sha256 must be 64 lowercase hex characters")
            spec["sha256"] = sha256
        size_bytes = item.get("size_bytes")
        if size_bytes is not None:
            if type(size_bytes) is not int or size_bytes < 0:
                raise _invalid("size_bytes must be a non-negative integer")
            spec["size_bytes"] = size_bytes
        specs.append(spec)
    return specs


def check_storage_ref(storage_ref: str, blob_store: Any) -> None:
    """Refuse a ref outside this deployment's blob scheme and bucket."""
    try:
        ref = BlobRef.parse(storage_ref)
    except ValueError as exc:
        raise _invalid("storage_ref must have the form s3://bucket/key") from exc
    scheme = getattr(blob_store, "scheme", "s3")
    bucket = getattr(blob_store, "bucket", None)
    if ref.scheme != scheme:
        raise _invalid("storage_ref uses a scheme this deployment does not store")
    if isinstance(bucket, str) and bucket:
        if not ref.key.startswith(f"{bucket}/") or ref.key == f"{bucket}/":
            raise _invalid("storage_ref names a bucket this deployment does not store")
    elif "/" not in ref.key.strip("/"):
        raise _invalid("storage_ref must have the form s3://bucket/key")


async def resolve_attachments(
    specs: Sequence[Mapping[str, Any]], blob_store: Any
) -> list[ResolvedAttachment]:
    """Fetch and verify every attachment before any send.

    Raises :class:`AttachmentError` for a missing blob or unavailable store
    (``attachment_unavailable``), a size violation (``attachment_too_large``)
    or a pinned digest that no longer matches (``attachment_digest_mismatch``).
    """
    resolved: list[ResolvedAttachment] = []
    for spec in specs:
        storage_ref = spec.get("storage_ref")
        media_type = spec["media_type"]
        if storage_ref is not None:
            if blob_store is None:
                raise AttachmentError(
                    "attachment_unavailable", "blob storage is not configured for this butler"
                )
            check_storage_ref(storage_ref, blob_store)
            try:
                data = await blob_store.get(storage_ref)
            except BlobNotFoundError as exc:
                raise AttachmentError("attachment_unavailable", "blob not found") from exc
            except AttachmentError:
                raise
            except Exception as exc:
                raise AttachmentError(
                    "attachment_unavailable", "blob storage is unreachable"
                ) from exc
            if not isinstance(data, bytes | bytearray):
                raise AttachmentError("attachment_unavailable", "blob storage returned no bytes")
            data = bytes(data)
        else:
            data = validate_inline_content(media_type, spec.get("content_text")).encode("utf-8")
        if len(data) > MAX_DOCUMENT_BYTES:
            raise AttachmentError("attachment_too_large", "attachments must be at most 50 MB")
        digest = hashlib.sha256(data).hexdigest()
        pinned = spec.get("sha256")
        if pinned is not None and pinned != digest:
            raise AttachmentError(
                "attachment_digest_mismatch", "attachment content changed after it was pinned"
            )
        pinned_size = spec.get("size_bytes")
        if pinned_size is not None and pinned_size != len(data):
            raise AttachmentError(
                "attachment_digest_mismatch", "attachment size changed after it was pinned"
            )
        resolved.append(
            ResolvedAttachment(
                filename=spec["filename"],
                media_type=media_type,
                data=data,
                sha256=digest,
                caption=spec.get("caption"),
            )
        )
    return resolved


def pin_attachments(
    specs: Sequence[Mapping[str, Any]], resolved: Sequence[ResolvedAttachment]
) -> list[dict[str, Any]]:
    """Return the specs with each verified sha256 digest and size pinned."""
    return [
        {**spec, "sha256": item.sha256, "size_bytes": item.size_bytes}
        for spec, item in zip(specs, resolved, strict=True)
    ]


def redact_attachment_content(specs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop inline text from attachment specs; refs, types and digests stay."""
    return [{key: value for key, value in spec.items() if key != "content_text"} for spec in specs]


def attachment_audit_summary(resolved: Sequence[ResolvedAttachment]) -> list[dict[str, Any]]:
    """Content-free audit view: media type, size and digest only."""
    return [
        {"media_type": item.media_type, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in resolved
    ]
