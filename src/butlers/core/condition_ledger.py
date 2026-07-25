"""The condition-ledger engine — shared reconciliation semantics behind every
durable append-per-episode standing-condition table in this codebase.

bu-ep4ks.6 — extracted from ``butlers.core.infra_conditions`` (bu-27dxl.6.2)
so the same open/aging/auto-resolve/re-escalate lifecycle machinery backs
more than infrastructure reliability: ``butlers.core.owner_conditions``
(bu-ep4ks.6) reuses this engine verbatim for owner-facing standing concerns
(an overdue bill, a refill due, an expiring document) that infra_conditions'
docstring explicitly scoped out. Every table this engine drives shares the
same twelve columns (see ``core_182_infra_conditions`` / the owner_conditions
migration): id, source, fingerprint, episode, state, first_detected_at,
last_confirmed_at, last_escalated_at, next_reescalate_at, escalation_level,
resolved_at, recovered_after_s, summary, metadata.

``infra_conditions.py`` and ``owner_conditions.py`` are thin facades over
this module: each binds ``table`` to its own fully-qualified table name and
re-exports the shared vocabulary (``Observation``, ``ConditionTransition``,
``compute_fingerprint``, ``VALID_STATES``, ``ESCALATION_LEVELS``) so existing
call sites (``butlers.jobs.deploy_drift``, ``butlers.jobs.
calendar_sync_deadman``) are unaffected by this extraction — same imports,
same signatures, same behavior. ``table`` is always a caller-supplied static
string (one of the two facade modules), never user input, so direct
interpolation into the SQL text below is safe — asyncpg cannot parameterize
identifiers.

Design
------
The ledger has at most one ACTIVE (``open``/``aging``) episode per
``(source, fingerprint)`` identity. An episode is one row; confirming
evidence mutates that row's ``last_confirmed_at`` (and optionally its
``summary``/``metadata``) in place rather than inserting a new row —
"append-per-episode", not "append-per-confirmation". A new row is only
inserted when a condition opens for the first time or recurs after its prior
episode resolved.

``reconcile_snapshot`` is the single entry point: it accepts everything a
producer currently observes for one ``source`` plus whether that observation
is a complete, successful snapshot of the producer's authoritative scope.
Only a ``snapshot_complete=True`` call may resolve an active episode that it
did not observe — a failed/degraded/partial producer run
(``snapshot_complete=False``) can still confirm evidence for what it DID see,
but can never resolve anything by omission.

Concurrency
-----------
All of one ``source``'s writes within a single ``reconcile_snapshot`` call
run inside one transaction holding a transaction-scoped Postgres advisory
lock keyed by ``hashtext(table || ':' || source)`` — the table prefix keeps
the same source string in two different condition tables (e.g. a butler
named "finance" as an infra_conditions source vs. an owner_conditions
source) from contending on the same lock key. That serializes every
concurrent reconciler for the same (table, source) pair; different sources —
or the same source in a different table — reconcile fully in parallel.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

import asyncpg

VALID_STATES = frozenset({"open", "aging", "resolved"})
ESCALATION_LEVELS = ("L0", "L1", "L2", "L3")

TransitionKind = Literal[
    "opened",
    "reopened",
    "confirmed",
    "escalation_due",
    "resolved",
    "no_change",
]

# Keyed by the escalation level a condition is AT when its due transition
# fires: (level it advances to, interval added to `now` for the FOLLOWING
# due date). L1 due after producer grace (handled at open time, not here),
# L2 one day after L1, L3 three additional days after L2, then L3 repeats
# every seven days.
_ESCALATION_ADVANCE: dict[str, tuple[str, timedelta]] = {
    "L0": ("L1", timedelta(days=1)),
    "L1": ("L2", timedelta(days=3)),
    "L2": ("L3", timedelta(days=7)),
    "L3": ("L3", timedelta(days=7)),
}


# ---------------------------------------------------------------------------
# Canonical condition identity
# ---------------------------------------------------------------------------


def _canonicalize(value: Any) -> Any:
    """Recursively sort dict keys and turn set-valued collections into sorted lists."""
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_canonicalize(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


def compute_fingerprint(source: str, version: int, identity_facts: dict[str, Any]) -> str:
    """Return the SHA-256 hex fingerprint for one versioned identity payload.

    ``identity_facts`` must contain only stable condition facts — timestamps,
    ages, revision numbers that can change during the same episode, and error
    prose belong in an :class:`Observation`'s ``summary``/``metadata``
    (evidence), never here. A producer that must change what its identity
    payload means increments ``version`` — that alone changes every future
    fingerprint for it, without reinterpreting any prior episode's stored
    fingerprint.
    """
    payload = {
        "source": source,
        "version": version,
        "facts": _canonicalize(identity_facts),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Reconciliation API
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One condition a producer currently observes, within a reconciliation snapshot.

    ``fingerprint`` should come from :func:`compute_fingerprint` (or an
    equivalently deterministic sorted-payload SHA-256) so recurring evidence
    for the same condition keeps landing on the same identity. ``summary``
    and ``metadata`` are sanitized evidence, not identity — they replace the
    episode's stored evidence on each confirmation rather than accumulating.
    """

    fingerprint: str
    summary: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConditionTransition:
    """One row's outcome from a single :func:`reconcile_snapshot` call."""

    condition_id: uuid.UUID
    source: str
    fingerprint: str
    episode: int
    state: str
    transition: TransitionKind
    escalation_level: str
    next_reescalate_at: datetime | None
    resolved_at: datetime | None = None
    recovered_after_s: float | None = None


def _dumps_metadata(metadata: dict[str, Any] | None) -> str | None:
    return json.dumps(metadata) if metadata is not None else None


async def reconcile_snapshot(
    pool: asyncpg.Pool,
    *,
    table: str,
    source: str,
    observations: Sequence[Observation],
    snapshot_complete: bool,
    initial_grace_seconds: float,
) -> list[ConditionTransition]:
    """Atomically reconcile one producer check-in against the condition ledger at ``table``.

    ``table`` must be a fully-qualified, caller-controlled static table name
    (e.g. ``"public.infra_conditions"``) — never derived from external input.

    For every :class:`Observation`:
      - no active episode exists for its fingerprint -> a new episode opens
        (``opened`` for that identity's first-ever episode, ``reopened`` when
        a prior episode for the same identity already resolved) at level
        ``L0``, due for ``L1`` after ``initial_grace_seconds``;
      - an active episode already exists -> its evidence is confirmed
        (``last_confirmed_at``, and ``summary``/``metadata`` when given), and
        if its ``next_reescalate_at`` has passed, the due level is claimed
        atomically in the same update (``escalation_due``) instead of a
        plain ``confirmed``.

    When ``snapshot_complete`` is True, every active episode for ``source``
    that was NOT named by any ``observations`` entry resolves — this is the
    ONLY path that can resolve a condition. Recurrence after a resolution
    always creates the next episode, never mutates the resolved row.

    Raises ``ValueError`` for an empty ``table``/``source``, a negative
    ``initial_grace_seconds``, or a duplicate fingerprint within
    ``observations``.
    """
    if not table:
        raise ValueError("reconcile_snapshot: table must be non-empty")
    if not source:
        raise ValueError("reconcile_snapshot: source must be non-empty")
    if initial_grace_seconds < 0:
        raise ValueError("reconcile_snapshot: initial_grace_seconds must be >= 0")

    observed_fingerprints = [o.fingerprint for o in observations]
    if len(set(observed_fingerprints)) != len(observed_fingerprints):
        raise ValueError("reconcile_snapshot: duplicate fingerprint in observations")

    async with pool.acquire() as conn:
        async with conn.transaction():
            return await _reconcile_source_locked(
                conn,
                table=table,
                source=source,
                observations=observations,
                snapshot_complete=snapshot_complete,
                initial_grace_seconds=initial_grace_seconds,
            )


async def _reconcile_source_locked(
    conn: asyncpg.Connection,
    *,
    table: str,
    source: str,
    observations: Sequence[Observation],
    snapshot_complete: bool,
    initial_grace_seconds: float,
) -> list[ConditionTransition]:
    # Serializes every concurrent reconciler for this (table, source) pair.
    # Different sources, or the same source in a different table, never
    # contend — see module docstring "Concurrency".
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{table}:{source}")

    # `now()` is stable for the lifetime of a Postgres transaction; fetching
    # it once and threading it through keeps every write in this call (open,
    # confirm, escalate, resolve) working off the exact same instant.
    now: datetime = await conn.fetchval("SELECT now()")

    active_rows = await conn.fetch(
        f"""
        SELECT id, fingerprint, episode, state, escalation_level,
               first_detected_at, next_reescalate_at
        FROM {table}
        WHERE source = $1 AND state IN ('open', 'aging')
        """,
        source,
    )
    active_by_fingerprint = {row["fingerprint"]: row for row in active_rows}

    results: list[ConditionTransition] = []

    for obs in observations:
        existing = active_by_fingerprint.get(obs.fingerprint)
        if existing is None:
            results.append(
                await _open_episode(
                    conn,
                    table=table,
                    source=source,
                    obs=obs,
                    initial_grace_seconds=initial_grace_seconds,
                    now=now,
                )
            )
        else:
            results.append(
                await _confirm_episode(conn, existing, table=table, source=source, obs=obs, now=now)
            )

    if snapshot_complete:
        observed_set = {obs.fingerprint for obs in observations}
        for fingerprint, row in active_by_fingerprint.items():
            if fingerprint in observed_set:
                continue
            results.append(
                await _resolve_episode(
                    conn, row, table=table, source=source, fingerprint=fingerprint, now=now
                )
            )

    return results


async def _open_episode(
    conn: asyncpg.Connection,
    *,
    table: str,
    source: str,
    obs: Observation,
    initial_grace_seconds: float,
    now: datetime,
) -> ConditionTransition:
    prior_episode = await conn.fetchval(
        f"""
        SELECT MAX(episode) FROM {table}
        WHERE source = $1 AND fingerprint = $2
        """,
        source,
        obs.fingerprint,
    )
    episode = int(prior_episode or 0) + 1

    row = await conn.fetchrow(
        f"""
        INSERT INTO {table}
            (source, fingerprint, episode, state, first_detected_at,
             last_confirmed_at, escalation_level, next_reescalate_at,
             summary, metadata)
        VALUES ($1, $2, $3, 'open', $4::timestamptz, $4::timestamptz, 'L0',
                $4::timestamptz + ($5 * INTERVAL '1 second'), $6, $7::jsonb)
        RETURNING id, episode, state, escalation_level, next_reescalate_at
        """,
        source,
        obs.fingerprint,
        episode,
        now,
        initial_grace_seconds,
        obs.summary,
        _dumps_metadata(obs.metadata),
    )
    return ConditionTransition(
        condition_id=row["id"],
        source=source,
        fingerprint=obs.fingerprint,
        episode=row["episode"],
        state=row["state"],
        transition="opened" if episode == 1 else "reopened",
        escalation_level=row["escalation_level"],
        next_reescalate_at=row["next_reescalate_at"],
    )


async def _confirm_episode(
    conn: asyncpg.Connection,
    existing: asyncpg.Record,
    *,
    table: str,
    source: str,
    obs: Observation,
    now: datetime,
) -> ConditionTransition:
    due = existing["next_reescalate_at"] is not None and existing["next_reescalate_at"] <= now

    if due:
        new_level, interval_to_next = _ESCALATION_ADVANCE[existing["escalation_level"]]
        new_state = "aging"
        transition: TransitionKind = "escalation_due"
    else:
        new_level = existing["escalation_level"]
        interval_to_next = timedelta(0)
        new_state = existing["state"]
        transition = "confirmed"

    row = await conn.fetchrow(
        f"""
        UPDATE {table}
        SET last_confirmed_at = $2::timestamptz,
            state = $3,
            escalation_level = $4,
            last_escalated_at = CASE WHEN $5 THEN $2::timestamptz ELSE last_escalated_at END,
            next_reescalate_at = CASE
                WHEN $5 THEN $2::timestamptz + $6 ELSE next_reescalate_at
            END,
            summary = COALESCE($7, summary),
            metadata = COALESCE($8::jsonb, metadata)
        WHERE id = $1
        RETURNING id, episode, state, escalation_level, next_reescalate_at
        """,
        existing["id"],
        now,
        new_state,
        new_level,
        due,
        interval_to_next,
        obs.summary,
        _dumps_metadata(obs.metadata),
    )
    return ConditionTransition(
        condition_id=row["id"],
        source=source,
        fingerprint=obs.fingerprint,
        episode=row["episode"],
        state=row["state"],
        transition=transition,
        escalation_level=row["escalation_level"],
        next_reescalate_at=row["next_reescalate_at"],
    )


async def _resolve_episode(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    *,
    table: str,
    source: str,
    fingerprint: str,
    now: datetime,
) -> ConditionTransition:
    recovered_after_s = (now - row["first_detected_at"]).total_seconds()
    updated = await conn.fetchrow(
        f"""
        UPDATE {table}
        SET state = 'resolved',
            resolved_at = $2,
            recovered_after_s = $3
        WHERE id = $1
        RETURNING id, episode, state, escalation_level
        """,
        row["id"],
        now,
        recovered_after_s,
    )
    return ConditionTransition(
        condition_id=updated["id"],
        source=source,
        fingerprint=fingerprint,
        episode=updated["episode"],
        state=updated["state"],
        transition="resolved",
        escalation_level=updated["escalation_level"],
        next_reescalate_at=None,
        resolved_at=now,
        recovered_after_s=recovered_after_s,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


async def get_active_condition(
    pool: asyncpg.Pool, *, table: str, source: str, fingerprint: str
) -> dict[str, Any] | None:
    """Return the active (``open``/``aging``) episode for ``(source, fingerprint)`` at ``table``.

    Returns ``None`` when there is no active episode — either the identity
    has never been observed, or its most recent episode already resolved.
    """
    row = await pool.fetchrow(
        f"""
        SELECT id, source, fingerprint, episode, state, first_detected_at,
               last_confirmed_at, last_escalated_at, next_reescalate_at,
               escalation_level, resolved_at, recovered_after_s, summary, metadata
        FROM {table}
        WHERE source = $1 AND fingerprint = $2 AND state IN ('open', 'aging')
        """,
        source,
        fingerprint,
    )
    return _row_to_dict(row) if row is not None else None


async def list_conditions(
    pool: asyncpg.Pool,
    *,
    table: str,
    source: str | None = None,
    state: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """List condition-ledger episodes at ``table``, most-recently-detected first.

    Returns ``(total, rows)`` where ``total`` is the unfiltered-by-page count
    matching the given filters (for pagination), and ``rows`` is the current
    page ordered by ``first_detected_at DESC``.
    """
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if source is not None:
        conditions.append(f"source = ${idx}")
        args.append(source)
        idx += 1
    if state is not None:
        conditions.append(f"state = ${idx}")
        args.append(state)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(
        f"SELECT count(*) FROM {table}{where}",
        *args,
    )
    rows = await pool.fetch(
        f"""
        SELECT id, source, fingerprint, episode, state, first_detected_at,
               last_confirmed_at, last_escalated_at, next_reescalate_at,
               escalation_level, resolved_at, recovered_after_s, summary, metadata
        FROM {table}{where}
        ORDER BY first_detected_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )
    return int(total or 0), [_row_to_dict(r) for r in rows]
