"""Domain payload custody for D13 amendments; staging never authorizes execution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from butlers.core.capability_json import arguments_digest, canonical_bytes, parse_json_object


class GoalEditRefused(ValueError):
    """A content-blind mismatch or an action no longer awaiting its owner."""


async def stage_goal_edit(
    pool: Any,
    goal_id: UUID,
    action_id: UUID,
    original_digest: str,
    edits: dict[str, Any],
    tool_name: str,
) -> tuple[UUID, dict[str, Any], tuple[str, ...]]:
    """Preserve both snapshots without changing the action or conferring authority.

    Called under the broker's goal lock. A crash before the receipt leaves only an
    inert snapshot; no recovery process searches this table for work.
    """
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT goal_id, tool_name, tool_args, status, expires_at FROM pending_actions "
            "WHERE id=$1 FOR UPDATE",
            action_id,
        )
        if row is None or row["goal_id"] != goal_id or row["tool_name"] != tool_name:
            raise GoalEditRefused("resource")
        if row["status"] != "pending":
            raise GoalEditRefused("stale")
        if row["expires_at"] is not None and row["expires_at"] <= datetime.now(UTC):
            raise GoalEditRefused("expired")
        original = parse_json_object(canonical_bytes(row["tool_args"]))
        if arguments_digest(original) != original_digest:
            raise GoalEditRefused("stale")
        approved = parse_json_object(canonical_bytes({**original, **edits}))
        names = _changed_fields(original, approved)
        if not names:
            raise GoalEditRefused("policy")
        amendment = uuid4()
        await conn.execute(
            "INSERT INTO goal_effect_edits (id, goal_id, action_id, original_digest, "
            "approved_digest, original_args, approved_args) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            amendment,
            goal_id,
            action_id,
            original_digest,
            arguments_digest(approved),
            original,
            approved,
        )
        return amendment, approved, names


async def mirror_goal_edit(
    conn: Any,
    goal_id: UUID,
    action_id: UUID,
    amendment_id: UUID,
    original_digest: str,
    approved_digest: str,
    current: dict[str, Any],
    *,
    pending: bool,
) -> tuple[str, ...]:
    """Apply exactly the receipt's amendment inside the canonical decision transaction.

    A replay must see the same approved snapshot. It cannot overwrite a changed call.
    The immutable row retains the original even after the action mirrors the edit.
    """
    row = await conn.fetchrow(
        "SELECT * FROM goal_effect_edits WHERE id=$1 AND goal_id=$2 AND action_id=$3",
        amendment_id,
        goal_id,
        action_id,
    )
    if (
        row is None
        or row["original_digest"] != original_digest
        or row["approved_digest"] != approved_digest
        or arguments_digest(row["original_args"]) != original_digest
        or arguments_digest(row["approved_args"]) != approved_digest
        or arguments_digest(current) != (original_digest if pending else approved_digest)
    ):
        raise GoalEditRefused("stale")
    if pending:
        await conn.execute(
            "UPDATE pending_actions SET tool_args=$1 WHERE id=$2",
            row["approved_args"],
            action_id,
        )
    return _changed_fields(row["original_args"], row["approved_args"])


def _changed_fields(original: dict, approved: dict) -> tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key in approved
            if key not in original
            or canonical_bytes({"v": original[key]}) != canonical_bytes({"v": approved[key]})
        )
    )
