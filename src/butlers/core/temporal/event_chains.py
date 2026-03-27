"""Event chain logic for temporal intelligence.

Provides pure functions for:
  - validate_chain_actions   — Validate the actions[] array of an event chain
  - materialize_chain_actions — Convert chain actions to one-shot scheduled_task dicts
  - should_fire_chain        — Check if a chain can fire given its depth

See: openspec/specs/event-chains/spec.md
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_VALID_ACTION_TYPES = {"prompt", "job"}
_MAX_CHAIN_DEPTH = 3  # chains beyond depth 2 (0-indexed) are skipped


def validate_chain_actions(actions: list[dict[str, Any]]) -> None:
    """Validate a chain's actions array.

    Raises:
        ValueError: If any action is invalid.
    """
    if not actions:
        raise ValueError("Event chain must have at least one action")

    for i, action in enumerate(actions):
        action_type = action.get("action_type")
        if action_type not in _VALID_ACTION_TYPES:
            raise ValueError(
                f"Action {i}: invalid action_type={action_type!r}. "
                f"Must be one of {sorted(_VALID_ACTION_TYPES)!r}"
            )

        delay = action.get("delay_minutes")
        if delay is None:
            raise ValueError(f"Action {i}: delay_minutes is required")
        if not isinstance(delay, int) or delay < 0:
            raise ValueError(
                f"Action {i}: delay_minutes must be a non-negative integer (got {delay!r})"
            )

        if action_type == "prompt":
            if not action.get("prompt"):
                raise ValueError(
                    f"Action {i}: action_type='prompt' requires a non-empty 'prompt' field"
                )

        elif action_type == "job":
            if not action.get("job_name"):
                raise ValueError(
                    f"Action {i}: action_type='job' requires a non-empty 'job_name' field"
                )


def materialize_chain_actions(
    *,
    chain_name: str,
    actions: list[dict[str, Any]],
    fired_at: datetime,
) -> list[dict[str, Any]]:
    """Materialize event chain actions into one-shot scheduled_task dicts.

    Each action becomes a scheduled_task with:
      - name: "chain:<chain_name>:<index>"
      - source: "chain"
      - next_run_at: fired_at + cumulative delay_minutes
      - until_at: next_run_at + 1 minute (auto-disable after firing)
      - trigger_source: "chain:<chain_name>"
      - dispatch_mode: "prompt" or "job"

    Returns:
        List of task dicts ready for DB insertion.
    """
    tasks = []

    for i, action in enumerate(actions):
        action_type = action["action_type"]
        total_minutes = sum(actions[j].get("delay_minutes", 0) for j in range(i + 1))
        next_run_at = fired_at + timedelta(minutes=total_minutes)
        until_at = next_run_at + timedelta(minutes=1)

        task: dict[str, Any] = {
            "name": f"chain:{chain_name}:{i}",
            "source": "chain",
            "next_run_at": next_run_at,
            "until_at": until_at,
            "trigger_source": f"chain:{chain_name}",
            "dispatch_mode": action_type,
        }

        if action_type == "prompt":
            task["prompt"] = action["prompt"]
            task["cron"] = "* * * * *"  # placeholder for one-shot tasks
        elif action_type == "job":
            task["job_name"] = action["job_name"]
            task["job_args"] = action.get("job_args")
            task["cron"] = "* * * * *"  # placeholder for one-shot tasks

        tasks.append(task)

    return tasks


def should_fire_chain(*, chain_depth: int, chain_name: str = "") -> bool:
    """Check if a chain is allowed to fire at the given depth.

    Chains cascade at most 3 levels deep (depth 0, 1, 2). A chain at depth 3
    or greater is skipped and a warning is logged.

    Args:
        chain_depth: Current cascade depth (0 = directly triggered).
        chain_name: Chain name for warning message (optional).

    Returns:
        True if the chain can fire, False if depth limit exceeded.
    """
    if chain_depth >= _MAX_CHAIN_DEPTH:
        logger.warning(
            "Event chain %r at depth %d exceeds maximum cascade depth %d — skipping",
            chain_name or "<unknown>",
            chain_depth,
            _MAX_CHAIN_DEPTH,
        )
        return False
    return True
