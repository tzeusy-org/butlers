"""Consolidation executor for the Memory Butler.

Takes a parsed ``ConsolidationResult`` (from ``consolidation_parser.py``) and
applies each action to the database via ``storage.py``.  Each action is wrapped
in its own try/except so that one failure does not prevent the remaining actions
from executing.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from butlers.modules.memory.consolidation_parser import ConsolidationResult
from butlers.modules.memory.storage import confirm_memory, create_link, store_fact, store_rule

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
) -> dict[str, Any]:
    """Apply parsed consolidation results to the database.

    For each action in the ConsolidationResult:
    1. New facts: store via store_fact(), create derived_from links to source episodes
    2. Updated facts: store via store_fact() (auto-supersedes), create derived_from links
    3. New rules: store via store_rule(), create derived_from links to source episodes
    4. Confirmations: call confirm_memory() for each referenced fact UUID
    5. Mark all source episodes as consolidated=true

    Args:
        pool: asyncpg connection pool
        embedding_engine: EmbeddingEngine for storing new facts/rules
        parsed: Parsed ConsolidationResult from consolidation_parser
        source_episode_ids: UUIDs of episodes that were consolidated
        butler_name: Name of the butler that sourced these episodes
        scope: Scope for new facts/rules (defaults to butler_name)

    Returns:
        Dict with stats: facts_created, facts_updated, rules_created,
        confirmations_made, episodes_consolidated, errors
    """
    effective_scope = scope if scope is not None else butler_name
    errors: list[str] = []
    facts_created = 0
    facts_updated = 0
    rules_created = 0
    confirmations_made = 0

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

    # --- Mark source episodes as consolidated ---
    episodes_consolidated = 0
    if source_episode_ids:
        try:
            await pool.execute(
                "UPDATE episodes SET consolidated = true WHERE id = ANY($1)",
                source_episode_ids,
            )
            episodes_consolidated = len(source_episode_ids)
        except Exception as exc:
            # Log detailed error internally
            logger.error("Failed to mark episodes as consolidated: %s", exc, exc_info=True)
            # Sanitize error message in return value
            errors.append("Failed to mark episodes as consolidated")

    return {
        "facts_created": facts_created,
        "facts_updated": facts_updated,
        "rules_created": rules_created,
        "confirmations_made": confirmations_made,
        "episodes_consolidated": episodes_consolidated,
        "errors": errors,
    }
