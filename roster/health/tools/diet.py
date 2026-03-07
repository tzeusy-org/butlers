"""Diet and nutrition — meal logging and nutrition summaries backed by temporal facts."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
_MEAL_PREDICATES = [f"meal_{t}" for t in VALID_MEAL_TYPES]

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


async def _get_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's id from shared.entities.

    Uses the canonical post-core_016 path: ``'owner' = ANY(roles)`` on
    ``shared.entities``.  Returns ``None`` gracefully when the table does not
    exist yet (pre-migration databases) or when no owner entity is present.
    """
    try:
        row = await pool.fetchrow(
            "SELECT id FROM shared.entities WHERE 'owner' = ANY(roles) LIMIT 1"
        )
        return row["id"] if row else None
    except asyncpg.PostgresError:
        logger.debug(
            "_get_owner_entity_id: shared.entities query failed (table may not exist yet)",
            exc_info=True,
        )
        return None


def _fact_to_meal(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the meal API shape."""
    predicate = row.get("predicate", "")
    meal_type = predicate.removeprefix("meal_")
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    estimated_calories = meta.get("estimated_calories") if isinstance(meta, dict) else None
    macros = meta.get("macros") if isinstance(meta, dict) else None
    meal_items = meta.get("meal_items") if isinstance(meta, dict) else None
    logged_at = meta.get("logged_at") if isinstance(meta, dict) else None
    notes = meta.get("notes") if isinstance(meta, dict) else None
    mood_before = meta.get("mood_before") if isinstance(meta, dict) else None
    satisfaction = meta.get("satisfaction") if isinstance(meta, dict) else None
    symptom_notes = meta.get("symptom_notes") if isinstance(meta, dict) else None
    tags = meta.get("tags") if isinstance(meta, dict) else None
    return {
        "id": str(row["id"]),
        "type": meal_type,
        "description": row.get("content", ""),
        "estimated_calories": estimated_calories,
        "macros": macros,
        "meal_items": meal_items,
        "logged_at": logged_at,
        "eaten_at": row.get("valid_at"),
        "notes": notes,
        "mood_before": mood_before,
        "satisfaction": satisfaction,
        "symptom_notes": symptom_notes,
        "tags": tags,
        "created_at": row.get("created_at"),
    }


async def meal_log(
    pool: asyncpg.Pool,
    type: str,
    description: str,
    nutrition: dict[str, Any] | None = None,
    eaten_at: datetime | None = None,
    notes: str | None = None,
    mood_before: int | None = None,
    satisfaction: int | None = None,
    symptom_notes: str | None = None,
    tags: list[str] | None = None,
    create_calendar_event_fn: Any = None,
) -> dict[str, Any]:
    """Log a meal. Type must be one of: breakfast, lunch, dinner, snack."""
    if type not in VALID_MEAL_TYPES:
        raise ValueError(
            f"Invalid meal type: {type!r}. Must be one of: {', '.join(sorted(VALID_MEAL_TYPES))}"
        )

    from butlers.modules.memory.storage import store_fact

    predicate = f"meal_{type}"
    owner_entity_id = await _get_owner_entity_id(pool)
    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)
    valid_at = eaten_at if eaten_at is not None else now

    metadata: dict[str, Any] = {
        "logged_at": now.isoformat(),
        "meal_items": [],
    }
    if nutrition is not None:
        metadata["estimated_calories"] = nutrition.get("calories")
        metadata["macros"] = {
            "protein_g": nutrition.get("protein_g"),
            "carbs_g": nutrition.get("carbs_g"),
            "fat_g": nutrition.get("fat_g"),
        }
    if notes is not None:
        metadata["notes"] = notes
    if mood_before is not None:
        metadata["mood_before"] = mood_before
    if satisfaction is not None:
        metadata["satisfaction"] = satisfaction
    if symptom_notes is not None:
        metadata["symptom_notes"] = symptom_notes
    if tags is not None:
        metadata["tags"] = tags

    fact_id = await store_fact(
        pool,
        subject="owner",
        predicate=predicate,
        content=description,
        embedding_engine=embedding_engine,
        permanence="stable",
        scope="health",
        entity_id=owner_entity_id,
        valid_at=valid_at,
        metadata=metadata,
    )

    if create_calendar_event_fn is not None and valid_at >= now:
        event_description = type.capitalize()
        if notes:
            event_description = f"{event_description} — {notes}"
        try:
            await create_calendar_event_fn(
                title=description,
                start_at=valid_at,
                end_at=valid_at + timedelta(minutes=30),
                description=event_description,
            )
        except Exception:
            logger.warning("meal_log: failed to create calendar event", exc_info=True)

    return {
        "id": str(fact_id),
        "type": type,
        "description": description,
        "estimated_calories": metadata.get("estimated_calories"),
        "macros": metadata.get("macros"),
        "meal_items": metadata["meal_items"],
        "logged_at": metadata["logged_at"],
        "eaten_at": valid_at,
        "notes": notes,
        "mood_before": mood_before,
        "satisfaction": satisfaction,
        "symptom_notes": symptom_notes,
        "tags": tags,
        "created_at": now,
    }


async def meal_history(
    pool: asyncpg.Pool,
    type: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Get meal history, optionally filtered by type and date range."""
    if type is not None and type not in VALID_MEAL_TYPES:
        raise ValueError(
            f"Invalid meal type: {type!r}. Must be one of: {', '.join(sorted(VALID_MEAL_TYPES))}"
        )

    predicates = [f"meal_{type}"] if type is not None else _MEAL_PREDICATES
    conditions = ["predicate = ANY($1)", "validity = 'active'", "scope = 'health'"]
    params: list[Any] = [predicates]
    idx = 2

    if start_date is not None:
        conditions.append(f"valid_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"valid_at <= ${idx}")
        params.append(end_date)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    rows = await pool.fetch(
        f"SELECT id, predicate, content, valid_at, created_at, metadata"
        f" FROM facts {where} ORDER BY valid_at DESC",
        *params,
    )
    return [_fact_to_meal(dict(r)) for r in rows]


async def nutrition_summary(
    pool: asyncpg.Pool,
    start_date: datetime,
    end_date: datetime,
) -> dict[str, Any]:
    """Aggregate nutrition data over a date range.

    Returns total and daily average calories, protein, carbs, and fat from meal facts
    with non-null nutrition in metadata. Meals without nutrition data are excluded.
    """
    rows = await pool.fetch(
        """
        SELECT metadata FROM facts
        WHERE predicate = ANY($1)
          AND validity = 'active'
          AND scope = 'health'
          AND valid_at >= $2 AND valid_at <= $3
          AND metadata ? 'estimated_calories'
        """,
        _MEAL_PREDICATES,
        start_date,
        end_date,
    )

    total_calories: float = 0.0
    total_protein: float = 0.0
    total_carbs: float = 0.0
    total_fat: float = 0.0
    meal_count = 0

    for row in rows:
        meta = row["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        if not isinstance(meta, dict):
            continue
        estimated_calories = meta.get("estimated_calories")
        if estimated_calories is None:
            continue
        macros = meta.get("macros") or {}
        if isinstance(macros, str):
            macros = json.loads(macros)
        meal_count += 1
        if isinstance(estimated_calories, int | float):
            total_calories += float(estimated_calories)
        if isinstance(macros, dict):
            if "protein_g" in macros and isinstance(macros["protein_g"], int | float):
                total_protein += float(macros["protein_g"])
            if "carbs_g" in macros and isinstance(macros["carbs_g"], int | float):
                total_carbs += float(macros["carbs_g"])
            if "fat_g" in macros and isinstance(macros["fat_g"], int | float):
                total_fat += float(macros["fat_g"])

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
