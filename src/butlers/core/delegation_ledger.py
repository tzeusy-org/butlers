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
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg

from butlers.core.memory_hooks import search_memory_catalog

logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"pending", "routed", "unroutable", "failed", "answered"})

# Statuses set at initial insert (before or in lieu of a dispatch attempt).
_INITIAL_STATUSES = frozenset({"pending", "unroutable"})

# Statuses a dispatch attempt may transition a 'pending' row into.
_DISPATCH_OUTCOME_STATUSES = frozenset({"routed", "failed"})


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


async def record_answer(
    pool: asyncpg.Pool,
    ledger_id: uuid.UUID | str,
    *,
    answering_butler: str,
    answer: str,
) -> dict[str, Any] | None:
    """Record an answer for a ``'routed'`` ledger row, guarded to its assigned target.

    Returns the updated row as a dict on success, or ``None`` if the guard
    failed: no row with this id, the row is not in ``'routed'`` status (never
    dispatched, already answered, or unroutable/failed), or
    ``answering_butler`` does not match the row's ``target_butler``. Callers
    must surface ``None`` as an explicit, honest error -- never treat it as a
    silent success.
    """
    row = await pool.fetchrow(
        """
        UPDATE public.delegation_ledger
        SET status = 'answered',
            answer = $3,
            answered_at = now(),
            answering_butler = $2
        WHERE id = $1
          AND status = 'routed'
          AND target_butler = $2
        RETURNING id, asking_butler, question, target_butler, catalog_match_id,
                  catalog_score, status, reason, answer, answered_at,
                  answering_butler, asked_at, metadata
        """,
        uuid.UUID(str(ledger_id)),
        answering_butler,
        answer,
    )
    return _row_to_dict(row) if row is not None else None


async def get_delegation(pool: asyncpg.Pool, ledger_id: uuid.UUID | str) -> dict[str, Any] | None:
    """Return a single delegation-ledger row by id, or ``None`` if it does not exist."""
    row = await pool.fetchrow(
        """
        SELECT id, asking_butler, question, target_butler, catalog_match_id,
               catalog_score, status, reason, answer, answered_at,
               answering_butler, asked_at, metadata
        FROM public.delegation_ledger
        WHERE id = $1
        """,
        uuid.UUID(str(ledger_id)),
    )
    return _row_to_dict(row) if row is not None else None


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
               answering_butler, asked_at, metadata
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
