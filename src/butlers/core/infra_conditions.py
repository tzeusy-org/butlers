"""The infrastructure condition ledger — durable append-per-episode reliability evidence.

bu-27dxl.6.2 — implements the representation and transition semantics defined
by the merged ``define-infrastructure-reliability-lifecycle`` OpenSpec change
(bu-27dxl.6.1, PR #3522). See
``openspec/changes/define-infrastructure-reliability-lifecycle/design.md``
and its ``specs/infrastructure-reliability/spec.md`` for the full normative
contract this module implements: canonical condition identity (Decision #1),
append-per-episode snapshot-authoritative recovery (Decision #2), and bounded
lifecycle escalation (Decision #3).

This module is representation + transition semantics ONLY (Migration Plan
step 1 of that design). It has no producer (``calendar_sync_deadman.py``,
``deploy_drift.py``), QA-dispatch, dashboard-lifespan-loop, or connector-
reader side effect, and it never invokes an LLM, healing attempt, or
notification — later children (bu-27dxl.6.3+) wire those up against
``reconcile_snapshot`` below.

Design
------
The ledger has at most one ACTIVE (``open``/``aging``) episode per
``(source, fingerprint)`` identity. An episode is one row; confirming
evidence mutates that row's ``last_confirmed_at`` (and optionally its
``summary``/``metadata``) in place rather than inserting a new row —
"append-per-episode", not "append-per-confirmation". A new row is only
inserted when a condition opens for the first time or recurs after its prior
episode resolved, which is exactly how resolved history is preserved
(the "resolved -> open" arrow in design.md's state diagram is a new row with
the next episode number, never a mutation of the resolved row).

``reconcile_snapshot`` is the single entry point: it accepts everything a
producer currently observes for one ``source`` plus whether that observation
is a complete, successful snapshot of the producer's authoritative scope.
Only a ``snapshot_complete=True`` call may resolve an active episode that it
did not observe (Decision #2) — a failed/degraded/partial producer run
(``snapshot_complete=False``) can still confirm evidence for what it DID see,
but can never resolve anything by omission.

Concurrency (AC4)
-----------------
All of one ``source``'s writes within a single ``reconcile_snapshot`` call
run inside one transaction holding a transaction-scoped Postgres advisory
lock keyed by ``hashtext(source)`` (the same pattern
``modules.approvals.decision_memory`` uses for its per-subject tally lock).
That serializes every concurrent reconciler for the same source, so two
overlapping callers can never both open a new episode for the same
fingerprint, and a due escalation level is claimed by exactly one caller.
Different sources reconcile fully in parallel — the lock never contends
across sources. ``uq_infra_conditions_active_episode`` (core_182) is a
DB-level backstop for the same invariant, not the primary mechanism.

Identity-version-bump totality (bu-27dxl.6.2 review-input; deeper fix tracked
as bu-rxo0l)
---------------------------------------------------------------------------
Decision #1 lets a producer bump its identity-payload version without
reinterpreting a prior episode's identity — see :func:`compute_fingerprint`.
A version bump computes a *new* fingerprint; it never rewrites an existing
episode's stored ``fingerprint``. Read naively, that would leave an episode
open/aging under a retired fingerprint permanently un-exitable, since no
future observation will ever carry that fingerprint again (enterable, not
exitable).

This module closes that gap structurally rather than adding a new terminal
state or a separate "superseded" status. ``reconcile_snapshot``'s
complete-snapshot resolution scope is ``source`` alone, never
``(source, fingerprint)`` or an identity version: a complete snapshot
enumerates everything the producer currently observes under its CURRENT
identity scheme, and ANY active episode for that source absent from that
enumeration is resolved — including one still keyed by a fingerprint the
producer stopped emitting after a version bump. This is not a special case;
it is the same "absent from a complete snapshot proves recovery" rule
Decision #2 already defines for ordinary recovery. A version bump makes the
old fingerprint permanently absent from every future snapshot, and permanent
absence from a complete snapshot is precisely what that rule already
resolves — see :func:`_reconcile_source_locked`'s absence pass, which is
keyed by ``source`` and never re-filters by fingerprint version.

What this deliberately does NOT do (left to bu-rxo0l): it does not forward
the old episode's identity/history onto the new fingerprint's first episode,
and it does not give an operator an explicit "resolved because superseded by
an identity-version bump" reason distinct from "resolved because it actually
recovered" — both currently read as an ordinary snapshot-absence resolution.
The guarantee this module makes is narrower but load-bearing: the state is
never a dead end. An in-flight episode under a retired fingerprint exits on
the producer's very next complete snapshot under the new identity scheme,
the same way any other recovered condition does.
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

#: design.md's "atomic snapshot reconciliation API" enumerates six
#: transitions. This module's ``reconcile_snapshot`` currently only ever
#: emits the first five — every call it makes either creates, confirms,
#: escalates, or resolves a row. ``no_change`` is reserved in the vocabulary
#: for a future explicit dry-run / no-op reconciliation mode (out of scope
#: for this representation-only child) rather than emitted speculatively
#: here; callers should not expect to see it yet.
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
# due date). Mirrors spec.md's "Bounded lifecycle escalation and recurrence":
# L1 due after producer grace (handled at open time, not here), L2 one day
# after L1, L3 three additional days after L2, then L3 repeats every seven
# days.
_ESCALATION_ADVANCE: dict[str, tuple[str, timedelta]] = {
    "L0": ("L1", timedelta(days=1)),
    "L1": ("L2", timedelta(days=3)),
    "L2": ("L3", timedelta(days=7)),
    "L3": ("L3", timedelta(days=7)),
}


# ---------------------------------------------------------------------------
# Canonical condition identity (Decision #1)
# ---------------------------------------------------------------------------


def _canonicalize(value: Any) -> Any:
    """Recursively sort dict keys and turn set-valued collections into sorted lists.

    ``json.dumps(..., sort_keys=True)`` already sorts dict keys recursively;
    this pass exists to make set/frozenset collections — which ``json``
    cannot serialize at all — into a deterministic, order-independent form
    before serialization.
    """
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_canonicalize(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


def compute_fingerprint(source: str, version: int, identity_facts: dict[str, Any]) -> str:
    """Return the SHA-256 hex fingerprint for one versioned identity payload.

    ``identity_facts`` must contain only stable condition facts (design.md
    Decision #1) — timestamps, ages, revision numbers that can change during
    the same outage, and error prose belong in a :class:`Observation`'s
    ``summary``/``metadata`` (evidence), never here. ``source`` and
    ``version`` are included in the hashed payload (so two sources, or two
    versions of the same source, never collide) as well as being the
    separately-stored namespace key the caller keeps alongside the result.

    A producer that must change what its identity payload means or contains
    increments ``version`` — that alone changes every future fingerprint for
    it, without reinterpreting any prior episode's stored fingerprint (see
    this module's docstring on identity-version-bump totality).
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
    episode's stored evidence on each confirmation rather than accumulating
    (the episode row is the current-evidence record, not an evidence log).
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
    source: str,
    observations: Sequence[Observation],
    snapshot_complete: bool,
    initial_grace_seconds: float,
) -> list[ConditionTransition]:
    """Atomically reconcile one producer check-in against the condition ledger.

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
    that was NOT named by any ``observations`` entry resolves (AC1) — this
    is the ONLY path that can resolve a condition (AC2: a
    ``snapshot_complete=False`` call, i.e. a failed/degraded/partial
    producer run, can confirm what it saw but never resolves by omission).
    Recurrence after a resolution always creates the next episode, never
    mutates the resolved row (AC3).

    Raises ``ValueError`` for an empty ``source``, a negative
    ``initial_grace_seconds``, or a duplicate fingerprint within
    ``observations`` (each fingerprint may appear at most once per call —
    a caller with two evidence fragments for the same identity must merge
    them into one :class:`Observation` first).
    """
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
                source=source,
                observations=observations,
                snapshot_complete=snapshot_complete,
                initial_grace_seconds=initial_grace_seconds,
            )


async def _reconcile_source_locked(
    conn: asyncpg.Connection,
    *,
    source: str,
    observations: Sequence[Observation],
    snapshot_complete: bool,
    initial_grace_seconds: float,
) -> list[ConditionTransition]:
    # Serializes every concurrent reconciler for this source (AC4). Different
    # sources never contend — see module docstring "Concurrency (AC4)".
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", source)

    # `now()` is stable for the lifetime of a Postgres transaction; fetching
    # it once and threading it through keeps every write in this call (open,
    # confirm, escalate, resolve) working off the exact same instant.
    now: datetime = await conn.fetchval("SELECT now()")

    active_rows = await conn.fetch(
        """
        SELECT id, fingerprint, episode, state, escalation_level,
               first_detected_at, next_reescalate_at
        FROM public.infra_conditions
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
                    source=source,
                    obs=obs,
                    initial_grace_seconds=initial_grace_seconds,
                    now=now,
                )
            )
        else:
            results.append(await _confirm_episode(conn, existing, source=source, obs=obs, now=now))

    if snapshot_complete:
        observed_set = {obs.fingerprint for obs in observations}
        for fingerprint, row in active_by_fingerprint.items():
            if fingerprint in observed_set:
                continue
            results.append(
                await _resolve_episode(conn, row, source=source, fingerprint=fingerprint, now=now)
            )

    return results


async def _open_episode(
    conn: asyncpg.Connection,
    *,
    source: str,
    obs: Observation,
    initial_grace_seconds: float,
    now: datetime,
) -> ConditionTransition:
    prior_episode = await conn.fetchval(
        """
        SELECT MAX(episode) FROM public.infra_conditions
        WHERE source = $1 AND fingerprint = $2
        """,
        source,
        obs.fingerprint,
    )
    episode = int(prior_episode or 0) + 1

    row = await conn.fetchrow(
        """
        INSERT INTO public.infra_conditions
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
        """
        UPDATE public.infra_conditions
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
    source: str,
    fingerprint: str,
    now: datetime,
) -> ConditionTransition:
    recovered_after_s = (now - row["first_detected_at"]).total_seconds()
    updated = await conn.fetchrow(
        """
        UPDATE public.infra_conditions
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
    pool: asyncpg.Pool, *, source: str, fingerprint: str
) -> dict[str, Any] | None:
    """Return the active (``open``/``aging``) episode for ``(source, fingerprint)``.

    Returns ``None`` when there is no active episode — either the identity
    has never been observed, or its most recent episode already resolved.
    Intended for future producer/QA-suppression consumers (bu-27dxl.6.3+)
    that need "is this condition currently active" without reconciling.
    """
    row = await pool.fetchrow(
        """
        SELECT id, source, fingerprint, episode, state, first_detected_at,
               last_confirmed_at, last_escalated_at, next_reescalate_at,
               escalation_level, resolved_at, recovered_after_s, summary, metadata
        FROM public.infra_conditions
        WHERE source = $1 AND fingerprint = $2 AND state IN ('open', 'aging')
        """,
        source,
        fingerprint,
    )
    return _row_to_dict(row) if row is not None else None


async def list_conditions(
    pool: asyncpg.Pool,
    *,
    source: str | None = None,
    state: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[dict[str, Any]]]:
    """List condition-ledger episodes, most-recently-detected first.

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
        f"SELECT count(*) FROM public.infra_conditions{where}",
        *args,
    )
    rows = await pool.fetch(
        f"""
        SELECT id, source, fingerprint, episode, state, first_detected_at,
               last_confirmed_at, last_escalated_at, next_reescalate_at,
               escalation_level, resolved_at, recovered_after_s, summary, metadata
        FROM public.infra_conditions{where}
        ORDER BY first_detected_at DESC
        OFFSET ${idx} LIMIT ${idx + 1}
        """,
        *args,
        offset,
        limit,
    )
    return int(total or 0), [_row_to_dict(r) for r in rows]
