"""Seasonal awareness — period definitions, active-period query, and MCP tools.

This module provides:

- ``validate_month_day``: validate that a month/day combination is a real
  calendar date (rejects Feb 30, Apr 31, etc.)
- ``get_active_seasons``: query ``seasonal_periods`` for periods whose date
  range contains today, with year-boundary wrapping support
- ``SEASONAL_PRESETS``: dict of built-in preset definitions
- CRUD helpers: ``seasonal_period_create``, ``seasonal_period_update``,
  ``seasonal_period_list``, ``seasonal_period_delete``
- ``register_seasonal_tools``: registers all five MCP tools on a FastMCP
  server instance
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Period type enum values
# ---------------------------------------------------------------------------

_ALLOWED_PERIOD_TYPES = {"annual", "academic", "fiscal", "custom"}

# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

#: Built-in seasonal period presets.
#:
#: Keys are preset names; values are dicts ready to pass to
#: ``seasonal_period_create`` (minus ``butler_name``).
SEASONAL_PRESETS: dict[str, dict[str, Any]] = {
    "us-tax-season": {
        "name": "us-tax-season",
        "period_type": "fiscal",
        "start_month": 1,
        "start_day": 1,
        "end_month": 4,
        "end_day": 15,
        "metadata": {
            "context_hint": (
                "US tax filing season. Prioritize financial document organization, "
                "expense tracking, and tax-related reminders."
            ),
        },
    },
    "year-end-holidays": {
        "name": "year-end-holidays",
        "period_type": "annual",
        "start_month": 12,
        "start_day": 15,
        "end_month": 1,
        "end_day": 5,
        "metadata": {
            "context_hint": (
                "Year-end holiday season. Expect reduced availability, "
                "travel, and family commitments."
            ),
        },
    },
    "back-to-school": {
        "name": "back-to-school",
        "period_type": "academic",
        "start_month": 8,
        "start_day": 1,
        "end_month": 9,
        "end_day": 15,
        "metadata": {
            "context_hint": (
                "Back-to-school season. Prioritize school supply organization, "
                "schedule setup, and routine establishment."
            ),
        },
    },
    "spring-semester": {
        "name": "spring-semester",
        "period_type": "academic",
        "start_month": 1,
        "start_day": 15,
        "end_month": 5,
        "end_day": 15,
        "metadata": {
            "context_hint": (
                "Spring academic semester. Expect academic deadlines, "
                "exams, and end-of-term reviews."
            ),
        },
    },
    "fall-semester": {
        "name": "fall-semester",
        "period_type": "academic",
        "start_month": 8,
        "start_day": 25,
        "end_month": 12,
        "end_day": 15,
        "metadata": {
            "context_hint": (
                "Fall academic semester. Expect academic deadlines, "
                "exams, and end-of-term reviews."
            ),
        },
    },
}

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

# Days in each month for a non-leap year.  February is treated as having 28
# days for structural validation purposes — there is no practical need to
# accept Feb 29 as a recurring annual boundary because Feb 29 only exists in
# leap years.  The spec requirement is to reject clearly invalid combos like
# Feb 30 or Apr 31.
_DAYS_IN_MONTH = {
    1: 31,
    2: 28,
    3: 31,
    4: 30,
    5: 31,
    6: 30,
    7: 31,
    8: 31,
    9: 30,
    10: 31,
    11: 30,
    12: 31,
}

_MONTH_NAMES = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def validate_month_day(month: int, day: int) -> None:
    """Raise ``ValueError`` if *month*/*day* is not a valid calendar date.

    Uses a non-leap-year calendar so that Feb 29 is also rejected (annual
    boundaries should use stable dates).

    Args:
        month: Month number (1-12).
        day: Day number (1-31).

    Raises:
        ValueError: If the month is out of range or the day exceeds the
            maximum for that month.
    """
    if month < 1 or month > 12:
        raise ValueError(f"month must be between 1 and 12, got {month!r}")
    if day < 1:
        raise ValueError(f"day must be at least 1, got {day!r}")
    max_day = _DAYS_IN_MONTH[month]
    if day > max_day:
        month_name = _MONTH_NAMES[month]
        raise ValueError(
            f"{month_name} {day} is not a valid date "
            f"({month_name} has at most {max_day} days)"
        )


def _is_date_in_range(
    today_month: int,
    today_day: int,
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
) -> bool:
    """Return True if today falls within [start, end] (inclusive), with wrapping.

    The comparison is purely on (month, day) tuples and ignores year.  For
    periods where *start > end* (year-boundary wrapping, e.g., Dec 15 – Jan 5),
    the period is considered active when today is on/after the start OR
    on/before the end.

    Args:
        today_month: Current month (1-12).
        today_day: Current day (1-31).
        start_month: Period start month.
        start_day: Period start day.
        end_month: Period end month.
        end_day: Period end day.

    Returns:
        True if today is within the period.
    """
    today = (today_month, today_day)
    start = (start_month, start_day)
    end = (end_month, end_day)

    if start <= end:
        # Normal (non-wrapping) period: start_date <= end_date within same year.
        return start <= today <= end
    else:
        # Year-boundary wrapping: e.g., Nov 15 – Jan 10.
        # Active when today >= start OR today <= end.
        return today >= start or today <= end


# ---------------------------------------------------------------------------
# Active-season query
# ---------------------------------------------------------------------------


async def get_active_seasons(
    pool: asyncpg.Pool,
    butler_name: str,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Return all enabled seasonal periods that are currently active.

    Queries ``seasonal_periods`` for the given butler and filters to periods
    whose month/day range contains *today*, accounting for year-boundary
    wrapping (e.g., winter holidays that span Dec→Jan).

    Args:
        pool: asyncpg connection pool.
        butler_name: Butler instance name to filter periods.
        today: Date to evaluate against.  Defaults to ``date.today()`` in UTC.

    Returns:
        List of period dicts for active periods.  Each dict contains all
        columns from the ``seasonal_periods`` table.
    """
    if today is None:
        today = datetime.now(UTC).date()

    rows = await pool.fetch(
        """
        SELECT id, name, period_type, start_month, start_day,
               end_month, end_day, timezone, metadata, butler_name,
               enabled, created_at, updated_at
        FROM seasonal_periods
        WHERE butler_name = $1 AND enabled = true
        ORDER BY name
        """,
        butler_name,
    )

    active: list[dict[str, Any]] = []
    for row in rows:
        if _is_date_in_range(
            today.month,
            today.day,
            row["start_month"],
            row["start_day"],
            row["end_month"],
            row["end_day"],
        ):
            period = _row_to_dict(row)
            active.append(period)

    return active


# ---------------------------------------------------------------------------
# Row serialisation helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record from seasonal_periods to a plain dict."""
    d = dict(row)
    # metadata may come back as a dict (asyncpg decodes JSONB) or a JSON string
    if isinstance(d.get("metadata"), str):
        try:
            d["metadata"] = json.loads(d["metadata"])
        except (json.JSONDecodeError, TypeError):
            pass
    # Normalise UUID to string
    if isinstance(d.get("id"), uuid.UUID):
        d["id"] = str(d["id"])
    return d


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------


async def seasonal_period_create(
    pool: asyncpg.Pool,
    butler_name: str,
    name: str,
    period_type: str,
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
    *,
    timezone: str = "UTC",
    metadata: dict[str, Any] | None = None,
    enabled: bool = True,
) -> uuid.UUID:
    """Create a new seasonal period.

    Args:
        pool: asyncpg connection pool.
        butler_name: Owning butler instance.
        name: Unique (per-butler) period name.
        period_type: One of ``annual``, ``academic``, ``fiscal``, ``custom``.
        start_month: Period start month (1-12).
        start_day: Period start day (1-31).
        end_month: Period end month (1-12).
        end_day: Period end day (1-31).
        timezone: IANA timezone string for the period (default ``UTC``).
        metadata: Optional JSONB metadata dict.
        enabled: Whether the period is active (default ``True``).

    Returns:
        UUID of the newly created period.

    Raises:
        ValueError: If the name already exists for this butler, the
            period_type is invalid, or a month/day combination is invalid.
    """
    # Validate
    name = name.strip()
    if not name:
        raise ValueError("name must be a non-empty string")

    period_type = period_type.strip().lower()
    if period_type not in _ALLOWED_PERIOD_TYPES:
        raise ValueError(
            f"period_type must be one of {sorted(_ALLOWED_PERIOD_TYPES)!r}, got {period_type!r}"
        )

    validate_month_day(start_month, start_day)
    validate_month_day(end_month, end_day)

    metadata_json = json.dumps(metadata) if metadata is not None else None

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO seasonal_periods (
                name, period_type, start_month, start_day,
                end_month, end_day, timezone, metadata, butler_name, enabled
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10)
            RETURNING id
            """,
            name,
            period_type,
            start_month,
            start_day,
            end_month,
            end_day,
            timezone,
            metadata_json,
            butler_name,
            enabled,
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(
            f"A seasonal period named {name!r} already exists for butler {butler_name!r}"
        )

    return row["id"]


async def seasonal_period_update(
    pool: asyncpg.Pool,
    butler_name: str,
    period_id: str | uuid.UUID,
    *,
    name: str | None = None,
    period_type: str | None = None,
    start_month: int | None = None,
    start_day: int | None = None,
    end_month: int | None = None,
    end_day: int | None = None,
    timezone: str | None = None,
    metadata: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> bool:
    """Update fields of an existing seasonal period.

    Only non-``None`` arguments are updated.  Month/day combinations are
    validated against the final state (existing values are used for fields
    not supplied).

    Args:
        pool: asyncpg connection pool.
        butler_name: Owning butler instance (used to scope the lookup).
        period_id: UUID of the period to update.
        name: New name (validated for uniqueness).
        period_type: New period type.
        start_month: New start month.
        start_day: New start day.
        end_month: New end month.
        end_day: New end day.
        timezone: New timezone string.
        metadata: New metadata dict (replaces existing).
        enabled: New enabled flag.

    Returns:
        ``True`` if the period was found and updated, ``False`` if not found.

    Raises:
        ValueError: If validation fails or the new name conflicts.
    """
    # Fetch existing row to validate the final month/day state.
    existing = await pool.fetchrow(
        """
        SELECT id, name, period_type, start_month, start_day,
               end_month, end_day, timezone, metadata, enabled
        FROM seasonal_periods
        WHERE id = $1 AND butler_name = $2
        """,
        str(period_id),
        butler_name,
    )
    if existing is None:
        return False

    # Merge
    final_start_month = start_month if start_month is not None else existing["start_month"]
    final_start_day = start_day if start_day is not None else existing["start_day"]
    final_end_month = end_month if end_month is not None else existing["end_month"]
    final_end_day = end_day if end_day is not None else existing["end_day"]

    # Validate final state
    validate_month_day(final_start_month, final_start_day)
    validate_month_day(final_end_month, final_end_day)

    if period_type is not None:
        period_type = period_type.strip().lower()
        if period_type not in _ALLOWED_PERIOD_TYPES:
            raise ValueError(
                f"period_type must be one of {sorted(_ALLOWED_PERIOD_TYPES)!r}, "
                f"got {period_type!r}"
            )

    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("name must be a non-empty string")

    # Build update SET clause dynamically
    updates: list[str] = []
    params: list[Any] = []
    param_idx = 1  # PostgreSQL uses $1, $2, …

    def _add(col: str, val: Any) -> None:
        nonlocal param_idx
        updates.append(f"{col} = ${param_idx}")
        params.append(val)
        param_idx += 1

    if name is not None:
        _add("name", name)
    if period_type is not None:
        _add("period_type", period_type)
    if start_month is not None:
        _add("start_month", final_start_month)
    if start_day is not None:
        _add("start_day", final_start_day)
    if end_month is not None:
        _add("end_month", final_end_month)
    if end_day is not None:
        _add("end_day", final_end_day)
    if timezone is not None:
        _add("timezone", timezone)
    if metadata is not None:
        _add("metadata", json.dumps(metadata))
    if enabled is not None:
        _add("enabled", enabled)
    _add("updated_at", datetime.now(UTC))

    if not updates:
        return True  # No-op — nothing to update

    set_clause = ", ".join(updates)
    params.extend([str(period_id), butler_name])
    id_param = param_idx
    butler_param = param_idx + 1

    sql = f"""
        UPDATE seasonal_periods
        SET {set_clause}
        WHERE id = ${id_param} AND butler_name = ${butler_param}
    """

    try:
        result = await pool.execute(sql, *params)
    except asyncpg.UniqueViolationError:
        raise ValueError(
            f"A seasonal period named {name!r} already exists for butler {butler_name!r}"
        )

    # asyncpg returns e.g. 'UPDATE 1'
    rows_affected = int(result.split()[-1])
    return rows_affected > 0


async def seasonal_period_list(
    pool: asyncpg.Pool,
    butler_name: str,
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Return all seasonal periods for a butler with their current active status.

    Args:
        pool: asyncpg connection pool.
        butler_name: Butler instance name.
        today: Date to evaluate active status against.  Defaults to today UTC.

    Returns:
        List of period dicts.  Each dict includes an ``is_active`` boolean
        field indicating whether the period is currently active.
    """
    if today is None:
        today = datetime.now(UTC).date()

    rows = await pool.fetch(
        """
        SELECT id, name, period_type, start_month, start_day,
               end_month, end_day, timezone, metadata, butler_name,
               enabled, created_at, updated_at
        FROM seasonal_periods
        WHERE butler_name = $1
        ORDER BY name
        """,
        butler_name,
    )

    result: list[dict[str, Any]] = []
    for row in rows:
        period = _row_to_dict(row)
        period["is_active"] = (
            row["enabled"]
            and _is_date_in_range(
                today.month,
                today.day,
                row["start_month"],
                row["start_day"],
                row["end_month"],
                row["end_day"],
            )
        )
        result.append(period)

    return result


async def seasonal_period_delete(
    pool: asyncpg.Pool,
    butler_name: str,
    period_id: str | uuid.UUID,
) -> bool:
    """Delete a seasonal period by ID.

    Args:
        pool: asyncpg connection pool.
        butler_name: Owning butler instance (used to scope the delete).
        period_id: UUID of the period to delete.

    Returns:
        ``True`` if the period was found and deleted, ``False`` if not found.
    """
    result = await pool.execute(
        """
        DELETE FROM seasonal_periods
        WHERE id = $1 AND butler_name = $2
        """,
        str(period_id),
        butler_name,
    )
    rows_affected = int(result.split()[-1])
    return rows_affected > 0


async def seasonal_period_create_preset(
    pool: asyncpg.Pool,
    butler_name: str,
    preset: str,
    *,
    timezone: str = "UTC",
) -> uuid.UUID:
    """Create a seasonal period from a built-in preset.

    Args:
        pool: asyncpg connection pool.
        butler_name: Owning butler instance.
        preset: Preset name.  Must be one of the keys in ``SEASONAL_PRESETS``.
        timezone: IANA timezone string (default ``UTC``).

    Returns:
        UUID of the newly created period.

    Raises:
        ValueError: If the preset name is not recognised or the period already
            exists for this butler.
    """
    preset = preset.strip()
    if preset not in SEASONAL_PRESETS:
        available = sorted(SEASONAL_PRESETS.keys())
        raise ValueError(
            f"Unknown preset {preset!r}. Available presets: {available!r}"
        )

    definition = SEASONAL_PRESETS[preset]
    return await seasonal_period_create(
        pool,
        butler_name,
        name=definition["name"],
        period_type=definition["period_type"],
        start_month=definition["start_month"],
        start_day=definition["start_day"],
        end_month=definition["end_month"],
        end_day=definition["end_day"],
        timezone=timezone,
        metadata=definition.get("metadata"),
    )


# ---------------------------------------------------------------------------
# MCP tool registration
# ---------------------------------------------------------------------------


def register_seasonal_tools(mcp: Any, pool: asyncpg.Pool, butler_name: str) -> None:
    """Register all seasonal-awareness MCP tools on *mcp*.

    Registers five tools:
    - ``seasonal_period_create``
    - ``seasonal_period_update``
    - ``seasonal_period_list``
    - ``seasonal_period_delete``
    - ``seasonal_period_create_preset``

    Args:
        mcp: FastMCP server instance.
        pool: asyncpg connection pool.
        butler_name: Butler instance name used to scope all DB operations.
    """
    import butlers.core.seasonal as _seasonal_mod

    @mcp.tool()
    async def seasonal_period_create(
        name: str,
        period_type: str = "annual",
        start_month: int = 1,
        start_day: int = 1,
        end_month: int = 12,
        end_day: int = 31,
        timezone: str = "UTC",
        metadata: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a new seasonal period for this butler.

        Seasonal periods define recurring calendar windows (e.g., tax season,
        academic terms) that inject context into task dispatch prompts.

        Args:
            name: Unique name for this period (per butler).
            period_type: One of 'annual', 'academic', 'fiscal', 'custom'.
            start_month: Period start month (1-12).
            start_day: Period start day (1-31).
            end_month: Period end month (1-12).
            end_day: Period end day (1-31).
            timezone: IANA timezone string (default 'UTC').
            metadata: Optional dict with context hints and priority modifiers.
            enabled: Whether the period is active immediately (default true).

        Returns:
            Dict with 'id' (UUID string) of the created period.
        """
        period_id = await _seasonal_mod.seasonal_period_create(
            pool,
            butler_name,
            name=name,
            period_type=period_type,
            start_month=start_month,
            start_day=start_day,
            end_month=end_month,
            end_day=end_day,
            timezone=timezone,
            metadata=metadata,
            enabled=enabled,
        )
        return {"id": str(period_id), "status": "created"}

    @mcp.tool()
    async def seasonal_period_update(
        period_id: str,
        name: str | None = None,
        period_type: str | None = None,
        start_month: int | None = None,
        start_day: int | None = None,
        end_month: int | None = None,
        end_day: int | None = None,
        timezone: str | None = None,
        metadata: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update an existing seasonal period.

        Only provided (non-null) fields are updated.  Month/day combinations
        are validated against the resulting final state.

        Args:
            period_id: UUID of the period to update.
            name: New name (must be unique per butler).
            period_type: New type ('annual', 'academic', 'fiscal', 'custom').
            start_month: New start month (1-12).
            start_day: New start day (1-31).
            end_month: New end month (1-12).
            end_day: New end day (1-31).
            timezone: New IANA timezone string.
            metadata: New metadata dict (replaces existing).
            enabled: New enabled flag.

        Returns:
            Dict with 'found' boolean.
        """
        found = await _seasonal_mod.seasonal_period_update(
            pool,
            butler_name,
            period_id=period_id,
            name=name,
            period_type=period_type,
            start_month=start_month,
            start_day=start_day,
            end_month=end_month,
            end_day=end_day,
            timezone=timezone,
            metadata=metadata,
            enabled=enabled,
        )
        return {"found": found, "status": "updated" if found else "not_found"}

    @mcp.tool()
    async def seasonal_period_list(
        include_disabled: bool = True,
    ) -> dict[str, Any]:
        """List all seasonal periods for this butler.

        Returns all seasonal periods with their current active status
        (whether today's date falls within each period's range).

        Args:
            include_disabled: If False, only return enabled periods
                (default True — return all).

        Returns:
            Dict with 'periods' list, each entry including 'is_active' field.
        """
        periods = await _seasonal_mod.seasonal_period_list(pool, butler_name)
        if not include_disabled:
            periods = [p for p in periods if p.get("enabled")]
        return {"periods": periods, "count": len(periods)}

    @mcp.tool()
    async def seasonal_period_delete(
        period_id: str,
    ) -> dict[str, Any]:
        """Delete a seasonal period.

        Args:
            period_id: UUID of the period to delete.

        Returns:
            Dict with 'found' boolean.
        """
        found = await _seasonal_mod.seasonal_period_delete(pool, butler_name, period_id=period_id)
        return {"found": found, "status": "deleted" if found else "not_found"}

    @mcp.tool()
    async def seasonal_period_create_preset(
        preset: str,
        timezone: str = "UTC",
    ) -> dict[str, Any]:
        """Create a seasonal period from a built-in preset.

        Available presets:
        - 'us-tax-season': Jan 1 - Apr 15 (US tax filing season)
        - 'year-end-holidays': Dec 15 - Jan 5 (year-end holiday season)
        - 'back-to-school': Aug 1 - Sep 15 (back-to-school season)
        - 'spring-semester': Jan 15 - May 15 (spring academic semester)
        - 'fall-semester': Aug 25 - Dec 15 (fall academic semester)

        Args:
            preset: Preset name (e.g., 'us-tax-season').
            timezone: IANA timezone string (default 'UTC').

        Returns:
            Dict with 'id' (UUID string) of the created period.
        """
        period_id = await _seasonal_mod.seasonal_period_create_preset(
            pool, butler_name, preset=preset, timezone=timezone
        )
        return {"id": str(period_id), "status": "created", "preset": preset}
