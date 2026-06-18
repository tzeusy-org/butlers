"""Spawner — session guardrail checks seam.

Responsible for detecting runaway or budget-exceeding sessions after invocation
completes and before the result is accepted. All checks return a human-readable
reason string when triggered, or ``None`` when the session is within limits.

Extracted from butlers.core.spawner as part of bu-dl98i.7.1 (structural
decomposition into internal seams).  The Spawner continues to use these via
re-exports so existing import paths and test patches remain valid.
"""

from __future__ import annotations

import json
from typing import Any

from butlers.core.tool_call_capture import fingerprint_tool_call_payload

# ---------------------------------------------------------------------------
# Module-level constants (copied from spawner for this seam's standalone use)
# ---------------------------------------------------------------------------

# Number of consecutive identical (name, input) tool call signatures that
# triggers a degenerate-loop guardrail. A conservative threshold keeps
# false-positive rates very low while catching true runaway loops.
# Only adjacent duplicates count — a loop requires the same call repeatedly
# without any different call in between.
_DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD = 6

# Default maximum number of tool calls allowed per session.
# 0 means disabled (no limit enforced).
_DEFAULT_MAX_TOOL_CALLS = 0


def _check_degenerate_tool_loop(
    tool_calls: list[dict[str, Any]],
    *,
    consecutive_threshold: int = _DEGENERATE_TOOL_LOOP_CONSECUTIVE_THRESHOLD,
) -> str | None:
    """Detect a degenerate tool loop in the session's tool-call list.

    A degenerate loop is defined as ``consecutive_threshold`` or more
    back-to-back tool calls with identical ``(name, input)`` signatures.
    Only adjacent duplicates count — non-identical calls reset the streak.

    Parameters
    ----------
    tool_calls:
        Full list of tool call records for the completed session.
    consecutive_threshold:
        Number of consecutive identical calls required to trigger the guard.

    Returns
    -------
    str | None
        A human-readable guardrail reason string when a loop is detected, or
        ``None`` when no degenerate pattern is found.
    """
    if consecutive_threshold <= 0 or len(tool_calls) < consecutive_threshold:
        return None

    def _call_signature(call: dict[str, Any]) -> str:
        name = str(call.get("name", "") or "")
        payload_fingerprint = call.get("input_fingerprint")
        if not isinstance(payload_fingerprint, str) or not payload_fingerprint:
            payload = call.get("input")
            if payload is None:
                payload = call.get("args")
            if payload is None:
                payload = call.get("arguments")
            if payload is None:
                payload = call.get("parameters")
            if isinstance(payload, str):
                stripped = payload.strip()
                if stripped:
                    try:
                        payload = json.loads(stripped)
                    except Exception:
                        pass
            payload_fingerprint = fingerprint_tool_call_payload(payload)
        return f"{name}|{payload_fingerprint}"

    streak = 1
    prev_sig = _call_signature(tool_calls[0])
    for call in tool_calls[1:]:
        sig = _call_signature(call)
        if sig == prev_sig:
            streak += 1
            if streak >= consecutive_threshold:
                return (
                    f"degenerate_tool_loop: {streak} consecutive identical calls to "
                    f"{str(call.get('name', '') or '')!r} detected; "
                    "session terminated to prevent runaway loop"
                )
        else:
            streak = 1
            prev_sig = sig
    return None


def _check_tool_call_budget(
    tool_calls: list[dict[str, Any]],
    *,
    max_tool_calls: int,
) -> str | None:
    """Return a guardrail reason string when the tool-call budget is exceeded.

    Parameters
    ----------
    tool_calls:
        Full list of tool call records for the completed session.
    max_tool_calls:
        Maximum allowed tool calls. ``0`` disables the check.

    Returns
    -------
    str | None
        A reason string when the budget is exceeded, or ``None`` otherwise.
    """
    if max_tool_calls <= 0:
        return None
    count = len(tool_calls)
    if count > max_tool_calls:
        return (
            f"tool_call_budget_exceeded: session made {count} tool calls, "
            f"exceeding budget of {max_tool_calls}"
        )
    return None


def _check_token_budget(
    input_tokens: int | None,
    *,
    max_token_budget: int | None,
) -> str | None:
    """Return a guardrail reason string when the token budget is exceeded.

    Parameters
    ----------
    input_tokens:
        Input token count reported by the adapter. ``None`` means unknown.
    max_token_budget:
        Maximum allowed input tokens. ``None`` disables the check.

    Returns
    -------
    str | None
        A reason string when the budget is exceeded, or ``None`` otherwise.
    """
    if max_token_budget is None or input_tokens is None:
        return None
    if input_tokens > max_token_budget:
        return (
            f"token_budget_exceeded: session consumed {input_tokens:,} input tokens, "
            f"exceeding budget of {max_token_budget:,} "
            f"(+{input_tokens - max_token_budget:,} over)"
        )
    return None
