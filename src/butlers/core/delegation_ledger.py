"""The delegation ledger — cross-butler question/answer records.

bu-gxmfx (2026-07-04 JARVIS pursuit dossier follow-on, sequenced after the
memory_catalog default-on flip, bu-qvnce.15 / PR #2919). See
``docs/redesigns/2026-07-04-jarvis-pursuit.md`` ("Dropped" ledger: "Cross-
butler delegation ask/answer ledger") and
``alembic/versions/core/core_162_delegation_ledger.py`` for the table.

Design
------
One butler can post a question that gets routed — via the Switchboard's
existing ``route()`` primitive (``roster/switchboard/tools/routing/route.py``),
never a parallel dispatch path — to whichever butler's domain covers it. "Whose
domain covers it" is resolved by reusing ``public.memory_catalog``'s
``source_schema`` attribution (the same discovery index the Fleet Knowledge
search and briefing-context consumers already read): the top hybrid-search hit
for the question text names the owning butler.

Status lifecycle (``VALID_STATUSES``):
    ``pending``    -- row reserved; dispatch to the target's ``delegate_receive``
                       tool is in flight.
    ``routed``     -- dispatch confirmed: the target acknowledged and scheduled
                       a one-shot task to answer it.
    ``unroutable`` -- terminal; no catalog domain match, or the resolved target
                       was the asking butler itself (self-delegation is never
                       useful -- the asker already owns that catalog data).
    ``failed``     -- terminal; a catalog match was found but the Switchboard
                       ``route()`` dispatch itself errored (target unreachable,
                       stale/quarantined, tool error, etc).
    ``answered``   -- terminal; the target posted its answer via
                       ``delegate_answer``.

Degraded-honesty contract: every terminal outcome (including ``unroutable``
and ``failed``) is always written. A question is never silently dropped --
see ``record_ask``/``mark_dispatch_outcome`` below.

Wake state (bu-27dxl.5.2, ``activate-delegation-wake-loop`` OpenSpec change)
-----------------------------------------------------------------------------
An ``answered`` row also carries a wake disposition that is orthogonal to
``status`` (``status`` never regresses once ``answered``; ``wake_state`` is
the separate, honestly-degradable record of the return-callback/task
lifecycle). See ``VALID_WAKE_STATES`` and ``src/butlers/core/delegation_wake.py``
for the full state machine, Switchboard-callback verification, and asker-local
one-shot task reconciliation. This module owns only the ledger-row reads/
writes; it does not touch ``scheduled_tasks`` (a per-butler-schema table,
RFC 0006) or invoke the Switchboard.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from butlers.core.memory_hooks import search_memory_catalog

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"pending", "routed", "unroutable", "failed", "answered"})

# Statuses set at initial insert (before or in lieu of a dispatch attempt).
_INITIAL_STATUSES = frozenset({"pending", "unroutable"})

# Statuses a dispatch attempt may transition a 'pending' row into.
_DISPATCH_OUTCOME_STATUSES = frozenset({"routed", "failed"})

# Wake-disposition vocabulary — mirrors chk_delegation_ledger_wake_state
# (core_181). Orthogonal to VALID_STATUSES: an 'answered' row's wake_state
# tracks callback/task progress without ever changing what status='answered'
# means (the answer stays durable regardless of wake outcome).
VALID_WAKE_STATES = frozenset(
    {
        "not_applicable",
        "callback_pending",
        "callback_failed",
        "callback_routed",
        "task_created",
        "task_conflict",
    }
)

_WAKE_KEY_PREFIX = "delegation-wake:v1"


def _dumps_metadata(metadata: dict[str, Any] | None) -> str | None:
    return json.dumps(metadata) if metadata is not None else None


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


async def record_ask(
    pool: asyncpg.Pool,
    *,
    asking_butler: str,
    question: str,
    status: str,
    target_butler: str | None = None,
    catalog_match_id: uuid.UUID | str | None = None,
    catalog_score: float | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Insert one delegation-ledger row and return its id.

    ``status`` must be ``'pending'`` (a catalog match was found and dispatch is
    about to be attempted) or ``'unroutable'`` (no match / self-target — a
    terminal outcome recorded immediately, with no dispatch attempt).

    Unlike ``attention_ledger.record_attention_event``, this is NOT
    best-effort: the ledger row *is* the delegation record, so a write
    failure here must propagate to the caller (``delegate_ask``), which
    returns an honest error rather than silently proceeding as if the
    question had been asked.
    """
    if status not in _INITIAL_STATUSES:
        raise ValueError(
            f"record_ask: status must be one of {sorted(_INITIAL_STATUSES)}, got {status!r}"
        )

    row_id = await pool.fetchval(
        """
        INSERT INTO public.delegation_ledger
            (asking_butler, question, target_butler, catalog_match_id,
             catalog_score, status, reason, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        RETURNING id
        """,
        asking_butler,
        question,
        target_butler,
        uuid.UUID(str(catalog_match_id)) if catalog_match_id is not None else None,
        catalog_score,
        status,
        reason,
        _dumps_metadata(metadata),
    )
    return str(row_id)


async def mark_dispatch_outcome(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    *,
    status: str,
    reason: str | None = None,
) -> None:
    """Transition a ``'pending'`` row to its dispatch outcome (``routed``/``failed``).

    Not best-effort — a failure here must surface to the caller, which is
    already in an error/exception path (the dispatch itself just
    succeeded/failed) and must not report a status it cannot confirm was
    persisted.
    """
    if status not in _DISPATCH_OUTCOME_STATUSES:
        raise ValueError(
            "mark_dispatch_outcome: status must be one of "
            f"{sorted(_DISPATCH_OUTCOME_STATUSES)}, got {status!r}"
        )
    await pool.execute(
        """
        UPDATE public.delegation_ledger
        SET status = $2, reason = $3
        WHERE id = $1 AND status = 'pending'
        """,
        uuid.UUID(str(ledger_id)),
        status,
        reason,
    )


def compute_answer_digest(answer: str) -> str:
    """Immutable SHA-256 hex digest of an answer's exact text.

    Used both to detect a same-text duplicate vs. a changed-answer replay
    (D2) and as the third component of ``wake_key``.
    """
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def compute_wake_key(ledger_id: uuid.UUID | str, answer_digest: str) -> str:
    """Immutable ``delegation-wake:v1:<ledger_id>:<answer_digest>`` replay identity."""
    return f"{_WAKE_KEY_PREFIX}:{ledger_id}:{answer_digest}"


async def record_answer(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    *,
    answering_butler: str,
    answer: str,
) -> dict[str, Any] | None:
    """Record the first answer for a ``'routed'`` ledger row, guarded to its assigned target.

    On success this is the atomic first-answer acceptance transaction (D2):
    ``status``, ``answer``, ``answered_at``, ``answering_butler``,
    ``answer_digest``, and ``wake_key`` are persisted together and
    ``wake_state`` becomes ``'callback_pending'``. Returns the updated row as
    a dict.

    Returns ``None`` if the guard failed: no row with this id, the row is not
    in ``'routed'`` status (never dispatched, already answered, or
    unroutable/failed), or ``answering_butler`` does not match the row's
    ``target_butler``. This is unchanged from the pre-wake contract — callers
    that only need "did a *new* answer get accepted" may treat ``None`` as
    before. Callers that must also honor D2's duplicate/changed-answer
    replay semantics should follow up a ``None`` result with
    :func:`classify_unaccepted_answer`.
    """
    answer_digest = compute_answer_digest(answer)
    wake_key = compute_wake_key(ledger_id, answer_digest)
    row = await pool.fetchrow(
        """
        UPDATE public.delegation_ledger
        SET status = 'answered',
            answer = $3,
            answered_at = now(),
            answering_butler = $2,
            answer_digest = $4,
            wake_key = $5,
            wake_state = 'callback_pending'
        WHERE id = $1
          AND status = 'routed'
          AND target_butler = $2
        RETURNING id, asking_butler, question, target_butler, catalog_match_id,
                  catalog_score, status, reason, answer, answered_at,
                  answering_butler, asked_at, metadata,
                  answer_digest, wake_key, wake_state,
                  wake_task_id, wake_task_name, wake_updated_at
        """,
        uuid.UUID(str(ledger_id)),
        answering_butler,
        answer,
        answer_digest,
        wake_key,
    )
    return _row_to_dict(row) if row is not None else None


@dataclass(frozen=True)
class UnacceptedAnswerClassification:
    """Why :func:`record_answer` returned ``None`` for a ``delegate_answer`` call.

    ``outcome`` values:
      - ``not_found``       -- no ledger row with this id.
      - ``wrong_target``     -- the row exists but ``answering_butler`` is not
                                its authoritative ``target_butler``.
      - ``not_answered``     -- the row exists, targets this butler, but was
                                never dispatched (still ``pending``) or is a
                                terminal non-answered outcome
                                (``unroutable``/``failed``).
      - ``legacy``           -- the row is already ``answered`` but predates
                                the v1 wake protocol (no ``wake_key``); D7/D9:
                                never auto-woken or backfilled.
      - ``duplicate``        -- the row is already ``answered`` with v1
                                provenance and the resubmitted answer's digest
                                matches the original: a legitimate replay of
                                the same wake identity, not a new answer.
      - ``changed``          -- the row is already ``answered`` with v1
                                provenance but the resubmitted answer's digest
                                differs from the original: an explicit
                                integrity conflict (D2) — schedule nothing.
    """

    outcome: str
    row: dict[str, Any] | None


async def classify_unaccepted_answer(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    *,
    answering_butler: str,
    answer: str,
) -> UnacceptedAnswerClassification:
    """Classify why :func:`record_answer` returned ``None``.

    Never mutates the ledger — read-only classification so the caller can
    return an honest, specific response instead of one generic error.
    """
    row = await get_delegation(pool, ledger_id)
    if row is None:
        return UnacceptedAnswerClassification("not_found", None)

    if row["status"] == "answered":
        if row.get("wake_key") is None:
            return UnacceptedAnswerClassification("legacy", row)
        new_digest = compute_answer_digest(answer)
        if row.get("answer_digest") == new_digest:
            return UnacceptedAnswerClassification("duplicate", row)
        return UnacceptedAnswerClassification("changed", row)

    if row["status"] == "routed" and row.get("target_butler") != answering_butler:
        return UnacceptedAnswerClassification("wrong_target", row)

    # pending / unroutable / failed, or a 'routed' row already correctly
    # targeted (a narrow read/write race with a concurrent accept) — treated
    # uniformly as "not yet answered by this call".
    return UnacceptedAnswerClassification("not_answered", row)


async def get_delegation(pool: asyncpg.Pool, ledger_id: uuid.UUID | str) -> dict[str, Any] | None:
    """Return a single delegation-ledger row by id, or ``None`` if it does not exist."""
    row = await pool.fetchrow(
        """
        SELECT id, asking_butler, question, target_butler, catalog_match_id,
               catalog_score, status, reason, answer, answered_at,
               answering_butler, asked_at, metadata,
               answer_digest, wake_key, wake_state,
               wake_task_id, wake_task_name, wake_updated_at
        FROM public.delegation_ledger
        WHERE id = $1
        """,
        uuid.UUID(str(ledger_id)),
    )
    return _row_to_dict(row) if row is not None else None


async def verify_wake_callback(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    wake_key: str,
    *,
    source_butler: str,
    target_butler: str,
) -> str | None:
    """Re-verify a delegated-answer callback before Switchboard routes it (D3).

    Returns ``None`` when authorized, else a human-readable rejection reason.
    Never raises for an invalid/malformed ``ledger_id`` -- an unparsable id
    is itself a rejection, not a caller-facing exception.
    """
    try:
        row = await get_delegation(pool, ledger_id)
    except (ValueError, TypeError):
        return f"Malformed ledger_id={ledger_id!r}."
    if row is None:
        return f"No delegation_ledger row for id={ledger_id!r}."
    if row["status"] != "answered":
        return f"delegation_ledger row {ledger_id!r} is not answered (status={row['status']!r})."
    if row.get("wake_key") is None:
        return f"delegation_ledger row {ledger_id!r} has no v1 wake provenance (legacy row)."
    if row["answering_butler"] != source_butler:
        return "Callback source_butler does not match the row's authoritative answering_butler."
    if row["asking_butler"] != target_butler:
        return "Callback target_butler does not match the row's authoritative asking_butler."
    if row["wake_key"] != wake_key:
        return "Callback wake_key does not match the row's immutable wake key."
    return None


async def mark_wake_callback_failed(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    wake_key: str,
) -> None:
    """Target-side: record that the Switchboard callback itself could not be routed.

    Guarded to ``wake_state = 'callback_pending'`` so a route()-level
    transport failure can never downgrade a wake that the asker's
    ``delegate_wake`` already advanced past pending (D6 invariant: callback
    progress only moves forward).
    """
    await pool.execute(
        """
        UPDATE public.delegation_ledger
        SET wake_state = 'callback_failed', wake_updated_at = now()
        WHERE id = $1 AND wake_key = $2 AND wake_state = 'callback_pending'
        """,
        uuid.UUID(str(ledger_id)),
        wake_key,
    )


async def advance_wake_callback_routed(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    wake_key: str,
) -> None:
    """Asker-side: mark that ``delegate_wake`` received and is processing this callback.

    Idempotent no-op once already past ``callback_pending`` (duplicate
    delivery / reconnect / replay never regresses wake_state).
    """
    await pool.execute(
        """
        UPDATE public.delegation_ledger
        SET wake_state = 'callback_routed', wake_updated_at = now()
        WHERE id = $1 AND wake_key = $2 AND wake_state = 'callback_pending'
        """,
        uuid.UUID(str(ledger_id)),
        wake_key,
    )


async def record_wake_task_created(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    wake_key: str,
    *,
    task_id: uuid.UUID | str,
    task_name: str,
) -> None:
    """Asker-side: bind the reconciled local one-shot task to the ledger row.

    Scoped to ``wake_key`` (the immutable replay identity), not to a specific
    prior ``wake_state`` — safe to call repeatedly (duplicate delivery,
    reconnect, or crash-replay reconciliation) with the same task binding.
    """
    await pool.execute(
        """
        UPDATE public.delegation_ledger
        SET wake_state = 'task_created',
            wake_task_id = $3,
            wake_task_name = $4,
            wake_updated_at = now()
        WHERE id = $1 AND wake_key = $2
        """,
        uuid.UUID(str(ledger_id)),
        wake_key,
        uuid.UUID(str(task_id)),
        task_name,
    )


async def record_wake_task_conflict(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    wake_key: str,
) -> None:
    """Asker-side: a deterministically-named local task exists with different provenance.

    Never downgrades an already-successful ``task_created`` (D5 point 4: "set
    or retain wake_state=task_conflict" — retained here by simply not
    touching a row that already reached task_created).
    """
    await pool.execute(
        """
        UPDATE public.delegation_ledger
        SET wake_state = 'task_conflict', wake_updated_at = now()
        WHERE id = $1 AND wake_key = $2 AND wake_state != 'task_created'
        """,
        uuid.UUID(str(ledger_id)),
        wake_key,
    )


async def record_wake_attempt(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    *,
    stage: str,
    result: str,
    actor_butler: str,
    retryable: bool | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    """Append one row of callback/wake-reconciliation evidence.

    Best-effort: an audit-log write failure must never mask (or be conflated
    with) the actual wake-state transition it is documenting, so callers
    should not let this raise past them into the caller-facing tool result.
    """
    await pool.execute(
        """
        INSERT INTO public.delegation_wake_attempts
            (ledger_id, stage, result, retryable, error_class, error_message, actor_butler)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        uuid.UUID(str(ledger_id)),
        stage,
        result,
        retryable,
        error_class,
        error_message,
        actor_butler,
    )


async def list_delegations(
    pool: asyncpg.Pool,
    *,
    status: str | None = None,
    asking_butler: str | None = None,
    target_butler: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """List delegation-ledger rows, most-recent first, with optional filters.

    Returns ``(total, rows)`` where ``total`` is the unfiltered-by-page count
    matching the given filters (for pagination), and ``rows`` is the current
    page ordered by ``asked_at DESC``.
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if status is not None:
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1
    if asking_butler is not None:
        conditions.append(f"asking_butler = ${idx}")
        args.append(asking_butler)
        idx += 1
    if target_butler is not None:
        conditions.append(f"target_butler = ${idx}")
        args.append(target_butler)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(
        f"SELECT count(*) FROM public.delegation_ledger{where}",
        *args,
    )
    rows = await pool.fetch(
        f"""
        SELECT id, asking_butler, question, target_butler, catalog_match_id,
               catalog_score, status, reason, answer, answered_at,
               answering_butler, asked_at, metadata,
               answer_digest, wake_key, wake_state,
               wake_task_id, wake_task_name, wake_updated_at
        FROM public.delegation_ledger{where}
        ORDER BY asked_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )
    return int(total or 0), [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Routing resolution — reuses public.memory_catalog attribution (no parallel
# index). See bu-qvnce.15 (memory_catalog default-on flip) for the catalog
# this reads from.
# ---------------------------------------------------------------------------


async def resolve_target_via_catalog(
    pool: asyncpg.Pool,
    question: str,
) -> tuple[str | None, str | None, float | None]:
    """Resolve "whose domain covers this question" via public.memory_catalog.

    Runs a hybrid (semantic + full-text, RRF-fused) search over the shared
    catalog and returns the top hit's owning butler, mirroring exactly what
    ``GET /api/memory/catalog/search`` (Fleet Knowledge) already surfaces --
    no separate/parallel relevance index is introduced here.

    Delegates to ``core.memory_hooks.search_memory_catalog`` (dependency
    inversion -- core must not import ``modules.memory`` directly; the memory
    module registers its search implementation, embedding engine included, on
    startup). Returns no hits -- not an exception -- when the memory module is
    not loaded on this butler.

    Returns ``(target_butler, catalog_match_id, score)``. ``target_butler`` is
    ``None`` when the catalog has no hit for this question -- callers must
    treat that as unroutable, not retry with a different index.
    """
    try:
        hits = await search_memory_catalog(pool, question, limit=1, mode="hybrid")
    except Exception:
        logger.warning(
            "resolve_target_via_catalog: catalog search failed; treating as no match",
            exc_info=True,
        )
        return None, None, None

    if not hits:
        return None, None, None

    top = hits[0]
    # source_butler is the explicit owning-butler column when the writer set
    # it; source_schema is the fallback (schema defaults to the butler name
    # in the single-DB-per-fleet topology -- see config.py's db_schema
    # default-to-name for the consolidated-DB case).
    target_butler = top.get("source_butler") or top.get("source_schema")
    if not target_butler:
        return None, None, None

    score = top.get("rrf_score")
    return str(target_butler), str(top["id"]), float(score) if score is not None else None
