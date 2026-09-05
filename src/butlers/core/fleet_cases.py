"""Fleet Case File data layer: ``public.fleet_cases``/``fleet_case_evidence``/
``fleet_case_links`` (bu-8cdl1.7 Slice 3, RFC 0032).

See ``about/legends-and-lore/rfcs/0032-fleet-case-file.md`` for the full
design and ``alembic/versions/core/core_217_fleet_case_file.py`` (Slice 1) for
the schema/RLS this module writes through.

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

import uuid
from typing import Any

import asyncpg

CASE_STATES = frozenset({"open", "watching", "closing", "closed"})
CASE_POSTURES = frozenset({"silent", "routine", "active", "urgent"})

_CASE_COLUMNS = "id, correlation_key, state, posture, outcome, opened_at, updated_at, closed_at"
_EVIDENCE_COLUMNS = "id, case_id, contributor, kind, ref, payload, contributed_at"
_LINK_COLUMNS = "id, case_id, link_kind, ref, metadata, linked_at"

# Defensive caps on one case's accreted evidence/links, mirroring the read
# API's limits (roster/switchboard/api/router.py) -- a case that has been
# accreting for months should never make read_case unbounded.
_EVIDENCE_LIMIT = 500
_LINKS_LIMIT = 500


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
