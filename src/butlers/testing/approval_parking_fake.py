"""Narrow park recorder for tests using only pending_actions stand-ins."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


async def record_pending_action(pool: Any, **kwargs: Any) -> Any:
    """Record producer arguments without claiming to test recovery admission."""
    await pool.execute(
        "INSERT INTO pending_actions "
        "(id, tool_name, tool_args, agent_summary, session_id, status, "
        "requested_at, expires_at, why, evidence, blast_radius, reversibility) "
        "VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $10, $11)",
        kwargs["action_id"],
        kwargs["tool_name"],
        kwargs["tool_args"],
        kwargs["agent_summary"],
        kwargs.get("session_id"),
        kwargs["requested_at"],
        kwargs["expires_at"],
        kwargs.get("why"),
        list(kwargs.get("evidence") or []),
        kwargs.get("blast_radius"),
        kwargs.get("reversibility"),
    )
    return SimpleNamespace(
        action_id=kwargs["action_id"],
        intent_id=None,
        duplicate=False,
        legacy_duplicate=True,
    )
