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
from butlers.modules.approvals.redaction import (
    REDACTION_MARKER,
    redact_execution_result,
    redact_tool_args,
    should_redact_for_presentation,
)
from butlers.modules.approvals.retention import (
    RetentionPolicy,
    cleanup_old_actions,
    cleanup_old_events,
    cleanup_old_rules,
    run_retention_cleanup,
)
from butlers.modules.approvals.rules import match_rules, match_rules_from_list
from butlers.modules.approvals.sensitivity import suggest_constraints

__all__ = [
    "ApprovalsConfig",
    "ApprovalsModule",
    "ApprovalEventType",
    "ExecutionResult",
    "InvalidTransitionError",
    "REDACTION_MARKER",
    "RetentionPolicy",
    "apply_approval_gates",
    "cleanup_old_actions",
    "cleanup_old_events",
    "cleanup_old_rules",
    "execute_approved_action",
    "list_executed_actions",
    "match_rules",
    "match_rules_from_list",
    "match_standing_rule",
    "record_approval_event",
    "redact_execution_result",
    "redact_tool_args",
    "run_retention_cleanup",
    "should_redact_for_presentation",
    "suggest_constraints",
    "validate_transition",
]
