"""The legacy approvals paths never decide or run a goal effect (bu-m1u7zy.6).

A pending action with goal lineage (``goal_id IS NOT NULL``) was parked by the
capability broker (RFC 0038 D13). Its execution must go through
``Broker.execute_approved``, which re-checks Stop, the grant and the catalog,
records the effect ledger and signs the assertion. The dashboard REST
decision and retry routes, the module's approve and dispatch tools, and the
shared executor therefore refuse it with one typed error code. Rejecting a
goal action stays allowed: it never runs anything. The owner's approval of a goal action
reaches the domain row only through the broker's owner-decision path
(``capability_broker.owner_decisions``, via ``goal_park.decide_goal_effect``).
"""

from __future__ import annotations

from typing import Any

GOAL_EFFECT_REQUIRES_BROKER = "goal_effect_requires_broker"


def is_goal_effect(row: Any) -> bool:
    """``row`` is a pending-action record or dict read with its ``goal_id``."""
    if row is None:
        return False
    getter = getattr(row, "goal_id", None) if not hasattr(row, "get") else row.get("goal_id")
    return getter is not None


def goal_effect_refusal(action_id: Any) -> dict[str, str]:
    return {
        "error": (f"Action {action_id} is a goal effect; only the capability broker may run it"),
        "error_code": GOAL_EFFECT_REQUIRES_BROKER,
    }
