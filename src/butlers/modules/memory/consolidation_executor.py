"""Consolidation executor for the Memory Butler.

Takes a parsed ``ConsolidationResult`` (from ``consolidation_parser.py``) and
applies each action to the database via ``storage.py``.  Each action is wrapped
in its own try/except so that one failure does not prevent the remaining actions
from executing.

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
from typing import TYPE_CHECKING, Any

from butlers.modules.memory.consolidation_parser import ConsolidationResult
from butlers.modules.memory.storage import (
    _lookup_episode_ttl_days,
    confirm_memory,
    create_link,
    store_fact,
    store_rule,
)

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)


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
    tenant_id: str = "owner",
    request_id: str | None = None,
) -> dict[str, Any]:
    """Apply parsed consolidation results to the database.

    For each action in the ConsolidationResult:
    1. New facts: store via store_fact(), create derived_from links to source episodes
    2. Updated facts: store via store_fact() (auto-supersedes), create derived_from links
    3. New rules: store via store_rule(), create derived_from links to source episodes
    4. Confirmations: call confirm_memory() for each referenced fact UUID
    5. Mark all source episodes as consolidated (terminal state), clearing leases

    Args:
        pool: asyncpg connection pool
        embedding_engine: EmbeddingEngine for storing new facts/rules
        parsed: Parsed ConsolidationResult from consolidation_parser
        source_episode_ids: UUIDs of episodes that were consolidated
        butler_name: Name of the butler that sourced these episodes
        scope: Scope for new facts/rules (defaults to butler_name)
        retention_class: Retention class to look up episode TTL from
            memory_policies (default 'transient').
        tenant_id: Tenant scope for all derived knowledge and audit events
            (default 'owner').  Must match the tenant of the source episodes.
        request_id: Optional request trace ID threaded through store calls
            for correlation.

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

    # Resolve episode TTL from memory_policies for the given retention_class.
    episode_ttl_days = await _lookup_episode_ttl_days(pool, retention_class)

    # --- New facts ---
    for fact in parsed.new_facts:
        try:
            new_fact_id = await store_fact(
                pool,
                fact.subject,
                fact.predicate,
                fact.content,
                embedding_engine,
                importance=fact.importance,
                permanence=fact.permanence,
                scope=effective_scope,
                tags=fact.tags,
                source_butler=butler_name,
                tenant_id=tenant_id,
                request_id=request_id,
            )
            for episode_id in source_episode_ids:
                await create_link(pool, "fact", new_fact_id, "episode", episode_id, "derived_from")
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
    for fact in parsed.updated_facts:
        try:
            new_fact_id = await store_fact(
                pool,
                fact.subject,
                fact.predicate,
                fact.content,
                embedding_engine,
                permanence=fact.permanence,
                scope=effective_scope,
                source_butler=butler_name,
                tenant_id=tenant_id,
                request_id=request_id,
            )
            for episode_id in source_episode_ids:
                await create_link(pool, "fact", new_fact_id, "episode", episode_id, "derived_from")
            facts_updated += 1
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to update fact (%s): %s", fact.target_id, exc, exc_info=True)
            # Sanitize error message in return value
            errors.append(f"Failed to update fact ({fact.target_id})")

    # --- New rules ---
    for rule in parsed.new_rules:
        try:
            new_rule_id = await store_rule(
                pool,
                rule.content,
                embedding_engine,
                scope=effective_scope,
                tags=rule.tags,
                source_butler=butler_name,
                tenant_id=tenant_id,
                request_id=request_id,
            )
            for episode_id in source_episode_ids:
                await create_link(pool, "rule", new_rule_id, "episode", episode_id, "derived_from")
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
