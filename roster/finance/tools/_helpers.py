"""Shared helpers for finance butler tools."""

from __future__ import annotations

import json
from typing import Any

import asyncpg


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a dict, parsing JSONB strings."""
    d = dict(row)
    for key in ("metadata",):
        if key in d and isinstance(d[key], str):
            d[key] = json.loads(d[key])
    # Normalize UUID fields to strings for JSON-serialization consistency
    for key, val in d.items():
        if hasattr(val, "hex") and callable(getattr(val, "hex", None)) and not isinstance(val, str):
            # UUID type — convert to str
            try:
                import uuid as _uuid

                if isinstance(val, _uuid.UUID):
                    d[key] = str(val)
            except Exception:
                pass
    return d
