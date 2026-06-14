"""Diet and nutrition — meal logging and nutrition summaries backed by temporal facts.

Write path summary
------------------
``meal_log()`` dual-writes every meal to two surfaces:

1. **``facts`` table** (memory module) — powers ``meal_history()``,
   ``nutrition_summary()``, semantic search, and the weekly health summary.
2. **``health.meals`` table** — powers the Chronicler ``MealsAdapter`` which
   projects meals into ``chronicler.point_events`` (``eating_event``) so they
   appear on the Chronicles dashboard Meal lane.

Trigger: the user (or an agent) calls ``meal_log`` via MCP (Telegram, direct
tool call, or an LLM session).  There is no external connector — meals are
always entered manually.  The Chronicler picks up the ``health.meals`` row
within one ``chronicler_project_meals`` tick (typically every 15 minutes).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.health._helpers import _get_owner_entity_id

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


async def _write_to_health_meals(
    pool: asyncpg.Pool,
    *,
    meal_id: uuid.UUID,
    type: str,
    description: str,
    nutrition: dict[str, Any] | None,
    eaten_at: datetime,
    notes: str | None,
) -> None:
    """Insert a row into ``health.meals`` so the Chronicler MealsAdapter can project it.

    This is the second leg of the dual-write in ``meal_log()``.  Failures are
    logged as warnings but do not raise — the primary facts write already
    succeeded and must not be rolled back.

    The ``meal_id`` is the ``fact_id`` returned by ``store_fact`` so both
    storage surfaces share the same stable identifier.  On retry, ``store_fact``
    returns the same ``fact_id`` for identical content, and the
    ``ON CONFLICT (id) DO NOTHING`` clause makes this write a safe no-op.
    """
    nutrition_jsonb: Any = None
    if nutrition is not None:
        # Build the JSONB payload that health.meals.nutrition expects:
        # { "calories": N, "macros": { "protein_g": N, "carbs_g": N, "fat_g": N } }
        # This mirrors the shape already stored in facts.metadata.
        nutrition_jsonb = {
            "calories": nutrition.get("calories"),
            "macros": {
                "protein_g": nutrition.get("protein_g"),
                "carbs_g": nutrition.get("carbs_g"),
                "fat_g": nutrition.get("fat_g"),
            },
        }

    try:
        await pool.execute(
            """
            INSERT INTO health.meals (id, type, description, nutrition, eaten_at, notes)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO NOTHING
            """,
            meal_id,
            type,
            description,
            json.dumps(nutrition_jsonb) if nutrition_jsonb is not None else None,
            eaten_at,
            notes,
        )
    except asyncpg.PostgresError:
        logger.warning(
            "meal_log: failed to dual-write to health.meals for meal_id=%s; "
            "Chronicler Meal lane will miss this entry until the row is replayed",
            meal_id,
            exc_info=True,
        )


def _fact_to_meal(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the meal API shape."""
    predicate = row.get("predicate", "")
    meal_type = predicate.removeprefix("meal_")
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    if not isinstance(meta, dict):
        meta = {}

    estimated_calories = meta.get("estimated_calories")
    macros = meta.get("macros")
    meal_items = meta.get("meal_items")
    logged_at = meta.get("logged_at")
    notes = meta.get("notes")
    mood_before = meta.get("mood_before")
    satisfaction = meta.get("satisfaction")
    symptom_notes = meta.get("symptom_notes")
    tags = meta.get("tags")
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
    eaten_at: datetime,
    nutrition: dict[str, Any] | None = None,
    notes: str | None = None,
    mood_before: int | None = None,
    satisfaction: int | None = None,
    symptom_notes: str | None = None,
    tags: list[str] | None = None,
    create_calendar_event_fn: Any = None,
) -> dict[str, Any]:
    """Log a meal. Type must be one of: breakfast, lunch, dinner, snack.

    eaten_at is required — the approximate time the meal was (or will be) eaten.
    An estimate is fine (e.g. "today at noon"); a future time is fine for planned meals.
    """
    if type not in VALID_MEAL_TYPES:
        raise ValueError(
            f"Invalid meal type: {type!r}. Must be one of: {', '.join(sorted(VALID_MEAL_TYPES))}"
        )
    if eaten_at is None:
        raise ValueError(
            "eaten_at is required. Provide the approximate time the meal was (or will be) eaten. "
            "An estimate is fine (e.g. 'today at 12:00'), and future times are accepted for "
            "planned meals. Example: eaten_at='2026-03-20T12:00:00Z'"
        )

    from butlers.modules.memory.storage import store_fact

    predicate = f"meal_{type}"
    owner_entity_id = await _get_owner_entity_id(pool)
    embedding_engine = _get_embedding_engine()
    now = datetime.now(UTC)
    valid_at = eaten_at

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
    optional_metadata = {
        "notes": notes,
        "mood_before": mood_before,
        "satisfaction": satisfaction,
        "symptom_notes": symptom_notes,
        "tags": tags,
    }
    metadata.update({key: value for key, value in optional_metadata.items() if value is not None})

    fact_id = (
        await store_fact(
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
    )["id"]

    # Dual-write: persist to health.meals so the Chronicler MealsAdapter can
    # project this meal into the Chronicles dashboard Meal lane.
    # Use fact_id as meal_id so both surfaces share the same stable UUID.
    # On retry, store_fact returns the same fact_id and ON CONFLICT DO NOTHING
    # makes this write idempotent.
    await _write_to_health_meals(
        pool,
        meal_id=fact_id,
        type=type,
        description=description,
        nutrition=nutrition,
        eaten_at=eaten_at,
        notes=notes,
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


async def meal_update(
    pool: asyncpg.Pool,
    meal_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """Update a logged meal. Allowed fields: type, description, eaten_at,
    nutrition, notes.

    Meals are TEMPORAL facts (``valid_at`` is the eating time and supersession
    is skipped for temporal facts), so — like symptoms — there is no superseding
    ``store_fact`` path keyed on (subject, predicate). Re-storing would create a
    *second* coexisting meal rather than replacing the first. Instead this
    performs an in-place UPDATE of the existing meal fact row so the edit
    preserves the same identity and the temporal log keeps exactly one entry per
    logged meal.

    Changing ``type`` rewrites the predicate to ``meal_{type}`` (the meal type is
    encoded in the predicate, not in metadata). ``nutrition`` is a dict shaped
    like the ``meal_log`` argument (``calories``/``protein_g``/``carbs_g``/
    ``fat_g``); it is translated into the ``estimated_calories`` + ``macros``
    metadata fields. The ``facts`` table has no ``updated_at`` column, so only
    ``content``, ``predicate``, ``metadata``, and ``valid_at`` are touched.
    """
    meal_uuid = uuid.UUID(meal_id) if isinstance(meal_id, str) else meal_id
    allowed = {"type", "description", "eaten_at", "nutrition", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}

    if not updates:
        raise ValueError("No valid fields to update")

    if "type" in updates and updates["type"] not in VALID_MEAL_TYPES:
        raise ValueError(
            f"Invalid meal type: {updates['type']!r}. "
            f"Must be one of: {', '.join(sorted(VALID_MEAL_TYPES))}"
        )

    row = await pool.fetchrow(
        "SELECT id, predicate, content, valid_at, created_at, metadata FROM facts"
        " WHERE id = $1 AND predicate = ANY($2) AND scope = 'health'"
        " AND validity = 'active'",
        meal_uuid,
        _MEAL_PREDICATES,
    )
    if row is None:
        raise ValueError(f"Meal {meal_id} not found")

    existing_meta = row["metadata"] or {}
    if isinstance(existing_meta, str):
        existing_meta = json.loads(existing_meta)
    new_meta = dict(existing_meta)

    if "nutrition" in updates:
        nutrition = updates["nutrition"]
        if nutrition is None:
            new_meta.pop("estimated_calories", None)
            new_meta.pop("macros", None)
        else:
            new_meta["estimated_calories"] = nutrition.get("calories")
            new_meta["macros"] = {
                "protein_g": nutrition.get("protein_g"),
                "carbs_g": nutrition.get("carbs_g"),
                "fat_g": nutrition.get("fat_g"),
            }

    if "notes" in updates:
        if updates["notes"] is None:
            new_meta.pop("notes", None)
        else:
            new_meta["notes"] = updates["notes"]

    predicate = f"meal_{updates['type']}" if "type" in updates else row["predicate"]
    description = updates.get("description", row["content"])
    eaten_at = updates.get("eaten_at", row["valid_at"])

    await pool.execute(
        "UPDATE facts SET predicate = $2, content = $3, metadata = $4, valid_at = $5 WHERE id = $1",
        meal_uuid,
        predicate,
        description,
        new_meta,
        eaten_at,
    )

    return _fact_to_meal(
        {
            "id": meal_uuid,
            "predicate": predicate,
            "content": description,
            "valid_at": eaten_at,
            "created_at": row["created_at"],
            "metadata": new_meta,
        }
    )


async def meal_delete(
    pool: asyncpg.Pool,
    meal_id: str,
) -> bool:
    """Soft-delete a logged meal by retracting its fact.

    Delegates to ``forget_memory(pool, "fact", id)``, which sets the fact's
    ``validity`` to ``'retracted'`` — the canonical soft-delete path used across
    the memory subsystem. The fact remains in the database for audit but is
    excluded from all ``validity = 'active'`` read surfaces (including the
    dashboard GET endpoint and ``meal_history``). Raises ``ValueError`` if no
    active meal fact with this id exists.
    """
    from butlers.modules.memory.storage import forget_memory

    meal_uuid = uuid.UUID(meal_id) if isinstance(meal_id, str) else meal_id

    row = await pool.fetchrow(
        "SELECT id FROM facts"
        " WHERE id = $1 AND predicate = ANY($2) AND scope = 'health'"
        " AND validity = 'active'",
        meal_uuid,
        _MEAL_PREDICATES,
    )
    if row is None:
        raise ValueError(f"Meal {meal_id} not found")

    return await forget_memory(pool, "fact", meal_uuid)


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
