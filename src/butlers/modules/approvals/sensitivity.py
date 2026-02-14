"""Sensitivity classification for tool arguments and constraint suggestion.

Provides heuristic detection of safety-critical arguments, a resolution
function that combines explicit module declarations, heuristic matching,
and a safe default, and a constraint suggestion engine for building standing
approval rules.

Resolution order (highest priority first):
    1. Explicit declaration via ``Module.tool_metadata()``
    2. Heuristic based on argument name
    3. Default: not sensitive

Constraint suggestion:
    - Sensitive args -> exact constraint (pinned to current value)
    - Non-sensitive args -> any constraint
"""

from __future__ import annotations

from typing import Any

from butlers.modules.base import Module, ToolMeta

# Argument names that are heuristically considered sensitive.
# Matched case-insensitively against the exact argument name.
SENSITIVE_ARG_NAMES: frozenset[str] = frozenset(
    {
        "to",
        "recipient",
        "recipients",
        "email",
        "address",
        "url",
        "uri",
        "amount",
        "price",
        "cost",
        "account",
    }
)


def is_sensitive_by_heuristic(arg_name: str) -> bool:
    """Return True if *arg_name* matches a known sensitive pattern.

    Matching is case-insensitive against the canonical set of sensitive
    argument names.
    """
    return arg_name.lower() in SENSITIVE_ARG_NAMES


def resolve_arg_sensitivity(
    tool_name: str,
    arg_name: str,
    module: Module | None = None,
) -> bool:
    """Determine whether *arg_name* on *tool_name* is sensitive.

    Resolution order:
        1. Explicit declaration in ``module.tool_metadata()`` — if the module
           provides a ``ToolMeta`` for *tool_name* that lists *arg_name*,
           that value wins.
        2. Heuristic — if *arg_name* matches a known sensitive pattern, it is
           considered sensitive.
        3. Default — the argument is **not** sensitive.

    Parameters
    ----------
    tool_name:
        The name of the MCP tool.
    arg_name:
        The name of the argument to classify.
    module:
        The ``Module`` instance that registered *tool_name*.  When ``None``
        (e.g. for core tools), only heuristic and default apply.

    Returns
    -------
    bool
        ``True`` if the argument is safety-critical.
    """
    # 1. Explicit declaration
    if module is not None:
        metadata = module.tool_metadata()
        tool_meta: ToolMeta | None = metadata.get(tool_name)
        if tool_meta is not None and arg_name in tool_meta.arg_sensitivities:
            return tool_meta.arg_sensitivities[arg_name]

    # 2. Heuristic
    if is_sensitive_by_heuristic(arg_name):
        return True

    # 3. Default
    return False


def classify_tool_args(
    tool_name: str,
    arg_names: list[str],
    module: Module | None = None,
) -> dict[str, bool]:
    """Classify all arguments of a tool as sensitive or not.

    Convenience wrapper around ``resolve_arg_sensitivity`` that processes
    an entire argument list at once.

    Returns a dict mapping each argument name to its sensitivity flag.
    """
    return {arg: resolve_arg_sensitivity(tool_name, arg, module) for arg in arg_names}


def suggest_constraints(
    tool_name: str,
    tool_args: dict[str, Any],
    module: Module | None = None,
) -> dict[str, dict[str, Any]]:
    """Suggest arg constraints for a standing approval rule.

    For each argument in *tool_args*:
    - If the argument is sensitive (per module metadata or heuristic), suggest
      an ``exact`` constraint pinned to the current value.
    - If the argument is not sensitive, suggest an ``any`` constraint.

    Parameters
    ----------
    tool_name:
        The name of the MCP tool.
    tool_args:
        The actual arguments from the tool invocation.
    module:
        The ``Module`` instance that registered *tool_name*. When ``None``,
        only heuristic and default classification apply.

    Returns
    -------
    dict[str, dict[str, Any]]
        Mapping of argument name to constraint dict with ``type`` and
        optionally ``value`` keys.
    """
    constraints: dict[str, dict[str, Any]] = {}

    for arg_name, arg_value in tool_args.items():
        is_sensitive = resolve_arg_sensitivity(tool_name, arg_name, module)
        if is_sensitive:
            constraints[arg_name] = {"type": "exact", "value": arg_value}
        else:
            constraints[arg_name] = {"type": "any"}

    return constraints


def redact_tool_args(
    tool_name: str,
    tool_args: dict[str, Any],
    module: Module | None = None,
) -> dict[str, Any]:
    """Redact sensitive arguments in tool_args for safe API exposure.

    For each argument in *tool_args*:
    - If the argument is sensitive (per module metadata or heuristic),
      replace its value with the string "[REDACTED]".
    - If the argument is not sensitive, preserve the original value.

    Parameters
    ----------
    tool_name:
        The name of the MCP tool.
    tool_args:
        The actual arguments from the tool invocation.
    module:
        The ``Module`` instance that registered *tool_name*. When ``None``,
        only heuristic and default classification apply.

    Returns
    -------
    dict[str, Any]
        A copy of tool_args with sensitive values redacted.
    """
    redacted: dict[str, Any] = {}

    for arg_name, arg_value in tool_args.items():
        is_sensitive = resolve_arg_sensitivity(tool_name, arg_name, module)
        if is_sensitive:
            redacted[arg_name] = "[REDACTED]"
        else:
            redacted[arg_name] = arg_value

    return redacted


def redact_constraints(
    tool_name: str,
    arg_constraints: dict[str, dict[str, Any]],
    module: Module | None = None,
) -> dict[str, dict[str, Any]]:
    """Redact sensitive constraint values for safe API exposure.

    For each constraint in *arg_constraints*:
    - If the argument is sensitive and the constraint has a "value" field,
      replace the value with "[REDACTED]".
    - If the argument is not sensitive, preserve the original constraint.

    Parameters
    ----------
    tool_name:
        The name of the MCP tool.
    arg_constraints:
        The constraint dict from a standing rule.
    module:
        The ``Module`` instance that registered *tool_name*. When ``None``,
        only heuristic and default classification apply.

    Returns
    -------
    dict[str, dict[str, Any]]
        A copy of arg_constraints with sensitive values redacted.
    """
    redacted: dict[str, dict[str, Any]] = {}

    for arg_name, constraint in arg_constraints.items():
        is_sensitive = resolve_arg_sensitivity(tool_name, arg_name, module)
        if is_sensitive and "value" in constraint:
            redacted[arg_name] = {**constraint, "value": "[REDACTED]"}
        else:
            redacted[arg_name] = constraint

    return redacted
