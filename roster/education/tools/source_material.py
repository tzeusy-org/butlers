"""Education source-material registry backed by the butler state store.

The registry intentionally stores metadata only.  Source contents are neither
retrieved nor parsed, and deleting a record does not inspect or rewrite mind
map node metadata that may still refer to its source ID.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.core.state import state_delete, state_list, state_set

SOURCE_KEY_PREFIX = "education/source/"
_SOURCE_TYPES = frozenset({"article", "book", "documentation", "paper"})


def _normalise_required(value: Any, field: str) -> str:
    """Return a non-empty string field or raise a caller-actionable error."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required and must be a non-empty string")
    return value.strip()


def _normalise_authors(authors: list[str] | tuple[str, ...] | str | None) -> list[str]:
    """Normalise the author(s) input into a JSON-friendly list of strings."""
    if authors is None:
        return []
    if isinstance(authors, str):
        authors = [authors]
    if not isinstance(authors, (list, tuple)):
        raise ValueError("authors must be a string or a list of strings")
    if any(not isinstance(author, str) or not author.strip() for author in authors):
        raise ValueError("authors must contain only non-empty strings")
    return [author.strip() for author in authors]


def _source_key(source_id: str) -> str:
    """Return the state-store key for a source ID."""
    source_id = _normalise_required(source_id, "source_id")
    return f"{SOURCE_KEY_PREFIX}{source_id}"


async def source_material_register(
    pool: asyncpg.Pool,
    title: str,
    type: str,
    authors: list[str] | tuple[str, ...] | str | None = None,
    toc: Any | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Register source metadata and return it with its generated source ID.

    ``type`` is restricted to the source vocabulary used by the capability
    spec.  ``toc`` and ``url`` are optional metadata and are passed through
    unchanged when supplied.  No network operation is performed.
    """
    source_title = _normalise_required(title, "title")
    source_type = _normalise_required(type, "type").lower()
    if source_type not in _SOURCE_TYPES:
        allowed = ", ".join(sorted(_SOURCE_TYPES))
        raise ValueError(f"type must be one of: {allowed}")

    source_id = str(uuid.uuid4())
    registered_at = datetime.now(UTC).isoformat()
    record: dict[str, Any] = {
        "title": source_title,
        "authors": _normalise_authors(authors),
        "type": source_type,
        "registered_at": registered_at,
    }
    if toc is not None:
        record["toc"] = toc
    if url is not None:
        record["url"] = url

    await state_set(pool, _source_key(source_id), record)
    return {"source_id": source_id, **record}


async def source_material_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return every currently registered source, ordered by state-store key."""
    entries = await state_list(pool, prefix=SOURCE_KEY_PREFIX, keys_only=False)
    sources: list[dict[str, Any]] = []
    for entry in entries:
        key = entry.get("key")
        value = entry.get("value")
        if not isinstance(key, str) or not key.startswith(SOURCE_KEY_PREFIX):
            continue
        source_id = key[len(SOURCE_KEY_PREFIX) :]
        if not source_id or not isinstance(value, dict):
            continue
        sources.append({"source_id": source_id, **value})
    return sources


async def source_material_remove(pool: asyncpg.Pool, source_id: str) -> dict[str, str]:
    """Remove a source metadata record without touching any node references.

    State deletion is intentionally idempotent.  A node that still carries the
    removed ID retains its dangling ``source_refs`` entry for the dashboard to
    render as an unregistered source.
    """
    await state_delete(pool, _source_key(source_id))
    return {"source_id": source_id, "status": "removed"}
