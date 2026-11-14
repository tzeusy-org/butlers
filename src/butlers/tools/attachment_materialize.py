"""Fetch a lazy Gmail attachment ref into blob storage on request (bu-jv1moa.48).

At ingest the Gmail connector records most attachments as metadata-only rows in
``attachment_refs``. This marks one row requested and waits for the connector
that owns it (only connectors hold Gmail credentials) to download the bytes
and record ``blob_ref``. The ref it returns is under the connector blob prefix,
so ``attachment_read``, ``attachment_view`` and ``get_attachment`` accept it.

Idempotent on ``(message_id, attachment_id)``: a fetched row returns its
existing blob ref with no download. A failed fetch is reported once; asking
again retries it.

The tool serves only the request it is handling (bu-jv1moa.59):
``materialize_routed_attachment`` accepts a ref only when it is one of the
lazy attachments in the calling runtime session's routing context, which the
daemon recorded from the ingested message, never from model input. Anything
else is refused before the table is read, so the refusal reveals nothing about
other accounts' refs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg

from butlers.core.resource_pairs import lazy_attachment_refs
from butlers.core.tool_call_capture import get_current_runtime_session_routing_context

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


def _routed_refs(routing_context: dict[str, Any] | None) -> set[tuple[str, str]]:
    """The lazy ``(message_id, attachment_id)`` refs of the routed request."""
    return set(lazy_attachment_refs((routing_context or {}).get("attachments")))


async def materialize_routed_attachment(
    pool: asyncpg.Pool,
    message_id: str,
    attachment_id: str,
    *,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
) -> dict[str, Any]:
    """``attachment_materialize`` for a ref of the calling session's routed request only.

    The session is the runtime session bound to this tool call; a call with no
    bound session, or one whose request has no such lazy ref, is refused.
    """
    routed = _routed_refs(get_current_runtime_session_routing_context())
    if not (
        isinstance(message_id, str)
        and isinstance(attachment_id, str)
        and (message_id, attachment_id) in routed
    ):
        return {
            "status": "refused",
            "reason": "attachment_not_in_request",
            **_ref(message_id, attachment_id),
            "hint": "Only attachments of the message this session is handling can be fetched.",
        }
    return await attachment_materialize(
        pool, message_id, attachment_id, wait_s=wait_s, poll_s=poll_s
    )


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
