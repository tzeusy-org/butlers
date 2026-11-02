"""Fetch a lazy Gmail attachment ref into blob storage on request (bu-jv1moa.48).

At ingest the Gmail connector records most attachments as metadata-only rows in
``attachment_refs``. This marks one row requested and waits for the connector
that owns it (only connectors hold Gmail credentials) to download the bytes
and record ``blob_ref``. The ref it returns is under the connector blob prefix,
so ``attachment_read``, ``attachment_view`` and ``get_attachment`` accept it.

Idempotent on ``(message_id, attachment_id)``: a fetched row returns its
existing blob ref with no download. A failed fetch is reported once; asking
again retries it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg

DEFAULT_WAIT_S = 20.0
DEFAULT_POLL_S = 0.5
_MAX_ID_CHARS = 512

_SELECT = """
    SELECT fetched, blob_ref, fetch_error, filename, media_type, size_bytes
    FROM attachment_refs
    WHERE message_id = $1 AND attachment_id = $2
"""

# A new request, or a retry of a failed one. A request already waiting or
# claimed is left alone so its claim is not reset.
_REQUEST = """
    UPDATE attachment_refs
    SET fetch_requested_at = now(), fetch_claimed_at = NULL, fetch_error = NULL
    WHERE message_id = $1 AND attachment_id = $2 AND NOT fetched
      AND (fetch_requested_at IS NULL OR fetch_error IS NOT NULL)
"""


def _ref(message_id: str, attachment_id: str) -> dict[str, str]:
    return {"message_id": message_id, "attachment_id": attachment_id}


def _ready(row: asyncpg.Record) -> dict[str, Any]:
    # The filename is the sender's: untrusted, like the bytes it names.
    return {
        "status": "ready",
        "storage_ref": row["blob_ref"],
        "media_type": row["media_type"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "content_trust": "untrusted",
    }


async def attachment_materialize(
    pool: asyncpg.Pool,
    message_id: str,
    attachment_id: str,
    *,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
) -> dict[str, Any]:
    """Return a ready blob ref for a lazy attachment, or a typed refusal or pending result."""
    ids = (message_id, attachment_id)
    if not all(isinstance(value, str) and 0 < len(value) <= _MAX_ID_CHARS for value in ids):
        return {"status": "refused", "reason": "invalid_reference"}

    row = await pool.fetchrow(_SELECT, message_id, attachment_id)
    if row is None:
        return {"status": "refused", "reason": "attachment_not_found", **_ref(*ids)}
    if row["fetched"] and row["blob_ref"]:
        return _ready(row)

    await pool.execute(_REQUEST, message_id, attachment_id)
    deadline = time.monotonic() + max(wait_s, 0.0)
    while True:
        row = await pool.fetchrow(_SELECT, message_id, attachment_id)
        if row is not None and row["fetched"] and row["blob_ref"]:
            return _ready(row)
        if row is not None and row["fetch_error"] == "too_large":
            return {
                "status": "refused",
                "reason": "attachment_too_large",
                "size_bytes": row["size_bytes"],
                "hint": "It is over the 25 MB attachment cap and will not be fetched.",
                **_ref(*ids),
            }
        if row is not None and row["fetch_error"]:
            return {
                "status": "refused",
                "reason": "fetch_failed",
                "hint": "The mail provider fetch failed; calling again retries it.",
                **_ref(*ids),
            }
        if time.monotonic() >= deadline:
            return {
                "status": "pending",
                "retry_after_s": 10,
                "hint": "The mail connector has not fetched it yet; call again shortly.",
                **_ref(*ids),
            }
        await asyncio.sleep(poll_s)
