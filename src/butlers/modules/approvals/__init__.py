"""Approvals module — human-in-the-loop approval mechanism for tool invocations."""

from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import (
    ExecutionResult,
    execute_approved_action,
    list_executed_actions,
)
from butlers.modules.approvals.gate import apply_approval_gates, match_standing_rule
from butlers.modules.approvals.module import (
    ApprovalsConfig,
    ApprovalsModule,
    InvalidTransitionError,
    validate_transition,
)
from butlers.modules.approvals.rules import match_rules, match_rules_from_list
from butlers.modules.approvals.sensitivity import suggest_constraints

__all__ = [
    "ApprovalsConfig",
    "ApprovalsModule",
    "ApprovalEventType",
    "ExecutionResult",
    "InvalidTransitionError",
    "apply_approval_gates",
    "execute_approved_action",
    "list_executed_actions",
    "record_approval_event",
    "match_rules",
    "match_rules_from_list",
    "match_standing_rule",
    "suggest_constraints",
    "validate_transition",
]
