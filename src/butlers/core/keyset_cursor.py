"""A tiny opaque (created_at, id) keyset cursor, shared by capture_search and
the /api/captures router so both page the same way over different tables.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime


def encode_cursor(created_at: datetime, row_id: object) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at_str, row_id = raw.split("|", 1)
        return datetime.fromisoformat(created_at_str), row_id
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"Invalid keyset cursor: {cursor!r}") from exc
