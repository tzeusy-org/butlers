"""Immutable typed-evidence ledger for ``relationship.entity_facts``.

A triple in ``entity_facts`` records *that* something is believed. This module
records *why*: the ordered typed references a writer cited when it asserted the
triple, persisted in ``relationship.fact_evidence`` in the SAME transaction as
the fact row so a fact can never become active with its justification missing.

Design rules (bu-6jv4m.9)
-------------------------

1. **References, never content.** An evidence row points *at* a source
   (``fact``/``entity``/``url``/``text``); it never carries a copy of the source
   message, document, or transcript. ``ref`` and ``note`` are capped at
   :data:`_MAX_TEXT_CHARS` so the ledger is structurally incapable of holding a
   body of text, and the same bound is a CHECK constraint in rel_034.

2. **Append-only.** Rows are inserted and never rewritten — a BEFORE UPDATE
   trigger (rel_034) rejects any in-place edit. Re-citing the same
   ``(kind, ref)`` on the same fact is the same evidence, so the unique index
   absorbs the repeat instead of growing the ledger.

3. **Supersession carries evidence forward.** When a fact is superseded, the new
   active row inherits copies of the prior row's evidence tagged with
   ``carried_from``. The superseded row keeps its own rows untouched, so "why is
   this fact true" survives re-assertion without any row ever being mutated.

4. **Provenance lives on the fact row.** ``src``/``origin``/``session_id``/
   ``action_id`` are stamped on each ledger row for auditability, but the
   authoritative per-assertion provenance is
   ``entity_facts.assert_origin``/``assert_session_id``/``assert_action_id`` —
   so a fact asserted with no cited evidence still records who asserted it and
   from which session.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

#: Evidence reference kinds. MUST stay in lockstep with
#: ``src/butlers/api/models/approval.py::ApprovalEvidence.type`` — the same typed
#: references are rendered in the approvals dossier before a write and read back
#: from the ledger after it.
EVIDENCE_KINDS: frozenset[str] = frozenset({"fact", "entity", "url", "text"})

#: Character ceiling for ``ref`` and ``note``. Enforced here (fail fast, with a
#: caller-actionable message) and again as a CHECK constraint in rel_034.
_MAX_TEXT_CHARS = 512

#: Ceiling on how many references one assert may cite. A packet is a citation
#: list, not a payload; an unbounded list is a content dump wearing a list's
#: clothes.
_MAX_PACKET_ITEMS = 32

#: How a fact row became active.
ASSERT_ORIGINS: frozenset[str] = frozenset({"direct", "approved"})

EvidenceReference = dict[str, str]


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    """The immutable truth packet persisted alongside one fact write.

    ``items`` are the cited references in caller order. ``src``/``origin``/
    ``session_id``/``action_id`` are the assertion provenance: who asserted it,
    whether the write was direct or the execution of an approved action, the
    runtime session that authored it, and the approved ``pending_actions`` row
    that authorised it (``approved`` origin only).
    """

    items: tuple[EvidenceReference, ...]
    src: str
    origin: str
    session_id: uuid.UUID | None = None
    action_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.origin not in ASSERT_ORIGINS:
            raise ValueError(f"EvidencePacket.origin must be one of {sorted(ASSERT_ORIGINS)}.")


def coerce_session_id(value: Any) -> uuid.UUID | None:
    """Best-effort coercion of a runtime session identifier to a UUID.

    Runtime session ids arrive as strings from the context-var capture layer and
    are not guaranteed to be UUIDs (tests and some triggers use opaque tokens).
    A non-UUID session id is recorded as *unknown* rather than failing the write:
    losing the session pointer must never cost the owner the fact itself.
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def validate_evidence(evidence: list[EvidenceReference]) -> None:
    """Reject a malformed or over-long evidence packet before any write.

    Raises ``ValueError`` naming the offending index so a caller (or an LLM
    session) can repair the specific reference rather than guess.
    """
    if len(evidence) > _MAX_PACKET_ITEMS:
        raise ValueError(
            f"evidence carries {len(evidence)} references; at most "
            f"{_MAX_PACKET_ITEMS} are accepted (cite sources, do not copy them)."
        )
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != {"type", "ref", "note"}:
            raise ValueError(
                f"evidence[{index}] must be a typed reference with type, ref, and note."
            )
        if item["type"] not in EVIDENCE_KINDS:
            raise ValueError(f"evidence[{index}].type is not a supported evidence type.")
        if not isinstance(item["ref"], str) or not item["ref"]:
            raise ValueError(f"evidence[{index}].ref must be a non-empty string.")
        if not isinstance(item["note"], str):
            raise ValueError(f"evidence[{index}].note must be a string.")
        if len(item["ref"]) > _MAX_TEXT_CHARS:
            raise ValueError(
                f"evidence[{index}].ref exceeds {_MAX_TEXT_CHARS} characters; "
                "the evidence ledger stores references, not source content."
            )
        if len(item["note"]) > _MAX_TEXT_CHARS:
            raise ValueError(
                f"evidence[{index}].note exceeds {_MAX_TEXT_CHARS} characters; "
                "the evidence ledger stores references, not source content."
            )


def normalize_evidence(evidence: Any) -> list[EvidenceReference]:
    """Coerce a stored or caller-supplied evidence value into a validated packet list.

    Accepts the ``list[dict]`` shape the writer and the ``pending_actions``
    dossier both use. Anything else is treated as *no evidence* rather than an
    error, because a malformed historical dossier row must not block the owner's
    approved write from landing.
    """
    if not isinstance(evidence, list):
        return []
    items = [item for item in evidence if isinstance(item, dict)]
    validate_evidence(items)
    return items


async def persist_evidence(
    conn: asyncpg.Connection,
    *,
    fact_id: uuid.UUID,
    packet: EvidencePacket,
) -> int:
    """Append *packet*'s references to the ledger for *fact_id*.

    Runs on the caller's connection so it commits with the fact write. Returns
    the number of NEW ledger rows; re-citing a ``(kind, ref)`` already recorded
    for this fact is absorbed by the unique index and counts as zero.
    """
    if not packet.items:
        return 0

    next_seq = await conn.fetchval(
        """
        SELECT COALESCE(MAX(seq), 0) + 1
        FROM relationship.fact_evidence
        WHERE fact_id = $1
        """,
        fact_id,
    )
    inserted = 0
    for offset, item in enumerate(packet.items):
        row_id = await conn.fetchval(
            """
            INSERT INTO relationship.fact_evidence (
                fact_id, seq, kind, ref, note, src, origin, session_id, action_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (fact_id, kind, ref) DO NOTHING
            RETURNING id
            """,
            fact_id,
            next_seq + offset,
            item["type"],
            item["ref"],
            item["note"],
            packet.src,
            packet.origin,
            packet.session_id,
            packet.action_id,
        )
        if row_id is not None:
            inserted += 1
    return inserted


async def carry_evidence_forward(
    conn: asyncpg.Connection,
    *,
    from_fact_id: uuid.UUID,
    to_fact_id: uuid.UUID,
) -> int:
    """Copy the superseded row's evidence onto its replacement.

    The source rows are never touched — this is an INSERT ... SELECT, so the
    append-only trigger is not engaged and the superseded fact keeps the exact
    ledger it was written with. Returns the number of rows carried.
    """
    status = await conn.execute(
        """
        INSERT INTO relationship.fact_evidence (
            fact_id, seq, kind, ref, note, src, origin, session_id, action_id,
            carried_from, recorded_at
        )
        SELECT $2, e.seq, e.kind, e.ref, e.note, e.src, e.origin, e.session_id, e.action_id,
               $1, e.recorded_at
        FROM relationship.fact_evidence e
        WHERE e.fact_id = $1
        ON CONFLICT (fact_id, kind, ref) DO NOTHING
        """,
        from_fact_id,
        to_fact_id,
    )
    # asyncpg returns the command tag, e.g. "INSERT 0 3".
    return int(status.rsplit(" ", 1)[-1]) if status.startswith("INSERT") else 0


async def read_fact_evidence(
    pool: asyncpg.Pool,
    fact_id: uuid.UUID,
) -> dict[str, Any]:
    """Read one fact's truth packet: the triple, its provenance, its evidence.

    Returns ``{"fact": None, "provenance": None, "evidence": []}`` for an unknown
    fact id — a miss is a value, not an exception, matching
    :func:`relationship_lookup`'s contract.
    """
    fact_row = await pool.fetchrow(
        """
        SELECT id, subject, predicate, object, object_kind, src, conf, verified,
               validity, observed_at, last_seen, created_at,
               assert_origin, assert_session_id, assert_action_id
        FROM relationship.entity_facts
        WHERE id = $1
        """,
        fact_id,
    )
    if fact_row is None:
        return {"fact": None, "provenance": None, "evidence": []}

    evidence_rows = await pool.fetch(
        """
        SELECT seq, kind, ref, note, src, origin, session_id, action_id,
               carried_from, recorded_at
        FROM relationship.fact_evidence
        WHERE fact_id = $1
        ORDER BY seq
        """,
        fact_id,
    )
    return {
        "fact": {
            "id": str(fact_row["id"]),
            "subject": str(fact_row["subject"]),
            "predicate": fact_row["predicate"],
            "object": fact_row["object"],
            "object_kind": fact_row["object_kind"],
            "src": fact_row["src"],
            "conf": float(fact_row["conf"]) if fact_row["conf"] is not None else 1.0,
            "verified": bool(fact_row["verified"]),
            "validity": fact_row["validity"],
            "observed_at": fact_row["observed_at"],
            "last_seen": fact_row["last_seen"],
            "created_at": fact_row["created_at"],
        },
        "provenance": {
            "origin": fact_row["assert_origin"],
            "session_id": (
                str(fact_row["assert_session_id"])
                if fact_row["assert_session_id"] is not None
                else None
            ),
            "action_id": (
                str(fact_row["assert_action_id"])
                if fact_row["assert_action_id"] is not None
                else None
            ),
        },
        "evidence": [
            {
                "seq": row["seq"],
                "type": row["kind"],
                "ref": row["ref"],
                "note": row["note"],
                "src": row["src"],
                "origin": row["origin"],
                "session_id": (str(row["session_id"]) if row["session_id"] is not None else None),
                "action_id": (str(row["action_id"]) if row["action_id"] is not None else None),
                "carried_from": (
                    str(row["carried_from"]) if row["carried_from"] is not None else None
                ),
                "recorded_at": row["recorded_at"],
            }
            for row in evidence_rows
        ],
    }
