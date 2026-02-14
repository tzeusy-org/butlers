"""Redaction utilities for sensitive data in approval payloads.

Provides functions to redact sensitive fields from tool arguments, execution
results, and agent summaries before persistence or presentation. Redaction
is applied based on sensitivity classification from the sensitivity module.

Redaction strategy:
- Sensitive fields are replaced with a redaction marker
- Structure is preserved for audit/debugging purposes
- Original values are never logged or persisted in cleartext
"""

from __future__ import annotations

import copy
from typing import Any

from butlers.modules.approvals.sensitivity import resolve_arg_sensitivity
from butlers.modules.base import Module

REDACTION_MARKER = "***REDACTED***"


def redact_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    module: Module | None = None,
) -> dict[str, Any]:
    """Redact sensitive arguments from a tool invocation payload.

    Returns a new dictionary with sensitive values replaced by the
    redaction marker. Non-sensitive values are preserved as-is.

    Parameters
    ----------
    tool_name:
        The name of the MCP tool.
    tool_args:
        The original tool arguments dict.
    module:
        The Module instance that registered the tool (if available).

    Returns
    -------
    dict[str, Any]
        A copy of tool_args with sensitive values redacted.
    """
    return {
        arg_name: REDACTION_MARKER
        if resolve_arg_sensitivity(tool_name, arg_name, module)
        else arg_value
        for arg_name, arg_value in tool_args.items()
    }


def redact_execution_result(result: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive fields from an execution result payload.

    Scans the result dict for known sensitive patterns (error messages
    containing tokens, URLs with credentials, etc.) and redacts them.

    Currently implements conservative redaction:
    - Full error messages are redacted (may contain secrets in stack traces)
    - Result values are preserved (assumed to be controlled by tool impl)

    Parameters
    ----------
    result:
        The execution result dict (may include 'result', 'error', 'success').

    Returns
    -------
    dict[str, Any]
        A deep copy of the result with sensitive fields redacted.
    """
    # Use deep copy to prevent mutations from leaking to nested structures
    redacted = copy.deepcopy(result)

    # Redact error messages (may contain secrets in exceptions/stack traces)
    if "error" in redacted and redacted["error"] is not None:
        redacted["error"] = REDACTION_MARKER

    # Preserve result for now (assume tool implementations control exposure)
    # Future: add pluggable redaction hooks for specific tool return types

    return redacted


def should_redact_for_presentation(viewer: str, owner: str | None) -> bool:
    """Determine if approval details should be redacted for a viewer.

    Implements access control logic: only the action owner (if known)
    should see unredacted sensitive details.

    Parameters
    ----------
    viewer:
        The user requesting access to the approval details.
    owner:
        The user who requested the approval (if known).

    Returns
    -------
    bool
        True if sensitive fields should be redacted for this viewer.
    """
    # If no owner is known, redact for everyone
    if owner is None:
        return True

    # Only the owner sees unredacted details
    return viewer != owner
