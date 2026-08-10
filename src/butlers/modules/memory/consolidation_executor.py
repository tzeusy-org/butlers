"""Consolidation executor for the Memory Butler.

Takes a parsed ``ConsolidationResult`` (from ``consolidation_parser.py``),
validates every artifact's claimed-episode evidence, and applies each action to
the database via ``storage.py``.  Each valid action is wrapped in its own
try/except so that one failure does not prevent the remaining actions from
executing.

Terminal state handling
-----------------------
When consolidation actions succeed, all source episodes are moved to the
``'consolidated'`` terminal state and their leases are cleared.

When consolidation actions partially fail (some facts/rules could not be
stored), episodes are still marked ``'consolidated'`` because the LLM
produced output and partial results were saved — the episode itself was
processed.  Errors are surfaced in the returned ``errors`` list.

Full group-level failures (spawner down, parse failure, etc.) are handled by
the caller (``consolidation.py``) which calls ``_mark_group_failed`` to
transition episodes to ``'failed'`` or ``'dead_letter'`` with exponential
backoff.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from butlers.modules.memory.consolidation_parser import ConsolidationResult
from butlers.modules.memory.storage import (
    StaleSupersessionTargetError,
    _lookup_episode_ttl_days,
    confirm_memory,
    create_link,
    store_fact,
    store_rule,
)
from butlers.modules.memory.tools.writing import (
    is_relational_registry_predicate,
    normalize_predicate,
    refresh_relational_registry_predicates,
)

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)


class ConsolidationEvidenceValidationError(ValueError):
    """Raised when an output artifact lacks valid claimed-episode evidence."""


class _ConnectionAcquire:
    """Make one acquired connection look like a pool to storage helpers."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def __aenter__(self) -> Any:
        return self._connection

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _ConnectionBackedPool:
    """Adapter that keeps nested storage writes on an outer transaction's connection."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def acquire(self) -> _ConnectionAcquire:
        return _ConnectionAcquire(self._connection)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _lost_claim_result() -> dict[str, Any]:
    """Return the no-side-effect result used when a claimant lost its fence."""
    return {
        "facts_created": 0,
        "facts_updated": 0,
        "rules_created": 0,
        "confirmations_made": 0,
        "episodes_consolidated": 0,
        "episode_ttl_days": 0,
        "errors": ["Consolidation lease was lost before episodes could be finalized"],
    }


async def _lock_and_renew_claim_for_persistence(
    connection: Any,
    *,
    source_episode_ids: list[uuid.UUID],
    claim_token: str,
    lease_duration_seconds: int,
) -> bool:
    """Fence every derived write behind the current claimant's locked lease.

    The row locks are intentionally held by the caller's outer transaction for
    the full artifact, confirmation, and terminal-event boundary.  A scheduler
    whose lease timestamp expires while that transaction is open sees those rows
    through ``FOR UPDATE SKIP LOCKED`` and cannot replace the claimant before the
    terminal transition commits.
    """
    claimed = await connection.fetch(
        """
        SELECT id
        FROM episodes
        WHERE id = ANY($1::uuid[])
          AND leased_by = $2
          AND leased_until > now()
          AND consolidation_status IN ('pending', 'failed')
        FOR UPDATE
        """,
        source_episode_ids,
        claim_token,
    )
    if len(claimed) != len(source_episode_ids):
        return False

    await connection.execute(
        """
        UPDATE episodes
        SET leased_until = now() + ($3 * interval '1 second')
        WHERE id = ANY($1::uuid[])
          AND leased_by = $2
        """,
        source_episode_ids,
        claim_token,
        lease_duration_seconds,
    )
    return True


def _validate_artifact_evidence(
    *,
    artifact_kind: str,
    artifacts: list[Any],
    source_episode_ids: list[uuid.UUID],
) -> list[list[uuid.UUID]]:
    """Validate evidence before any output artifact is persisted.

    Parser-level validation cannot establish group membership.  This function
    deliberately validates every artifact first so one malformed or foreign
    evidence reference fails the whole claimed group through the caller's
    existing retry/dead-letter path rather than allowing partial persistence.
    """
    source_episode_id_set = set(source_episode_ids)
    evidence_by_artifact: list[list[uuid.UUID]] = []

    for index, artifact in enumerate(artifacts):
        evidence = artifact.evidence_episode_ids
        prefix = f"invalid consolidation episode evidence for {artifact_kind}[{index}]"
        if not isinstance(evidence, list) or not evidence:
            raise ConsolidationEvidenceValidationError(f"{prefix}: expected a non-empty list")

        normalized_ids: list[uuid.UUID] = []
        seen_ids: set[uuid.UUID] = set()
        for item_index, episode_id_raw in enumerate(evidence):
            if not isinstance(episode_id_raw, str):
                raise ConsolidationEvidenceValidationError(
                    f"{prefix}: item {item_index} must be a UUID string"
                )
            try:
                episode_id = uuid.UUID(episode_id_raw)
            except ValueError:
                raise ConsolidationEvidenceValidationError(
                    f"{prefix}: item {item_index} is not a valid UUID"
                ) from None
            if episode_id in seen_ids:
                raise ConsolidationEvidenceValidationError(f"{prefix}: duplicate episode ID")
            if episode_id not in source_episode_id_set:
                raise ConsolidationEvidenceValidationError(
                    f"{prefix}: episode is outside the claimed group"
                )
            seen_ids.add(episode_id)
            normalized_ids.append(episode_id)
        evidence_by_artifact.append(normalized_ids)

    return evidence_by_artifact


def _validate_all_artifact_evidence(
    parsed: ConsolidationResult,
    source_episode_ids: list[uuid.UUID],
) -> tuple[list[list[uuid.UUID]], list[list[uuid.UUID]], list[list[uuid.UUID]]]:
    """Return validated evidence for every output artifact in a non-empty group."""
    if not source_episode_ids:
        # Direct executor callers without a claimed group retain the historical
        # no-link behavior. Production consolidation always passes a claimed
        # non-empty group, where evidence is mandatory.
        return [], [], []

    return (
        _validate_artifact_evidence(
            artifact_kind="new_facts",
            artifacts=parsed.new_facts,
            source_episode_ids=source_episode_ids,
        ),
        _validate_artifact_evidence(
            artifact_kind="updated_facts",
            artifacts=parsed.updated_facts,
            source_episode_ids=source_episode_ids,
        ),
        _validate_artifact_evidence(
            artifact_kind="new_rules",
            artifacts=parsed.new_rules,
            source_episode_ids=source_episode_ids,
        ),
    )


async def _persist_artifact_with_evidence(
    pool: Pool,
    *,
    artifact_type: str,
    evidence_episode_ids: list[uuid.UUID],
    persist: Callable[[Any], Awaitable[uuid.UUID]],
) -> uuid.UUID:
    """Persist one artifact and all of its evidence links atomically."""
    if not evidence_episode_ids:
        return await persist(pool)

    async with pool.acquire() as connection:
        async with connection.transaction():
            connection_pool = _ConnectionBackedPool(connection)
            artifact_id = await persist(connection_pool)
            for episode_id in evidence_episode_ids:
                await create_link(
                    connection_pool,
                    artifact_type,
                    artifact_id,
                    "episode",
                    episode_id,
                    "derived_from",
                )
    return artifact_id


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


async def execute_consolidation(
    pool: Pool,
    embedding_engine: Any,
    parsed: ConsolidationResult,
    source_episode_ids: list[uuid.UUID],
    butler_name: str,
    *,
    scope: str | None = None,
    retention_class: str = "transient",
    tenant_id: str = "shared",
    request_id: str | None = None,
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
    claim_token: str | None = None,
    lease_duration_seconds: int = 300,
    _claim_persistence_fenced: bool = False,
    _episode_ttl_days: int | None = None,
) -> dict[str, Any]:
    """Apply parsed consolidation results to the database.

    For each action in the ConsolidationResult:
    1. New facts: store via store_fact(), create derived_from links to that
       artifact's validated evidence episodes
    2. Updated facts: reload the target's identity key, store via store_fact()
       (auto-supersedes), create derived_from links to validated evidence episodes
    3. New rules: store via store_rule(), create derived_from links to validated
       evidence episodes
    4. Confirmations: call confirm_memory() for each referenced fact UUID
    5. Mark all source episodes as consolidated (terminal state), clearing leases

    Args:
        pool: asyncpg connection pool
        embedding_engine: EmbeddingEngine for storing new facts/rules
        parsed: Parsed ConsolidationResult from consolidation_parser
        source_episode_ids: UUIDs of the claimed group used to validate exact
            artifact evidence and later mark episodes consolidated
        butler_name: Name of the butler that sourced these episodes
        scope: Scope for new facts/rules (defaults to butler_name)
        retention_class: Retention class to look up episode TTL from
            memory_policies (default 'transient').
        tenant_id: Tenant scope for all derived knowledge and audit events
            (default 'shared').  Must match the tenant of the source episodes.
        request_id: Optional request trace ID threaded through store calls
            for correlation.
        enable_shared_catalog: When True, forwarded to every ``store_fact``/
            ``store_rule`` call so consolidation-derived facts/rules get a
            ``public.memory_catalog`` row exactly like directly-stored ones
            (previously dropped here — consolidation output was silently
            invisible to cross-butler catalog search even when the module's
            ``enable_shared_catalog`` config was on). Defaults to False,
            matching ``store_fact``/``store_rule``'s own conservative default.
        source_schema: The butler schema name written to catalog rows.
            Required (by ``store_fact``/``store_rule``'s own gate) when
            ``enable_shared_catalog=True``; ignored otherwise.
        claim_token: Opaque lease owner from ``run_consolidation``. When set,
            every derived artifact, confirmation, terminal state change, and
            lifecycle event is fenced to that still-active claim in one outer
            transaction.
        lease_duration_seconds: Lease duration used to renew a valid claim
            immediately before protected persistence begins.

    Returns:
        Dict with stats: facts_created, facts_updated, rules_created,
        confirmations_made, episodes_consolidated, episode_ttl_days, errors
    """
    # Runtime execution happens outside a transaction, but persistence cannot:
    # a replacement claimant must win before any derived write becomes visible.
    # Lock and renew every source row first, then recurse through the existing
    # executor using a connection-backed pool so all nested storage transactions
    # remain under this outer row-lock fence through the terminal event.
    if claim_token is not None and source_episode_ids and not _claim_persistence_fenced:
        # The TTL helper deliberately tolerates a pre-migration absent policy
        # table. Resolve it before opening the protected transaction so that
        # fallback cannot leave PostgreSQL's outer transaction aborted.
        episode_ttl_days = await _lookup_episode_ttl_days(pool, retention_class)
        async with pool.acquire() as connection:
            async with connection.transaction():
                if not await _lock_and_renew_claim_for_persistence(
                    connection,
                    source_episode_ids=source_episode_ids,
                    claim_token=claim_token,
                    lease_duration_seconds=lease_duration_seconds,
                ):
                    return _lost_claim_result()
                return await execute_consolidation(
                    pool=_ConnectionBackedPool(connection),
                    embedding_engine=embedding_engine,
                    parsed=parsed,
                    source_episode_ids=source_episode_ids,
                    butler_name=butler_name,
                    scope=scope,
                    retention_class=retention_class,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    enable_shared_catalog=enable_shared_catalog,
                    source_schema=source_schema,
                    claim_token=claim_token,
                    lease_duration_seconds=lease_duration_seconds,
                    _claim_persistence_fenced=True,
                    _episode_ttl_days=episode_ttl_days,
                )

    effective_scope = scope if scope is not None else butler_name
    errors: list[str] = []
    facts_created = 0
    facts_updated = 0
    rules_created = 0
    confirmations_made = 0

    (
        new_fact_evidence,
        updated_fact_evidence,
        new_rule_evidence,
    ) = _validate_all_artifact_evidence(parsed, source_episode_ids)

    # When the caller wants catalog write-behind but didn't resolve a schema
    # itself (e.g. the deterministic scheduled-job path, which has no access
    # to the module's toml config), fall back to the pool's own
    # current_schema() — the same idiom used by
    # scheduled_jobs.py::_infer_current_schema and core/scheduler.py — rather
    # than silently dropping catalog writes for the whole run.
    if enable_shared_catalog and not source_schema:
        source_schema = await pool.fetchval("SELECT current_schema()")

    # Resolve episode TTL from memory_policies for the given retention_class.
    episode_ttl_days = (
        _episode_ttl_days
        if _episode_ttl_days is not None
        else await _lookup_episode_ttl_days(pool, retention_class)
    )

    # --- New facts ---
    for fact_index, fact in enumerate(parsed.new_facts):
        try:
            fact_entity_id = uuid.UUID(fact.entity_id) if fact.entity_id else None
            fact_object_entity_id = (
                uuid.UUID(fact.object_entity_id) if fact.object_entity_id else None
            )
            if fact_object_entity_id is not None:
                normalized_predicate = normalize_predicate(fact.predicate)
                relational_predicates = await refresh_relational_registry_predicates(pool)
                if is_relational_registry_predicate(
                    normalized_predicate,
                    relational_predicates,
                ):
                    raise ValueError(
                        f"Registry-relational predicate {fact.predicate!r} is out of scope "
                        "for memory consolidation edge-facts; use "
                        "relationship_assert_fact(object_kind='entity')"
                    )
            if fact_entity_id is None:
                logger.warning(
                    "Consolidation: new fact %s/%s has no entity_id — "
                    "facts should always be anchored to an entity",
                    fact.subject,
                    fact.predicate,
                )

            async def persist_new_fact(connection_pool: Any) -> uuid.UUID:
                store_result = await store_fact(
                    connection_pool,
                    fact.subject,
                    fact.predicate,
                    fact.content,
                    embedding_engine,
                    importance=fact.importance,
                    permanence=fact.permanence,
                    scope=effective_scope,
                    tags=fact.tags,
                    source_butler=butler_name,
                    entity_id=fact_entity_id,
                    object_entity_id=fact_object_entity_id,
                    valid_at=fact.valid_at,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    enable_shared_catalog=enable_shared_catalog,
                    source_schema=source_schema,
                )
                return store_result["id"]

            await _persist_artifact_with_evidence(
                pool,
                artifact_type="fact",
                evidence_episode_ids=(new_fact_evidence[fact_index] if source_episode_ids else []),
                persist=persist_new_fact,
            )
            facts_created += 1
        except Exception as exc:
            # Log detailed error internally
            logger.error(
                "Failed to store new fact (%s/%s): %s",
                fact.subject,
                fact.predicate,
                exc,
                exc_info=True,
            )
            # Sanitize error message in return value
            errors.append(f"Failed to store new fact ({fact.subject}/{fact.predicate})")

    # --- Updated facts ---
    for fact_index, fact in enumerate(parsed.updated_facts):
        try:
            target_id = uuid.UUID(fact.target_id)
            target = await pool.fetchrow(
                """
                SELECT subject, predicate, entity_id, scope
                FROM facts
                WHERE id = $1
                  AND tenant_id = $2
                  AND source_butler = $3
                  AND validity IN ('active', 'fading')
                  AND valid_at IS NULL
                  AND object_entity_id IS NULL
                """,
                target_id,
                tenant_id,
                butler_name,
            )
            if target is None:
                # This is an expected optimistic-concurrency outcome: the
                # model may reference a fact that was superseded after prompt
                # construction, or one outside this executor's update
                # boundary. Preserve the rejected action in the result without
                # reporting a runtime exception to operational error scanners.
                logger.warning(
                    "Skipping non-live property fact update %s for tenant %s and butler %s",
                    target_id,
                    tenant_id,
                    butler_name,
                )
                errors.append(f"Failed to update fact ({fact.target_id})")
                continue

            predicate_is_temporal = await pool.fetchval(
                "SELECT is_temporal FROM predicate_registry "
                "WHERE name = $1 OR $1 = ANY(aliases) "
                "ORDER BY ($1 = ANY(aliases)) DESC LIMIT 1",
                target["predicate"],
            )
            if predicate_is_temporal:
                logger.warning(
                    "Consolidation: skipping updated fact %s because predicate %s "
                    "is registered as temporal; temporal observations must use new_facts",
                    fact.target_id,
                    target["predicate"],
                )
                errors.append(f"Skipped temporal updated fact ({fact.target_id})")
                continue

            # ``target_id`` is the authority for an update's identity key. The
            # model repeats subject/predicate/entity_id in its output, but those
            # values can be stale or malformed by the time execution begins.
            # Reloading the persisted row prevents an update from being
            # accidentally retargeted and guarantees store_fact supersedes the
            # selected fact's current identity key.
            fact_entity_id = target["entity_id"]
            if fact_entity_id is None:
                logger.warning(
                    "Consolidation: updated fact %s has no entity_id — "
                    "facts should always be anchored to an entity",
                    fact.target_id,
                )
            try:

                async def persist_updated_fact(connection_pool: Any) -> uuid.UUID:
                    store_result = await store_fact(
                        connection_pool,
                        target["subject"],
                        target["predicate"],
                        fact.content,
                        embedding_engine,
                        permanence=fact.permanence,
                        scope=target["scope"],
                        source_butler=butler_name,
                        entity_id=fact_entity_id,
                        tenant_id=tenant_id,
                        request_id=request_id,
                        expected_supersedes_id=target_id,
                        enable_shared_catalog=enable_shared_catalog,
                        source_schema=source_schema,
                    )
                    return store_result["id"]

                await _persist_artifact_with_evidence(
                    pool,
                    artifact_type="fact",
                    evidence_episode_ids=(
                        updated_fact_evidence[fact_index] if source_episode_ids else []
                    ),
                    persist=persist_updated_fact,
                )
            except StaleSupersessionTargetError:
                logger.warning(
                    "Skipping stale property fact update %s for tenant %s and butler %s",
                    target_id,
                    tenant_id,
                    butler_name,
                )
                errors.append(f"Failed to update fact ({fact.target_id})")
                continue
            facts_updated += 1
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to update fact (%s): %s", fact.target_id, exc, exc_info=True)
            # Sanitize error message in return value
            errors.append(f"Failed to update fact ({fact.target_id})")

    # --- New rules ---
    for rule_index, rule in enumerate(parsed.new_rules):
        try:

            async def persist_new_rule(connection_pool: Any) -> uuid.UUID:
                return await store_rule(
                    connection_pool,
                    rule.content,
                    embedding_engine,
                    scope=effective_scope,
                    tags=rule.tags,
                    source_butler=butler_name,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    enable_shared_catalog=enable_shared_catalog,
                    source_schema=source_schema,
                )

            await _persist_artifact_with_evidence(
                pool,
                artifact_type="rule",
                evidence_episode_ids=(new_rule_evidence[rule_index] if source_episode_ids else []),
                persist=persist_new_rule,
            )
            rules_created += 1
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to store new rule: %s", exc, exc_info=True)
            # Sanitize error message in return value
            errors.append("Failed to store new rule")

    # --- Confirmations ---
    for confirmation_id in parsed.confirmations:
        try:
            await confirm_memory(pool, "fact", uuid.UUID(confirmation_id))
            confirmations_made += 1
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to confirm fact %s: %s", confirmation_id, exc, exc_info=True)
            # Sanitize error message in return value
            errors.append(f"Failed to confirm fact {confirmation_id}")

    # --- Mark source episodes as consolidated (terminal state) ---
    # Clear the retry/failure lifecycle fields as well as the lease. Production
    # callers provide the opaque claim token, which turns this into an all-or-
    # nothing, owner-fenced terminal transition. The legacy no-token path is
    # retained for direct executor callers that predate leasing (not the
    # scheduler path).
    episodes_consolidated = 0
    if source_episode_ids:
        try:
            if claim_token is None:
                await pool.execute(
                    """
                    UPDATE episodes
                    SET consolidated                 = true,
                        consolidation_status         = 'consolidated',
                        leased_until                 = NULL,
                        leased_by                    = NULL,
                        last_consolidation_error     = NULL,
                        dead_letter_reason           = NULL,
                        next_consolidation_retry_at  = NULL
                    WHERE id = ANY($1::uuid[])
                    """,
                    source_episode_ids,
                )
                episodes_consolidated = len(source_episode_ids)
            else:
                async with pool.acquire() as connection:
                    async with connection.transaction():
                        transitioned = await connection.fetch(
                            """
                            WITH claimed AS (
                                SELECT id
                                FROM episodes
                                WHERE id = ANY($1::uuid[])
                                  AND leased_by = $2
                                  AND ($4::boolean OR leased_until > now())
                                  AND consolidation_status IN ('pending', 'failed')
                                FOR UPDATE
                            ), transitioned AS (
                                UPDATE episodes
                                SET consolidated                 = true,
                                    consolidation_status         = 'consolidated',
                                    leased_until                 = NULL,
                                    leased_by                    = NULL,
                                    last_consolidation_error     = NULL,
                                    dead_letter_reason           = NULL,
                                    next_consolidation_retry_at  = NULL
                                WHERE id IN (SELECT id FROM claimed)
                                  AND (SELECT count(*) FROM claimed) = cardinality($1::uuid[])
                                RETURNING id, butler, tenant_id
                            ), recorded AS (
                                INSERT INTO memory_events (
                                    event_type, actor, tenant_id, actor_butler,
                                    memory_type, memory_id, payload
                                )
                                SELECT
                                    'episode_consolidated',
                                    'consolidation_worker',
                                    COALESCE($3, tenant_id),
                                    butler,
                                    'episode',
                                    id,
                                    jsonb_build_object('outcome', 'consolidated')
                                FROM transitioned
                            )
                            SELECT id FROM transitioned
                            """,
                            source_episode_ids,
                            claim_token,
                            tenant_id,
                            _claim_persistence_fenced,
                        )
                if len(transitioned) == len(source_episode_ids):
                    episodes_consolidated = len(transitioned)
                else:
                    errors.append("Consolidation lease was lost before episodes could be finalized")
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to mark episodes as consolidated: %s", exc, exc_info=True)
            # Sanitize error message in return value
            errors.append("Failed to mark episodes as consolidated")

    # Legacy direct callers have no claim token, so retain their best-effort
    # event emission. Leased production callers write this event atomically in
    # the fenced CTE above.
    if episodes_consolidated > 0 and claim_token is None:
        try:
            await pool.execute(
                """
                INSERT INTO memory_events (event_type, actor, tenant_id, actor_butler,
                                           memory_type, memory_id, payload)
                SELECT
                    'episode_consolidated',
                    'consolidation_worker',
                    $2,
                    butler,
                    'episode',
                    id,
                    jsonb_build_object('outcome', 'consolidated')
                FROM episodes
                WHERE id = ANY($1::uuid[])
                """,
                source_episode_ids,
                tenant_id,
            )
        except Exception as exc:
            logger.warning("Failed to emit episode_consolidated events: %s", exc)

    return {
        "facts_created": facts_created,
        "facts_updated": facts_updated,
        "rules_created": rules_created,
        "confirmations_made": confirmations_made,
        "episodes_consolidated": episodes_consolidated,
        "episode_ttl_days": episode_ttl_days,
        "errors": errors,
    }
