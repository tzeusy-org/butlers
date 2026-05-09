"""Templated fallback paragraphs for each dashboard briefing state class.

elaborate_fallback(state, state_class) -> str
    Returns a fully-canned elaboration paragraph for the given state class.
    Each paragraph must comply with the dashboard voice rules:
        - Past tense for events, present tense for state.
        - No future tense ("will be", "is going to").
        - No first-person pronouns (I, we, us, our).
        - No exclamation marks.
        - No em-dashes.
        - No hedging adverbs (currently, presently, just, simply, basically).

Design reference: openspec/changes/dashboard-overview-briefing/design.md D2.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Fallback paragraph table
#
# Keys are the five state_class values. Values are the canned paragraphs
# served when the LLM is unavailable, timed out, returned empty, or failed
# the voice lint. Each paragraph is written in butler voice per the
# dashboard design-language voice rules.
#
# Voice compliance checklist (per design-language.md):
#   - Past tense for completed events, present tense for current state.
#   - No future tense ("will be", "is going to").
#   - No first person (I, we, us, our).
#   - No exclamation marks.
#   - No em-dashes.
#   - No hedging adverbs (currently, presently, just, simply, basically).
#   - No "your" when "the" works.
# ---------------------------------------------------------------------------

_FALLBACK_TABLE: dict[str, str] = {
    "urgent": (
        "One or more items in the attention list carry high severity and need a decision now. "
        "Open each flagged item to review what the butler reported and what action is available."
    ),
    "busy": (
        "Several items are waiting across the attention list. "
        "None reached high severity, but the queue is long enough that a review pass is warranted."
    ),
    "mild": (
        "One or two items surfaced since the last review cycle. "
        "None carry high severity. A brief look at the attention list is enough to clear them."
    ),
    "degraded-quiet": (
        "No attention items are waiting, but at least one butler is reporting a degraded state. "
        "The system continues to operate, but the affected butler may not be processing "
        "its full workload."
    ),
    "quiet": (
        "All butlers are healthy and the attention list is empty. "
        "The system ran its most recent cycles without flagging anything for review."
    ),
}


def elaborate_fallback(state: dict, state_class: str) -> str:
    """Return the templated fallback paragraph for a given state class.

    The paragraph is fully canned (no LLM) and is guaranteed to comply
    with the dashboard voice rules.

    Args:
        state: The current dashboard state dict (not used in v1 canned
               paragraphs, kept in the signature for API stability).
        state_class: One of the five state class values.

    Returns:
        A one-to-two sentence elaboration paragraph in butler voice.
        Falls back to the quiet paragraph if state_class is unrecognised.
    """
    return _FALLBACK_TABLE.get(state_class, _FALLBACK_TABLE["quiet"])
