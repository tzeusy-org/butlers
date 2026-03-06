"""Diet and nutrition — meal logging and nutrition summaries."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _normalize_end_date, _row_to_dict

logger = logging.getLogger(__name__)

VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}


async def meal_log(
    pool: asyncpg.Pool,
    type: str,
    description: str,
    nutrition: dict[str, Any] | None = None,
    eaten_at: datetime | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Log a meal. Type must be one of: breakfast, lunch, dinner, snack."""
    if type not in VALID_MEAL_TYPES:
        raise ValueError(
            f"Invalid meal type: {type!r}. Must be one of: {', '.join(sorted(VALID_MEAL_TYPES))}"
        )
    row = await pool.fetchrow(
        """
        INSERT INTO meals (type, description, nutrition, eaten_at, notes)
        VALUES ($1, $2, $3::jsonb, COALESCE($4, now()), $5)
        RETURNING *
        """,
        type,
        description,
        json.dumps(nutrition) if nutrition is not None else None,
        eaten_at,
        notes,
    )
    return _row_to_dict(row)


async def meal_history(
    pool: asyncpg.Pool,
    type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get meal history, optionally filtered by type and date range."""
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if type is not None:
        conditions.append(f"type = ${idx}")
        params.append(type)
        idx += 1

    if start_date is not None:
        conditions.append(f"eaten_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"eaten_at <= ${idx}")
        params.append(_normalize_end_date(end_date))
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await pool.fetch(
        f"SELECT * FROM meals {where} ORDER BY eaten_at DESC",
        *params,
    )
    return [_row_to_dict(r) for r in rows]


async def nutrition_summary(
    pool: asyncpg.Pool,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Aggregate nutrition data over a date range.

    Returns total and daily average calories, protein, carbs, and fat from meals
    with non-null nutrition JSONB. Meals without nutrition data are excluded.
    """
    normalized_end = _normalize_end_date(end_date)
    rows = await pool.fetch(
        """
        SELECT nutrition FROM meals
        WHERE eaten_at >= $1 AND eaten_at <= $2 AND nutrition IS NOT NULL
        """,
        start_date,
        normalized_end,
    )

    total_calories: float = 0.0
    total_protein: float = 0.0
    total_carbs: float = 0.0
    total_fat: float = 0.0
    meal_count = len(rows)

    for row in rows:
        nutr = row["nutrition"]
        if isinstance(nutr, str):
            nutr = json.loads(nutr)
        if isinstance(nutr, dict):
            if "calories" in nutr and isinstance(nutr["calories"], int | float):
                total_calories += float(nutr["calories"])
            if "protein_g" in nutr and isinstance(nutr["protein_g"], int | float):
                total_protein += float(nutr["protein_g"])
            if "carbs_g" in nutr and isinstance(nutr["carbs_g"], int | float):
                total_carbs += float(nutr["carbs_g"])
            if "fat_g" in nutr and isinstance(nutr["fat_g"], int | float):
                total_fat += float(nutr["fat_g"])

    days = max((end_date - start_date).days, 1)

    return {
        "total_calories": total_calories,
        "daily_avg_calories": round(total_calories / days, 1),
        "total_protein_g": total_protein,
        "daily_avg_protein_g": round(total_protein / days, 1),
        "total_carbs_g": total_carbs,
        "daily_avg_carbs_g": round(total_carbs / days, 1),
        "total_fat_g": total_fat,
        "daily_avg_fat_g": round(total_fat / days, 1),
        "meal_count": meal_count,
    }
