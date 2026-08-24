"""Education butler — concept-type classification for curriculum nodes.

A concept's *type* tells the teaching phase which evidence-based technique fits
it: ``factual`` concepts want retrieval practice, ``procedural`` ones want
worked examples, ``conceptual`` ones want elaboration, and ``creative`` ones
want open-ended prompts.  The curriculum planner infers the type from the
node's label and description at generation time and stores it in node metadata.

Classification is a deliberately conservative keyword heuristic.  It scores the
label (weighted higher) and description against per-type marker patterns and
returns a type only when exactly one category wins outright.  Everything else
returns ``None``, which the teaching phase reads as "fall back to Socratic".
Guessing here would be worse than abstaining: a wrong type silently changes how
a concept is taught.
"""

from __future__ import annotations

import re

CONCEPT_TYPES: tuple[str, ...] = ("factual", "procedural", "conceptual", "creative")

# Marker patterns per concept type.  Kept narrow on purpose — a marker that
# shows up in ordinary prose (e.g. "concept", "understand") inflates every node
# into the same category and destroys the signal.
_MARKERS: dict[str, tuple[str, ...]] = {
    "factual": (
        r"\bdefinitions?\b",
        r"\bdefine[sd]?\b",
        r"\bwhat (?:is|are)\b",
        r"\bterminology\b",
        r"\bvocabulary\b",
        r"\bglossary\b",
        r"\bsyntax\b",
        r"\bnotation\b",
        r"\bfacts?\b",
        r"\b(?:names?|types?|kinds?|units?) of\b",
    ),
    "procedural": (
        r"\bhow (?:to|do)\b",
        r"\bimplement\w*",
        r"\bbuild\w*",
        r"\bconstruct\w*",
        r"\bconfigur\w*",
        r"\binstall\w*",
        r"\bset(?:ting)? up\b",
        r"\bsteps?\b",
        r"\bprocedur\w*",
        r"\bworkflows?\b",
        r"\bapply\b|\bapplying\b",
        r"\bperform\w*",
        r"\bcalculat\w*",
        r"\bcomput(?:e|es|ing|ation)\b",
        r"\bsolv\w*",
        r"\balgorithms?\b",
        r"\busage\b",
    ),
    "conceptual": (
        r"\bwhy\b",
        r"\bprincipl\w*",
        r"\btheory\b|\btheories\b|\btheoretical\b",
        r"\bintuition\b",
        r"\btrade-?offs?\b",
        r"\brelationships?\b",
        r"\bmental model\b",
        r"\breasoning\b",
        r"\bimplications?\b",
        r"\bunderlying\b",
    ),
    "creative": (
        r"\bdesign\w*",
        r"\bcompos(?:e|es|ing|ition|itions)\b",
        r"\bcreativ\w*",
        r"\binvent\w*",
        r"\bimprovis\w*",
        r"\bbrainstorm\w*",
        r"\boriginal\b",
        r"\bartistic\b",
        r"\byour own\b",
        r"\bstyle\b",
    ),
}

_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    concept_type: tuple(re.compile(marker) for marker in markers)
    for concept_type, markers in _MARKERS.items()
}

# The label names the concept; the description merely elaborates on it, so a
# label marker counts double.
_LABEL_WEIGHT = 2
_DESCRIPTION_WEIGHT = 1


def classify_concept_type(label: str, description: str | None = None) -> str | None:
    """Infer a concept type from a node's label and description.

    Parameters
    ----------
    label:
        The node's short concept name (e.g. ``"How to Implement Quicksort"``).
    description:
        Optional longer description of the concept.

    Returns
    -------
    str or None
        One of :data:`CONCEPT_TYPES`, or ``None`` when no category wins
        outright — no markers matched, or two categories tied.  Callers should
        leave ``concept_type`` unset rather than substituting a default.
    """
    scores = dict.fromkeys(CONCEPT_TYPES, 0)

    for text, weight in ((label, _LABEL_WEIGHT), (description, _DESCRIPTION_WEIGHT)):
        if not text:
            continue
        lowered = text.lower()
        for concept_type, patterns in _PATTERNS.items():
            matches = sum(1 for pattern in patterns if pattern.search(lowered))
            scores[concept_type] += matches * weight

    best = max(scores.values())
    if best == 0:
        return None

    winners = [concept_type for concept_type, score in scores.items() if score == best]
    if len(winners) != 1:
        return None
    return winners[0]
