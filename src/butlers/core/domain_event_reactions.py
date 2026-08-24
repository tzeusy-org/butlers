"""Append-only reaction receipts: what the subscriber actually did (bu-6jv4m.8).

Why this exists
---------------
``public.domain_event_deliveries`` answers a transport question -- did the
fan-out succeed in scheduling a wake task on the subscriber. It has never
answered the domain question: did the subscriber *act*, decide the event was
irrelevant, defer it, fail, or simply never say. Reading ``delivered`` as
"the collaboration happened" is the exact conflation this module removes:
a delivery is a handoff, a reaction is an outcome, and the two now have
separate ledgers with separate vocabularies.

Nothing here infers an outcome. A session reports its own reaction through
the ``report_event_reaction`` tool; the correlation sweep may only ever
write ``unreported`` -- the honest verdict about silence -- and never
promotes a completed task, a clean exit code, or an absent error into
``acted``. An LLM run that ends without a receipt produced no evidence that
it did anything, and this ledger says exactly that.

Vocabulary
----------
``scheduled`` and ``running`` are in-flight; the wake exists but its outcome
is not yet known. The five terminal statuses close it:

``acted``      the subscriber took a domain action, with evidence.
``ignored``    the subscriber judged the event not actionable. A real,
               reportable outcome -- silence is not.
``deferred``   the subscriber decided to act later (and should say where).
``failed``     the subscriber tried and could not.
``unreported`` the wake ran (or its window passed) and closed without a
               receipt. Only the sweep writes this.

A wake closes exactly once: the terminal slot is guarded by a partial unique
index on ``(event_id, subscriber_butler)``, so a late sweep can never
overwrite a receipt a session already filed, and two writers racing produce
a conflict rather than two contradictory outcomes.

Evidence
--------
Evidence refs are typed and closed. A receipt that claims ``acted`` should
point at something durable that a human or a later session can open --
``{"kind": "task", "ref": "<task name>"}`` -- not at prose. Prose belongs in
``note``. An unknown ``kind`` is refused rather than stored, because
un-typed evidence is indistinguishable from a claim.

Whether reacting at all is *correct* is not decided here. The publisher's
contract (``butlers.core.domain_event_contracts``) documents what a
reaction would mean, and the subscriber's manifesto owns whether to take it.
This module only records what happened.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

REACTION_STATUSES = frozenset(
    {"scheduled", "running", "acted", "ignored", "deferred", "failed", "unreported"}
)

TERMINAL_REACTION_STATUSES = frozenset({"acted", "ignored", "deferred", "failed", "unreported"})

#: What a waking session may claim about itself. ``unreported`` is excluded
#: deliberately: it is the sweep's finding about silence, and a session that
#: is running is by definition not silent.
REPORTABLE_REACTION_STATUSES = frozenset({"acted", "ignored", "deferred", "failed"})

#: Closed vocabulary of evidence reference kinds, each naming a subsystem a
#: reader can actually open.
EVIDENCE_KINDS = frozenset({"task", "session", "event", "delegation", "memory"})

_EVIDENCE_KEYS = frozenset({"kind", "ref", "label"})

_REACTION_COLUMNS = (
    "id, event_id, subscriber_butler, status, session_id, task_name, note, evidence, recorded_at"
)


class DomainEventReactionError(Exception):
    """A receipt that cannot be trusted -- refused rather than stored."""


def is_terminal_reaction(status: str) -> bool:
    """Return whether *status* closes the reaction lifecycle."""
    return status in TERMINAL_REACTION_STATUSES


def validate_evidence(evidence: Any) -> list[dict[str, str]]:
    """Normalize typed evidence refs, or raise :class:`DomainEventReactionError`.

    Fail closed in every direction: a non-list, a non-mapping entry, an
    unknown ``kind``, a blank ``ref``, or any key outside ``kind``/``ref``/
    ``label`` is refused. ``None`` and ``[]`` are both "no evidence", which
    is a legitimate shape for ``ignored``, ``deferred``, and ``failed``.
    """
    if evidence is None:
        return []
    if not isinstance(evidence, list):
        raise DomainEventReactionError(
            f"evidence must be a list of typed refs, got {type(evidence).__name__}."
        )

    normalized: list[dict[str, str]] = []
    for index, entry in enumerate(evidence):
        where = f"evidence[{index}]"
        if not isinstance(entry, dict):
            raise DomainEventReactionError(
                f"{where} must be a mapping with 'kind' and 'ref', got {type(entry).__name__}."
            )
        unsupported = sorted(set(entry) - _EVIDENCE_KEYS)
        if unsupported:
            raise DomainEventReactionError(
                f"{where} has unsupported key(s) {', '.join(unsupported)}. Evidence carries "
                "a typed reference only; prose belongs in the receipt's 'note'."
            )
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in EVIDENCE_KINDS:
            raise DomainEventReactionError(
                f"{where} has kind {kind!r}, which is not one of "
                f"{', '.join(sorted(EVIDENCE_KINDS))}. Untyped evidence is a claim, not "
                "evidence."
            )
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref.strip():
            raise DomainEventReactionError(f"{where} needs a non-empty 'ref' string.")
        normalized_entry: dict[str, str] = {"kind": kind, "ref": ref.strip()}
        label = entry.get("label")
        if label is not None:
            if not isinstance(label, str):
                raise DomainEventReactionError(f"{where} label must be a string.")
            normalized_entry["label"] = label
        normalized.append(normalized_entry)
    return normalized


async def record_reaction(
    pool: asyncpg.Pool | asyncpg.Connection,
    *,
    event_id: uuid.UUID | str,
    subscriber_butler: str,
    status: str,
    session_id: str | None = None,
    task_name: str | None = None,
    note: str | None = None,
    evidence: Any = None,
) -> str:
    """Append one reaction row and return its id.

    Validation happens before any write, so a malformed receipt never lands
    a half-recorded row. A second terminal receipt for the same wake raises
    rather than overwriting the first: the ledger is append-only and a wake
    closes once.
    """
    if status not in REACTION_STATUSES:
        raise DomainEventReactionError(
            f"Unknown reaction status {status!r}. Expected one of "
            f"{', '.join(sorted(REACTION_STATUSES))}."
        )
    normalized_evidence = validate_evidence(evidence)

    try:
        reaction_id = await pool.fetchval(
            """
            INSERT INTO public.domain_event_reactions
                (event_id, subscriber_butler, status, session_id, task_name, note, evidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING id
            """,
            uuid.UUID(str(event_id)),
            subscriber_butler,
            status,
            session_id,
            task_name,
            note,
            json.dumps(normalized_evidence),
        )
    except asyncpg.UniqueViolationError as exc:
        raise DomainEventReactionError(
            f"The wake for event {event_id} on {subscriber_butler!r} is already closed; "
            "a reaction lifecycle ends exactly once and this ledger is append-only."
        ) from exc
    return str(reaction_id)


async def latest_reaction_for(
    pool: asyncpg.Pool,
    *,
    event_id: uuid.UUID | str,
    subscriber_butler: str,
) -> dict[str, Any] | None:
    """Return the newest reaction row for one wake, or ``None`` if it never started."""
    row = await pool.fetchrow(
        f"""
        SELECT {_REACTION_COLUMNS}
        FROM public.domain_event_reactions
        WHERE event_id = $1 AND subscriber_butler = $2
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
        """,
        uuid.UUID(str(event_id)),
        subscriber_butler,
    )
    return dict(row) if row is not None else None


async def list_reactions_for_event(
    pool: asyncpg.Pool,
    *,
    event_id: uuid.UUID | str,
) -> list[dict[str, Any]]:
    """Return the full collaboration trace for one event, oldest step first."""
    rows = await pool.fetch(
        f"""
        SELECT {_REACTION_COLUMNS}
        FROM public.domain_event_reactions
        WHERE event_id = $1
        ORDER BY recorded_at ASC, id ASC
        """,
        uuid.UUID(str(event_id)),
    )
    return [dict(row) for row in rows]


async def latest_reactions_for_events(
    pool: asyncpg.Pool,
    event_ids: list[uuid.UUID | str],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return the newest reaction per ``(event_id, subscriber_butler)`` pair.

    Batched for the deliveries list view so rendering N deliveries costs one
    query rather than N.
    """
    if not event_ids:
        return {}
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT ON (event_id, subscriber_butler) {_REACTION_COLUMNS}
        FROM public.domain_event_reactions
        WHERE event_id = ANY($1::uuid[])
        ORDER BY event_id, subscriber_butler, recorded_at DESC, id DESC
        """,
        [uuid.UUID(str(event_id)) for event_id in event_ids],
    )
    return {(str(row["event_id"]), str(row["subscriber_butler"])): dict(row) for row in rows}


async def has_terminal_reaction(
    pool: asyncpg.Pool,
    *,
    event_id: uuid.UUID | str,
    subscriber_butler: str,
) -> bool:
    """Return whether this wake has already been closed by any terminal receipt."""
    found = await pool.fetchval(
        """
        SELECT 1
        FROM public.domain_event_reactions
        WHERE event_id = $1 AND subscriber_butler = $2 AND status = ANY($3::text[])
        LIMIT 1
        """,
        uuid.UUID(str(event_id)),
        subscriber_butler,
        sorted(TERMINAL_REACTION_STATUSES),
    )
    return found is not None
