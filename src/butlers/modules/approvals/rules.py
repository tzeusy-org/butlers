"""Standing approval rules engine — match tool invocations against pre-approved patterns.

Provides the core rule-matching logic that determines whether a tool invocation
can be auto-approved based on standing rules stored in the database.

Constraint types:
    - exact: arg value must equal the constraint value exactly
    - pattern: fnmatch-style glob matching (e.g. '*@mycompany.com')
    - any: always matches regardless of argument value

Specificity:
    When multiple rules match, the most specific rule wins. Specificity is
    determined by counting the number of "pinned" (exact or pattern) constraints.
    A rule with more pinned constraints is considered more specific.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from datetime import UTC, datetime
from typing import Any

from butlers.modules.approvals.models import ApprovalRule

logger = logging.getLogger(__name__)

RULE_MATCH_PRECEDENCE: tuple[str, ...] = (
    "constraint_specificity_desc",
    "bounded_scope_desc",
    "created_at_desc",
    "rule_id_asc",
)


# ---------------------------------------------------------------------------
# Constraint evaluation
# ---------------------------------------------------------------------------


def _evaluate_constraint(
    arg_value: Any,
    constraint: dict[str, Any] | str,
) -> bool:
    """Evaluate a single argument constraint against an actual value.

    Constraint may be:
    - A dict with {"type": "exact"|"pattern"|"any", "value": ...}
    - A plain string/value for backward compat (treated as exact match)
    - The string "*" for backward compat (treated as "any")

    Returns True if the constraint is satisfied.
    """
    # Normalize legacy formats
    if isinstance(constraint, dict) and "type" in constraint:
        ctype = constraint["type"]
        cvalue = constraint.get("value")

        if ctype == "any":
            return True
        elif ctype == "exact":
            return arg_value == cvalue
        elif ctype == "pattern":
            # fnmatch-style glob against string representation
            return fnmatch.fnmatch(str(arg_value), str(cvalue))
        else:
            logger.warning("Unknown constraint type %r, treating as exact", ctype)
            return arg_value == cvalue

    # Legacy: plain value — "*" means any, otherwise exact
    if constraint == "*":
        return True
    return arg_value == constraint


def _constraint_specificity(constraint: dict[str, Any] | str) -> int:
    """Return a specificity score for a single constraint.

    Higher score = more specific:
    - exact: 2
    - pattern: 1
    - any / "*": 0
    """
    if isinstance(constraint, dict) and "type" in constraint:
        ctype = constraint["type"]
        if ctype == "exact":
            return 2
        elif ctype == "pattern":
            return 1
        else:
            return 0

    # Legacy formats
    if constraint == "*":
        return 0
    return 2  # plain value = exact match


def _rule_specificity(arg_constraints: dict[str, Any]) -> int:
    """Compute total specificity score for a rule's constraints.

    Sum of individual constraint specificities. Higher = more specific.
    """
    return sum(_constraint_specificity(c) for c in arg_constraints.values())


def _is_bounded_rule(rule: ApprovalRule) -> bool:
    """Return whether the rule has bounded scope via expiry or max uses."""
    return rule.expires_at is not None or rule.max_uses is not None


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


def _args_match_constraints(
    tool_args: dict[str, Any],
    constraints: dict[str, Any],
) -> bool:
    """Check whether tool_args satisfy all constraint entries.

    Empty constraints ({}) match any args. Each key in constraints
    must have a corresponding match in tool_args.
    """
    for key, constraint in constraints.items():
        if key not in tool_args:
            # The constraint references an arg that wasn't provided
            # For "any" constraints, missing key is acceptable
            if isinstance(constraint, dict) and constraint.get("type") == "any":
                continue
            if constraint == "*":
                continue
            return False
        actual = tool_args[key]
        if not _evaluate_constraint(actual, constraint):
            return False
    return True


def _parse_constraints(raw: Any) -> dict[str, Any]:
    """Parse arg_constraints from DB value (may be JSON string or dict)."""
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    return dict(raw)


async def match_rules(
    pool: Any,
    tool_name: str,
    tool_args: dict[str, Any],
) -> ApprovalRule | None:
    """Find the most specific matching standing approval rule for a tool invocation.

    Fetches active, non-expired rules for the given tool_name from the database,
    filters out rules that exceeded max_uses, evaluates arg_constraints against
    tool_args, and returns the most specific match.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the butler's database.
    tool_name:
        The name of the tool being invoked.
    tool_args:
        The arguments passed to the tool.

    Returns
    -------
    ApprovalRule | None
        The most specific matching rule, or None if no rule matches.
    """
    rows = await pool.fetch(
        "SELECT * FROM approval_rules WHERE tool_name = $1 AND active = true "
        "ORDER BY created_at DESC, id ASC",
        tool_name,
    )

    return match_rules_from_list(tool_name, tool_args, rows)


def match_rules_from_list(
    tool_name: str,
    tool_args: dict[str, Any],
    rules: list[Any],
) -> ApprovalRule | None:
    """Match rules from an already-fetched list (for use without a DB pool).

    This is the pure-logic counterpart of ``match_rules`` that works on
    a pre-fetched list of rule rows/dicts. Useful for testing and for
    the gate module which already fetches rules.

    Parameters
    ----------
    tool_name:
        The name of the tool being invoked.
    tool_args:
        The arguments passed to the tool.
    rules:
        List of rule dicts or asyncpg Records, pre-fetched from DB.

    Returns
    -------
    ApprovalRule | None
        The most specific matching rule, or None if no rule matches.
    """
    now = datetime.now(UTC)
    candidates: list[tuple[int, int, datetime, str, ApprovalRule]] = []

    for rule_data in rules:
        # Normalize to dict if needed (asyncpg Record or dict)
        rule = dict(rule_data) if not isinstance(rule_data, dict) else rule_data

        # Tool name must match
        if rule.get("tool_name") != tool_name:
            continue

        # Check active flag
        if not rule.get("active", True):
            continue

        # Check expiry
        expires_at = rule.get("expires_at")
        if expires_at is not None and expires_at < now:
            continue

        # Check max_uses
        max_uses = rule.get("max_uses")
        use_count = rule.get("use_count", 0)
        if max_uses is not None and use_count >= max_uses:
            continue

        # Parse and check arg constraints
        constraints = _parse_constraints(rule.get("arg_constraints", "{}"))
        if not _args_match_constraints(tool_args, constraints):
            continue

        # Build an ApprovalRule model
        try:
            approval_rule = ApprovalRule.from_row(rule)
        except (KeyError, ValueError):
            logger.warning("Failed to parse rule row: %s", rule)
            continue

        specificity = _rule_specificity(constraints)
        bounded_scope = int(_is_bounded_rule(approval_rule))
        created_at = approval_rule.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        candidates.append(
            (
                specificity,
                bounded_scope,
                created_at,
                str(approval_rule.id),
                approval_rule,
            )
        )

    if not candidates:
        return None

    # Sort by explicit precedence policy:
    # 1) higher specificity first
    # 2) bounded scope before unbounded
    # 3) newer rules before older
    # 4) lexical rule ID for deterministic tie-breaking
    candidates.sort(
        key=lambda item: (
            -item[0],
            -item[1],
            -item[2].timestamp(),
            item[3],
        )
    )
    return candidates[0][4]
