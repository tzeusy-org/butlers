"""Shared helpers for health butler tools."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime, time
from typing import Any

import asyncpg

from butlers.core.owner import fetch_owner_entity_id as _fetch_owner_entity_id


async def _get_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's UUID from ``public.entities``.

    Delegates to the shared ``butlers.core.owner.fetch_owner_entity_id`` helper.
    Kept here so that health-tool modules can import it from a single intra-package
    location without taking a direct dependency on the core package hierarchy.
    """
    return await _fetch_owner_entity_id(pool)


def _row_to_dict(row: Mapping[str, Any] | asyncpg.Record) -> dict[str, Any]:
    """Convert a Health row to a dict, parsing JSONB strings.

    Health fact metadata treats a falsey stored value as an empty object while
    nonempty JSON strings remain strict: malformed data raises on read.
    """
    d = dict(row)
    for key in ("value", "nutrition", "tags", "schedule", "metadata"):
        if key not in d:
            continue
        if key == "metadata" and not d[key]:
            d[key] = {}
        elif isinstance(d[key], str):
            d[key] = json.loads(d[key])
    return d


def _normalize_end_date(dt: datetime) -> datetime:
    """Extend a midnight datetime to end-of-day.

    When an LLM passes a date-only string like "2026-03-18", it gets parsed as
    midnight (00:00:00). Using that as an upper bound (eaten_at <= midnight)
    excludes all events that actually occurred during the day. This helper
    extends midnight to 23:59:59.999999 so the full day is included.
    """
    if dt.time() == time(0):
        return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt
