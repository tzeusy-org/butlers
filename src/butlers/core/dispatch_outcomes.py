"""Atomic dispatch-attempt persistence and runtime-attention production.

Qualifying breaker outcomes are serialized per catalog entry.  The attempt
and any resulting durable attention episode commit together; persistence is
best-effort so an observability failure never changes the runtime result that
the spawner already obtained.  The one bounded exception to "together" is a
producer that refuses the call outright for lack of a canonical ``SET ROLE``:
that is rolled back to a savepoint so the attempt row still commits, edgeless.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

from butlers.core.model_routing import CEILING_DENIAL_REASON_PREFIX, get_breaker_state
from butlers.metrics_registry import get_or_create_counter

logger = logging.getLogger(__name__)

runtime_attention_recorder_total = get_or_create_counter(
    "runtime_attention_recorder_total",
    "Serialized dispatch recorder outcomes and bounded runtime-attention edge results.",
    labelnames=["outcome", "edge"],
)

_QUALIFYING_BREAKER_OUTCOMES = frozenset({"runtime_failure", "success"})


def _safe_inc(outcome: str, edge: str) -> None:
    """Increment the recorder counter without letting a metrics failure escape.

    The spawner calls this module unguarded, so a metrics backend that raises
    must not turn provenance degradation into a caller-visible spawn failure.
    This matters most in the degraded handler below, which increments while
    already handling an exception.
    """
    try:
        runtime_attention_recorder_total.labels(outcome=outcome, edge=edge).inc()
    except Exception:
        logger.debug(
            "Runtime-attention recorder metric increment failed for outcome=%s edge=%s",
            outcome,
            edge,
            exc_info=True,
        )


async def _produce_edge(
    connection: asyncpg.Connection,
    statement: str,
    *args: object,
) -> tuple[object | None, bool]:
    """Call a v2 attention producer inside its own savepoint.

    The producer deliberately runs in the same transaction as the dispatch-
    attempt insert so the attempt row and its operational edge commit
    together.  Postgres poisons the whole transaction on a raised exception,
    though, so an unauthorized producer would take the attempt row down with
    it: both v2 producers raise ``42501`` unless ``current_setting('role')``
    is a canonical ``butler_*_rw``, and outside hardened posture ``db.py``
    fails open and runs the pool with no ``SET ROLE`` at all.  On a dev stack
    that lost every breaker-edge failure row permanently, so the breaker
    could never trip.  The nested transaction is the savepoint we roll back
    to, leaving the outer transaction — and the attempt row — intact.

    Returns ``(episode_id, unauthorized)``.  Only ``42501`` is absorbed; any
    other producer failure propagates, so an attempt row whose edge failed
    for a real reason still rolls back with it.
    """
    try:
        async with connection.transaction():
            return await connection.fetchval(statement, *args), False
    except asyncpg.InsufficientPrivilegeError as exc:
        logger.warning(
            "Runtime-attention producer refused the call (sqlstate 42501); the "
            "attempt row is preserved and its edge is skipped: %s -- %s",
            statement.strip(),
            exc,
        )
        return None, True


# ``ts`` is deliberately ``clock_timestamp()`` and must stay that way:
# REQ-model-catalog-001 orders outcomes by the instant they were serialized,
# not by BEGIN time, so a transaction that waited on the breaker lock has to
# stamp its row after the transaction it waited for.  ``now()`` would order the
# waiter first.  ``ts`` is now the only clock this module reads at all -- the
# fleet-halt month is the producer's alone (see below).
_DISPATCH_ATTEMPTS_INSERT = """
    INSERT INTO public.model_dispatch_attempts
        (session_id, catalog_entry_id, butler, outcome,
         failure_reason, error_code, error_message,
         tool_call_count, attempt_index, logical_session_id, duration_ms, ts)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, clock_timestamp())
"""

_DISPATCH_ATTEMPTS_INSERT_RETURNING_ID = _DISPATCH_ATTEMPTS_INSERT + " RETURNING id"

_BREAKER_LOCK_SQL = """
    SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))
"""

# There is deliberately no fleet-halt counterpart to ``_BREAKER_LOCK_SQL``, and
# adding one back would be a regression (bu-86t7r).  The breaker edge needs a
# recorder-held lock because its decision spans two statements this module issues
# itself -- ``get_breaker_state`` then the producer -- so nothing else makes that
# read-then-write pair atomic.  The fleet-halt decision is a single statement, and
# the whole once-per-month guarantee lives inside
# ``public.append_runtime_attention_fleet_halt`` (defined once in
# ``runtime_attention_admin.install_fleet_halt_producer_v2``, scripts/init-db.sql):
# it serializes on its own month-scoped ``pg_advisory_xact_lock``, dedupes through
# ``INSERT ... ON CONFLICT (fleet_halt_month) DO NOTHING`` against the partial
# unique index ``ux_runtime_attention_outbox_fleet_halt_month`` -- which waits out
# an uncommitted conflicting insert rather than skipping past it -- and re-SELECTs
# so a loser is handed the winner's episode.  A recorder-held lock on that same key
# is the same lock taken earlier: advisory locks are re-entrant within one
# transaction, so it adds no exclusion the producer does not already hold.
#
# What it does add is scope.  It widens the critical section to also cover the
# denial row's INSERT and the producer's month-wide ``count(*)`` evidence query --
# on the one path that fires for *every* spawn while the fleet is halted, and whose
# evidence query gets slower with each denial the month accumulates.  All that buys
# is an exactly-serialized ``denied_count``/``first_denied_at`` in
# ``source_snapshot``, which is provenance: no reader branches on either, and the
# producer's own gates need only ``count(*) >= 1`` (its own row, always visible to
# its own snapshot) and ``min(ts) >= producer_activated_at``.  Activation cannot be
# straddled by an uncommitted denial whatever this module locks: it is a one-shot
# migration whose ``CREATE TRIGGER ... BEFORE INSERT ON model_dispatch_attempts``
# takes SHARE ROW EXCLUSIVE in the same transaction that writes
# ``producer_activated_at``, and that conflicts with every inserter's ROW EXCLUSIVE.
#
# Not locking here also removes a bug class rather than trading one for another: a
# second, independently evaluated month expression in this file is what let the
# lock key and the producer's ``v_month`` name different months across a UTC
# rollover (bu-jxelx, #3822).  The month is now named once, by the producer, and
# that one value is what it locks on, what it filters evidence by, and what it
# writes.


async def record_dispatch_attempt(
    pool: asyncpg.Pool,
    *,
    catalog_entry_id: uuid.UUID,
    butler: str,
    outcome: str,
    attempt_index: int,
    session_id: uuid.UUID | None = None,
    failure_reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    tool_call_count: int | None = None,
    logical_session_id: str | None = None,
    duration_ms: int | None = None,
    produce_fleet_halt: bool = False,
) -> int | None:
    """Persist one attempt and atomically append any operational edge.

    ``runtime_failure`` and ``success`` are serialized with a transaction-
    scoped advisory lock keyed by catalog entry.  A closed-to-open transition
    calls the validated server-side model-breaker producer before commit.

    ``produce_fleet_halt`` is reserved for the spawner's monthly-ceiling deny
    path.  It invokes the validated fleet-halt producer in the same transaction
    as the denial row and takes no recorder-held lock: the once-per-month
    guarantee is the producer's own, so the deny path — which fires for every
    spawn while the fleet is halted — is not serialized fleet-wide.

    Non-qualifying outcomes retain the existing lightweight best-effort insert
    path.  The returned bigint is stable for transactional writes; ``None``
    means persistence degraded or the outcome was intentionally non-
    qualifying.  A producer that is unauthorized to run still returns the
    attempt id — see ``_produce_edge`` — and reports the ``*_unauthorized``
    edge.  This function never raises into runtime/failover handling:
    the spawner's call sites are unguarded, so the whole body — argument
    handling and fleet-halt provenance validation included — runs under the
    degraded handler, and every metric increment goes through ``_safe_inc``.
    """
    try:
        safe_error_message = error_message[:4096] if error_message else None
        if produce_fleet_halt and (
            outcome != "quota_skip"
            or not (failure_reason or "").startswith(CEILING_DENIAL_REASON_PREFIX)
        ):
            logger.warning(
                "Rejected invalid fleet-halt provenance request for "
                "butler=%s catalog_entry_id=%s outcome=%s",
                butler,
                catalog_entry_id,
                outcome,
            )
            _safe_inc("rejected", "fleet_halt")
            return None

        values = (
            session_id,
            catalog_entry_id,
            butler,
            outcome,
            failure_reason,
            error_code,
            safe_error_message,
            tool_call_count,
            attempt_index,
            logical_session_id,
            duration_ms,
        )

        if outcome not in _QUALIFYING_BREAKER_OUTCOMES and not produce_fleet_halt:
            try:
                await pool.execute(_DISPATCH_ATTEMPTS_INSERT, *values)
            except Exception:
                _safe_inc("degraded", "none")
                logger.debug(
                    "Failed to write non-qualifying dispatch attempt for "
                    "butler=%s catalog_entry_id=%s outcome=%s",
                    butler,
                    catalog_entry_id,
                    outcome,
                    exc_info=True,
                )
            else:
                _safe_inc("persisted", "none")
            return None

        edge_outcome = "none"
        async with pool.acquire() as connection:
            async with connection.transaction():
                breaker_was_open = False
                if outcome in _QUALIFYING_BREAKER_OUTCOMES:
                    await connection.execute(_BREAKER_LOCK_SQL, str(catalog_entry_id))
                    breaker_was_open = (await get_breaker_state(connection, catalog_entry_id)).open
                # ``produce_fleet_halt`` deliberately has no matching branch: the
                # producer owns the fleet-halt critical section, so this path does
                # not serialize denials fleet-wide.  See the note beside
                # ``_BREAKER_LOCK_SQL`` before adding one back.

                # core_199's cutover trigger treats the absence of this
                # transaction-local ABI as an old direct-delivery binary and
                # plants only the legacy helper's suppression marker.  Set it
                # after serialization so both the timestamp and producer ABI
                # describe the transaction that actually owns the outcome.
                await connection.execute(
                    "SELECT set_config('butlers.runtime_attention_producer_abi', '2', true)"
                )
                attempt_id = await connection.fetchval(
                    _DISPATCH_ATTEMPTS_INSERT_RETURNING_ID,
                    *values,
                )
                if not isinstance(attempt_id, int):
                    raise RuntimeError("dispatch-attempt insert returned no stable bigint id")

                if outcome == "runtime_failure" and not breaker_was_open:
                    # The breaker path keeps a clock asymmetry the fleet-halt
                    # month above does not, and it is load-bearing.  Edge
                    # detection here evaluates the half-open cooldown against
                    # ``now()`` (model_routing's ``_BREAKER_OPEN_CTE``); the v2
                    # producer re-proves the same window against
                    # ``clock_timestamp()`` and raises ``23514`` -- which is not
                    # absorbed, so it would roll the attempt row back too -- if it
                    # finds the edge was already open.  Because now() <=
                    # clock_timestamp, ``now() - ts < 15 min`` is the weaker test:
                    # "Python says already open" is a superset of "SQL says already
                    # open", so whenever the producer would raise, this branch has
                    # already declined to call it.  Keep it that way; aligning the
                    # producer to now() here without re-deriving that containment
                    # would put a transaction-losing raise back in reach.
                    breaker_is_open = (await get_breaker_state(connection, catalog_entry_id)).open
                    if breaker_is_open:
                        episode_id, unauthorized = await _produce_edge(
                            connection,
                            "SELECT public.append_runtime_attention_model_breaker($1)",
                            attempt_id,
                        )
                        if unauthorized:
                            edge_outcome = "model_breaker_unauthorized"
                        elif isinstance(episode_id, uuid.UUID):
                            edge_outcome = "model_breaker_created"
                        else:
                            edge_outcome = "model_breaker_suppressed"
                elif produce_fleet_halt:
                    episode_id, unauthorized = await _produce_edge(
                        connection,
                        "SELECT public.append_runtime_attention_fleet_halt()",
                    )
                    if unauthorized:
                        edge_outcome = "fleet_halt_unauthorized"
                    elif isinstance(episode_id, uuid.UUID):
                        edge_outcome = "fleet_halt_created"
                    else:
                        edge_outcome = "fleet_halt_suppressed"

        _safe_inc("persisted", edge_outcome)
        logger.info(
            "Dispatch outcome recorder committed for butler=%s "
            "catalog_entry_id=%s outcome=%s attempt_id=%s edge=%s",
            butler,
            catalog_entry_id,
            outcome,
            attempt_id,
            edge_outcome,
        )
        return attempt_id
    except Exception:
        requested_edge = (
            "fleet_halt"
            if produce_fleet_halt
            else "model_breaker"
            if outcome == "runtime_failure"
            else "none"
        )
        _safe_inc("degraded", requested_edge)
        logger.warning(
            "Dispatch outcome provenance degraded; runtime result is unchanged "
            "for butler=%s catalog_entry_id=%s outcome=%s fleet_halt=%s",
            butler,
            catalog_entry_id,
            outcome,
            produce_fleet_halt,
            exc_info=True,
        )
        return None
