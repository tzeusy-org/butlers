"""Commitments — owner-declared obligations carried on the owner condition ledger.

bu-j87m4 / RFC 0026 §3-§4. A commitment ("I told Sam I'd send him that
book") is not a new lifecycle: it is a ``public.owner_conditions`` row whose
``metadata->>'class'`` is ``'commitment'``. RFC 0026 rejected a parallel
``open_loops`` table precisely because the condition ledger already owns the
correct open -> aging -> resolved lifecycle, the L0-L3 escalation schedule,
the source-scoped advisory lock, and the query surface. This module is the
thin, validating doorway onto that ledger — it adds a metadata convention, a
fingerprint recipe, and a creation threshold, and nothing else.

What makes a commitment different from a bill-overdue condition is that it
has no deterministic producer. No scheduled job can survey the world and
decide the owner did or did not send Sam the book, so a commitment can never
be resolved by omission from a complete snapshot. Every creation therefore
goes through ``reconcile_snapshot(snapshot_complete=False)`` — which can
confirm what it observes but is structurally incapable of resolving anything
it did not — and every closure goes through the explicit
``owner_conditions.resolve_condition`` path with a mandatory
``evidence_closed`` receipt (REQ-commitment-lifecycle-008: no silent
resolution).

Two failure modes, deliberately distinct
----------------------------------------
``create_commitment`` fails in two different ways, and the difference is
load-bearing:

* **Malformed input raises ``ValueError``** — an unknown ``kind``, a missing
  ``evidence_opened``, a confidence outside 0.0-1.0. These are programming
  errors in the calling producer; the ledger must never see them
  (REQ-commitment-lifecycle-002: "raises a validation error without touching
  the database").
* **Confidence below ``CREATION_CONFIDENCE_THRESHOLD`` returns ``None``** —
  a well-formed request carrying an honest judgement that this is not yet a
  commitment. RFC 0026 §8 frames it as a policy outcome ("too uncertain to
  warrant a durable record"), not a caller mistake: the extraction pipeline
  is *expected* to hand over hedged statements and be told no. Raising here
  would force every caller to wrap a routine outcome in ``try``.

Confidence bands (RFC 0026 §8, REQ-commitment-lifecycle-004)
------------------------------------------------------------
``< 0.6`` not created at all; ``0.6 <= c < 0.8`` created and queryable but
never surfaced proactively; ``>= 0.8`` eligible for proactive surfacing.
This module only enforces the creation floor. Surfacing is the escalation
job's concern (``butlers.jobs.commitment_escalation``,
REQ-commitment-lifecycle-005), which is why ``SURFACING_CONFIDENCE_THRESHOLD``
is exported rather than applied here — a 0.9 commitment and a 0.7 commitment
are created by identical code paths.

Duplicate creation
------------------
Re-observing the same commitment produces the same fingerprint, so
``reconcile_snapshot`` confirms the existing episode in place rather than
inserting a second row (REQ-commitment-lifecycle-002). Note the ledger's
confirmation semantics: ``summary`` and ``metadata`` are evidence, and a
confirmation *replaces* them with the newest observation's values. A
duplicate ``create_commitment`` therefore leaves ``first_detected_at`` (the
durable creation instant) and the episode intact, but rewrites
``evidence_opened`` to the provenance of the latest observation. Resolution
is the opposite: ``resolve_condition`` merges creation-wins, so closing
evidence can never clobber ``evidence_opened``
(REQ-commitment-lifecycle-001). The mirror of that rule is that
``resolution_reason`` and ``evidence_closed`` are reserved to the ledger and
rejected in creation metadata (REQ-owner-condition-ledger-006) — which is why
``create_commitment`` builds a closed metadata dict rather than passing a
caller's through, and why it must keep doing so.

Escalation grace
----------------
``initial_grace_seconds`` defaults to ``DEFAULT_INITIAL_GRACE_SECONDS``
(24h, RFC 0026 §6 "Escalation Integration": "Grace period defaults to 24h
or until ``next_action_window``, whichever is sooner"). Deadline-aware
shortening — pulling L1 in front of a deadline that falls inside the grace
window — belongs to the escalation job (REQ-commitment-lifecycle-005); the
seam for it is this parameter, which a caller computing its own grace may
override. tests/contracts/test_commitment_grace_rfc_contract.py derives the
constant from the RFC so this citation cannot drift.
"""

from __future__ import annotations

import hashlib
import json
import logging
import unicodedata
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core import entity_graph_edges, owner_conditions
from butlers.core.condition_ledger import (
    ConditionTransition,
    Observation,
    # The engine's row decoder. This module is a third facade over
    # ``condition_ledger`` alongside ``owner_conditions`` and
    # ``infra_conditions``, and its metadata-filtered queries do not go
    # through ``list_conditions``, so it decodes rows with the engine's own
    # decoder rather than a local copy free to drift from the writes.
    row_to_dict,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COMMITMENT_DIRECTIONS",
    "COMMITMENT_IDENTITY_VERSION",
    "COMMITMENT_KINDS",
    "COMMITMENT_METADATA_CLASS",
    "CREATION_CONFIDENCE_THRESHOLD",
    "DEFAULT_INITIAL_GRACE_SECONDS",
    "RESOLUTION_REASONS",
    "SURFACING_CONFIDENCE_THRESHOLD",
    "ConditionTransition",
    "commitment_fingerprint",
    "create_commitment",
    "list_active_commitments",
    "list_entity_commitments",
    "normalize_action_description",
    "resolve_commitment",
    "row_to_dict",
]

COMMITMENT_METADATA_CLASS = "commitment"
COMMITMENT_KINDS = frozenset({"promise", "waiting_for", "follow_up", "obligation", "decision"})
COMMITMENT_DIRECTIONS = frozenset({"owner_to_other", "other_to_owner", "self"})
RESOLUTION_REASONS = frozenset({"satisfied", "cancelled", "superseded", "expired"})

CREATION_CONFIDENCE_THRESHOLD = 0.6
SURFACING_CONFIDENCE_THRESHOLD = 0.8

DEFAULT_INITIAL_GRACE_SECONDS = 24 * 60 * 60.0

# The producer's versioned identity-payload contract (see
# ``condition_ledger.compute_fingerprint``). Bump this only to deliberately
# re-key every future commitment fingerprint; existing episodes keep theirs.
COMMITMENT_IDENTITY_VERSION = 1

# Must stay identical to ``owner_conditions._TABLE``; a unit test asserts it
# rather than reaching into that module's private name at import time.
_TABLE = "public.owner_conditions"

_ACTIVE_STATES = ("open", "aging")

_ROW_COLUMNS = """
    id, source, fingerprint, episode, state, first_detected_at,
    last_confirmed_at, last_escalated_at, next_reescalate_at,
    escalation_level, resolved_at, recovered_after_s, summary, metadata
"""


# ---------------------------------------------------------------------------
# Commitment identity
# ---------------------------------------------------------------------------


def normalize_action_description(action_description: str) -> str:
    """Return the canonical form of an action description for identity purposes.

    Two phrasings of the same promise must land on the same commitment, so
    the description is compared modulo the things a speaker varies freely:
    Unicode composition (NFKC), case (``casefold``), punctuation — treated as
    a word separator, so ``"send-book"`` and ``"send book"`` agree — and
    runs of whitespace. Everything else is significant: this is
    normalization, not paraphrase detection. ``"send Sam the book"`` and
    ``"mail Sam the book"`` are different commitments.
    """
    folded = unicodedata.normalize("NFKC", action_description).casefold()
    separated = "".join(
        " " if unicodedata.category(char).startswith("P") else char for char in folded
    )
    return " ".join(separated.split())


def commitment_fingerprint(
    *,
    source: str,
    counterparty_entity_id: str | None,
    action_description: str,
    version: int = COMMITMENT_IDENTITY_VERSION,
) -> str:
    """Return the ledger fingerprint identifying one commitment (RFC 0026 §4).

    Identity is exactly the two facts that define "the same commitment":
    who it is with, and what was promised. The action description is
    normalized (see :func:`normalize_action_description`) and hashed, so an
    equivalent restatement re-confirms the existing episode instead of
    opening a second one. Mutable fields — deadline, confidence, summary
    prose — are deliberately excluded so they can change mid-episode without
    forking the commitment (REQ-commitment-lifecycle-003).

    Delegates to ``condition_ledger.compute_fingerprint``, so a commitment
    fingerprint is a full SHA-256 hex digest scoped to ``source``, exactly
    like every other identity in this ledger family.
    """
    normalized = normalize_action_description(action_description)
    return owner_conditions.compute_fingerprint(
        source,
        version,
        {
            "counterparty_entity_id": counterparty_entity_id,
            "action_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        },
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _require_text(caller: str, name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{caller}: {name} must be a non-empty string")
    return value


def _require_evidence(caller: str, name: str, evidence: Any) -> dict[str, Any]:
    """Require an evidence object carrying at least a non-empty ``source``.

    Provenance receipts are the point of RFC 0026 — an unattributed
    commitment or a silent resolution is exactly what the ledger exists to
    prevent. The ``source`` vocabulary itself is left open: RFC 0026 §3
    lists the expected values illustratively, and producers legitimately
    extend it (the Relationship extractor uses
    ``"conversation_extraction"``).
    """
    if not isinstance(evidence, dict):
        raise ValueError(f"{caller}: {name} must be an object with a source field")
    _require_text(caller, f"{name}.source", evidence.get("source"))
    try:
        json.dumps(evidence, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{caller}: {name} must be JSON-serializable") from exc
    return dict(evidence)


def _require_confidence(caller: str, confidence: Any) -> float:
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError(f"{caller}: confidence must be a number between 0.0 and 1.0")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError(f"{caller}: confidence must be a number between 0.0 and 1.0")
    return float(confidence)


def _require_deadline(caller: str, deadline: datetime | str | None) -> str | None:
    if deadline is None:
        return None
    if isinstance(deadline, datetime):
        return deadline.isoformat()
    if not isinstance(deadline, str) or not deadline.strip():
        raise ValueError(f"{caller}: deadline must be a datetime, an ISO-8601 string, or None")
    try:
        return datetime.fromisoformat(deadline).isoformat()
    except ValueError as exc:
        raise ValueError(f"{caller}: deadline must be a valid ISO-8601 timestamp") from exc


# ---------------------------------------------------------------------------
# Entity-graph projection (RFC 0031, bu-8cdl1.8 Slice 2)
# ---------------------------------------------------------------------------
#
# A directed commitment (``owner_to_other``/``other_to_owner``) with a
# counterparty is an entity-to-entity relationship exactly like a
# relationship.entity_facts triple or a memory edge-fact: "the owner
# committed to this person". ``self`` commitments have no counterparty to
# link and are never projected. Runs as ``reconcile_snapshot``'s
# ``post_write`` hook, inside the SAME transaction as the ledger write (RFC
# 0031 write-behind contract) -- a real projection failure (FK violation,
# connection loss) propagates and rolls back the commitment write with it.
#
# Resolution (``resolve_commitment``) deliberately does NOT retract the
# projected edge: a satisfied/cancelled/expired commitment is still a true
# historical fact ("the owner committed to Sam"), not a source-row deletion
# -- unlike a superseded fact or a retracted entity_facts triple, the
# owner_conditions row itself is never deleted or corrected on resolution.


async def _project_commitment_edge(
    conn: asyncpg.Connection,
    condition_id: uuid.UUID,
    *,
    direction: str,
    counterparty_entity_id: str | None,
) -> None:
    """Project one commitment's counterparty onto ``public.entity_graph_edges``.

    A no-op when there is no counterparty, the direction is ``self``, or no
    owner entity exists yet (e.g. an unbootstrapped test pool) -- none of
    these are projection FAILURES, there is simply nothing graphable yet.
    A malformed (non-UUID) ``counterparty_entity_id`` is a pre-existing
    caller data-quality issue unrelated to this projection's own
    correctness; it is logged and skipped rather than failing the
    commitment write.
    """
    if counterparty_entity_id is None or direction not in (
        "owner_to_other",
        "other_to_owner",
    ):
        return
    try:
        counterparty_id = uuid.UUID(counterparty_entity_id)
    except ValueError:
        logger.warning(
            "commitments: counterparty_entity_id %r is not a UUID; "
            "skipping entity-graph projection for condition %s",
            counterparty_entity_id,
            condition_id,
        )
        return

    owner_id = await conn.fetchval(
        "SELECT id FROM public.entities WHERE 'owner' = ANY(roles) LIMIT 1"
    )
    if owner_id is None:
        return

    if direction == "owner_to_other":
        subject_entity_id, object_entity_id = owner_id, counterparty_id
    else:
        subject_entity_id, object_entity_id = counterparty_id, owner_id

    await entity_graph_edges.project_entity_graph_edge(
        conn,
        source_schema="public",
        source_table="owner_conditions",
        source_id=condition_id,
        subject_entity_id=subject_entity_id,
        predicate="committed-to",
        object_entity_id=object_entity_id,
    )


async def _post_write_commitment_edges(
    conn: asyncpg.Connection,
    transitions: list[ConditionTransition],
    *,
    direction: str,
    counterparty_entity_id: str | None,
) -> None:
    """``reconcile_snapshot`` ``post_write`` hook for a single-observation call.

    ``create_commitment`` always reconciles exactly one observation, so
    *transitions* has exactly one entry regardless of transition kind
    (opened/reopened/confirmed/escalation_due) -- the natural key is the
    condition row's own id, which is stable across all of them, so
    re-projecting on every confirm is a harmless idempotent upsert.
    """
    for transition in transitions:
        await _project_commitment_edge(
            conn,
            transition.condition_id,
            direction=direction,
            counterparty_entity_id=counterparty_entity_id,
        )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def create_commitment(
    pool: asyncpg.Pool,
    *,
    source: str,
    summary: str,
    kind: str,
    direction: str,
    counterparty_entity_id: str | None,
    confidence: float,
    evidence_opened: dict[str, Any],
    action_description: str,
    deadline: datetime | str | None = None,
    initial_grace_seconds: float = DEFAULT_INITIAL_GRACE_SECONDS,
) -> ConditionTransition | None:
    """Create (or re-confirm) one commitment on the owner condition ledger.

    ``source`` follows the ledger's ``"{origin_butler}:{category}"``
    convention (``"relationship:commitment"``, ``"health:follow-up"``);
    ``action_description`` is the stable statement of what was promised and
    feeds the fingerprint, while ``summary`` is the display prose and does
    not. ``counterparty_entity_id`` is a ``public.entities`` UUID, or
    ``None`` for a commitment with no other party.

    Returns the ``ConditionTransition`` for the affected episode —
    ``"opened"`` for a first-ever commitment, ``"reopened"`` after a prior
    episode resolved, ``"confirmed"``/``"escalation_due"`` when an
    equivalent commitment is already active (REQ-commitment-lifecycle-002:
    the duplicate confirms, it does not fork).

    Returns ``None``, without any database access, when ``confidence`` is
    below :data:`CREATION_CONFIDENCE_THRESHOLD` — a judgement that this is
    not yet a commitment, not an error. See this module's docstring for why
    that is a return value while malformed input is an exception.

    Raises ``ValueError`` — before touching the pool — for an empty
    ``source``/``summary``/``action_description``, an unknown ``kind`` or
    ``direction``, a non-string ``counterparty_entity_id``, a ``confidence``
    outside 0.0-1.0, an ``evidence_opened`` without a ``source``, an
    unparseable ``deadline``, or a negative ``initial_grace_seconds``.
    """
    caller = "create_commitment"
    _require_text(caller, "source", source)
    _require_text(caller, "summary", summary)
    _require_text(caller, "action_description", action_description)
    if normalize_action_description(action_description) == "":
        raise ValueError(f"{caller}: action_description must contain identifying content")
    if kind not in COMMITMENT_KINDS:
        raise ValueError(f"{caller}: kind must be one of {sorted(COMMITMENT_KINDS)}")
    if direction not in COMMITMENT_DIRECTIONS:
        raise ValueError(f"{caller}: direction must be one of {sorted(COMMITMENT_DIRECTIONS)}")
    if counterparty_entity_id is not None:
        _require_text(caller, "counterparty_entity_id", counterparty_entity_id)
    checked_confidence = _require_confidence(caller, confidence)
    checked_evidence = _require_evidence(caller, "evidence_opened", evidence_opened)
    deadline_iso = _require_deadline(caller, deadline)
    if initial_grace_seconds < 0:
        raise ValueError(f"{caller}: initial_grace_seconds must be >= 0")

    if checked_confidence < CREATION_CONFIDENCE_THRESHOLD:
        return None

    metadata: dict[str, Any] = {
        "class": COMMITMENT_METADATA_CLASS,
        "kind": kind,
        "direction": direction,
        "counterparty_entity_id": counterparty_entity_id,
        "confidence": checked_confidence,
        "evidence_opened": checked_evidence,
    }
    if deadline_iso is not None:
        metadata["deadline"] = deadline_iso

    observation = Observation(
        fingerprint=commitment_fingerprint(
            source=source,
            counterparty_entity_id=counterparty_entity_id,
            action_description=action_description,
        ),
        summary=summary,
        metadata=metadata,
        identity_version=COMMITMENT_IDENTITY_VERSION,
    )

    # snapshot_complete=False is not an optimization: a commitment has no
    # producer that can survey the world, so this call must be structurally
    # incapable of resolving any commitment it did not observe.
    async def _post_write(conn: asyncpg.Connection, transitions: list[ConditionTransition]) -> None:
        await _post_write_commitment_edges(
            conn,
            transitions,
            direction=direction,
            counterparty_entity_id=counterparty_entity_id,
        )

    transitions = await owner_conditions.reconcile_snapshot(
        pool,
        source=source,
        observations=[observation],
        snapshot_complete=False,
        initial_grace_seconds=initial_grace_seconds,
        post_write=_post_write,
    )
    return transitions[0] if transitions else None


async def resolve_commitment(
    pool: asyncpg.Pool,
    *,
    source: str,
    fingerprint: str,
    resolution_reason: str,
    evidence_closed: dict[str, Any],
) -> ConditionTransition | None:
    """Close one active commitment with a mandatory closure receipt.

    ``evidence_closed`` must carry at least a ``source`` naming who or what
    proved the commitment closed (``owner_confirmed``, ``evidence_observed``,
    ``expired``, ``cancelled``, ``superseded``); a session id and detail
    string belong there too. REQ-commitment-lifecycle-008 forbids silent
    resolution, which is why this is a required argument rather than an
    optional one.

    Both fields are written into the row's ``metadata`` by the ledger's
    creation-wins shallow merge, so closing evidence is added alongside —
    never on top of — ``evidence_opened`` and the rest of the creation-time
    convention (REQ-commitment-lifecycle-001). Creation-wins cannot swallow
    them in turn: both key names are reserved at the reconcile boundary
    (REQ-owner-condition-ledger-006), so no producer can have claimed either.

    Returns the resolved ``ConditionTransition``, or ``None`` when the
    identity has no active episode — never observed, or already resolved.

    Raises ``ValueError``, before touching the pool, for an empty
    ``source``/``fingerprint``, a ``resolution_reason`` outside
    :data:`RESOLUTION_REASONS`, or an ``evidence_closed`` without a
    ``source``.
    """
    caller = "resolve_commitment"
    _require_text(caller, "source", source)
    _require_text(caller, "fingerprint", fingerprint)
    if resolution_reason not in RESOLUTION_REASONS:
        raise ValueError(f"{caller}: resolution_reason must be one of {sorted(RESOLUTION_REASONS)}")
    checked_evidence = _require_evidence(caller, "evidence_closed", evidence_closed)

    return await owner_conditions.resolve_condition(
        pool,
        source=source,
        fingerprint=fingerprint,
        resolution_metadata={
            "resolution_reason": resolution_reason,
            "evidence_closed": checked_evidence,
        },
    )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_active_commitments(
    pool: asyncpg.Pool,
    *,
    source: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List open/aging commitment-class conditions, most-recently-detected first.

    Commitment-ness is a metadata convention, not a column, so this filters
    on ``metadata->>'class'`` — non-commitment owner conditions from the
    same producer are never returned. Without ``source`` the result spans
    every butler's commitment categories.

    Returns rows in ``condition_ledger`` shape with ``metadata`` decoded to a
    dict regardless of whether the pool has a JSONB codec registered.
    """
    where = [
        "metadata->>'class' = $1",
        "state = ANY($2::text[])",
    ]
    args: list[Any] = [COMMITMENT_METADATA_CLASS, list(_ACTIVE_STATES)]
    if source is not None:
        where.append(f"source = ${len(args) + 1}")
        args.append(source)

    rows = await pool.fetch(
        f"""
        SELECT {_ROW_COLUMNS}
        FROM {_TABLE}
        WHERE {" AND ".join(where)}
        ORDER BY first_detected_at DESC
        LIMIT ${len(args) + 1}
        """,
        *args,
        limit,
    )
    return [row_to_dict(row) for row in rows]


async def list_entity_commitments(
    pool: asyncpg.Pool,
    *,
    entity_id: str,
    include_resolved: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List every commitment anchored to one counterparty entity.

    Deliberately unscoped by ``source``: "what is outstanding between me and
    Sam" spans every butler that files commitments, so a relationship
    promise and a finance obligation for the same person come back together
    (REQ-commitment-lifecycle-001). ``include_resolved`` widens the result
    from active episodes to the entity's full commitment history.
    """
    _require_text("list_entity_commitments", "entity_id", entity_id)

    where = [
        "metadata->>'class' = $1",
        "metadata->>'counterparty_entity_id' = $2",
    ]
    args: list[Any] = [COMMITMENT_METADATA_CLASS, entity_id]
    if not include_resolved:
        where.append(f"state = ANY(${len(args) + 1}::text[])")
        args.append(list(_ACTIVE_STATES))

    rows = await pool.fetch(
        f"""
        SELECT {_ROW_COLUMNS}
        FROM {_TABLE}
        WHERE {" AND ".join(where)}
        ORDER BY first_detected_at DESC
        LIMIT ${len(args) + 1}
        """,
        *args,
        limit,
    )
    return [row_to_dict(row) for row in rows]
