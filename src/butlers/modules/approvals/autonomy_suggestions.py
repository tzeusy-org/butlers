"""Autonomy suggestions — promotion and demotion suggestion lifecycle.

Provides:
- create_promotion_suggestion: insert a pending promotion suggestion
- create_demotion_suggestion: insert a pending demotion suggestion
- confirm_suggestion: confirm a pending suggestion (creates rule or revokes rule)
- dismiss_suggestion: dismiss with cooldown
- list_suggestions: paginated listing with scope descriptions
- supersede_matching_suggestions: supersede suggestions covered by a new rule
- generate_scope_description: human-readable description of what a rule would approve
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def generate_scope_description(tool_name: str, representative_args: dict[str, Any]) -> str:
    """Generate a human-readable scope description for a promotion suggestion.

    Parameters
    ----------
    tool_name:
        The tool name this suggestion would approve.
    representative_args:
        The exact argument values from the triggering approval.

    Returns
    -------
    str
        Human-readable description like:
        "Auto-approve send_telegram when chat_id = 'mom_123' AND text = 'hello'"
    """
    if not representative_args:
        return f"Auto-approve {tool_name} (no argument constraints)"

    conditions = []
    for key in sorted(representative_args.keys()):
        val = representative_args[key]
        if isinstance(val, str):
            conditions.append(f"{key} = '{val}'")
        else:
            conditions.append(f"{key} = {json.dumps(val)}")

    conditions_str = " AND ".join(conditions)
    return f"Auto-approve {tool_name} when {conditions_str}"


async def create_promotion_suggestion(
    pool: Any,
    pattern_fingerprint: str,
    tool_name: str,
    representative_args: dict[str, Any],
    approval_count: int,
) -> dict[str, Any]:
    """Create a pending promotion suggestion.

    Records a ``promotion_suggested`` audit event.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    pattern_fingerprint:
        SHA-256 fingerprint for the tool invocation pattern.
    tool_name:
        The tool name this suggestion is for.
    representative_args:
        The exact tool_args from the most recent approval that triggered this.
    approval_count:
        Number of manual approvals that triggered this suggestion.

    Returns
    -------
    dict
        The new suggestion row as a dict.
    """
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

    suggestion_id = uuid.uuid4()
    now = datetime.now(UTC)
    scope_description = generate_scope_description(tool_name, representative_args)

    await pool.execute(
        "INSERT INTO autonomy_suggestions "
        "(id, suggestion_type, pattern_fingerprint, tool_name, representative_args, status, "
        "approval_count_at_creation, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        suggestion_id,
        "promotion",
        pattern_fingerprint,
        tool_name,
        json.dumps(representative_args),
        "pending",
        approval_count,
        now,
    )

    await record_approval_event(
        pool,
        ApprovalEventType.PROMOTION_SUGGESTED,
        actor="system:autonomy-tracker",
        action_id=None,
        rule_id=None,
        reason=scope_description,
        metadata={
            "pattern_fingerprint": pattern_fingerprint,
            "tool_name": tool_name,
            "approval_count": approval_count,
            "scope_description": scope_description,
        },
    )

    return {
        "id": str(suggestion_id),
        "suggestion_type": "promotion",
        "pattern_fingerprint": pattern_fingerprint,
        "tool_name": tool_name,
        "representative_args": representative_args,
        "status": "pending",
        "approval_count_at_creation": approval_count,
        "created_at": now.isoformat(),
        "decided_at": None,
        "decided_by": None,
        "resulting_rule_id": None,
        "cooldown_until": None,
        "dismissal_reason": None,
        "scope_description": scope_description,
    }


async def create_demotion_suggestion(
    pool: Any,
    action: Any,
    rule_id: uuid.UUID,
    error_details: str,
) -> dict[str, Any]:
    """Create a pending demotion suggestion when an auto-approved action fails.

    Records a ``demotion_suggested`` audit event.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    action:
        PendingAction that failed execution (must have id, tool_name, tool_args).
    rule_id:
        UUID of the standing approval rule that auto-approved the action.
    error_details:
        The execution error message.

    Returns
    -------
    dict
        The new suggestion row as a dict.
    """
    from butlers.modules.approvals.autonomy_tracker import compute_fingerprint
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

    suggestion_id = uuid.uuid4()
    now = datetime.now(UTC)
    pattern_fingerprint = compute_fingerprint(action.tool_name, action.tool_args)
    scope_description = generate_scope_description(action.tool_name, action.tool_args)

    await pool.execute(
        "INSERT INTO autonomy_suggestions "
        "(id, suggestion_type, pattern_fingerprint, tool_name, representative_args, status, "
        "approval_count_at_creation, created_at, resulting_rule_id) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
        suggestion_id,
        "demotion",
        pattern_fingerprint,
        action.tool_name,
        json.dumps(action.tool_args),
        "pending",
        0,
        now,
        rule_id,
    )

    await record_approval_event(
        pool,
        ApprovalEventType.DEMOTION_SUGGESTED,
        actor="system:executor",
        action_id=action.id,
        rule_id=rule_id,
        reason=f"Auto-approved action failed: {error_details}",
        metadata={
            "pattern_fingerprint": pattern_fingerprint,
            "tool_name": action.tool_name,
            "error_details": error_details,
            "scope_description": scope_description,
        },
    )

    return {
        "id": str(suggestion_id),
        "suggestion_type": "demotion",
        "pattern_fingerprint": pattern_fingerprint,
        "tool_name": action.tool_name,
        "representative_args": action.tool_args,
        "status": "pending",
        "approval_count_at_creation": 0,
        "created_at": now.isoformat(),
        "decided_at": None,
        "decided_by": None,
        "resulting_rule_id": str(rule_id),
        "cooldown_until": None,
        "dismissal_reason": None,
        "scope_description": scope_description,
    }


async def confirm_suggestion(
    pool: Any,
    suggestion_id: str | uuid.UUID,
    actor: str,
) -> dict[str, Any]:
    """Confirm a pending promotion or demotion suggestion.

    For promotion suggestions: creates a standing rule with exact constraints
    and transitions suggestion to ``confirmed``.

    For demotion suggestions: revokes the referenced standing rule and
    transitions suggestion to ``confirmed``.

    Records the appropriate audit event.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    suggestion_id:
        UUID of the suggestion to confirm.
    actor:
        Human actor ID performing the confirmation.

    Returns
    -------
    dict
        Updated suggestion dict or ``{"error": "<message>"}``.
    """
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

    try:
        parsed_id = uuid.UUID(str(suggestion_id))
    except ValueError:
        return {"error": f"Invalid suggestion_id: {suggestion_id}"}

    row = await pool.fetchrow(
        "SELECT * FROM autonomy_suggestions WHERE id = $1",
        parsed_id,
    )
    if row is None:
        return {"error": f"Suggestion not found: {suggestion_id}"}

    status = row["status"]
    if status != "pending":
        return {"error": f"Suggestion is already {status} and cannot be confirmed"}

    suggestion_type = row["suggestion_type"]
    tool_name = row["tool_name"]
    representative_args_raw = row["representative_args"]
    if isinstance(representative_args_raw, str):
        representative_args = json.loads(representative_args_raw)
    else:
        representative_args = dict(representative_args_raw)

    referenced_rule_id_raw = (
        row.get("resulting_rule_id") if hasattr(row, "get") else row["resulting_rule_id"]
    )
    referenced_rule_id: uuid.UUID | None = None
    if referenced_rule_id_raw is not None:
        try:
            referenced_rule_id = uuid.UUID(str(referenced_rule_id_raw))
        except ValueError:
            pass

    now = datetime.now(UTC)
    new_rule_id: uuid.UUID | None = None
    event_type: ApprovalEventType

    if suggestion_type == "promotion":
        # Create a standing rule with exact constraints for ALL args
        new_rule_id = uuid.uuid4()
        arg_constraints = {
            key: {"type": "exact", "value": val} for key, val in representative_args.items()
        }
        scope_description = generate_scope_description(tool_name, representative_args)
        description = f"Auto-created from promotion suggestion: {scope_description}"

        await pool.execute(
            "INSERT INTO approval_rules "
            "(id, tool_name, arg_constraints, description, created_at, max_uses, active) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            new_rule_id,
            tool_name,
            json.dumps(arg_constraints),
            description,
            now,
            None,
            True,
        )

        # Update suggestion
        await pool.execute(
            "UPDATE autonomy_suggestions "
            "SET status = $1, decided_at = $2, decided_by = $3, resulting_rule_id = $4 "
            "WHERE id = $5",
            "confirmed",
            now,
            f"human:{actor}",
            new_rule_id,
            parsed_id,
        )

        await record_approval_event(
            pool,
            ApprovalEventType.RULE_CREATED,
            actor=f"user:{actor}",
            rule_id=new_rule_id,
            reason="Rule created from confirmed promotion suggestion",
            metadata={"tool_name": tool_name, "suggestion_id": str(parsed_id)},
            occurred_at=now,
        )

        event_type = ApprovalEventType.PROMOTION_CONFIRMED

    else:  # demotion
        # Revoke the referenced rule
        if referenced_rule_id is not None:
            await pool.execute(
                "UPDATE approval_rules SET active = false WHERE id = $1",
                referenced_rule_id,
            )
            await record_approval_event(
                pool,
                ApprovalEventType.RULE_REVOKED,
                actor=f"user:{actor}",
                rule_id=referenced_rule_id,
                reason=f"Rule revoked via confirmed demotion suggestion {parsed_id}",
                occurred_at=now,
            )

        # Update suggestion
        await pool.execute(
            "UPDATE autonomy_suggestions "
            "SET status = $1, decided_at = $2, decided_by = $3 "
            "WHERE id = $4",
            "confirmed",
            now,
            f"human:{actor}",
            parsed_id,
        )

        event_type = ApprovalEventType.DEMOTION_CONFIRMED

    await record_approval_event(
        pool,
        event_type,
        actor=f"user:{actor}",
        rule_id=new_rule_id or referenced_rule_id,
        reason=f"Suggestion {parsed_id} confirmed",
        metadata={
            "suggestion_id": str(parsed_id),
            "suggestion_type": suggestion_type,
            "tool_name": tool_name,
        },
        occurred_at=now,
    )

    updated_row = await pool.fetchrow(
        "SELECT * FROM autonomy_suggestions WHERE id = $1",
        parsed_id,
    )
    return _row_to_dict(updated_row)


async def dismiss_suggestion(
    pool: Any,
    suggestion_id: str | uuid.UUID,
    actor: str,
    reason: str | None = None,
    cooldown_days: int = 30,
) -> dict[str, Any]:
    """Dismiss a pending promotion or demotion suggestion.

    Transitions to ``dismissed`` status with cooldown applied.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    suggestion_id:
        UUID of the suggestion to dismiss.
    actor:
        Human actor ID performing the dismissal.
    reason:
        Optional human-readable reason for dismissal.
    cooldown_days:
        Days before the pattern can produce another suggestion (default 30).

    Returns
    -------
    dict
        Updated suggestion dict or ``{"error": "<message>"}``.
    """
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

    try:
        parsed_id = uuid.UUID(str(suggestion_id))
    except ValueError:
        return {"error": f"Invalid suggestion_id: {suggestion_id}"}

    row = await pool.fetchrow(
        "SELECT * FROM autonomy_suggestions WHERE id = $1",
        parsed_id,
    )
    if row is None:
        return {"error": f"Suggestion not found: {suggestion_id}"}

    status = row["status"]
    if status != "pending":
        return {"error": f"Suggestion is already {status} and cannot be dismissed"}

    suggestion_type = row["suggestion_type"]
    tool_name = row["tool_name"]
    now = datetime.now(UTC)
    cooldown_until = now + timedelta(days=cooldown_days)

    await pool.execute(
        "UPDATE autonomy_suggestions "
        "SET status = $1, decided_at = $2, decided_by = $3, "
        "cooldown_until = $4, dismissal_reason = $5 "
        "WHERE id = $6",
        "dismissed",
        now,
        f"human:{actor}",
        cooldown_until,
        reason,
        parsed_id,
    )

    event_type = (
        ApprovalEventType.PROMOTION_DISMISSED
        if suggestion_type == "promotion"
        else ApprovalEventType.DEMOTION_DISMISSED
    )
    await record_approval_event(
        pool,
        event_type,
        actor=f"user:{actor}",
        reason=reason or f"Suggestion {parsed_id} dismissed",
        metadata={
            "suggestion_id": str(parsed_id),
            "suggestion_type": suggestion_type,
            "tool_name": tool_name,
            "cooldown_until": cooldown_until.isoformat(),
        },
    )

    updated_row = await pool.fetchrow(
        "SELECT * FROM autonomy_suggestions WHERE id = $1",
        parsed_id,
    )
    return _row_to_dict(updated_row)


async def list_suggestions(
    pool: Any,
    status: str | None = "pending",
    suggestion_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List promotion/demotion suggestions with optional filters.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    status:
        Status filter (default ``"pending"``). Pass ``"all"`` or ``None`` to
        return all statuses.
    suggestion_type:
        Filter by ``"promotion"`` or ``"demotion"`` (or ``None`` for both).
    limit:
        Maximum number of results (default 20).
    offset:
        Pagination offset (default 0).

    Returns
    -------
    list[dict]
        List of suggestion dicts ordered by created_at DESC, each including a
        ``scope_description`` field.
    """
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if status and status != "all":
        conditions.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    if suggestion_type is not None:
        conditions.append(f"suggestion_type = ${idx}")
        params.append(suggestion_type)
        idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        f"SELECT * FROM autonomy_suggestions {where_clause} "
        f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
    )
    params.extend([limit, offset])

    rows = await pool.fetch(query, *params)
    return [_row_to_dict(row) for row in rows]


async def supersede_matching_suggestions(
    pool: Any,
    tool_name: str,
    arg_constraints: dict[str, Any],
) -> int:
    """Supersede pending promotion suggestions covered by a new standing rule.

    Finds all pending promotion suggestions for ``tool_name`` whose
    ``representative_args`` match the new rule's ``arg_constraints``, then
    transitions them to ``superseded``.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    tool_name:
        Tool name of the newly created rule.
    arg_constraints:
        Constraint dict of the new rule.

    Returns
    -------
    int
        Number of suggestions superseded.
    """
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event

    rows = await pool.fetch(
        "SELECT id, representative_args FROM autonomy_suggestions "
        "WHERE tool_name = $1 AND status = 'pending' AND suggestion_type = 'promotion'",
        tool_name,
    )

    superseded_count = 0
    now = datetime.now(UTC)

    for row in rows:
        representative_args_raw = row["representative_args"]
        if isinstance(representative_args_raw, str):
            rep_args = json.loads(representative_args_raw)
        else:
            rep_args = dict(representative_args_raw)

        if _suggestion_covered_by_constraints(rep_args, arg_constraints):
            suggestion_id = row["id"]
            await pool.execute(
                "UPDATE autonomy_suggestions SET status = $1, decided_at = $2 WHERE id = $3",
                "superseded",
                now,
                suggestion_id,
            )
            await record_approval_event(
                pool,
                ApprovalEventType.PROMOTION_SUPERSEDED,
                actor="system:rule-creation",
                reason=f"Superseded by new rule for {tool_name}",
                metadata={
                    "suggestion_id": str(suggestion_id),
                    "tool_name": tool_name,
                },
            )
            superseded_count += 1

    return superseded_count


def _suggestion_covered_by_constraints(
    representative_args: dict[str, Any],
    arg_constraints: dict[str, Any],
) -> bool:
    """Return True if arg_constraints covers representative_args.

    The new rule covers the suggestion if every arg in representative_args
    satisfies the corresponding constraint (or the constraint is a wildcard).
    """
    if not arg_constraints:
        # Unconstrained rule covers everything
        return True

    for key, actual_val in representative_args.items():
        constraint = arg_constraints.get(key)
        if constraint is None:
            # New rule doesn't constrain this arg — it's open
            continue
        if isinstance(constraint, dict):
            ctype = str(constraint.get("type", "")).lower()
            if ctype == "exact":
                if actual_val != constraint.get("value"):
                    return False
        elif constraint != "*":
            if actual_val != constraint:
                return False

    return True


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a database row to a suggestion dict including scope_description."""
    if row is None:
        return {}

    representative_args_raw = row["representative_args"] if hasattr(row, "__getitem__") else None
    if isinstance(representative_args_raw, str):
        representative_args = json.loads(representative_args_raw)
    elif representative_args_raw is None:
        representative_args = {}
    else:
        representative_args = dict(representative_args_raw)

    tool_name = row["tool_name"] if hasattr(row, "__getitem__") else ""
    scope_description = generate_scope_description(tool_name, representative_args)

    def _get(key: str, default: Any = None) -> Any:
        if hasattr(row, "get"):
            return row.get(key, default)
        try:
            return row[key]
        except (KeyError, IndexError):
            return default

    result_rule_id = _get("resulting_rule_id")
    cooldown_until = _get("cooldown_until")
    decided_at = _get("decided_at")

    created_at = _get("created_at")
    created_at_str = (
        created_at.isoformat()
        if hasattr(created_at, "isoformat")
        else (str(created_at) if created_at else None)
    )
    decided_at_str = (
        decided_at.isoformat()
        if hasattr(decided_at, "isoformat")
        else (str(decided_at) if decided_at else None)
    )
    cooldown_until_str = (
        cooldown_until.isoformat()
        if hasattr(cooldown_until, "isoformat")
        else (str(cooldown_until) if cooldown_until else None)
    )
    return {
        "id": str(_get("id", "")),
        "suggestion_type": _get("suggestion_type", "promotion"),
        "pattern_fingerprint": _get("pattern_fingerprint", ""),
        "tool_name": tool_name,
        "representative_args": representative_args,
        "status": _get("status", "pending"),
        "approval_count_at_creation": _get("approval_count_at_creation", 0),
        "created_at": created_at_str,
        "decided_at": decided_at_str,
        "decided_by": _get("decided_by"),
        "resulting_rule_id": str(result_rule_id) if result_rule_id is not None else None,
        "cooldown_until": cooldown_until_str,
        "dismissal_reason": _get("dismissal_reason"),
        "scope_description": scope_description,
    }
