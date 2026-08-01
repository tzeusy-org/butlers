"""Deterministic day-close prose admission predicate.

``clarify-chronicles-narrative-truth`` design.md decision 2: an admissible
day-close cache entry is non-empty, owner-facing retrospective prose bound by
its structured ``date_label`` to the closed local day. The cache writer (and,
on read, the cache reader) apply the SAME deterministic shape predicate
before persisting or returning any candidate — never a model judgment, never
a second LLM call. The predicate fails closed: anything not affirmatively
prose-shaped is rejected.

This module owns only the shape half of admission (``classify_prose_shape``)
and the date-binding half (``date_label_matches``). Callers combine both into
a single ``invalid_reason`` (``inadmissible_prose`` | ``date_mismatch``).
"""

from __future__ import annotations

import ast
import json
import re

# A fenced code block anywhere in the candidate is a strong non-prose signal
# (a raw tool/session trace, not retrospective narration).
_CODE_FENCE_RE = re.compile(r"```")

# Machine role / protocol framing a day-close narration should never start
# with (a leaked transcript line or assignment-form tool payload, not
# owner-facing prose).
_PROTOCOL_MARKER_RE = re.compile(
    r"^\s*(?:system|assistant|user|(?:tool|function)(?:[_ -]*(?:calls?|result))?)\s*(?::|=)",
    re.IGNORECASE,
)

# Tool-call / agent-protocol payload markers.
_TOOL_CALL_MARKER_RE = re.compile(
    r"<function_calls>|<invoke[\s>]|<parameter[\s>]|\btool_use\b|\btool_result\b",
    re.IGNORECASE,
)

# Execution-planning / planning-verb preambles: the model narrating its own
# next action rather than producing retrospective prose.
_PLANNING_PREAMBLE_RE = re.compile(
    r"^\s*(I(?:'|’)ll|I will|I(?:'|’)m going to|Let me|First,? I|"
    r"I need to|Now I(?:'|’)ll|Okay,? I(?:'|’)ll|Sure,? I(?:'|’)ll)\b",
    re.IGNORECASE,
)

# A heading that frames the answer as an execution plan is control-plane
# scaffolding, not the completed-day narration the cache may retain.
_EXECUTION_SCAFFOLD_RE = re.compile(
    r"^\s*(?:execution\s+)?(?:plan|steps?|next\s+steps?)\s*:", re.IGNORECASE
)

INADMISSIBLE_PROSE = "inadmissible_prose"
DATE_MISMATCH = "date_mismatch"


def classify_prose_shape(text: str | None) -> str | None:
    """Deterministic shape predicate for a day-close prose candidate.

    Returns ``None`` when the shape is admissible, else ``inadmissible_prose``.
    Does not check date-label binding; see ``date_label_matches``.

    Rejects: empty/whitespace-only text, a fenced code block, a leaked
    machine-role/protocol prefix, a tool-call/agent-protocol payload, a
    planning-verb preamble, or a candidate that is itself a serialized JSON or
    Python-literal container. Fails closed: only affirmatively prose-shaped
    text passes.
    """
    if text is None:
        return INADMISSIBLE_PROSE
    stripped = text.strip()
    if not stripped:
        return INADMISSIBLE_PROSE
    if _CODE_FENCE_RE.search(stripped):
        return INADMISSIBLE_PROSE
    if _TOOL_CALL_MARKER_RE.search(stripped):
        return INADMISSIBLE_PROSE
    if _PROTOCOL_MARKER_RE.match(stripped):
        return INADMISSIBLE_PROSE
    if _PLANNING_PREAMBLE_RE.match(stripped):
        return INADMISSIBLE_PROSE
    if _EXECUTION_SCAFFOLD_RE.match(stripped):
        return INADMISSIBLE_PROSE
    # JSON containers begin with ``{``/``[``; Python literal containers can
    # also begin with ``(`` (tuple) or ``set`` (empty sets accept arbitrary
    # source whitespace). Let the literal parser normalize candidates instead
    # of matching one textual empty-set spelling. A parenthetical narrative or
    # ordinary prose beginning with ``set`` that is not a parseable literal
    # stays admissible.
    if stripped[0] in "{[(" or stripped.startswith("set"):
        try:
            json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                pass
            else:
                if isinstance(parsed, (dict, list, set, tuple)):
                    return INADMISSIBLE_PROSE
        else:
            return INADMISSIBLE_PROSE
    return None


def date_label_matches(date_label: str | None, expected_date_iso: str) -> bool:
    """True when the candidate's structured date_label binds to the closed local day."""
    return date_label is not None and date_label == expected_date_iso


def classify_day_close_candidate(
    text: str | None, *, date_label: str | None, expected_date_iso: str
) -> str | None:
    """Full admission check: shape first, then date-label binding.

    Returns ``None`` when admissible, else ``inadmissible_prose`` or
    ``date_mismatch``. Shape is checked first so an empty/tool-trace
    candidate is never reported as merely a date mismatch.
    """
    shape_reason = classify_prose_shape(text)
    if shape_reason is not None:
        return shape_reason
    if not date_label_matches(date_label, expected_date_iso):
        return DATE_MISMATCH
    return None


__all__ = [
    "DATE_MISMATCH",
    "INADMISSIBLE_PROSE",
    "classify_day_close_candidate",
    "classify_prose_shape",
    "date_label_matches",
]
