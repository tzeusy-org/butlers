"""Autonomy tracker — pattern fingerprinting, approval history, and promotion thresholds.

Provides:
- compute_fingerprint: deterministic SHA-256 hash of (tool_name, tool_args)
- record_approval: insert into autonomy_approval_history
- get_approval_count: count manual approvals for a fingerprint
- check_promotion_threshold: decide whether to create a suggestion
- update_velocity / get_velocity: rolling avg time_to_decision_seconds in state store
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from butlers.core.state import state_get, state_set

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# State store key prefix for velocity data
_VELOCITY_KEY_PREFIX = "autonomy:velocity:"

# Fast-approval threshold in seconds (below this is considered fast)
_FAST_APPROVAL_THRESHOLD_SECONDS = 5.0


def compute_fingerprint(tool_name: str, tool_args: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 fingerprint for a tool invocation.

    The fingerprint is derived from the canonical JSON of ``(tool_name, tool_args)``
    with dictionary keys sorted alphabetically at every level.

    Parameters
    ----------
    tool_name:
        The name of the tool being invoked.
    tool_args:
        The arguments to the tool invocation.

    Returns
    -------
    str
        Lowercase hex SHA-256 digest.
    """
    payload = {"tool_name": tool_name, "tool_args": tool_args}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def record_approval(pool: Any, action: Any) -> None:
    """Record a manual approval in ``autonomy_approval_history``.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    action:
        PendingAction that was just manually approved. Must have
        ``id``, ``tool_name``, ``tool_args``, ``requested_at``,
        and ``decided_at``.
    """
    fingerprint = compute_fingerprint(action.tool_name, action.tool_args)
    now = datetime.now(UTC)
    approved_at = action.decided_at if action.decided_at is not None else now

    time_to_decision: float | None = None
    if action.decided_at is not None and action.requested_at is not None:
        delta = action.decided_at - action.requested_at
        time_to_decision = delta.total_seconds()

    await pool.execute(
        "INSERT INTO autonomy_approval_history "
        "(id, pattern_fingerprint, tool_name, tool_args, "
        "action_id, approved_at, time_to_decision_seconds) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        uuid.uuid4(),
        fingerprint,
        action.tool_name,
        json.dumps(action.tool_args),
        action.id,
        approved_at,
        time_to_decision,
    )


async def get_approval_count(pool: Any, pattern_fingerprint: str) -> int:
    """Count manual approvals recorded for a given pattern fingerprint.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    pattern_fingerprint:
        SHA-256 fingerprint to count.

    Returns
    -------
    int
        Number of rows in ``autonomy_approval_history`` matching the fingerprint.
    """
    row = await pool.fetchrow(
        "SELECT COUNT(*) AS cnt FROM autonomy_approval_history WHERE pattern_fingerprint = $1",
        pattern_fingerprint,
    )
    if row is None:
        return 0
    return int(row["cnt"])


async def check_promotion_threshold(
    pool: Any,
    pattern_fingerprint: str,
    tool_name: str,
    tool_args: dict[str, Any],
    config: Any,
) -> None:
    """Check if the promotion threshold is met and create a suggestion if needed.

    Reads ``config.promotion_threshold``, ``config.suggestion_cooldown_days``.
    If the approval count >= threshold and no active suggestion / standing rule
    exists for this fingerprint, creates a promotion suggestion.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    pattern_fingerprint:
        Fingerprint for the tool invocation pattern.
    tool_name:
        Tool name for suggestion creation.
    tool_args:
        Tool args for the representative_args on the suggestion.
    config:
        ApprovalsConfig-like object with threshold/cooldown attributes.
    """
    threshold = getattr(config, "promotion_threshold", 5)

    count = await get_approval_count(pool, pattern_fingerprint)
    if count < threshold:
        return

    # Check for an existing standing rule that exactly matches
    rule_row = await pool.fetchrow(
        "SELECT id FROM approval_rules WHERE tool_name = $1 AND active = true LIMIT 1",
        tool_name,
    )
    if rule_row is not None:
        # A standing rule already exists for this tool; check if args match
        # (We do a simple check: if any active rule covers this tool, skip)
        # For more precision we'd evaluate constraints, but for threshold check
        # the spec says "matching standing rule already exists"
        # The spec scenario says: if a matching standing rule already exists, no suggestion.
        # We'll check for rules with no constraints or constraints matching all args.
        active_rules = await pool.fetch(
            "SELECT id, arg_constraints FROM approval_rules WHERE tool_name = $1 AND active = true",
            tool_name,
        )
        for rule_row in active_rules:
            constraints = rule_row["arg_constraints"]
            if isinstance(constraints, str):
                constraints = json.loads(constraints)
            if _args_match_constraints(tool_args, constraints):
                logger.debug(
                    "Existing standing rule covers pattern %s, skipping suggestion",
                    pattern_fingerprint,
                )
                return

    # Check for active/pending suggestion or cooldown
    suggestion_row = await pool.fetchrow(
        "SELECT id, status, cooldown_until FROM autonomy_suggestions "
        "WHERE pattern_fingerprint = $1 AND suggestion_type = 'promotion' "
        "ORDER BY created_at DESC LIMIT 1",
        pattern_fingerprint,
    )
    if suggestion_row is not None:
        status = suggestion_row["status"]
        if status == "pending":
            logger.debug("Active suggestion already exists for pattern %s", pattern_fingerprint)
            return
        if status == "dismissed":
            cooldown_until = suggestion_row["cooldown_until"]
            if cooldown_until is not None:
                now = datetime.now(UTC)
                # Handle both timezone-aware and naive datetimes
                if hasattr(cooldown_until, "tzinfo") and cooldown_until.tzinfo is None:
                    cooldown_until = cooldown_until.replace(tzinfo=UTC)
                if now < cooldown_until:
                    logger.debug(
                        "Suggestion for pattern %s is in cooldown until %s",
                        pattern_fingerprint,
                        cooldown_until,
                    )
                    return

    # Create the promotion suggestion
    from butlers.modules.approvals.autonomy_suggestions import create_promotion_suggestion

    await create_promotion_suggestion(
        pool=pool,
        pattern_fingerprint=pattern_fingerprint,
        tool_name=tool_name,
        representative_args=tool_args,
        approval_count=count,
    )


def _args_match_constraints(tool_args: dict[str, Any], constraints: dict[str, Any]) -> bool:
    """Return True if tool_args satisfies all constraints exactly.

    Parameters
    ----------
    tool_args:
        Actual tool arguments.
    constraints:
        Constraint dict (may use ``{"type": "exact", "value": ...}`` or legacy ``"*"``).

    Returns
    -------
    bool
        True if every constraint key matches the corresponding tool_arg value.
    """
    if not constraints:
        return True

    for key, constraint in constraints.items():
        actual = tool_args.get(key)
        if isinstance(constraint, dict):
            ctype = str(constraint.get("type", "")).lower()
            if ctype == "exact":
                if actual != constraint.get("value"):
                    return False
        elif constraint == "*":
            continue
        else:
            if actual != constraint:
                return False
    return True


async def update_velocity(
    pool: Any,
    state_pool: Any,
    pattern_fingerprint: str,
    config: Any,
) -> None:
    """Compute and store rolling approval velocity for a pattern fingerprint.

    Reads the last N ``time_to_decision_seconds`` values from
    ``autonomy_approval_history`` (N = ``config.velocity_window``) and stores
    the rolling average in the butler's state store under
    ``autonomy:velocity:{pattern_fingerprint}``.

    Parameters
    ----------
    pool:
        asyncpg connection pool for approval history reads.
    state_pool:
        asyncpg connection pool for state store writes (may be same as pool).
    pattern_fingerprint:
        Fingerprint to compute velocity for.
    config:
        ApprovalsConfig-like object with ``velocity_window`` attribute.
    """
    window = getattr(config, "velocity_window", 10)

    rows = await pool.fetch(
        "SELECT time_to_decision_seconds FROM autonomy_approval_history "
        "WHERE pattern_fingerprint = $1 AND time_to_decision_seconds IS NOT NULL "
        "ORDER BY approved_at DESC LIMIT $2",
        pattern_fingerprint,
        window,
    )

    if not rows:
        return

    times = [float(row["time_to_decision_seconds"]) for row in rows]
    avg_seconds = sum(times) / len(times)
    fast_approval = avg_seconds < _FAST_APPROVAL_THRESHOLD_SECONDS

    velocity_data = {
        "avg_seconds": avg_seconds,
        "sample_count": len(times),
        "fast_approval": fast_approval,
        "updated_at": datetime.now(UTC).isoformat(),
    }

    state_key = f"{_VELOCITY_KEY_PREFIX}{pattern_fingerprint}"
    await state_set(state_pool, state_key, velocity_data)


async def get_velocity(state_pool: Any, pattern_fingerprint: str) -> dict[str, Any] | None:
    """Retrieve velocity data from the state store.

    Parameters
    ----------
    state_pool:
        asyncpg connection pool for state store reads.
    pattern_fingerprint:
        Fingerprint to look up.

    Returns
    -------
    dict or None
        Dict with ``avg_seconds``, ``sample_count``, ``fast_approval``,
        ``updated_at`` keys, or None if no data exists.
    """
    state_key = f"{_VELOCITY_KEY_PREFIX}{pattern_fingerprint}"
    return await state_get(state_pool, state_key)
