"""Which parked entity merges earn a dashboard structural review (REQ-module-approvals-008/009).

Relationship's entity-dedup curation parks ``memory_entity_merge`` (and older rows the
legacy ``entity_merge`` name) with exactly ``{source_entity_id, target_entity_id}``. No
shared exact-call preview is registered for either name: a preview would also unlock
one-tap notification decisions and an older dashboard's plain Approve button, neither of
which shows the structural comparison a merge needs. Instead the dashboard detail carries
a typed binding built here, and only the dashboard approval routes accept it.

The binding keeps the stored tool name and hashes the stored arguments unchanged, so a
legacy row is reviewed as stored, never renamed. Anything the review cannot show
completely (an extra argument, a non-UUID or identical pair) has no binding and stays
unapprovable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from butlers.modules.approvals.previews import args_digest

#: Both names dispatch to Relationship's single merge authority (``daemon.py`` aliases
#: the legacy name). Notification callbacks never decide either one.
ENTITY_MERGE_TOOLS = frozenset({"memory_entity_merge", "entity_merge"})
_MERGE_ARG_KEYS = frozenset({"source_entity_id", "target_entity_id"})


@dataclass(frozen=True, slots=True)
class EntityMergeBinding:
    """The stored merge as reviewed: fixed direction plus the exact-call digest."""

    tool_name: str
    source_entity_id: UUID
    target_entity_id: UUID
    args_digest: str


def is_entity_merge_tool(tool_name: Any) -> bool:
    return tool_name in ENTITY_MERGE_TOOLS


async def is_entity_merge_action(connection: Any, action_id: UUID) -> bool:
    """Read whether *action_id* stores an entity merge; unknown ids are not."""
    row = await connection.fetchrow(
        "SELECT tool_name FROM pending_actions WHERE id = $1", action_id
    )
    return row is not None and is_entity_merge_tool(row["tool_name"])


def _uuid(value: Any) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def entity_merge_binding(tool_name: Any, tool_args: Any) -> EntityMergeBinding | None:
    """The review binding for a showable merge call, else ``None``.

    Both names reach Relationship's single merge authority wherever they are parked, so
    the review does not depend on the parking butler; dispatch still goes to that butler.
    """
    if not is_entity_merge_tool(tool_name):
        return None
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except json.JSONDecodeError:
            return None
    if not isinstance(tool_args, Mapping) or set(tool_args) != _MERGE_ARG_KEYS:
        return None
    source = _uuid(tool_args["source_entity_id"])
    target = _uuid(tool_args["target_entity_id"])
    if source is None or target is None or source == target:
        return None
    return EntityMergeBinding(
        tool_name=tool_name,
        source_entity_id=source,
        target_entity_id=target,
        args_digest=args_digest(dict(tool_args)),
    )


__all__ = [
    "ENTITY_MERGE_TOOLS",
    "EntityMergeBinding",
    "entity_merge_binding",
    "is_entity_merge_action",
    "is_entity_merge_tool",
]
