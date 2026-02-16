"""Consolidation executor for the Memory Butler.

Takes a parsed ``ConsolidationResult`` (from ``consolidation_parser.py``) and
applies each action to the database via ``storage.py``.  Each action is wrapped
in its own try/except so that one failure does not prevent the remaining actions
from executing.
"""

from __future__ import annotations

import importlib.util
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

# ---------------------------------------------------------------------------
# Load sibling modules from disk (roster/ is not a Python package).
# ---------------------------------------------------------------------------

_MODULE_DIR = Path(__file__).resolve().parent


def _load_module(name: str):
    path = _MODULE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_storage = _load_module("storage")
_parser = _load_module("consolidation_parser")

store_fact = _storage.store_fact
store_rule = _storage.store_rule
create_link = _storage.create_link
confirm_memory = _storage.confirm_memory

ConsolidationResult = _parser.ConsolidationResult

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
    max_retries: int = 3,
) -> dict[str, Any]:
    """Apply parsed consolidation results to the database.

    For each action in the ConsolidationResult:
    1. New facts: store via store_fact(), create derived_from links to source episodes
    2. Updated facts: store via store_fact() (auto-supersedes), create derived_from links
    3. New rules: store via store_rule(), create derived_from links to source episodes
    4. Confirmations: call confirm_memory() for each referenced fact UUID
    5. Mark all source episodes as consolidated or failed based on execution outcome

    Args:
        pool: asyncpg connection pool
        embedding_engine: EmbeddingEngine for storing new facts/rules
        parsed: Parsed ConsolidationResult from consolidation_parser
        source_episode_ids: UUIDs of episodes that were consolidated
        butler_name: Name of the butler that sourced these episodes
        scope: Scope for new facts/rules (defaults to butler_name)
        max_retries: Maximum retry count before marking as dead_letter (default 3)

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
            msg = f"Failed to store new fact ({fact.subject}/{fact.predicate}): {exc}"
            logger.error(msg)
            errors.append(msg)

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
            msg = f"Failed to update fact ({fact.target_id}): {exc}"
            logger.error(msg)
            errors.append(msg)

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
            msg = f"Failed to store new rule: {exc}"
            logger.error(msg)
            errors.append(msg)

    # --- Confirmations ---
    for confirmation_id in parsed.confirmations:
        try:
            await confirm_memory(pool, "fact", uuid.UUID(confirmation_id))
            confirmations_made += 1
        except Exception as exc:
            msg = f"Failed to confirm fact {confirmation_id}: {exc}"
            logger.error(msg)
            errors.append(msg)

    # --- Mark source episodes based on outcome ---
    episodes_consolidated = 0
    if source_episode_ids:
        try:
            if errors:
                # If there were any errors, mark as failed and increment retry count
                await pool.execute(
                    """
                    UPDATE episodes
                    SET consolidation_status = CASE
                            WHEN retry_count + 1 >= $2 THEN 'dead_letter'
                            ELSE 'failed'
                        END,
                        retry_count = retry_count + 1,
                        last_error = $3
                    WHERE id = ANY($1)
                    """,
                    source_episode_ids,
                    max_retries,
                    "; ".join(errors[:5]),  # Store up to 5 error messages
                )
            else:
                # Success: mark as consolidated
                await pool.execute(
                    """
                    UPDATE episodes
                    SET consolidation_status = 'consolidated',
                        consolidated = true
                    WHERE id = ANY($1)
                    """,
                    source_episode_ids,
                )
                episodes_consolidated = len(source_episode_ids)
        except Exception as exc:
            msg = f"Failed to update episode consolidation status: {exc}"
            logger.error(msg)
            errors.append(msg)

    return {
        "facts_created": facts_created,
        "facts_updated": facts_updated,
        "rules_created": rules_created,
        "confirmations_made": confirmations_made,
        "episodes_consolidated": episodes_consolidated,
        "errors": errors,
    }
