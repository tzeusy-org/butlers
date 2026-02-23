"""Shared helpers for travel butler tools."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

import asyncpg


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict with UUID/datetime/date serialization.

    - UUID values are converted to strings.
    - datetime values are ISO-formatted strings.
    - date values are ISO-formatted strings.
    - JSONB fields (metadata): asyncpg normally deserializes JSONB to Python
      dicts on read, but the ``isinstance(val, str)`` guard is kept as a
      defensive fallback for environments where the JSONB codec is not
      registered.
    """
    d = dict(row)
    for key, val in d.items():
        if isinstance(val, uuid.UUID):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
        elif isinstance(val, date):
            d[key] = val.isoformat()
        elif isinstance(val, str) and key == "metadata":
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
    return d
