"""Spawner — system-prompt and context-fetch seam.

Responsible for assembling the final system prompt from its constituent sources:
 - on-disk CLAUDE.md (base prompt)
 - live DB override from public.system_prompt_history
 - owner routing instructions
 - memory context (when the memory module is enabled)
 - situational context preamble (from the context bus)
 - general timezone instruction

All async fetch helpers here are **fail-open**: any error is logged at WARNING
and ``None`` is returned so the spawn proceeds with whatever context was
successfully retrieved.

Extracted from butlers.core.spawner as part of bu-dl98i.7.1 (structural
decomposition into internal seams).  The Spawner continues to use these via
re-exports so existing import paths and test patches remain valid.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

from butlers.config import ButlerConfig
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-private constants
# ---------------------------------------------------------------------------

_MEMORY_TABLE_NAMES = ("episodes", "facts", "rules", "memory_links", "memory_events")
_missing_memory_table_warnings: set[tuple[str, str]] = set()
_missing_context_table_logged: set[str] = set()

_ROUTING_INSTRUCTIONS_TABLE = "routing_instructions"
_missing_routing_instructions_warnings: set[str] = set()


# ---------------------------------------------------------------------------
# Memory-table error helpers
# ---------------------------------------------------------------------------


def _is_missing_memory_table_error(exc: Exception) -> bool:
    """Return whether an exception indicates missing memory module tables."""
    if exc.__class__.__name__ == "UndefinedTableError":
        return True
    msg = str(exc).lower()
    if "relation" not in msg or "does not exist" not in msg:
        return False
    return any(table in msg for table in _MEMORY_TABLE_NAMES)


def _log_missing_memory_table_once(*, butler_name: str, operation: str) -> None:
    """Log missing-memory-schema warning once per butler+operation."""
    warning_key = (butler_name, operation)
    if warning_key in _missing_memory_table_warnings:
        logger.debug(
            "Skipping memory %s for butler %s; memory tables are still missing",
            operation,
            butler_name,
        )
        return

    _missing_memory_table_warnings.add(warning_key)
    logger.warning(
        "Skipping memory %s for butler %s because memory tables are missing. "
        "Run migrations or disable [modules.memory].",
        operation,
        butler_name,
    )


# ---------------------------------------------------------------------------
# Memory module config helpers
# ---------------------------------------------------------------------------


def _memory_module_enabled(config: ButlerConfig) -> bool:
    raw = config.modules.get("memory")
    return isinstance(raw, dict)


def _memory_context_token_budget(config: ButlerConfig) -> int:
    raw = config.modules.get("memory", {})
    if not isinstance(raw, dict):
        return 3000
    retrieval = raw.get("retrieval", {})
    if not isinstance(retrieval, dict):
        return 3000
    budget = retrieval.get("context_token_budget", 3000)
    try:
        parsed = int(budget)
    except (TypeError, ValueError):
        return 3000
    return parsed if parsed > 0 else 3000


# ---------------------------------------------------------------------------
# System prompt composition
# ---------------------------------------------------------------------------


def _compose_system_prompt(
    base_system_prompt: str,
    memory_context: str | None,
    general_timezone_instruction: str | None = None,
    routing_instructions: str | None = None,
    context_preamble: str | None = None,
) -> str:
    """Compose the runtime system prompt from base instructions, routing instructions, and memory.

    Layering order (stable for token-cache efficiency):
    1. Base system prompt (CLAUDE.md — static)
    2. Situational context preamble (dynamic, from context bus)
    3. Owner routing instructions (semi-static, sorted by priority)
    4. Memory context (dynamic per-request)

    Contract:
    - Runtime always receives the raw CLAUDE.md-derived system prompt when no
      additional context is available.
    - Each layer is appended as a suffix separated from the previous by exactly
      one blank line.
    """
    prompt = base_system_prompt
    if general_timezone_instruction:
        prompt = f"{prompt}\n\n{general_timezone_instruction}"
    if context_preamble:
        prompt = f"{prompt}\n\n{context_preamble}"
    if routing_instructions:
        prompt = f"{prompt}\n\n{routing_instructions}"
    if memory_context:
        prompt = f"{prompt}\n\n{memory_context}"
    return prompt


# ---------------------------------------------------------------------------
# Context fetchers (all fail-open)
# ---------------------------------------------------------------------------


async def fetch_general_timezone_instruction(
    pool: asyncpg.Pool | None,
    butler_name: str,
    credential_store: CredentialStore | None = None,
) -> str | None:
    """Fetch the shared general timezone instruction, failing open."""
    from butlers.core.general_settings import (
        build_general_timezone_instruction,
        load_general_settings,
    )

    shared_pool = credential_store.shared_pool if credential_store is not None else None
    settings_pool = shared_pool or pool
    if settings_pool is None:
        return None

    try:
        settings = await load_general_settings(settings_pool)
        return build_general_timezone_instruction(settings)
    except Exception:
        logger.warning(
            "Failed to fetch general timezone instruction for butler %s",
            butler_name,
            exc_info=True,
        )
        return None


async def fetch_memory_context(
    pool: asyncpg.Pool | None,
    butler_name: str,
    prompt: str,
    *,
    token_budget: int = 3000,
) -> str | None:
    """Fetch memory context via local memory tools when the module is enabled."""
    if pool is None:
        return None

    try:
        from butlers.core.memory_hooks import fetch_memory_context as _hook

        return await _hook(pool, butler_name, prompt, token_budget=token_budget)
    except Exception as exc:
        if _is_missing_memory_table_error(exc):
            _log_missing_memory_table_once(butler_name=butler_name, operation="context fetch")
            return None
        logger.warning(
            "Failed to fetch memory context for butler %s",
            butler_name,
            exc_info=True,
        )
        return None


async def fetch_system_prompt_override(
    pool: asyncpg.Pool | None,
    butler_name: str,
) -> str | None:
    """Fetch the live system-prompt override from ``public.system_prompt_history``.

    Returns the HEAD (highest-version) prompt body for *butler_name*, or
    ``None`` when no prompt has been recorded, the pool is absent, or the table
    does not yet exist. This is the live override set by the dashboard prompt
    editor; the on-disk ``CLAUDE.md`` remains the seed/default fallback.

    This function is **fail-open**: any unexpected error is logged at WARNING
    level and ``None`` is returned so spawning proceeds with the on-disk prompt.
    """
    if pool is None:
        return None

    try:
        prompt = await pool.fetchval(
            "SELECT prompt FROM public.system_prompt_history"
            " WHERE butler_name = $1"
            " ORDER BY version DESC"
            " LIMIT 1",
            butler_name,
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg and "system_prompt_history" in msg:
            logger.debug(
                "system_prompt_history table not yet created for %s; using on-disk prompt",
                butler_name,
            )
            return None
        logger.warning(
            "Failed to fetch system prompt override for %s: %s",
            butler_name,
            exc,
        )
        return None

    if not isinstance(prompt, str) or not prompt.strip():
        return None
    return prompt


async def fetch_routing_instructions(
    pool: asyncpg.Pool | None,
    butler_name: str,
) -> str | None:
    """Fetch enabled routing instructions and format as a system prompt section.

    Returns a markdown section string ready for injection, or ``None`` when
    there are no instructions or the table doesn't exist.

    Instructions are sorted by ``(priority ASC, created_at ASC)`` for
    deterministic ordering that maximises token-cache hit rates.
    """
    if pool is None:
        return None

    try:
        rows = await pool.fetch(
            "SELECT instruction FROM routing_instructions"
            " WHERE enabled = TRUE AND deleted_at IS NULL"
            " ORDER BY priority ASC, created_at ASC, id ASC"
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg and "routing_instructions" in msg:
            if butler_name not in _missing_routing_instructions_warnings:
                _missing_routing_instructions_warnings.add(butler_name)
                logger.debug(
                    "routing_instructions table not yet created for %s; skipping",
                    butler_name,
                )
            return None
        logger.warning(
            "Failed to fetch routing instructions for %s: %s",
            butler_name,
            exc,
        )
        return None

    if not rows:
        return None

    lines = [f"- {row['instruction']}" for row in rows]
    return (
        "## Owner Routing Instructions\n\n"
        "The following routing directives have been set by the owner."
        " Follow these exactly when classifying and routing messages:\n\n" + "\n".join(lines)
    )


async def fetch_situational_context_preamble(
    pool: asyncpg.Pool | None,
    butler_name: str,
) -> str | None:
    """Fetch the situational context preamble from the context bus.

    Calls ``get_active_context()`` and formats the result via
    ``format_context_preamble()``.  Returns ``None`` when there are no active
    signals, the pool is absent, or the query fails.

    This function is **fail-open**: any exception is logged at WARNING level
    and the caller proceeds without a context preamble.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the shared database.
    butler_name:
        Name of the calling butler (used for log messages).

    Returns
    -------
    str | None
        Formatted preamble string, or ``None`` when no active signals exist
        or the query fails.
    """
    if pool is None:
        return None

    try:
        from butlers.context_bus import format_context_preamble, get_active_context

        signals = await get_active_context(pool)
        preamble = format_context_preamble(signals)
        return preamble if preamble else None
    except Exception as exc:
        msg = str(exc).lower()
        is_missing_table = exc.__class__.__name__ == "UndefinedTableError" or (
            "relation" in msg and "does not exist" in msg and "user_context" in msg
        )
        if is_missing_table:
            if butler_name not in _missing_context_table_logged:
                _missing_context_table_logged.add(butler_name)
                logger.warning(
                    "Skipping situational context preamble for butler %s: "
                    "public.user_context table is missing. Run migrations (bu-1e2p).",
                    butler_name,
                )
            else:
                logger.debug(
                    "Skipping situational context preamble for butler %s; "
                    "public.user_context table is still missing",
                    butler_name,
                )
        else:
            logger.warning(
                "Failed to fetch situational context preamble for butler %s",
                butler_name,
                exc_info=True,
            )
        return None


async def store_session_episode(
    pool: asyncpg.Pool | None,
    butler_name: str,
    session_output: str,
    session_id: uuid.UUID | None = None,
) -> bool:
    """Store a session episode through local memory module tools."""
    if pool is None:
        return False

    try:
        from butlers.core.memory_hooks import store_session_episode as _hook

        return await _hook(pool, butler_name, session_output, session_id)
    except Exception as exc:
        if _is_missing_memory_table_error(exc):
            _log_missing_memory_table_once(butler_name=butler_name, operation="episode storage")
            return False
        logger.warning(
            "Failed to store session episode for butler %s",
            butler_name,
            exc_info=True,
        )
        return False
