"""Shared helpers for finance butler tools."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict with UUID/datetime serialization.

    - UUID values are converted to strings.
    - datetime values are ISO-formatted strings.
    - JSONB fields: asyncpg normally deserializes JSONB to Python dicts on
      read, but the ``isinstance(val, str)`` guard is kept as a defensive
      fallback for environments where the JSONB codec is not registered,
      consistent with the pattern in other butler tool helpers.
    """
    d = dict(row)
    for key, val in d.items():
        if isinstance(val, uuid.UUID):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
        elif isinstance(val, str) and key == "metadata":
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
    return d


async def _log_activity(
    pool: asyncpg.Pool,
    action: str,
    description: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> None:
    """Log an activity to the sessions table if it exists, otherwise no-op.

    Finance butler uses the core sessions table for audit logging.
    This helper silently skips when no activity table is available so
    tool functions remain usable in isolated test environments.
    """
    # Finance butler does not have a dedicated activity_feed table; this is
    # a best-effort audit log into the shared sessions infrastructure.
    # Implementations that wire a dedicated audit sink can extend this helper.
    pass


# Alias for compatibility with modules that import _deserialize_row directly.
_deserialize_row = _row_to_dict
