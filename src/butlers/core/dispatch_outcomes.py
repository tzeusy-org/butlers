"""Atomic dispatch-attempt persistence and runtime-attention production.

Qualifying breaker outcomes are serialized per catalog entry.  The attempt
and any resulting durable attention episode commit together; persistence is
best-effort so an observability failure never changes the runtime result that
the spawner already obtained.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

from butlers.core.model_routing import CEILING_DENIAL_REASON_PREFIX, get_breaker_state

logger = logging.getLogger(__name__)

_QUALIFYING_BREAKER_OUTCOMES = frozenset({"runtime_failure", "success"})

_DISPATCH_ATTEMPTS_INSERT = """
    INSERT INTO public.model_dispatch_attempts
        (session_id, catalog_entry_id, butler, outcome,
         failure_reason, error_code, error_message,
         tool_call_count, attempt_index, logical_session_id, duration_ms)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""

_DISPATCH_ATTEMPTS_INSERT_RETURNING_ID = _DISPATCH_ATTEMPTS_INSERT + " RETURNING id"

_BREAKER_LOCK_SQL = """
    SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))
"""

_FLEET_HALT_LOCK_SQL = """
    SELECT pg_advisory_xact_lock(
        hashtextextended(
            'runtime_attention_fleet_halt:'
            || date_trunc('month', now() AT TIME ZONE 'UTC')::date::text,
            0
        )
    )
"""


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
    path.  It serializes by UTC calendar month and invokes the validated
    fleet-halt producer in the same transaction as the denial row.

    Non-qualifying outcomes retain the existing lightweight best-effort insert
    path.  The returned bigint is stable for transactional writes; ``None``
    means persistence degraded or the outcome was intentionally non-
    qualifying.  This function never raises into runtime/failover handling.
    """
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
            logger.debug(
                "Failed to write non-qualifying dispatch attempt for "
                "butler=%s catalog_entry_id=%s outcome=%s",
                butler,
                catalog_entry_id,
                outcome,
                exc_info=True,
            )
        return None

    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                breaker_was_open = False
                if outcome in _QUALIFYING_BREAKER_OUTCOMES:
                    await connection.execute(_BREAKER_LOCK_SQL, str(catalog_entry_id))
                    breaker_was_open = (await get_breaker_state(connection, catalog_entry_id)).open
                elif produce_fleet_halt:
                    await connection.execute(_FLEET_HALT_LOCK_SQL)

                attempt_id = await connection.fetchval(
                    _DISPATCH_ATTEMPTS_INSERT_RETURNING_ID,
                    *values,
                )
                if not isinstance(attempt_id, int):
                    raise RuntimeError("dispatch-attempt insert returned no stable bigint id")

                if outcome == "runtime_failure" and not breaker_was_open:
                    breaker_is_open = (await get_breaker_state(connection, catalog_entry_id)).open
                    if breaker_is_open:
                        await connection.fetchval(
                            "SELECT public.append_runtime_attention_model_breaker($1)",
                            attempt_id,
                        )
                elif produce_fleet_halt:
                    await connection.fetchval("SELECT public.append_runtime_attention_fleet_halt()")

        return attempt_id
    except Exception:
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
