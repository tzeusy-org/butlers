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

Version-bump provenance is opt-in evidence. A producer records the version of
its identity payload with each observation and, on the first successor after a
deliberate version change, explicitly supplies the predecessor fingerprint.
Only a complete snapshot with a strictly higher declared successor version
records reciprocal episode links and the superseded terminal reason. The
fingerprint itself is never migrated or reinterpreted.

Resolution evidence has one location
------------------------------------
``metadata.resolution_reason`` (top-level) is where a resolved episode says
why it ended, for BOTH resolution paths: the explicit
:func:`resolve_condition` (whose caller supplies the reason) and the
identity-version supersede path inside :func:`reconcile_snapshot` (which
records ``superseded_by_identity_version_bump``). A reader therefore never
needs to know which path ended an episode before it knows where to look, and
one query answers "why did this close" across every row. Identity lineage —
the ``successor``/``predecessor`` cross-references — stays under
``metadata.identity_payload`` beside the ``version`` it correlates; it is
lineage, not resolution evidence. ``RESOLUTION_METADATA_KEYS`` is refused at
the producer boundary so the creation-wins merge below can never swallow
what the ledger writes there.

``reconcile_snapshot`` is the snapshot-driven reconciliation entry point: it
accepts everything a producer currently observes for one ``source`` plus
whether that observation is a complete, successful snapshot of the producer's
authoritative scope. Only a ``snapshot_complete=True`` call may resolve an
active episode that it did not observe — a failed/degraded/partial producer
run (``snapshot_complete=False``) can still confirm evidence for what it DID
see, but can never resolve anything by omission. ``resolve_condition`` is the
explicit-resolution entry point for closing one active identity without a
complete producer snapshot. Both APIs emit ``ConditionTransition`` values for
their row-level outcomes.

Concurrency
-----------
All writes from either ``reconcile_snapshot`` or ``resolve_condition`` for a
``source`` run inside one transaction holding a transaction-scoped Postgres
advisory lock keyed by ``hashtext(table || ':' || source)`` — the table prefix keeps
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

#: The terminal reason the supersede path records on a predecessor episode.
SUPERSEDED_BY_IDENTITY_VERSION_BUMP = "superseded_by_identity_version_bump"

#: Top-level ``metadata`` keys the ledger itself writes when an episode is
#: resolved — see :func:`resolve_condition` (explicit resolution) and
#: :func:`_resolve_episode`'s supersede path. Both write ``resolution_reason``
#: to the same top-level key, so a reader never has to know which path ended
#: the episode (bu-o4i4j).
#:
#: REQ-owner-condition-ledger-004 merges resolution metadata *creation-wins*,
#: the right rule for a producer's own evidence: closing a condition must
#: never rewrite why it opened. Applied to these two keys it inverts into a
#: trap — a producer that set ``resolution_reason`` or ``evidence_closed`` at
#: creation time would keep its own value, and the ledger's closing evidence,
#: including the ``session_id`` provenance REQ-owner-condition-ledger-005
#: requires, would be dropped with no error and no signal in the returned
#: envelope. :func:`reconcile_snapshot` therefore refuses them at the producer
#: boundary (REQ-owner-condition-ledger-006). These names belong to the
#: ledger, not to the producer.
#:
#: The reservation lives here rather than on the owner-conditions facade
#: because the ledger writes ``resolution_reason`` for BOTH tables now: the
#: supersede path resolves infra episodes too, so refusing the key only at the
#: owner boundary would leave the infra terminal reason silently droppable.
RESOLUTION_METADATA_KEYS: frozenset[str] = frozenset({"resolution_reason", "evidence_closed"})

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
# Deliberately no ``__all__``: this engine has always exposed its public
# surface implicitly, and a new one-name list would silently hide its other
# public ledger APIs. This name is public because the commitment job must share
# this exact cadence object rather than maintain a second schedule.
ESCALATION_ADVANCE: dict[str, tuple[str, timedelta]] = {
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

    ``identity_version`` records the producer's versioned identity-payload
    contract as durable evidence. When a producer deliberately changes that
    contract, its first successor observation MAY name the old fingerprint in
    ``predecessor_fingerprint``. A complete snapshot can then distinguish the
    predecessor's absence from ordinary recovery without reinterpreting or
    rewriting its historical fingerprint.
    """

    fingerprint: str
    summary: str | None = None
    metadata: dict[str, Any] | None = None
    identity_version: int | None = None
    predecessor_fingerprint: str | None = None


@dataclass(frozen=True)
class ConditionTransition:
    """One row-level outcome from snapshot reconciliation or explicit resolution."""

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
    identity_version: int | None = None


def _dumps_metadata(metadata: dict[str, Any] | None) -> str | None:
    return json.dumps(metadata) if metadata is not None else None


def _validate_resolution_inputs(
    *,
    table: str,
    source: str,
    fingerprint: str,
    resolution_metadata: dict[str, Any] | None,
) -> None:
    """Validate explicit-resolution inputs before acquiring a pool connection."""
    for name, value in (
        ("table", table),
        ("source", source),
        ("fingerprint", fingerprint),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"resolve_condition: {name} must be non-empty")

    if resolution_metadata is None:
        return
    if not isinstance(resolution_metadata, dict):
        raise ValueError("resolve_condition: resolution_metadata must be an object or None")
    try:
        json.dumps(resolution_metadata, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "resolve_condition: resolution_metadata must be JSON-serializable"
        ) from exc


def _reject_reserved_metadata_keys(observations: Sequence[Observation]) -> None:
    """Raise ``ValueError`` if any observation claims a ledger-owned metadata key.

    Raised before the pool is touched so a rejected snapshot writes nothing —
    the same "invalid input never reaches the pool" contract the broker's
    fingerprint and ``resolution_reason`` validation already keeps.
    """
    for obs in observations:
        metadata = obs.metadata
        if not isinstance(metadata, dict):
            continue
        reserved = sorted(RESOLUTION_METADATA_KEYS.intersection(metadata))
        if reserved:
            raise ValueError(
                "reconcile_snapshot: observation metadata may not set "
                f"{', '.join(repr(k) for k in reserved)} "
                f"(fingerprint={obs.fingerprint!r}) — "
                "reserved for the closing evidence written when the condition is "
                "resolved. Record producer-side context under a different key."
            )


def _metadata_with_identity_payload(obs: Observation) -> dict[str, Any] | None:
    """Add declared identity-version evidence without changing caller metadata."""
    if obs.identity_version is None:
        return obs.metadata

    metadata = dict(obs.metadata or {})
    payload: dict[str, Any] = {"version": obs.identity_version}
    metadata["identity_payload"] = payload
    return metadata


def _metadata_object(value: Any) -> dict[str, Any] | None:
    """Normalize asyncpg JSONB text when a pool has no JSON codec registered."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else None
    return None


def _identity_version(row: asyncpg.Record) -> int | None:
    metadata = _metadata_object(row["metadata"])
    if metadata is None:
        return None
    payload = metadata.get("identity_payload")
    version = payload.get("version") if isinstance(payload, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def _identity_predecessor_fingerprint(row: asyncpg.Record) -> str | None:
    metadata = _metadata_object(row["metadata"])
    if metadata is None:
        return None
    payload = metadata.get("identity_payload")
    predecessor = payload.get("predecessor") if isinstance(payload, dict) else None
    fingerprint = predecessor.get("fingerprint") if isinstance(predecessor, dict) else None
    return fingerprint if isinstance(fingerprint, str) else None


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
    that was NOT named by any ``observations`` entry resolves by snapshot
    omission. This is the snapshot-driven resolution path; callers with
    explicit recovery confirmation can use :func:`resolve_condition` to
    resolve one active identity without a complete producer snapshot.
    Recurrence after a resolution always creates the next episode, never
    mutates the resolved row.

    Raises ``ValueError`` for an empty ``table``/``source``, a negative
    ``initial_grace_seconds``, a duplicate fingerprint within
    ``observations``, or an observation whose ``metadata`` claims one of
    :data:`RESOLUTION_METADATA_KEYS`. All of it before any database access.
    """
    if not table:
        raise ValueError("reconcile_snapshot: table must be non-empty")
    if not source:
        raise ValueError("reconcile_snapshot: source must be non-empty")
    if initial_grace_seconds < 0:
        raise ValueError("reconcile_snapshot: initial_grace_seconds must be >= 0")
    for obs in observations:
        if obs.identity_version is not None and obs.identity_version < 1:
            raise ValueError("reconcile_snapshot: identity_version must be >= 1")
        if obs.predecessor_fingerprint is not None and obs.identity_version is None:
            raise ValueError(
                "reconcile_snapshot: predecessor_fingerprint requires identity_version"
            )

    observed_fingerprints = [o.fingerprint for o in observations]
    if len(set(observed_fingerprints)) != len(observed_fingerprints):
        raise ValueError("reconcile_snapshot: duplicate fingerprint in observations")
    predecessor_fingerprints = [
        o.predecessor_fingerprint for o in observations if o.predecessor_fingerprint is not None
    ]
    if len(set(predecessor_fingerprints)) != len(predecessor_fingerprints):
        raise ValueError("reconcile_snapshot: duplicate predecessor_fingerprint in observations")
    _reject_reserved_metadata_keys(observations)

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


async def resolve_condition(
    pool: asyncpg.Pool,
    *,
    table: str,
    source: str,
    fingerprint: str,
    resolution_metadata: dict[str, Any] | None = None,
) -> ConditionTransition | None:
    """Explicitly resolve the active episode for one condition identity.

    ``table`` is a fully-qualified, caller-controlled static table name (for
    example ``"public.owner_conditions"``), never request or tool input. The
    resolver takes the same source-scoped transaction advisory lock as
    :func:`reconcile_snapshot`, so an explicit resolution and a complete
    snapshot cannot produce duplicate terminal transitions. ``resolution_metadata``
    is shallow-merged with creation-wins semantics: existing top-level row
    values win over keys supplied by the resolver.

    Returns ``None`` when the identity has no active ``open``/``aging``
    episode, including after a previous explicit or snapshot resolution.

    Raises ``ValueError`` for a non-string or empty ``table``, ``source``, or
    ``fingerprint``, or when ``resolution_metadata`` is not a JSON object (or
    ``None``). Validation happens before acquiring a pool connection.
    """
    _validate_resolution_inputs(
        table=table,
        source=source,
        fingerprint=fingerprint,
        resolution_metadata=resolution_metadata,
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"{table}:{source}")
            now: datetime = await conn.fetchval("SELECT now()")
            row = await conn.fetchrow(
                f"""
                SELECT id, fingerprint, episode, state, escalation_level,
                       first_detected_at, metadata
                FROM {table}
                WHERE source = $1 AND fingerprint = $2 AND state IN ('open', 'aging')
                """,
                source,
                fingerprint,
            )
            if row is None:
                return None
            return await _resolve_episode(
                conn,
                row,
                table=table,
                source=source,
                fingerprint=fingerprint,
                now=now,
                resolution_metadata=resolution_metadata,
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
               first_detected_at, next_reescalate_at, metadata
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
        observed_transitions = {transition.fingerprint: transition for transition in results}
        successors_by_predecessor = {
            obs.predecessor_fingerprint: (obs, observed_transitions[obs.fingerprint])
            for obs in observations
            if obs.predecessor_fingerprint is not None
        }
        for fingerprint, row in active_by_fingerprint.items():
            if fingerprint in observed_set:
                continue
            successor = successors_by_predecessor.get(fingerprint)
            successor_transition: ConditionTransition | None = None
            if successor is not None:
                successor_obs, candidate = successor
                prior_version = _identity_version(row)
                if (
                    prior_version is not None
                    and successor_obs.identity_version is not None
                    and successor_obs.identity_version > prior_version
                    and _identity_predecessor_fingerprint(
                        active_by_fingerprint.get(successor_obs.fingerprint, row)
                    )
                    in (None, fingerprint)
                ):
                    successor_transition = candidate
            results.append(
                await _resolve_episode(
                    conn,
                    row,
                    table=table,
                    source=source,
                    fingerprint=fingerprint,
                    now=now,
                    successor=successor_transition,
                )
            )
            if successor_transition is not None:
                await _link_identity_predecessor(
                    conn,
                    table=table,
                    predecessor=row,
                    successor=successor_transition,
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
                $4::timestamptz + ($5 * INTERVAL '1 second'), $6, $7::text::jsonb)
        RETURNING id, episode, state, escalation_level, next_reescalate_at
        """,
        source,
        obs.fingerprint,
        episode,
        now,
        initial_grace_seconds,
        obs.summary,
        _dumps_metadata(_metadata_with_identity_payload(obs)),
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
        identity_version=obs.identity_version,
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
        new_level, interval_to_next = ESCALATION_ADVANCE[existing["escalation_level"]]
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
            metadata = CASE
                WHEN $8::text::jsonb IS NULL THEN metadata
                WHEN $8::text::jsonb ? 'identity_payload' THEN jsonb_set(
                    $8::text::jsonb,
                    '{{identity_payload}}',
                    COALESCE(metadata -> 'identity_payload', '{{}}'::jsonb)
                        || ($8::text::jsonb -> 'identity_payload'),
                    true
                )
                ELSE $8::text::jsonb
            END
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
        _dumps_metadata(_metadata_with_identity_payload(obs)),
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
        identity_version=obs.identity_version,
    )


async def _resolve_episode(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    *,
    table: str,
    source: str,
    fingerprint: str,
    now: datetime,
    successor: ConditionTransition | None = None,
    resolution_metadata: dict[str, Any] | None = None,
) -> ConditionTransition:
    recovered_after_s = (now - row["first_detected_at"]).total_seconds()
    # Why the supersede reason goes top-level and its successor link does not
    # (bu-o4i4j): `resolution_reason` is the ledger's answer to "why did this
    # episode end", and it has exactly ONE home — top-level `metadata` —
    # whichever path ended the episode. The supersede path used to nest it
    # inside `identity_payload`, so a reader had to know which path had run
    # before it knew where to look. The successor reference stays nested
    # because it is identity lineage, not resolution evidence: it belongs
    # beside the `version` it correlates and the reciprocal `predecessor`
    # link `_link_identity_predecessor` writes on the successor row.
    provenance: str | None = None
    if successor is not None:
        resolution_metadata = {
            **(resolution_metadata or {}),
            "resolution_reason": SUPERSEDED_BY_IDENTITY_VERSION_BUMP,
        }
        provenance = json.dumps(
            {
                "successor": {
                    "condition_id": str(successor.condition_id),
                    "fingerprint": successor.fingerprint,
                    "version": successor.identity_version,
                },
            }
        )
    updated = await conn.fetchrow(
        f"""
        UPDATE {table}
        SET state = 'resolved',
            resolved_at = $2,
            recovered_after_s = $3,
            metadata = CASE
                WHEN $4::text::jsonb IS NULL AND $5::text::jsonb IS NULL THEN metadata
                WHEN $5::text::jsonb IS NULL THEN
                    COALESCE($4::text::jsonb, '{{}}'::jsonb) || COALESCE(metadata, '{{}}'::jsonb)
                ELSE jsonb_set(
                    COALESCE($4::text::jsonb, '{{}}'::jsonb)
                        || COALESCE(metadata, '{{}}'::jsonb),
                    '{{identity_payload}}',
                    COALESCE(
                        (COALESCE($4::text::jsonb, '{{}}'::jsonb)
                            || COALESCE(metadata, '{{}}'::jsonb)) -> 'identity_payload',
                        '{{}}'::jsonb
                    ) || $5::text::jsonb,
                    true
                )
            END
        WHERE id = $1
        RETURNING id, episode, state, escalation_level
        """,
        row["id"],
        now,
        recovered_after_s,
        _dumps_metadata(resolution_metadata),
        provenance,
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


async def _link_identity_predecessor(
    conn: asyncpg.Connection,
    *,
    table: str,
    predecessor: asyncpg.Record,
    successor: ConditionTransition,
) -> None:
    """Record the immutable predecessor link on the declared successor."""
    predecessor_version = _identity_version(predecessor)
    assert predecessor_version is not None
    await conn.execute(
        f"""
        UPDATE {table}
        SET metadata = jsonb_set(
            COALESCE(metadata, '{{}}'::jsonb),
            '{{identity_payload}}',
            COALESCE(metadata -> 'identity_payload', '{{}}'::jsonb) || $2::text::jsonb,
            true
        )
        WHERE id = $1
        """,
        successor.condition_id,
        json.dumps(
            {
                "predecessor": {
                    "condition_id": str(predecessor["id"]),
                    "fingerprint": predecessor["fingerprint"],
                    "version": predecessor_version,
                }
            }
        ),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def row_to_dict(row: Any) -> dict[str, Any]:
    """Decode one ledger row into the shape every ledger reader returns.

    Public because the facades (``owner_conditions``, ``infra_conditions``,
    ``commitments``) re-export it: a facade that runs its own query instead of
    :func:`list_conditions` still has to hand back rows with ``metadata``
    decoded to a dict whether or not the pool registered a JSONB codec. One
    decoder, owned here beside the writes, keeps those results from drifting.
    """
    result = dict(row)
    metadata = _metadata_object(result.get("metadata"))
    if metadata is not None:
        result["metadata"] = metadata
    return result


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
    return row_to_dict(row) if row is not None else None


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
    return int(total or 0), [row_to_dict(r) for r in rows]
