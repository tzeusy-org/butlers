"""State-store helpers for metric definition persistence.

Metric definitions are stored as individual JSON blobs in the butler's
existing state store under keys ``metrics_catalogue:<metric_name>``.
This avoids any Alembic migration — the state store is already present
in every butler schema.

All functions accept an asyncpg Pool directly (not a Database wrapper)
so they compose cleanly with the rest of the state-store API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from butlers.core.state import state_list, state_set

if TYPE_CHECKING:
    import asyncpg

_KEY_PREFIX = "metrics_catalogue:"


def _make_key(name: str) -> str:
    """Return the state store key for a metric definition."""
    return f"{_KEY_PREFIX}{name}"


async def save_definition(pool: asyncpg.Pool, name: str, defn: dict[str, Any]) -> None:
    """Persist a metric definition to the state store.

    Overwrites any existing entry with the same name.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the butler's schema.
    name:
        Bare metric name (without the ``metrics_catalogue:`` prefix).
    defn:
        JSON-serialisable dict describing the metric (type, help, labels, etc.).
    """
    await state_set(pool, _make_key(name), defn)


async def load_all_definitions(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """Return all persisted metric definitions.

    Scans all state store keys with the ``metrics_catalogue:`` prefix and
    returns their decoded values as a list of dicts, in key-sorted order.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the butler's schema.

    Returns
    -------
    list[dict]
        Each element is a definition dict as originally passed to
        :func:`save_definition`.  Returns an empty list if no definitions
        have been saved yet.
    """
    rows: list[dict[str, Any]] = await state_list(pool, prefix=_KEY_PREFIX, keys_only=False)
    return [row["value"] for row in rows]


async def count_definitions(pool: asyncpg.Pool) -> int:
    """Return the number of metric definitions currently persisted.

    Used by ``metrics_define`` to enforce the per-butler hard cap of 1,000
    metrics before creating a new instrument.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the butler's schema.

    Returns
    -------
    int
        Count of ``metrics_catalogue:*`` keys in the state store.
    """
    keys: list[str] = await state_list(pool, prefix=_KEY_PREFIX, keys_only=True)
    return len(keys)
