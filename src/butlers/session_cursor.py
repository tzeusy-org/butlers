"""Shared legacy session keyset cursor codec.

This preserves the existing sessions cursor bytes and ValueError contract for
both the API's accepted sessions exception and the Concierge fleet-view tools.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID


def encode_session_cursor(started_at: datetime, row_id: UUID | str) -> str:
    """Encode a keyset position into an opaque cursor string.

    The cursor encodes the ``(started_at, id)`` tuple of the last row returned.
    It is base64url-encoded JSON so it is safe to use as a query parameter.

    Parameters
    ----------
    started_at:
        Timestamp of the last row (``started_at`` column, tz-aware).
    row_id:
        Primary key (UUID) of the last row.
    """
    payload = {"t": started_at.isoformat(), "id": str(row_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def decode_session_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque session cursor back to ``(started_at, id)``.

    Parameters
    ----------
    cursor:
        Opaque cursor string as returned by :func:`encode_session_cursor`.

    Returns
    -------
    tuple[datetime, UUID]
        The ``(started_at, id)`` keyset position.

    Raises
    ------
    ValueError
        If the cursor is malformed or cannot be decoded.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        payload = json.loads(raw)
        started_at = datetime.fromisoformat(payload["t"])
        row_id = UUID(str(payload["id"]))
    except (KeyError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid session cursor: {exc}") from exc
    return started_at, row_id
