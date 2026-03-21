"""Memory context building — deterministic section compiler for CC system prompts.

Builds a four-section context block with strict quota allocation:
  1. Profile Facts     (30% of budget) — owner entity facts by importance
  2. Task-Relevant Facts (35% of budget) — recall matches excluding profile facts
  3. Active Rules      (20% of budget) — sorted by maturity rank then effectiveness
  4. Recent Episodes   (15% of budget) — opt-in via include_recent_episodes=True

All sections use deterministic tie-breaking: score/importance DESC, created_at DESC, id ASC.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _search

logger = logging.getLogger(__name__)

# Maturity rank for Active Rules sorting: higher = more mature
_MATURITY_RANK: dict[str, int] = {
    "proven": 3,
    "established": 2,
    "candidate": 1,
    "anti_pattern": 0,
}

# Section budget fractions
_PROFILE_FACTS_FRAC = 0.30
_TASK_FACTS_FRAC = 0.35
_RULES_FRAC = 0.20
_EPISODES_FRAC = 0.15


async def _fetch_profile_facts(
    pool: Pool,
    tenant_id: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch facts anchored to the owner entity.

    Sorted by importance DESC, created_at DESC, id ASC.
    """
    sql = """
        SELECT f.*
        FROM facts f
        JOIN shared.entities e ON f.entity_id = e.id
        WHERE f.tenant_id = $1
          AND f.validity = 'active'
          AND 'owner' = ANY(e.roles)
        ORDER BY f.importance DESC, f.created_at DESC, f.id ASC
        LIMIT $2
    """
    try:
        rows = await pool.fetch(sql, tenant_id, limit)
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("Profile facts query failed (likely missing shared.entities)", exc_info=True)
        return []


async def _fetch_recent_episodes(
    pool: Pool,
    butler: str,
    tenant_id: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch most recent episodes for the butler, ordered by created_at DESC."""
    sql = """
        SELECT *
        FROM episodes
        WHERE butler = $1
          AND tenant_id = $2
        ORDER BY created_at DESC
        LIMIT $3
    """
    try:
        rows = await pool.fetch(sql, butler, tenant_id, limit)
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("Recent episodes query failed", exc_info=True)
        return []


def _effective_confidence(row: dict[str, Any]) -> float:
    """Compute effective (decayed) confidence for a fact or rule row."""
    confidence = row.get("confidence", 1.0) or 1.0
    decay_rate = row.get("decay_rate", 0.0) or 0.0
    last_confirmed_at = row.get("last_confirmed_at")

    if decay_rate == 0.0:
        return confidence
    if last_confirmed_at is None:
        return 0.0

    now = datetime.now(UTC)
    days_elapsed = max((now - last_confirmed_at).total_seconds() / 86400.0, 0.0)
    return confidence * math.exp(-decay_rate * days_elapsed)


def _format_fact_line(f: dict[str, Any]) -> str:
    subject = f.get("subject", "?")
    predicate = f.get("predicate", "?")
    content = f.get("content", "")
    eff_conf = _effective_confidence(f)
    return f"- [{subject}] [{predicate}]: {content} (confidence: {eff_conf:.2f})\n"


def _format_rule_line(r: dict[str, Any]) -> str:
    content = r.get("content", "")
    maturity = r.get("maturity", "?")
    effectiveness = r.get("effectiveness_score", 0.0)
    return f"- {content} (maturity: {maturity}, effectiveness: {effectiveness:.2f})\n"


def _format_episode_line(ep: dict[str, Any]) -> str:
    content = ep.get("content", "")
    created_at = ep.get("created_at")
    ts = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or "")
    if ts:
        return f"- [{ts}] {content}\n"
    return f"- {content}\n"


def _fill_section(
    header: str,
    items: list[dict[str, Any]],
    format_fn,
    char_budget: int,
) -> str:
    """Build a section string that fits within char_budget.

    Returns empty string if no items fit, or the full section header was not
    affordable (header alone would blow budget).
    """
    if not items:
        return ""

    # Header must fit too
    if len(header) >= char_budget:
        return ""

    lines: list[str] = [header]
    used = len(header)

    for item in items:
        line = format_fn(item)
        if used + len(line) > char_budget:
            break
        lines.append(line)
        used += len(line)

    # If we only wrote the header (no items fit), omit entirely
    if len(lines) == 1:
        return ""

    return "".join(lines)


async def memory_context(
    pool: Pool,
    embedding_engine,
    trigger_prompt: str,
    butler: str,
    *,
    token_budget: int = 3000,
    include_recent_episodes: bool = False,
    request_context: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic, sectioned memory context block for CC system prompt injection.

    Sections (in order, empty sections omitted):
      ## Profile Facts     — 30% of budget, owner entity facts sorted by importance
      ## Task-Relevant Facts — 35% of budget, recall matches (excluding profile facts)
      ## Active Rules      — 20% of budget, sorted by maturity rank then effectiveness
      ## Recent Episodes   — 15% of budget, opt-in only via include_recent_episodes=True

    Args:
        pool: asyncpg connection pool.
        embedding_engine: EmbeddingEngine instance for semantic search.
        trigger_prompt: The prompt/topic to search for relevant memories.
        butler: Butler name used as scope filter.
        token_budget: Maximum approximate token count (1 token ~ 4 chars).
        include_recent_episodes: If True, include Recent Episodes section.
        request_context: Optional dict with 'tenant_id' and 'request_id' for
            trace correlation and tenant scoping.

    Returns:
        Formatted memory context string. Same inputs always produce the same output.
    """
    # Resolve tenant_id from request_context
    tenant_id = "shared"
    if isinstance(request_context, dict):
        rc_tenant = request_context.get("tenant_id")
        if isinstance(rc_tenant, str) and rc_tenant.strip():
            tenant_id = rc_tenant.strip()

    total_chars = token_budget * 4

    profile_budget = int(total_chars * _PROFILE_FACTS_FRAC)
    task_budget = int(total_chars * _TASK_FACTS_FRAC)
    rules_budget = int(total_chars * _RULES_FRAC)
    episodes_budget = int(total_chars * _EPISODES_FRAC)

    # --- 1. Fetch profile facts (owner entity) ---
    profile_facts = await _fetch_profile_facts(pool, tenant_id)
    profile_ids: set = {r.get("id") for r in profile_facts if r.get("id") is not None}

    # --- 2. Fetch task-relevant facts via recall (exclude profile facts) ---
    # recall returns facts + rules sorted by composite_score DESC
    recall_results = await _search.recall(
        pool,
        trigger_prompt,
        embedding_engine,
        scope=butler,
        limit=30,
        tenant_id=tenant_id,
    )
    task_facts = [
        r
        for r in recall_results
        if r.get("memory_type") == "fact" and r.get("id") not in profile_ids
    ]
    # Deterministic sort: composite_score DESC, created_at DESC, id ASC
    task_facts.sort(
        key=lambda r: (
            -(r.get("composite_score") or 0.0),
            # created_at: use epoch 0 for None so they sort last
            -(r.get("created_at") or datetime.min.replace(tzinfo=UTC)).timestamp()
            if isinstance(r.get("created_at"), datetime)
            else 0,
            str(r.get("id") or ""),
        )
    )

    # --- 3. Fetch active rules ---
    rules = [r for r in recall_results if r.get("memory_type") == "rule"]
    # Deterministic sort: maturity_rank DESC, effectiveness_score DESC, created_at DESC, id ASC
    rules.sort(
        key=lambda r: (
            -_MATURITY_RANK.get(r.get("maturity", ""), 0),
            -(r.get("effectiveness_score") or 0.0),
            -(r.get("created_at") or datetime.min.replace(tzinfo=UTC)).timestamp()
            if isinstance(r.get("created_at"), datetime)
            else 0,
            str(r.get("id") or ""),
        )
    )

    # --- 4. Optionally fetch recent episodes ---
    recent_episodes: list[dict[str, Any]] = []
    if include_recent_episodes:
        recent_episodes = await _fetch_recent_episodes(pool, butler, tenant_id)

    # --- Assemble sections ---
    preamble = "# Memory Context\n"
    sections: list[str] = [preamble]

    profile_section = _fill_section(
        "\n## Profile Facts\n",
        profile_facts,
        _format_fact_line,
        profile_budget,
    )
    if profile_section:
        sections.append(profile_section)

    task_section = _fill_section(
        "\n## Task-Relevant Facts\n",
        task_facts,
        _format_fact_line,
        task_budget,
    )
    if task_section:
        sections.append(task_section)

    rules_section = _fill_section(
        "\n## Active Rules\n",
        rules,
        _format_rule_line,
        rules_budget,
    )
    if rules_section:
        sections.append(rules_section)

    if include_recent_episodes:
        episodes_section = _fill_section(
            "\n## Recent Episodes\n",
            recent_episodes,
            _format_episode_line,
            episodes_budget,
        )
        if episodes_section:
            sections.append(episodes_section)

    return "".join(sections)
