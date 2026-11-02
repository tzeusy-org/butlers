"""Owner-logged measurements dominate device readings on the same owner-local day.

A connected scale and a hand-logged weight can both describe one morning.  Wherever Health
reports a latest or per-day value, the owner's own entry wins for its owner-local calendar day
and the device reading stays stored (never deleted) but is left out of that report
(bu-q7vx1q.35).

A row is **owner-logged** when ``metadata.source`` is absent or ``owner_log`` (every manual
``measurement_log`` write, and rows that predate the source stamp).  Any other stamped source
(``google_health``, ``home_assistant``) is a **device** row.

The helpers are additive and lazy: they resolve the owner timezone and issue extra queries only
when a device row is actually present, so result sets without device rows are untouched.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from butlers.core.general_settings import resolve_general_timezone

logger = logging.getLogger(__name__)

OWNER_SOURCE = "owner_log"

# SQL predicate for an owner-logged row (``metadata`` is the facts jsonb column).
_OWNER_ROW_SQL = f"COALESCE(metadata->>'source', '{OWNER_SOURCE}') = '{OWNER_SOURCE}'"


def _field(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _metadata(row: Any) -> dict[str, Any]:
    meta = _field(row, "metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return {}
    return meta if isinstance(meta, dict) else {}


def is_device_row(row: Any) -> bool:
    """True when the row carries a stamped source other than the owner's own log."""
    source = _metadata(row).get("source")
    return isinstance(source, str) and bool(source) and source != OWNER_SOURCE


async def owner_zone(pool: Any) -> ZoneInfo:
    """The owner's configured timezone; UTC when unreadable or invalid."""
    try:
        return ZoneInfo(await resolve_general_timezone(pool))
    except Exception:  # noqa: BLE001
        logger.warning("owner_dominance: owner timezone unavailable; using UTC", exc_info=True)
        return ZoneInfo("UTC")


def local_day(moment: datetime, zone: ZoneInfo) -> date:
    return moment.astimezone(zone).date()


def day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Half-open ``[start, end)`` of an owner-local calendar day (DST-safe)."""
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


async def owner_days(pool: Any, predicate: str, zone: ZoneInfo, days: Iterable[date]) -> set[date]:
    """The subset of ``days`` on which the owner logged ``predicate`` themselves."""
    wanted = sorted(set(days))
    if not wanted:
        return set()
    window_start = day_bounds(wanted[0], zone)[0]
    window_end = day_bounds(wanted[-1], zone)[1]
    rows = await pool.fetch(
        "SELECT valid_at FROM facts"
        " WHERE predicate = $1 AND scope = 'health' AND validity = 'active'"
        f" AND {_OWNER_ROW_SQL} AND valid_at >= $2 AND valid_at < $3",
        predicate,
        window_start,
        window_end,
    )
    found = {local_day(r["valid_at"], zone) for r in rows if r["valid_at"] is not None}
    return found & set(wanted)


async def drop_dominated(pool: Any, predicate: str, rows: list[Any]) -> list[Any]:
    """Remove device rows that share an owner-local day with an owner-logged row.

    ``rows`` keeps its order.  Device rows are only dropped, never altered or deleted from
    storage.
    """
    device = [r for r in rows if _field(r, "valid_at") is not None and is_device_row(r)]
    if not device:
        return rows
    zone = await owner_zone(pool)
    dominated = await owner_days(
        pool, predicate, zone, (local_day(r["valid_at"], zone) for r in device)
    )
    if not dominated:
        return rows
    return [
        r
        for r in rows
        if not (
            _field(r, "valid_at") is not None
            and is_device_row(r)
            and local_day(r["valid_at"], zone) in dominated
        )
    ]


async def owner_row_on_same_day(
    pool: Any, predicate: str, device_row: Any, columns: str = "valid_at, metadata"
) -> Any | None:
    """The latest owner-logged row on the device row's owner-local day, if any."""
    zone = await owner_zone(pool)
    start, end = day_bounds(local_day(device_row["valid_at"], zone), zone)
    return await pool.fetchrow(
        f"SELECT {columns} FROM facts"
        " WHERE predicate = $1 AND scope = 'health' AND validity = 'active'"
        f" AND {_OWNER_ROW_SQL} AND valid_at >= $2 AND valid_at < $3"
        " ORDER BY valid_at DESC LIMIT 1",
        predicate,
        start,
        end,
    )


async def dominant_latest(
    pool: Any, predicate: str, latest: Any, columns: str = "valid_at, metadata"
) -> Any:
    """Return the owner-logged row that outranks a device ``latest`` row, else ``latest``."""
    if latest is None or _field(latest, "valid_at") is None or not is_device_row(latest):
        return latest
    owner = await owner_row_on_same_day(pool, predicate, latest, columns)
    return owner if owner is not None else latest
