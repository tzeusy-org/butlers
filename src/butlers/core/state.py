"""Key-value state store backed by PostgreSQL JSONB.

Provides async CRUD operations on the ``state`` table. Each butler owns
its own database, so keys are globally unique within a butler.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


def decode_jsonb(val: Any) -> Any:
    """Decode a JSONB value, handling potential double-encoding.

    asyncpg returns JSONB columns as Python strings (text representation)
    when no custom codec is registered.  Normally one ``json.loads`` pass
    suffices.  If the stored JSONB was accidentally double-encoded (a JSON
    string containing JSON text), a second pass is needed.
    """
    if not isinstance(val, str):
        return val
    val = json.loads(val)
    if isinstance(val, str):
        logger.warning("Double-encoded JSONB detected — applying second decode pass")
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, ValueError):
            pass
    return val


class CASConflictError(Exception):
    """Raised by state_compare_and_set when the expected version does not match.

    Attributes:
        key: The state key involved in the conflict.
        expected_version: The version the caller expected.
        actual_version: The version found in the database (or None if key absent).
    """

    def __init__(
        self,
        key: str,
        expected_version: int,
        actual_version: int | None,
    ) -> None:
        self.key = key
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"CAS conflict on key {key!r}: expected version {expected_version}, "
            f"got {actual_version!r}"
        )


async def state_get(pool: asyncpg.Pool, key: str) -> Any | None:
    """Return the JSONB value for *key*, or ``None`` if the key does not exist."""
    row = await pool.fetchval(
        "SELECT value FROM state WHERE key = $1",
        key,
    )
    if row is None:
        return None
    return decode_jsonb(row)


async def state_set(pool: asyncpg.Pool, key: str, value: Any) -> int:
    """Upsert *key* with *value* (any JSON-serialisable type).

    If the key already exists its value, ``updated_at`` timestamp, and
    ``version`` are updated; otherwise a new row is inserted with version=1.

    Returns:
        The new version number for the row after the upsert.
    """
    json_value = json.dumps(value)
    new_version: int = await pool.fetchval(
        """
        INSERT INTO state (key, value, updated_at, version)
        VALUES ($1, $2::jsonb, now(), 1)
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value,
                updated_at = now(),
                version = state.version + 1
        RETURNING version
        """,
        key,
        json_value,
    )
    return new_version


async def state_compare_and_set(
    pool: asyncpg.Pool,
    key: str,
    expected_version: int,
    new_value: Any,
) -> int:
    """Conditionally update *key* only if the current version matches *expected_version*.

    This provides safe concurrent KV writes. Two sessions that read the same
    key, modify it, and write back will have exactly one succeed: the second
    write will see a higher version and raise :exc:`CASConflictError`.

    Args:
        pool: asyncpg connection pool.
        key: The state key to update.
        expected_version: The version the caller read the key at. The update
            only proceeds if the stored version equals this value.
        new_value: The new JSON-serialisable value to store.

    Returns:
        The new version number after a successful update.

    Raises:
        CASConflictError: If the stored version does not match *expected_version*,
            or the key does not exist.
    """
    json_value = json.dumps(new_value)
    row = await pool.fetchrow(
        """
        UPDATE state
        SET value = $3::jsonb,
            updated_at = now(),
            version = version + 1
        WHERE key = $1 AND version = $2
        RETURNING version
        """,
        key,
        expected_version,
        json_value,
    )
    if row is not None:
        return row["version"]

    # The update matched nothing. Determine whether it's a missing key or a
    # version mismatch so we can surface a helpful error.
    actual = await pool.fetchval(
        "SELECT version FROM state WHERE key = $1",
        key,
    )
    raise CASConflictError(
        key=key,
        expected_version=expected_version,
        actual_version=actual,
    )


async def state_delete(pool: asyncpg.Pool, key: str) -> None:
    """Delete *key* from the state store.  No-op if the key does not exist."""
    await pool.execute("DELETE FROM state WHERE key = $1", key)


async def state_list(
    pool: asyncpg.Pool,
    prefix: str | None = None,
    keys_only: bool = True,
) -> list[str] | list[dict[str, Any]]:
    """Return state entries, optionally filtered by key prefix.

    Args:
        pool: asyncpg connection pool.
        prefix: If given, only keys starting with this string are returned
            (SQL ``LIKE prefix%``).
        keys_only: If True (default), return a list of key strings.
            If False, return a list of ``{"key": ..., "value": ...}`` dicts
            for backward compatibility.

    Returns:
        By default (keys_only=True): A list of key strings ordered by key.
        If keys_only=False: A list of ``{"key": ..., "value": ...}`` dicts.
    """
    if prefix is not None:
        rows = await pool.fetch(
            "SELECT key, value FROM state WHERE key LIKE $1 ORDER BY key",
            f"{prefix}%",
        )
    else:
        rows = await pool.fetch("SELECT key, value FROM state ORDER BY key")

    if keys_only:
        return [row["key"] for row in rows]

    results: list[dict[str, Any]] = []
    for row in rows:
        val = decode_jsonb(row["value"])
        results.append({"key": row["key"], "value": val})
    return results
