"""Fleet Case File data layer: ``public.fleet_cases``/``fleet_case_evidence``/
``fleet_case_links`` (bu-8cdl1.7 Slices 3-6, RFC 0032).

See ``about/legends-and-lore/rfcs/0032-fleet-case-file.md`` for the full
design and ``alembic/versions/core/core_217_fleet_case_file.py`` (Slice 1) for
the schema/RLS this module writes through. :func:`evaluate_case_attention`
(Slice 4) is the situation-scoped attention bypass; :func:`run_lapse_sweep`
(Slice 5) is the scheduled lapse-close sweep; :func:`backfill_historical_case`/
:func:`backfill_from_owner_conditions` (Slice 6) is the one-time historical
backfill -- see their docstrings.

Write authority mirrors the RLS policy exactly: ``fleet_case_evidence`` has no
role restriction (any butler may contribute), while ``fleet_cases`` restricts
INSERT/UPDATE to ``butler_switchboard_rw`` — enforced by Postgres, not by this
module. A non-Switchboard caller's pool gets a different failure shape per
statement: :func:`open_case`'s INSERT raises ``asyncpg.InsufficientPrivilegeError``
(an RLS ``WITH CHECK`` violation is a hard error), while
:func:`propose_posture`/:func:`close_case`'s conditional UPDATE instead
matches zero rows silently (RLS's ``USING`` clause just makes the row
invisible) and raises :class:`FleetCaseError` with an explicit "did not
apply" message rather than misreporting the case as closed. The MCP tool
layer (``butlers.core_tools._fleet_cases``) is what forwards these calls
through the Switchboard's ``route()`` primitive so they execute against
Switchboard's own pool instead of hitting either failure mode. This module
never does that forwarding itself — it only issues SQL against whatever pool
it is given.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.core.approvals_policy import (
    get_approvals_policy_quiet_hours,
    is_policy_quiet_now,
    policy_quiet_hours_window_start,
)
from butlers.core.attention_ledger import attention_event_recorded_since, record_attention_event

CASE_STATES = frozenset({"open", "watching", "closing", "closed"})
CASE_POSTURES = frozenset({"silent", "routine", "active", "urgent"})

# Prefix for the attention-ledger dedup_key used by evaluate_case_attention,
# namespaced so a case's bypass key can never collide with an insight
# candidate's dedup_key (roster/switchboard/tools/insight/broker.py) sharing
# the same public.attention_ledger table.
CASE_ATTENTION_DEDUP_PREFIX = "fleet_case:"

_CASE_COLUMNS = "id, correlation_key, state, posture, outcome, opened_at, updated_at, closed_at"
_EVIDENCE_COLUMNS = "id, case_id, contributor, kind, ref, payload, contributed_at"
_LINK_COLUMNS = "id, case_id, link_kind, ref, metadata, linked_at"

# Defensive caps on one case's accreted evidence/links, mirroring the read
# API's limits (roster/switchboard/api/router.py) -- a case that has been
# accreting for months should never make read_case unbounded.
_EVIDENCE_LIMIT = 500
_LINKS_LIMIT = 500

# Postures the lapse sweep (Slice 5) may close unattended. ``silent``/
# ``routine`` are the two postures where nobody is actively engaged with the
# situation, so a long unattended silence plausibly means it resolved itself
# without ceremony. ``active`` means a contributor is presently working the
# case and ``urgent`` means the owner needs to see it -- both stay excluded
# so an automated sweep can never make an engaged or owner-facing case
# disappear without an explicit close_case. This mirrors the RFC's own
# framing of outcome="lapsed" as a value the sweep is allowed to write, never
# a fifth state, and the doctrine that nothing closes silently for a case
# that still needed eyes on it.
LAPSE_ELIGIBLE_POSTURES = frozenset({"silent", "routine"})

# How long a silent/routine case must go without a fresh evidence
# contribution or a state/posture update before the sweep may lapse it. Seven
# days gives a slow-building but still-live situation (RFC 0032's own
# "multi-day illness" example) ample room to keep accreting evidence -- each
# new contribution or posture change resets the clock -- while still
# reclaiming cases that were plainly abandoned rather than resolved.
DEFAULT_LAPSE_STALENESS_WINDOW = timedelta(days=7)

LAPSE_OUTCOME = "lapsed"


class FleetCaseError(Exception):
    """A fleet-case request refused before touching the database, or a
    business-rule violation the caller should see as a normal error result
    rather than a raised exception reaching the MCP layer uncaught."""


def _parse_case_id(case_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(case_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise FleetCaseError(f"case_id={case_id!r} is not a valid UUID.") from exc


async def find_open_case(pool: Any, correlation_key: str) -> dict[str, Any] | None:
    """Return the non-closed case for *correlation_key*, or ``None``.

    At most one non-closed case can exist per key
    (``uq_fleet_cases_active_correlation_key``), so this is a lookup, not a
    list.
    """
    row = await pool.fetchrow(
        f"SELECT {_CASE_COLUMNS} FROM public.fleet_cases"
        " WHERE correlation_key = $1 AND state <> 'closed'",
        correlation_key,
    )
    return dict(row) if row is not None else None


async def get_case_summary(pool: Any, case_id: str) -> dict[str, Any] | None:
    """Return ``{id, correlation_key, state, posture}`` for one case, or ``None``.

    A lighter read than :func:`read_case` for callers (like
    :func:`evaluate_case_attention`'s tool-layer callers) that only need to
    branch on a case's current state/posture, not its full accreted evidence
    and links.
    """
    case_uuid = _parse_case_id(case_id)
    row = await pool.fetchrow(
        "SELECT id, correlation_key, state, posture FROM public.fleet_cases WHERE id = $1",
        case_uuid,
    )
    return dict(row) if row is not None else None


async def open_case(pool: Any, *, correlation_key: str) -> dict[str, Any]:
    """Open a new case at ``state='open', posture='silent'`` (the schema
    defaults). Refused with a concrete conflict message -- naming the
    existing case -- when an open case already exists for this key, rather
    than a raw ``UniqueViolationError`` bubbling to the caller.
    """
    if not correlation_key or not correlation_key.strip():
        raise FleetCaseError("correlation_key must be a non-empty string.")
    try:
        row = await pool.fetchrow(
            f"INSERT INTO public.fleet_cases (correlation_key) VALUES ($1) "
            f"RETURNING {_CASE_COLUMNS}",
            correlation_key,
        )
    except asyncpg.UniqueViolationError as exc:
        existing = await find_open_case(pool, correlation_key)
        existing_id = existing["id"] if existing else "unknown"
        raise FleetCaseError(
            f"An open case already exists for correlation_key={correlation_key!r} "
            f"(id={existing_id}). Use find_open_case/contribute_case_evidence instead "
            "of opening a duplicate."
        ) from exc
    return dict(row)


async def contribute_evidence(
    pool: Any,
    *,
    case_id: str,
    contributor: str,
    kind: str,
    ref: str,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Insert one evidence row, idempotently.

    Returns ``(row, newly_recorded)``. A repeat of the same
    ``(case_id, contributor, kind, ref)`` is a no-op at the database
    (``uq_fleet_case_evidence_contributor``) -- this returns the existing row
    with ``newly_recorded=False`` rather than raising, so a contributor that
    re-reports the same observation gets a normal success result.
    """
    if not kind or not kind.strip():
        raise FleetCaseError("kind must be a non-empty string.")
    if not ref or not ref.strip():
        raise FleetCaseError("ref must be a non-empty string.")
    case_uuid = _parse_case_id(case_id)

    try:
        row = await pool.fetchrow(
            "INSERT INTO public.fleet_case_evidence (case_id, contributor, kind, ref, payload) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (case_id, contributor, kind, ref) DO NOTHING "
            f"RETURNING {_EVIDENCE_COLUMNS}",
            case_uuid,
            contributor,
            kind,
            ref,
            payload,
        )
    except asyncpg.ForeignKeyViolationError as exc:
        raise FleetCaseError(f"No fleet case with id={case_id!r}.") from exc

    if row is not None:
        return dict(row), True

    existing = await pool.fetchrow(
        f"SELECT {_EVIDENCE_COLUMNS} FROM public.fleet_case_evidence "
        "WHERE case_id = $1 AND contributor = $2 AND kind = $3 AND ref = $4",
        case_uuid,
        contributor,
        kind,
        ref,
    )
    if existing is None:
        # ON CONFLICT DO NOTHING fired but a concurrent DELETE cannot happen
        # (fleet_case_evidence rows are never deleted except by case cascade,
        # and the case still exists -- the FK check above would have raised
        # otherwise). Treat this as an honest failure rather than fabricating
        # a row.
        raise FleetCaseError(
            f"Evidence insert for case_id={case_id!r} conflicted but the existing row "
            "could not be re-read."
        )
    return dict(existing), False


async def _no_op_update_error(
    pool: Any, case_uuid: uuid.UUID, case_id: str, *, verb: str
) -> FleetCaseError:
    """Explain why a conditional ``UPDATE ... WHERE state <> 'closed' RETURNING``
    matched zero rows.

    Postgres RLS treats INSERT's ``WITH CHECK`` and UPDATE's ``USING`` clause
    differently: a ``WITH CHECK`` violation raises ``InsufficientPrivilegeError``
    (see :func:`open_case`), but ``USING`` simply makes a disallowed row
    invisible to the UPDATE -- it matches zero rows silently, with no
    exception. So "the case doesn't exist", "the case is already closed",
    and "this pool isn't Switchboard's own and RLS filtered the row" are all
    the same zero-rows-returned outcome, and only a follow-up SELECT (itself
    readable by every role) can tell them apart.
    """
    state = await pool.fetchval("SELECT state FROM public.fleet_cases WHERE id = $1", case_uuid)
    if state is None:
        return FleetCaseError(f"No fleet case with id={case_id!r}.")
    if state == "closed":
        return FleetCaseError(f"Case {case_id} is already closed; {verb}.")
    return FleetCaseError(
        f"Case {case_id} exists (state={state!r}) but the write did not apply. Only "
        "butler_switchboard_rw may write public.fleet_cases -- if this pool is not "
        "Switchboard's own, row-level security silently filtered the row rather than "
        "raising."
    )


async def propose_posture(pool: Any, *, case_id: str, posture: str) -> dict[str, Any]:
    """Set a case's posture. Refused once the case is closed (a terminal
    state where posture no longer means anything).

    This is the actual write, not the "propose" side of arbitration: it must
    only ever run against Switchboard's own pool (RLS enforces that at the
    database level). A contributor's proposal is last-write-wins -- there is
    no quorum/voting model in this slice; see the MCP tool layer for how a
    non-Switchboard contributor's call gets here.
    """
    if posture not in CASE_POSTURES:
        raise FleetCaseError(
            f"posture={posture!r} must be one of {', '.join(sorted(CASE_POSTURES))}."
        )
    case_uuid = _parse_case_id(case_id)
    row = await pool.fetchrow(
        f"UPDATE public.fleet_cases SET posture = $2, updated_at = now() "
        f"WHERE id = $1 AND state <> 'closed' RETURNING {_CASE_COLUMNS}",
        case_uuid,
        posture,
    )
    if row is not None:
        return dict(row)

    raise await _no_op_update_error(pool, case_uuid, case_id, verb="posture can no longer change")


async def close_case(pool: Any, *, case_id: str, outcome: str) -> dict[str, Any]:
    """Close a case with a required *outcome*.

    ``chk_fleet_cases_closed_needs_outcome`` enforces this at the database
    too; this validation exists so a missing/blank outcome is refused with a
    clear message instead of a raw CheckViolationError.
    """
    if not outcome or not outcome.strip():
        raise FleetCaseError("close_case requires a non-empty outcome.")
    case_uuid = _parse_case_id(case_id)
    row = await pool.fetchrow(
        f"UPDATE public.fleet_cases SET state = 'closed', outcome = $2, closed_at = now(), "
        f"updated_at = now() WHERE id = $1 AND state <> 'closed' RETURNING {_CASE_COLUMNS}",
        case_uuid,
        outcome,
    )
    if row is not None:
        return dict(row)

    raise await _no_op_update_error(pool, case_uuid, case_id, verb="it cannot be closed again")


async def read_case(pool: Any, case_id: str) -> dict[str, Any] | None:
    """Return one case with its accreted evidence and ledger links, or
    ``None`` if it does not exist. Mirrors
    ``roster/switchboard/api/router.py``'s ``GET /cases/{case_id}`` shape for
    the MCP surface.
    """
    case_uuid = _parse_case_id(case_id)
    case_row = await pool.fetchrow(
        f"SELECT {_CASE_COLUMNS} FROM public.fleet_cases WHERE id = $1", case_uuid
    )
    if case_row is None:
        return None

    evidence_rows = await pool.fetch(
        f"SELECT {_EVIDENCE_COLUMNS} FROM public.fleet_case_evidence WHERE case_id = $1 "
        f"ORDER BY contributed_at ASC LIMIT {_EVIDENCE_LIMIT}",
        case_uuid,
    )
    link_rows = await pool.fetch(
        f"SELECT {_LINK_COLUMNS} FROM public.fleet_case_links WHERE case_id = $1 "
        f"ORDER BY linked_at ASC LIMIT {_LINKS_LIMIT}",
        case_uuid,
    )
    return {
        **dict(case_row),
        "evidence": [dict(r) for r in evidence_rows],
        "links": [dict(r) for r in link_rows],
    }


def case_attention_dedup_key(correlation_key: str) -> str:
    """Return the ``public.attention_ledger`` dedup key for one case's bypass."""
    return f"{CASE_ATTENTION_DEDUP_PREFIX}{correlation_key}"


async def evaluate_case_attention(
    pool: Any,
    *,
    case_id: str,
    correlation_key: str,
    posture: str,
    state: str,
    origin_butler: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide, and record, whether *this* moment breaks quiet hours for a case.

    RFC 0032 Slice 4 -- situation-scoped attention. The insight broker's
    existing priority-urgent bypass (``roster/switchboard/tools/insight/
    broker.py``) fires per *candidate*: five butlers independently noticing
    the same situation break quiet hours five times. This is the case-scoped
    equivalent: any number of contributions/proposals against the same
    ``correlation_key`` collapse to at most one recorded bypass per
    quiet-hours window, because they all key off the same
    :func:`case_attention_dedup_key` regardless of which butler or which
    fleet-case tool call triggered the check.

    Only a non-closed case at ``posture='urgent'`` can trigger a bypass -- a
    case's attention need is exactly its current posture, so closing it (or
    stepping it down from urgent) clears the need with no separate cleanup.
    Outside quiet hours there is nothing to bypass -- normal delivery already
    reaches the owner, so this is a no-op.

    Returns ``{"bypass": bool, "reason": str}``. ``reason`` is one of:
    ``case_closed``, ``not_urgent``, ``quiet_hours_inactive``,
    ``already_bypassed_this_window``, ``urgent_case_bypass``.
    """
    if state == "closed":
        return {"bypass": False, "reason": "case_closed"}
    if posture != "urgent":
        return {"bypass": False, "reason": "not_urgent"}

    if now is None:
        now = datetime.now(UTC)
    policy = await get_approvals_policy_quiet_hours(pool)
    if not is_policy_quiet_now(policy, now=now):
        return {"bypass": False, "reason": "quiet_hours_inactive"}

    dedup_key = case_attention_dedup_key(correlation_key)
    window_start = policy_quiet_hours_window_start(policy, now=now)
    if window_start is not None and await attention_event_recorded_since(
        pool, dedup_key=dedup_key, since=window_start
    ):
        return {"bypass": False, "reason": "already_bypassed_this_window"}

    await record_attention_event(
        pool,
        origin_butler=origin_butler,
        source="insight",
        outcome="delivered",
        intent="fleet_case",
        dedup_key=dedup_key,
        reason="urgent_case_bypass",
        metadata={"case_id": case_id, "correlation_key": correlation_key},
    )
    return {"bypass": True, "reason": "urgent_case_bypass"}


async def run_lapse_sweep(
    pool: Any,
    *,
    staleness_window: timedelta = DEFAULT_LAPSE_STALENESS_WINDOW,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Close every silent/routine case that has gone stale.

    RFC 0032 Slice 5 -- the scheduled lapse sweep. A case is eligible only
    when *all* of the following hold, checked in one atomic
    ``UPDATE ... RETURNING``:

    - ``state <> 'closed'`` -- an already-closed case is never touched, so
      this can never resurrect a case (there is no code path here that ever
      writes anything but ``state = 'closed'``).
    - ``posture`` is in :data:`LAPSE_ELIGIBLE_POSTURES` -- ``active``/
      ``urgent`` cases are excluded entirely; they need an explicit
      close_case, not a silent unattended lapse.
    - ``updated_at`` (bumped by :func:`propose_posture`/:func:`close_case`)
      predates the cutoff -- a posture change or an attempted re-close resets
      the clock.
    - no :func:`contribute_evidence` row for the case at or after the cutoff
      -- a still-accreting situation never lapses no matter how old the case
      itself is.

    Doing the eligibility check and the write in one statement (rather than
    a SELECT candidates then per-row close_case) closes the TOCTOU window
    where a case could flip to ``urgent`` or gain fresh evidence between
    selection and write -- the single ``UPDATE``'s ``WHERE`` clause is
    re-evaluated against the current row, so a case that stopped being
    eligible in that instant is simply not touched. Running the sweep twice
    is a no-op the second time: the first run's writes already flipped
    ``state`` to ``closed``, which drops every row from the second run's
    ``WHERE state <> 'closed'``.

    Returns ``{"lapsed_case_ids": [...], "lapsed_count": int}``.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - staleness_window

    rows = await pool.fetch(
        f"""
        UPDATE public.fleet_cases c
        SET state = 'closed', outcome = $1, closed_at = $2, updated_at = $2
        WHERE c.state <> 'closed'
          AND c.posture = ANY($3::text[])
          AND c.updated_at < $4
          AND NOT EXISTS (
              SELECT 1 FROM public.fleet_case_evidence e
              WHERE e.case_id = c.id AND e.contributed_at >= $4
          )
        RETURNING {_CASE_COLUMNS}
        """,
        LAPSE_OUTCOME,
        now,
        list(LAPSE_ELIGIBLE_POSTURES),
        cutoff,
    )
    lapsed = [dict(row) for row in rows]
    return {
        "lapsed_case_ids": [str(row["id"]) for row in lapsed],
        "lapsed_count": len(lapsed),
    }


# ---------------------------------------------------------------------------
# Slice 6 -- historical backfill. RFC 0032's Non-goals section names the
# single hard invariant this section exists to enforce: "Backfill (S6) never
# resurrects a case as active -- it only writes historical closed/lapsed
# rows."
# ---------------------------------------------------------------------------

# Namespaces every correlation_key this module backfills, so a historical row
# can never collide with a correlation_key a live S3 contribution tool opens
# for the same underlying identity after this backfill runs --
# uq_fleet_cases_active_correlation_key only constrains non-closed rows, so
# nothing at the database would otherwise stop that collision. Also makes a
# backfilled row self-describing to an operator scanning the table: this
# case's history predates RFC 0032.
BACKFILL_CORRELATION_KEY_PREFIX = "backfill:owner_condition:"

# Fallback outcome for a resolved owner_conditions episode whose
# metadata.resolution_reason is absent (an explicit resolve_condition() call
# made with no resolution_metadata) -- chk_fleet_cases_closed_needs_outcome
# still requires a non-NULL value, and "resolved" is the honest generic
# answer when the producer recorded no more specific reason.
DEFAULT_BACKFILL_OUTCOME = "resolved"


async def backfill_historical_case(
    pool: Any,
    *,
    correlation_key: str,
    outcome: str,
    opened_at: datetime,
    closed_at: datetime,
) -> dict[str, Any] | None:
    """Insert one historical case at ``state='closed'``, or no-op if a case
    with this exact ``correlation_key`` already exists.

    Every backfill source must go through this one write path. The INSERT
    below hard-codes ``state = 'closed'`` in the SQL text itself -- there is
    no parameter that can make it write anything else, so no future caller
    can make this function resurrect a case as active (RFC 0032's Non-goals:
    "Backfill (S6) never resurrects a case as active"). Because every row
    this ever writes already has ``state = 'closed'``, it can never collide
    with ``uq_fleet_cases_active_correlation_key`` -- that partial index only
    constrains rows where ``state <> 'closed'``.

    The ``WHERE NOT EXISTS`` guard makes rerunning a backfill over the same
    source data idempotent: a ``correlation_key`` this function has already
    written is skipped (returns ``None``) rather than inserted again, so no
    duplicate rows accumulate across reruns.

    Raises :class:`FleetCaseError` for a blank ``correlation_key``/``outcome``
    or a ``closed_at`` that precedes ``opened_at`` -- refused before any
    database access, mirroring :func:`open_case`/:func:`close_case`'s own
    input validation.
    """
    if not correlation_key or not correlation_key.strip():
        raise FleetCaseError("backfill_historical_case requires a non-empty correlation_key.")
    if not outcome or not outcome.strip():
        raise FleetCaseError("backfill_historical_case requires a non-empty outcome.")
    if closed_at < opened_at:
        raise FleetCaseError(
            f"backfill_historical_case: closed_at={closed_at!r} precedes opened_at={opened_at!r}."
        )

    row = await pool.fetchrow(
        f"""
        INSERT INTO public.fleet_cases
            (correlation_key, state, posture, outcome, opened_at, updated_at, closed_at)
        SELECT $1, 'closed', 'silent', $2, $3, $4, $4
        WHERE NOT EXISTS (
            SELECT 1 FROM public.fleet_cases WHERE correlation_key = $1
        )
        RETURNING {_CASE_COLUMNS}
        """,
        correlation_key,
        outcome,
        opened_at,
        closed_at,
    )
    return dict(row) if row is not None else None


def _owner_condition_backfill_outcome(metadata: dict[str, Any] | None) -> str:
    """Map a resolved owner_conditions episode's ``resolution_reason`` to a
    fleet_cases ``outcome``. See :func:`backfill_from_owner_conditions`."""
    if isinstance(metadata, dict):
        reason = metadata.get("resolution_reason")
        if isinstance(reason, str) and reason.strip():
            return reason
    return DEFAULT_BACKFILL_OUTCOME


async def backfill_from_owner_conditions(pool: Any, *, page_size: int = 200) -> dict[str, Any]:
    """One-time/idempotent-rerun backfill of closed ``fleet_cases`` rows from
    already-resolved ``public.owner_conditions`` episodes.

    Source choice
    -------------
    RFC 0032's Context section names the insight broker's correlated-cluster
    computation (``roster/switchboard/tools/insight/broker.py::
    _cluster_candidates``/``_synthesize_cluster_sentence``) as this feature's
    original motivation, but that computation is, by the RFC's own words,
    discarded every delivery cycle: it runs in-memory over whichever
    ``insight_candidates`` rows happen to be ``status='pending'`` at that
    instant and never persists a cluster or a correlation_key anywhere.
    There is no durable cluster-history table to backfill from --
    reapplying ``_cluster_candidates`` to old ``insight_candidates`` rows
    (which are themselves purged by ``cleanup_old_rows``' 30-day retention
    regardless) would not reproduce what was actually shown to the owner at
    delivery time, since a cycle's clustering depends on exactly which
    candidates happened to still be pending together at that moment --
    fabricating history a synthetic re-clustering cannot actually know.

    ``public.owner_conditions`` (``butlers.core.owner_conditions``,
    bu-ep4ks.6) is the clean historical source instead: a durable
    append-per-episode ledger of owner-facing standing concerns (an overdue
    bill, an expiring document, and similar) that already existed before RFC
    0032, with an explicit terminal ``state='resolved'`` and a
    ``chk_owner_conditions_resolved_fields`` guarantee that ``resolved_at``
    is always set once it is. Each resolved episode is one concluded
    situation -- exactly what a backfilled fleet case represents.

    Mapping
    -------
    - ``correlation_key`` = ``f"{BACKFILL_CORRELATION_KEY_PREFIX}{source}:
      {fingerprint}:{episode}"`` -- namespaced by
      :data:`BACKFILL_CORRELATION_KEY_PREFIX` so a backfilled row can never
      collide with a live case a later S3 tool opens for the same identity,
      and suffixed with ``episode`` because one ``(source, fingerprint)``
      identity can have multiple past resolved episodes (it reopened and
      resolved more than once) -- each is a distinct historical situation,
      not the same case repeated.
    - ``outcome`` = the episode's ``metadata.resolution_reason`` when it
      recorded one (see :func:`_owner_condition_backfill_outcome`), else
      :data:`DEFAULT_BACKFILL_OUTCOME`.
    - ``opened_at``/``closed_at`` = the episode's own ``first_detected_at``/
      ``resolved_at``.

    No ``fleet_case_links`` row is written back to the source episode --
    binding a case to another ledger's entry is RFC 0032 Slice 7's job
    ("three-ledger binding"), not this slice's.

    Idempotent: a ``correlation_key`` this function has already backfilled is
    skipped on a rerun (see :func:`backfill_historical_case`), so processing
    the same ``owner_conditions`` rows twice creates no duplicate
    ``fleet_cases`` rows.

    Returns ``{"created_case_ids": [...], "created_count": int, "skipped_count": int}``.
    """
    created: list[str] = []
    skipped = 0
    offset = 0
    while True:
        rows = await pool.fetch(
            """
            SELECT source, fingerprint, episode, first_detected_at, resolved_at, metadata
            FROM public.owner_conditions
            WHERE state = 'resolved'
            ORDER BY first_detected_at ASC
            OFFSET $1 LIMIT $2
            """,
            offset,
            page_size,
        )
        if not rows:
            break
        offset += len(rows)

        for row in rows:
            resolved_at = row["resolved_at"]
            if resolved_at is None:
                # chk_owner_conditions_resolved_fields guarantees this cannot
                # happen for state='resolved'; skip defensively rather than
                # ever writing a fleet_cases row with closed_at=NULL.
                skipped += 1
                continue
            metadata = row["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            correlation_key = (
                f"{BACKFILL_CORRELATION_KEY_PREFIX}{row['source']}:"
                f"{row['fingerprint']}:{row['episode']}"
            )
            result = await backfill_historical_case(
                pool,
                correlation_key=correlation_key,
                outcome=_owner_condition_backfill_outcome(metadata),
                opened_at=row["first_detected_at"],
                closed_at=resolved_at,
            )
            if result is not None:
                created.append(str(result["id"]))
            else:
                skipped += 1

    return {
        "created_case_ids": created,
        "created_count": len(created),
        "skipped_count": skipped,
    }
