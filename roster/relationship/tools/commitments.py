"""Commitment extraction — turning what the owner says into ledger commitments.

bu-s208f / RFC 0026 §5, REQ-commitment-lifecycle-007 and -008. The Relationship
butler is the first producer of commitment-class ``public.owner_conditions``
rows, and it produces them from one place only: something the owner actually
said. This module is that producer. It does two symmetric jobs — recognise an
*opening* ("I'll send Sam that book tomorrow") and recognise a *closure* ("I
sent Sam the book") — and it delegates every database effect to
``butlers.core.commitments``. There is no SQL here (REQ-commitment-lifecycle-002
keeps the ledger behind one validating doorway).

How this reaches the butler
---------------------------
Two MCP tools in ``roster/relationship/modules/tools.py`` (group ``tracking``)
wrap the two capture functions: ``commitment_capture`` and
``commitment_resolve_from_utterance``. The ``commitment-capture`` session skill
tells the session what to pass them — the owner's sentence verbatim — and the
closures take ``session_id`` from the runtime contextvar rather than from a tool
argument, so the provenance REQ-commitment-lifecycle-008 requires is the
runtime's account of the session and not the model's.

Why a deterministic predicate rather than an LLM judgement
----------------------------------------------------------
RFC 0026 §8 says "confidence is an LLM judgment", and that governs
``create_commitment``'s general contract — which ``butlers.core.commitments``
honours, since confidence is a caller-supplied parameter there and any butler
may pass its own. This module is narrower. REQ-commitment-lifecycle-007 scopes
itself to "explicit first-person commitment patterns", gives literal patterns as
its examples ("I'll send", "I promised", "I need to follow up"), and fixes its
scenario's confidence at 0.9 — a constant, not a judgement. A marker table is a
faithful reading of *that* requirement.

The division of labour is therefore: the session decides whether a turn is worth
offering at all, and this gate decides whether the words are explicit enough to
create a durable record. Both must say yes. The gate is a floor on how wrong the
system can be, not a replacement for the model's reading — and unlike the
model's reading it can be *measured*, which
``roster/relationship/tests/test_commitment_extraction.py`` does against a
curated near-miss set.

A false positive is expensive in a specific way, which is what justifies the
floor: it manufactures an obligation the owner never took on, and then escalates
it at them for weeks.

What this module cannot currently express is an LLM confidence *lower* than the
marker's — a session that reads a commitment as explicit-but-shaky has no way to
file it in the 0.6-0.8 "created but never surfaced" band RFC 0026 §8 defines.
That band is reachable through the core facade directly; wiring it through here
needs a caller-supplied confidence parameter and is deliberately left out until
something needs it.

The predicate, stated once
--------------------------
A sentence opens a commitment when **all** of these hold:

1. it carries an explicit first-person commitment marker (``I'll``,
   ``I will``, ``I'm going to``, ``I promised X``, ``I told X I'd``,
   ``I need to``, ``I have to``, ``I've got to``);
2. it carries no hedge (``should``, ``probably``, ``might``, ``get around
   to``, …), no conditional subordinator (``if``, ``unless``, ``once``, ``as
   soon as``, …), and no negation;
3. it is not a question;
4. a counterparty named in it resolves to a ``public.entities`` row with HIGH
   confidence.

Conditions 2 and 3 are what make the gate worth having. "If the meeting ends
early, I'll call Devi" contains marker 1 verbatim; "You should send Maya that
book tomorrow" contains the whole action verbatim; "Priya said she'll send me
the invoice" contains ``'ll send``. A marker-substring matcher fires on all
three. This one does not.

Condition 4 is deliberately strict: an entity anchor is what makes a commitment
queryable "between me and Sam" (REQ-commitment-lifecycle-001), and a MEDIUM
partial-name match would anchor it to the wrong person. An unresolved
counterparty logs a warning and creates nothing.

Casing carries information here
-------------------------------
Markers are matched case-tolerantly (chat lowercases ``i``), but counterparty
candidates must be capitalised, because that is the only signal separating a
name from a common noun without a parser. An all-lowercase "i'll send sam that
book" therefore yields no counterparty and no commitment. That is a known,
deliberate floor rather than an oversight; widening it needs a real
name-recognition path, not a looser regex.

Closure matching
----------------
A completion utterance names an action, not a fingerprint, so closure cannot go
through :func:`butlers.core.commitments.commitment_fingerprint` directly — the
owner says "I sent Sam the book" for a commitment recorded as "send Sam that
book". Instead the counterparty's active commitments are fetched
(``list_entity_commitments``) and scored by stemmed content-word overlap
against the completion's action. This is why :func:`capture_commitment` writes
the action description into the ledger row's ``summary``: ``summary`` is the
only creation-time text the ledger stores verbatim (the action itself survives
only as a hash inside the fingerprint), so it is the only thing a later closure
has to match against. A match must clear :data:`MIN_ACTION_MATCH` *and* be the
unique best; a tie resolves nothing, because closing the wrong commitment is
worse than closing none.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.commitments import (
    create_commitment,
    list_entity_commitments,
    normalize_action_description,
    resolve_commitment,
)
from butlers.tools.relationship._entity_resolve import resolve_contact_entity_id
from butlers.tools.relationship.resolve import CONFIDENCE_HIGH, contact_resolve

logger = logging.getLogger(__name__)

__all__ = [
    "COMMITMENT_SOURCE",
    "EXTRACTION_EVIDENCE_SOURCE",
    "MIN_ACTION_MATCH",
    "CommitmentSignal",
    "CompletionSignal",
    "capture_commitment",
    "capture_completion",
    "detect_commitment",
    "detect_completion",
]

#: The ledger source every Relationship-extracted commitment is filed under
#: (RFC 0026 §5). Its advisory-lock scope is what keeps a relationship promise
#: from ever interfering with a health follow-up.
COMMITMENT_SOURCE = "relationship:commitment"

#: ``evidence_opened.source`` for anything this module creates. Named for the
#: mechanism, not the speaker: it says a machine read this off a conversation,
#: which is a weaker claim than ``owner_confirmed``.
EXTRACTION_EVIDENCE_SOURCE = "conversation_extraction"

#: Minimum stemmed-token Jaccard overlap between a completion utterance's
#: action and a candidate commitment's summary before the closure is believed.
MIN_ACTION_MATCH = 0.5

_DIRECTION_OWNER_TO_OTHER = "owner_to_other"

# --------------------------------------------------------------------------
# Lexicon
# --------------------------------------------------------------------------

# One capitalised run, e.g. "Sam" or "Sam Rivera". The lookahead stops the run
# from swallowing the relayed first-person pronoun in "I promised Maya I'd …",
# where "I'd" is otherwise just another capitalised token.
_NAME_RUN = r"[A-Z][\w'’-]*(?:\s+(?![Ii](?:['’]|\b))[A-Z][\w'’-]*)*"

# Ordered most-specific first: "I told Devi I would book the table" must be read
# as the relay form (counterparty Devi, action "book the table"), not as the
# bare "I will" that also appears inside it.
_MARKERS: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (
        re.compile(
            r"\b[Ii]\s+told\s+(?P<name>" + _NAME_RUN + r")"
            r"\s+(?:that\s+)?[Ii]\s*(?:['’]d|['’]ll|would|will)\s+"
        ),
        "promise",
        0.9,
    ),
    (
        re.compile(
            r"\b[Ii]\s+promised\s+(?P<name>" + _NAME_RUN + r")"
            r"\s+(?:that\s+)?(?:[Ii]\s*(?:['’]d|['’]ll|would|will)\s+)?"
        ),
        "promise",
        0.9,
    ),
    (re.compile(r"\b[Ii]\s*['’]ll\s+"), "promise", 0.9),
    (re.compile(r"\b[Ii]\s+will\s+"), "promise", 0.9),
    (re.compile(r"\b[Ii]\s*['’]m\s+going\s+to\s+"), "promise", 0.9),
    (re.compile(r"\b[Ii]\s+am\s+going\s+to\s+"), "promise", 0.9),
    (re.compile(r"\b[Ii]\s*['’]ve\s+got\s+to\s+"), "obligation", 0.85),
    (re.compile(r"\b[Ii]\s+have\s+got\s+to\s+"), "obligation", 0.85),
    (re.compile(r"\b[Ii]\s+need\s+to\s+"), "obligation", 0.85),
    (re.compile(r"\b[Ii]\s+have\s+to\s+"), "obligation", 0.85),
)

# Anything in here means the speaker left themselves an exit. A commitment that
# needs an exit is not yet a commitment (RFC 0026 §8: "too uncertain to warrant
# a durable record").
_HEDGES = (
    "should",
    "probably",
    "maybe",
    "perhaps",
    "possibly",
    "might",
    "hopefully",
    "ought to",
    "could",
    "at some point",
    "sometime",
    "some time",
    "one of these days",
    "get around to",
    "getting around to",
    "thinking about",
    "thinking of",
    "meaning to",
    "eventually",
    "i guess",
    "i suppose",
    "not sure",
    "kind of",
    "sort of",
    "hope to",
    "hoping to",
    "was going to",
    "were going to",
)

# A promise contingent on something that has not happened is a plan, not a
# commitment. Deliberately broad — "I'll send Sam the book once I find it" is
# refused too, and that is the direction to be wrong in.
_CONDITIONALS = (
    "if",
    "unless",
    "in case",
    "assuming",
    "provided that",
    "whenever",
    "once",
    "as soon as",
    "depending on",
    "when i",
)


def _word_regex(terms: Sequence[str]) -> re.Pattern[str]:
    """Compile an alternation of *terms* matched on word boundaries."""
    joined = "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{joined})\b")


_HEDGE_RE = _word_regex(_HEDGES)
_CONDITIONAL_RE = _word_regex(_CONDITIONALS)
_NEGATION_RE = re.compile(r"\bnot\b|n['’]t\b|\bnever\b|\bno longer\b")
_FUTURE_RE = re.compile(r"\bwill\b|['’]ll\b|\bgoing to\b|\bgonna\b")

# Capitalised runs that are never people. Weekday and month names are the ones
# that actually collide ("I'll call Sam on Friday" must not propose "Friday").
_NON_NAME_CAPITALS = frozenset(
    {
        "i",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "today",
        "tomorrow",
        "tonight",
        "ok",
        "okay",
        "the",
        "a",
        "an",
        "and",
        "but",
        "so",
        "then",
        "also",
        "next",
        "this",
        "my",
    }
)

_NAME_RE = re.compile(r"\b" + _NAME_RUN)

# Trailing clitics stripped off a candidate before it is judged as a name.
_CLITIC_RE = re.compile(r"['’](?:ll|ve|re|d|m|s)$", re.IGNORECASE)

_FOLLOW_UP_HINTS = ("follow up", "follow-up", "check in", "check-in", "circle back", "get back to")

# Content-free tokens dropped before overlap scoring. Kept deliberately small:
# every word removed is a word two different commitments can no longer be told
# apart by.
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "that",
        "this",
        "these",
        "those",
        "my",
        "his",
        "her",
        "their",
        "our",
        "your",
        "its",
        "it",
        "him",
        "them",
        "me",
        "us",
        "to",
        "for",
        "with",
        "about",
        "of",
        "on",
        "in",
        "at",
        "and",
        "or",
        "some",
        "back",
    }
)

# Past tense / participle forms whose base cannot be reached by suffix rules.
_IRREGULAR_PAST = {
    "sent": "send",
    "told": "tell",
    "gave": "give",
    "given": "give",
    "wrote": "write",
    "written": "write",
    "made": "make",
    "took": "take",
    "taken": "take",
    "got": "get",
    "gotten": "get",
    "met": "meet",
    "bought": "buy",
    "brought": "bring",
    "paid": "pay",
    "spoke": "speak",
    "spoken": "speak",
    "ran": "run",
    "drove": "drive",
    "driven": "drive",
    "read": "read",
    "put": "put",
    "left": "leave",
    "found": "find",
    "did": "do",
    "done": "do",
    "rang": "ring",
    "rung": "ring",
    "lent": "lend",
    "kept": "keep",
    "held": "hold",
    "set": "set",
    "let": "let",
}


# --------------------------------------------------------------------------
# Temporal expressions
# --------------------------------------------------------------------------

_WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _end_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=23, minute=59, second=59, microsecond=0)


def _next_weekday(now: datetime, weekday: int) -> datetime:
    """Return the next date falling on *weekday*, never today."""
    ahead = (weekday - now.weekday()) % 7
    return now + timedelta(days=ahead or 7)


def _weekday_deadline(now: datetime, match: re.Match[str]) -> datetime:
    return _end_of_day(_next_weekday(now, _WEEKDAY_INDEX[match.group("weekday").lower()]))


_TemporalHandler = Callable[[datetime, "re.Match[str]"], datetime]

_TEMPORAL_PATTERNS: tuple[tuple[re.Pattern[str], _TemporalHandler], ...] = (
    (
        re.compile(
            r"\b(?:by\s+|on\s+)?tomorrow(?:\s+(?:morning|afternoon|evening|night))?\b",
            re.IGNORECASE,
        ),
        lambda now, _match: _end_of_day(now + timedelta(days=1)),
    ),
    (
        re.compile(
            r"\b(?:by\s+|on\s+)?(?:today|tonight|this\s+(?:morning|afternoon|evening))\b",
            re.IGNORECASE,
        ),
        lambda now, _match: _end_of_day(now),
    ),
    (
        re.compile(r"\b(?:by\s+)?(?:the\s+)?end\s+of\s+(?:the\s+)?day\b", re.IGNORECASE),
        lambda now, _match: _end_of_day(now),
    ),
    (
        re.compile(r"\b(?:by\s+|on\s+)?(?:this\s+|next\s+)?weekend\b", re.IGNORECASE),
        lambda now, _match: _end_of_day(_next_weekday(now, 5)),
    ),
    (
        re.compile(r"\b(?:by\s+)?(?:the\s+)?end\s+of\s+(?:the\s+)?week\b", re.IGNORECASE),
        lambda now, _match: _end_of_day(_next_weekday(now, 6)),
    ),
    (
        re.compile(r"\b(?:by\s+|on\s+)?next\s+week\b", re.IGNORECASE),
        lambda now, _match: _end_of_day(_next_weekday(now, 6) + timedelta(days=7)),
    ),
    (
        re.compile(r"\b(?:by\s+|on\s+)?this\s+week\b", re.IGNORECASE),
        lambda now, _match: _end_of_day(_next_weekday(now, 6)),
    ),
    (
        re.compile(
            r"\b(?:by\s+|on\s+|this\s+|next\s+)?"
            r"(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            re.IGNORECASE,
        ),
        _weekday_deadline,
    ),
    (
        re.compile(r"\byesterday\b|\blast\s+(?:week|night)\b", re.IGNORECASE),
        lambda now, _match: _end_of_day(now),
    ),
)


def _split_temporal(text: str, now: datetime) -> tuple[datetime | None, str]:
    """Return (deadline, text with every recognised temporal phrase removed).

    The deadline comes from the first pattern that matches; every pattern's
    matches are stripped regardless, because a temporal phrase is exactly the
    kind of mutable detail RFC 0026 §4 keeps out of commitment identity — a
    promise restated tomorrow must land on the same fingerprint.
    """
    deadline: datetime | None = None
    remaining = text
    for pattern, handler in _TEMPORAL_PATTERNS:
        match = pattern.search(remaining)
        if match is None:
            continue
        if deadline is None:
            deadline = handler(now, match)
        remaining = pattern.sub(" ", remaining)
    return deadline, _tidy(remaining)


def _tidy(text: str) -> str:
    """Collapse whitespace and drop dangling edge punctuation."""
    return re.sub(r"\s+", " ", text).strip().strip(",.;:!?- ").strip()


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitmentSignal:
    """One recognised commitment opening, before any entity or ledger contact."""

    kind: str
    direction: str
    action_description: str
    counterparty_candidates: tuple[str, ...]
    confidence: float
    deadline: datetime | None
    marker: str
    utterance: str


@dataclass(frozen=True)
class CompletionSignal:
    """One recognised commitment closure, before any entity or ledger contact."""

    action_description: str
    counterparty_candidates: tuple[str, ...]
    utterance: str


def _sentences(text: str) -> list[str]:
    """Split *text* into sentences.

    Hedges and conditionals are scoped to the clause that carries the marker,
    so "I'll call Devi. Maybe I'll call Sam too." must not let the second
    sentence's hedge veto the first sentence's promise, nor the reverse.
    """
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _name_candidates(text: str) -> tuple[str, ...]:
    """Capitalised runs in *text* that could name a person, in reading order."""
    candidates: list[str] = []
    for match in _NAME_RE.finditer(text):
        name = _strip_clitic(match.group(0))
        if not name or name.lower() in _NON_NAME_CAPITALS:
            continue
        if name not in candidates:
            candidates.append(name)
    return tuple(candidates)


def _strip_clitic(name: str) -> str:
    """Drop a trailing clitic so "Maya's draft" proposes the person, not the string.

    The possessive is the obvious case. The pronoun contractions matter for a
    less obvious one: candidates are found by scanning for capitalised runs, and
    "I'll" in "… that book and I'll call Devi" is a capitalised run that is not
    a name. Reducing it to "I" hands it to :data:`_NON_NAME_CAPITALS`, which
    already refuses it, instead of sending it to the entity resolver.
    """
    return _CLITIC_RE.sub("", name.strip()).strip()


def _is_vetoed(sentence: str) -> bool:
    """True when *sentence* hedges, conditions, negates, or asks."""
    lowered = sentence.lower()
    return bool(
        sentence.rstrip().endswith("?")
        or _HEDGE_RE.search(lowered)
        or _CONDITIONAL_RE.search(lowered)
        or _NEGATION_RE.search(lowered)
    )


def detect_commitment(utterance: str, *, now: datetime | None = None) -> CommitmentSignal | None:
    """Return the commitment *utterance* explicitly opens, or ``None``.

    ``now`` anchors relative deadlines ("tomorrow"); it defaults to the current
    time in the caller's timezone-aware sense only when supplied by the caller,
    because a naive ``utcnow`` here would silently produce naive deadlines.
    Callers that care about deadlines should pass one.

    Returns ``None`` for anything that is not an explicit first-person
    commitment — see the module docstring for the full predicate. This function
    performs no I/O; the counterparty it names is a *candidate*, unresolved.
    """
    anchor = now or datetime.now().astimezone()
    for sentence in _sentences(utterance):
        signal = _detect_commitment_in_sentence(sentence, anchor)
        if signal is not None:
            return signal
    return None


def _detect_commitment_in_sentence(sentence: str, now: datetime) -> CommitmentSignal | None:
    for pattern, kind, confidence in _MARKERS:
        match = pattern.search(sentence)
        if match is None:
            continue
        if _is_vetoed(sentence):
            return None
        relay_name = match.groupdict().get("name")
        deadline, action = _split_temporal(sentence[match.end() :], now)
        if not action:
            return None
        candidates: tuple[str, ...] = ()
        if relay_name:
            candidates += (_strip_clitic(relay_name),)
        candidates += tuple(n for n in _name_candidates(action) if n not in candidates)
        lowered_action = action.lower()
        resolved_kind = (
            "follow_up" if any(hint in lowered_action for hint in _FOLLOW_UP_HINTS) else kind
        )
        return CommitmentSignal(
            kind=resolved_kind,
            direction=_DIRECTION_OWNER_TO_OTHER,
            action_description=action,
            counterparty_candidates=candidates,
            confidence=confidence,
            deadline=deadline,
            marker=_tidy(match.group(0)),
            utterance=sentence,
        )
    return None


_COMPLETION_RE = re.compile(
    r"^[Ii](?:\s*['’]ve|\s+have|\s+had)?\s+"
    r"(?:(?:just|already|finally|yesterday)\s+)*"
    r"(?P<verb>[A-Za-z]+)\b"
)


def _is_past_form(word: str) -> bool:
    lowered = word.lower()
    return lowered in _IRREGULAR_PAST or (lowered.endswith("ed") and len(lowered) > 3)


def detect_completion(utterance: str) -> CompletionSignal | None:
    """Return the commitment closure *utterance* reports, or ``None``.

    A closure is a first-person report of a completed action ("I sent Sam the
    book", "I've already called Priya"). Anything that also reads as a
    commitment opening is refused here — "I told Sam I'd send the book" opens a
    promise even though ``told`` is past tense — so the two detectors never both
    claim the same sentence.
    """
    for sentence in _sentences(utterance):
        signal = _detect_completion_in_sentence(sentence)
        if signal is not None:
            return signal
    return None


def _detect_completion_in_sentence(sentence: str) -> CompletionSignal | None:
    if any(pattern.search(sentence) for pattern, _kind, _conf in _MARKERS):
        return None
    if _is_vetoed(sentence) or _FUTURE_RE.search(sentence.lower()):
        return None
    match = _COMPLETION_RE.match(sentence)
    if match is None or not _is_past_form(match.group("verb")):
        return None
    tail = sentence[match.start("verb") :]
    _deadline, action = _split_temporal(tail, datetime.now().astimezone())
    if not action:
        return None
    return CompletionSignal(
        action_description=action,
        counterparty_candidates=_name_candidates(sentence[match.end("verb") :]),
        utterance=sentence,
    )


# --------------------------------------------------------------------------
# Action similarity
# --------------------------------------------------------------------------


def _stem(word: str) -> str:
    """Reduce *word* to a comparison stem, symmetrically for both sides.

    Irregular past forms map to their base; otherwise one inflectional suffix
    is stripped and a trailing doubled consonant collapsed. The doubling rule
    is what lets ``called``/``call`` and ``dropped``/``drop`` agree without a
    real lemmatiser: both sides lose the double, so ``call`` -> ``cal`` and
    ``called`` -> ``call`` -> ``cal``.
    """
    lowered = _IRREGULAR_PAST.get(word.lower(), word.lower())
    for suffix in ("ing", "ed", "s"):
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= 3:
            lowered = lowered[: -len(suffix)]
            break
    if len(lowered) >= 3 and lowered[-1] == lowered[-2] and lowered[-1].isalpha():
        lowered = lowered[:-1]
    return lowered


def _content_tokens(text: str, *, drop: Sequence[str] = ()) -> frozenset[str]:
    """Stemmed content words of *text*, minus stopwords and *drop* terms."""
    dropped = {token for term in drop for token in normalize_action_description(term).split()}
    return frozenset(
        _stem(token)
        for token in normalize_action_description(text).split()
        if len(token) > 1 and token not in _STOPWORDS and token not in dropped
    )


def _overlap(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# --------------------------------------------------------------------------
# Entity resolution
# --------------------------------------------------------------------------


async def _resolve_counterparty(
    pool: asyncpg.Pool,
    candidates: Sequence[str],
) -> tuple[uuid.UUID, str] | None:
    """Resolve the first candidate name that lands on an entity with HIGH confidence.

    Goes through the production path — ``contact_resolve`` for name -> contact,
    ``resolve_contact_entity_id`` for contact -> ``public.entities`` — so a
    commitment is anchored to exactly the entity the rest of the Relationship
    butler would anchor a fact to. MEDIUM (partial-name) matches are refused:
    see the module docstring.
    """
    for name in candidates:
        resolution = await contact_resolve(pool, name)
        if resolution.get("confidence") != CONFIDENCE_HIGH:
            continue
        contact_id = resolution.get("contact_id")
        if contact_id is None:
            continue
        entity_id = await resolve_contact_entity_id(pool, contact_id)
        if entity_id is not None:
            return entity_id, name
    return None


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------


def _summary_for(action_description: str) -> str:
    """Display prose for the ledger row — and the closure matcher's only handle.

    Kept as the action text with a leading capital rather than a fuller
    sentence: :func:`capture_completion` matches against this field, so
    anything added here is a word that must also appear in a future completion
    utterance to keep the overlap score honest.
    """
    return action_description[:1].upper() + action_description[1:]


async def capture_commitment(
    pool: asyncpg.Pool,
    *,
    utterance: str,
    session_id: str | None = None,
    now: datetime | None = None,
    source: str = COMMITMENT_SOURCE,
) -> dict[str, Any]:
    """Open a commitment from *utterance*, if it explicitly states one.

    Returns a structured outcome rather than raising, because "that was not a
    commitment" is the common case, not an error:

    - ``{"status": "created", ...}`` — a new episode was opened (or reopened).
    - ``{"status": "confirmed", ...}`` — an equivalent commitment was already
      active and was re-confirmed in place (REQ-commitment-lifecycle-002).
    - ``{"status": "skipped", "reason": ...}`` — ``no_commitment_pattern``,
      ``counterparty_unresolved``, or ``below_threshold``.

    Every database effect goes through ``butlers.core.commitments``; this
    function issues no SQL of its own.
    """
    signal = detect_commitment(utterance, now=now)
    if signal is None:
        return {"status": "skipped", "reason": "no_commitment_pattern"}

    resolved = await _resolve_counterparty(pool, signal.counterparty_candidates)
    if resolved is None:
        logger.warning(
            "commitment extraction: no counterparty resolved for action %r "
            "(candidates=%s); no commitment created",
            signal.action_description,
            list(signal.counterparty_candidates),
        )
        return {
            "status": "skipped",
            "reason": "counterparty_unresolved",
            "candidates": list(signal.counterparty_candidates),
            "action_description": signal.action_description,
        }

    entity_id, counterparty_name = resolved
    transition = await create_commitment(
        pool,
        source=source,
        summary=_summary_for(signal.action_description),
        kind=signal.kind,
        direction=signal.direction,
        counterparty_entity_id=str(entity_id),
        confidence=signal.confidence,
        evidence_opened={
            "source": EXTRACTION_EVIDENCE_SOURCE,
            "session_id": session_id,
            "utterance": signal.utterance,
            "marker": signal.marker,
            "counterparty_name": counterparty_name,
        },
        action_description=signal.action_description,
        deadline=signal.deadline,
    )
    if transition is None:
        return {
            "status": "skipped",
            "reason": "below_threshold",
            "confidence": signal.confidence,
        }

    return {
        "status": "created" if transition.transition in ("opened", "reopened") else "confirmed",
        "transition": transition.transition,
        "source": transition.source,
        "fingerprint": transition.fingerprint,
        "episode": transition.episode,
        "kind": signal.kind,
        "direction": signal.direction,
        "counterparty_entity_id": str(entity_id),
        "counterparty_name": counterparty_name,
        "confidence": signal.confidence,
        "deadline": signal.deadline.isoformat() if signal.deadline else None,
        "action_description": signal.action_description,
    }


async def capture_completion(
    pool: asyncpg.Pool,
    *,
    utterance: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the active commitment *utterance* reports as done, if one matches.

    Returns ``{"status": "resolved", ...}`` on a unique confident match, or
    ``{"status": "skipped", "reason": ...}`` with ``no_completion_pattern``,
    ``counterparty_unresolved``, ``no_matching_commitment``, or
    ``ambiguous_match``. The closure carries ``evidence_closed`` with the
    session id and the utterance that proved it, which
    REQ-commitment-lifecycle-008 requires of every resolution.
    """
    signal = detect_completion(utterance)
    if signal is None:
        return {"status": "skipped", "reason": "no_completion_pattern"}

    resolved = await _resolve_counterparty(pool, signal.counterparty_candidates)
    if resolved is None:
        logger.warning(
            "commitment extraction: no counterparty resolved for completion %r "
            "(candidates=%s); nothing resolved",
            signal.action_description,
            list(signal.counterparty_candidates),
        )
        return {
            "status": "skipped",
            "reason": "counterparty_unresolved",
            "candidates": list(signal.counterparty_candidates),
        }

    entity_id, counterparty_name = resolved
    rows = await list_entity_commitments(pool, entity_id=str(entity_id))
    spoken = _content_tokens(signal.action_description, drop=[counterparty_name])
    scored = sorted(
        (
            (_overlap(spoken, _content_tokens(row["summary"] or "", drop=[counterparty_name])), row)
            for row in rows
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] < MIN_ACTION_MATCH:
        logger.info(
            "commitment extraction: no active commitment for %s matches completion %r",
            counterparty_name,
            signal.action_description,
        )
        return {"status": "skipped", "reason": "no_matching_commitment"}
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        logger.warning(
            "commitment extraction: completion %r matches %d active commitments for %s "
            "equally well; resolving none",
            signal.action_description,
            sum(1 for score, _row in scored if score == scored[0][0]),
            counterparty_name,
        )
        return {"status": "skipped", "reason": "ambiguous_match"}

    score, row = scored[0]
    transition = await resolve_commitment(
        pool,
        source=row["source"],
        fingerprint=row["fingerprint"],
        resolution_reason="satisfied",
        evidence_closed={
            "source": "owner_confirmed",
            "session_id": session_id,
            "utterance": signal.utterance,
            "detail": f"owner reported completing {row['summary']!r}",
            "match_score": round(score, 3),
        },
    )
    if transition is None:
        return {"status": "skipped", "reason": "no_matching_commitment"}

    return {
        "status": "resolved",
        "source": transition.source,
        "fingerprint": transition.fingerprint,
        "episode": transition.episode,
        "resolution_reason": "satisfied",
        "counterparty_entity_id": str(entity_id),
        "counterparty_name": counterparty_name,
        "match_score": round(score, 3),
        "summary": row["summary"],
    }
