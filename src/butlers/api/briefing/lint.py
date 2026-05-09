"""Post-generation voice lint for LLM elaboration responses.

voice_lint_passes(text) -> bool
    Returns True if the text complies with all dashboard voice rules,
    False if any banned token is detected.

Banned tokens (from design.md D5):
    - Exclamation marks (!)
    - Em-dashes (U+2014)
    - First-person pronouns: I, we, us, our (word-boundary matched)
    - Future-tense markers: "will be", "is going to" (word-boundary matched)
    - Hedging adverbs: currently, presently, just, simply, basically
      (word-boundary matched, so "factually" does NOT match "actually" etc.)

Word-boundary note: the regex uses r'\b' so that "factually" does not match
the rule for "actually". The hedging-adverb list does not include "actually"
(the spec cites it only as a false-positive example), but the same word-boundary
logic protects all token matches from substring collisions.

Design reference: openspec/changes/dashboard-overview-briefing/design.md D5.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled patterns (module-level for efficiency)
# ---------------------------------------------------------------------------

# Literal characters that indicate a violation.
_EXCLAMATION = re.compile(r"!")
_EM_DASH = re.compile(r"—")  # U+2014 —

# Word-boundary patterns (case-insensitive).
# \b ensures "factually" does not trigger "actually" (if it were in the list).
_FIRST_PERSON = re.compile(r"\b(I|we|us|our)\b", re.IGNORECASE)

# Future-tense phrases (word-boundary on both ends as a phrase).
_FUTURE_TENSE = re.compile(r"\b(will be|is going to)\b", re.IGNORECASE)

# Hedging adverbs (word-boundary matched to prevent substring collisions).
# "actually" is NOT in this list; design.md names it only as a false-positive
# example for "factually". The regex uses \b which already handles that.
_HEDGING_ADVERBS = re.compile(
    r"\b(currently|presently|just|simply|basically)\b",
    re.IGNORECASE,
)

# Ordered list of (label, pattern) pairs used for lint reporting.
_LINT_RULES: list[tuple[str, re.Pattern]] = [
    ("exclamation_mark", _EXCLAMATION),
    ("em_dash", _EM_DASH),
    ("first_person_pronoun", _FIRST_PERSON),
    ("future_tense", _FUTURE_TENSE),
    ("hedging_adverb", _HEDGING_ADVERBS),
]


def voice_lint_passes(text: str) -> bool:
    """Return True if the text passes all dashboard voice rules.

    Returns False as soon as the first violation is found. Does not
    accumulate all violations (early exit for performance).
    """
    for _label, pattern in _LINT_RULES:
        if pattern.search(text):
            return False
    return True


def first_violation(text: str) -> str | None:
    """Return the label of the first violated rule, or None if the text passes.

    Useful for logging and metrics attribution.
    """
    for label, pattern in _LINT_RULES:
        if pattern.search(text):
            return label
    return None
