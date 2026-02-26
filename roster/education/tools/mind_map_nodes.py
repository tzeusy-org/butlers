"""Education butler — mind map node CRUD and mastery state machine."""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from butlers.tools.education._helpers import _row_to_dict
from butlers.tools.education.mind_maps import mind_map_update_status

# ---------------------------------------------------------------------------
# Mastery status state machine
# ---------------------------------------------------------------------------

# Valid transitions: {from_status: {to_status, ...}}
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "unseen": {"diagnosed", "learning"},
    "diagnosed": {"learning", "mastered"},
    "learning": {"reviewing", "mastered"},
    "reviewing": {"mastered", "learning"},  # learning = regression
    "mastered": {"reviewing"},  # reviewing = spaced repetition
}

_ALL_STATUSES = {"unseen", "diagnosed", "learning", "reviewing", "mastered"}

# Writable fields (all others are silently ignored)
_WRITABLE_FIELDS = {
    "mastery_score",
    "mastery_status",
    "ease_factor",
    "repetitions",
    "next_review_at",
    "last_reviewed_at",
    "effort_minutes",
    "metadata",
}


async def mind_map_node_create(
    pool: asyncpg.Pool,
    mind_map_id: str,
    label: str,
    description: str | None = None,
    depth: int | None = None,
    effort_minutes: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Create a new node in a mind map.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the parent mind map.
    label:
        Short name for the concept (e.g. "List Comprehensions").
    description:
        Optional longer description of the concept.
    depth:
        Depth in the prerequisite DAG. Defaults to 0 if None.
    effort_minutes:
        Optional estimated effort to master this node.
    metadata:
        Optional JSONB metadata dict.

    Returns
    -------
    str
        The UUID of the newly created node.
    """
    effective_depth = depth if depth is not None else 0
    effective_metadata = json.dumps(metadata or {})
    row = await pool.fetchrow(
        """
        INSERT INTO education.mind_map_nodes
            (mind_map_id, label, description, depth, effort_minutes, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        RETURNING id
        """,
        mind_map_id,
        label,
        description,
        effective_depth,
        effort_minutes,
        effective_metadata,
    )
    return str(row["id"])


async def mind_map_node_get(
    pool: asyncpg.Pool,
    node_id: str,
) -> dict[str, Any] | None:
    """Retrieve a single node by ID.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    node_id:
        UUID of the node.

    Returns
    -------
    dict or None
        The node row as a dict, or None if not found.
    """
    row = await pool.fetchrow(
        "SELECT * FROM education.mind_map_nodes WHERE id = $1",
        node_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def mind_map_node_list(
    pool: asyncpg.Pool,
    mind_map_id: str,
    mastery_status: str | None = None,
) -> list[dict[str, Any]]:
    """List nodes in a mind map, optionally filtered by mastery_status.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    mind_map_id:
        UUID of the mind map.
    mastery_status:
        Optional filter for mastery status.

    Returns
    -------
    list of dict
        Node rows ordered by depth ascending, then label.
    """
    if mastery_status is not None:
        rows = await pool.fetch(
            """
            SELECT * FROM education.mind_map_nodes
            WHERE mind_map_id = $1 AND mastery_status = $2
            ORDER BY depth ASC, label ASC
            """,
            mind_map_id,
            mastery_status,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM education.mind_map_nodes
            WHERE mind_map_id = $1
            ORDER BY depth ASC, label ASC
            """,
            mind_map_id,
        )
    return [_row_to_dict(row) for row in rows]


async def mind_map_node_update(
    pool: asyncpg.Pool,
    node_id: str,
    **fields: Any,
) -> None:
    """Update writable fields on a node.

    Non-writable fields are silently ignored. Always updates ``updated_at``.
    Enforces the mastery status state machine when ``mastery_status`` is present.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    node_id:
        UUID of the node to update.
    **fields:
        Keyword arguments for fields to update. Writable fields:
        mastery_score, mastery_status, ease_factor, repetitions,
        next_review_at, last_reviewed_at, effort_minutes, metadata.

    Raises
    ------
    ValueError
        If the node is not found, or if the mastery_status transition is invalid.
    """
    # Filter to only writable fields
    updates = {k: v for k, v in fields.items() if k in _WRITABLE_FIELDS}
    if not updates:
        # No writable fields — still verify the node exists
        row = await pool.fetchrow(
            "SELECT id FROM education.mind_map_nodes WHERE id = $1",
            node_id,
        )
        if row is None:
            raise ValueError(f"Node not found: {node_id}")
        return

    # Validate mastery_status transition if provided
    if "mastery_status" in updates:
        new_status = updates["mastery_status"]
        current_row = await pool.fetchrow(
            "SELECT mastery_status, mind_map_id FROM education.mind_map_nodes WHERE id = $1",
            node_id,
        )
        if current_row is None:
            raise ValueError(f"Node not found: {node_id}")
        current_status = current_row["mastery_status"]
        allowed = _VALID_TRANSITIONS.get(current_status, set())
        if new_status != current_status and new_status not in allowed:
            raise ValueError(
                f"Invalid mastery_status transition: {current_status!r} → {new_status!r}. "
                f"Allowed from {current_status!r}: {sorted(allowed)}"
            )
        mind_map_id = str(current_row["mind_map_id"])
    else:
        mind_map_id = None

    # Build SET clause dynamically
    set_parts = []
    values: list[Any] = []
    param_idx = 1

    for key, val in updates.items():
        if key == "metadata":
            set_parts.append(f"metadata = ${param_idx}::jsonb")
            values.append(json.dumps(val) if isinstance(val, dict) else val)
        else:
            set_parts.append(f"{key} = ${param_idx}")
            values.append(val)
        param_idx += 1

    # Always update updated_at
    set_parts.append("updated_at = now()")
    values.append(node_id)

    sql = f"""
        UPDATE education.mind_map_nodes
        SET {", ".join(set_parts)}
        WHERE id = ${param_idx}
    """
    result = await pool.execute(sql, *values)
    affected = int(result.split()[-1])
    if affected == 0:
        raise ValueError(f"Node not found: {node_id}")

    # Auto-completion: if mastery_status changed to mastered, check if all nodes mastered
    if "mastery_status" in updates and updates["mastery_status"] == "mastered" and mind_map_id:
        unmastered_count = await pool.fetchval(
            """
            SELECT COUNT(*) FROM education.mind_map_nodes
            WHERE mind_map_id = $1 AND mastery_status != 'mastered'
            """,
            mind_map_id,
        )
        if unmastered_count == 0:
            # Check map has at least one node
            node_count = await pool.fetchval(
                "SELECT COUNT(*) FROM education.mind_map_nodes WHERE mind_map_id = $1",
                mind_map_id,
            )
            if node_count > 0:
                await mind_map_update_status(pool, mind_map_id, "completed")
