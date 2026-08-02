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

    Returns:
        Dict with stats: facts_created, facts_updated, rules_created,
        confirmations_made, episodes_consolidated, episode_ttl_days, errors
    """
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
    episode_ttl_days = await _lookup_episode_ttl_days(pool, retention_class)

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
    # Clear lease columns and set terminal consolidation_status.
    # Also set consolidated=true for backward compatibility with cleanup queries.
    episodes_consolidated = 0
    if source_episode_ids:
        try:
            await pool.execute(
                """
                UPDATE episodes
                SET consolidated         = true,
                    consolidation_status = 'consolidated',
                    leased_until         = NULL,
                    leased_by            = NULL
                WHERE id = ANY($1)
                """,
                source_episode_ids,
            )
            episodes_consolidated = len(source_episode_ids)
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to mark episodes as consolidated: %s", exc, exc_info=True)
            # Sanitize error message in return value
            errors.append("Failed to mark episodes as consolidated")

    # Emit memory_events for successful consolidation (best-effort)
    # Include tenant_id so the audit trail preserves tenant lineage.
    if episodes_consolidated > 0:
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
                    jsonb_build_object(
                        'episode_id', id::text,
                        'butler',     butler
                    )
                FROM episodes
                WHERE id = ANY($1)
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
