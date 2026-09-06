"""Memory context building — deterministic section compiler for CC system prompts.

Builds a sectioned context block with strict quota allocation:
  1. Profile Facts     (20% of budget) — owner entity facts by importance
  2. Task-Relevant Facts (35% of budget) — recall matches excluding profile facts
  3. Active Rules      (20% of budget) — sorted by maturity rank then effectiveness
  4. Recent Episodes   (15% of budget) — opt-in via include_recent_episodes=True
  5. Fleet Knowledge   (10% of budget) — opt-in via include_fleet_knowledge=True

All sections use deterministic tie-breaking: score/importance DESC, created_at DESC, id ASC.

A single server-held read ceiling (``CatalogReadPolicy``, loaded once per
assembly) governs every fetch in this module — Profile Facts, Task-Relevant
Facts/recall, and Recent Episodes all apply the same ``allowed_sensitivities``
in SQL, matching the ceiling ``search_catalog`` already enforces for
cross-butler reads. Facts withheld from Profile Facts by the ceiling are
reported via a ``withheld: N`` marker rather than silently disappearing.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _search, validate_tenant_id

logger = logging.getLogger(__name__)

# Maturity rank for Active Rules sorting: higher = more mature
_MATURITY_RANK: dict[str, int] = {
    "proven": 3,
    "established": 2,
    "candidate": 1,
    "anti_pattern": 0,
}

# Section budget fractions. MUST sum to <= 1.0 across every section, including
# the opt-in ones — memory_context assumes the worst case (every section
# active at once) rather than budgeting only the always-on subset.
_PROFILE_FACTS_FRAC = 0.20
_TASK_FACTS_FRAC = 0.35
_RULES_FRAC = 0.20
_EPISODES_FRAC = 0.15
_FLEET_KNOWLEDGE_FRAC = 0.10

_SECTION_FRACTION_TOTAL = (
    _PROFILE_FACTS_FRAC + _TASK_FACTS_FRAC + _RULES_FRAC + _EPISODES_FRAC + _FLEET_KNOWLEDGE_FRAC
)
assert _SECTION_FRACTION_TOTAL <= 1.0 + 1e-9, (
    f"memory_context section fractions sum to {_SECTION_FRACTION_TOTAL}, "
    "which exceeds the token_budget they are meant to partition"
)

# Cross-butler catalog results to request from public.memory_catalog before
# filtering out this butler's own entries (see _fetch_fleet_knowledge).
_FLEET_KNOWLEDGE_QUERY_LIMIT = 10


async def _fetch_profile_facts(
    pool: Pool,
    tenant_id: str,
    *,
    limit: int = 50,
    allowed_sensitivities: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Fetch facts anchored to the owner entity.

    Sorted by importance DESC, created_at DESC, id ASC. The read ceiling is
    applied in SQL, matching ``search_catalog``'s sensitivity filter, so an
    above-ceiling owner fact is never fetched at all — a real Postgres row
    that only exists in the excluded tier returns zero rows here, not a
    post-fetch drop.

    Returns ``(facts, withheld_count)`` where ``withheld_count`` is the
    number of owner facts that matched every condition except the ceiling
    (0 when ``allowed_sensitivities`` is None).
    """
    conditions = [
        "f.tenant_id = $1",
        "f.validity IN ('active', 'fading')",
        "'owner' = ANY(e.roles)",
    ]
    params: list[Any] = [tenant_id]
    if allowed_sensitivities is not None:
        params.append(list(allowed_sensitivities))
        conditions.append(f"COALESCE(f.sensitivity, 'normal') = ANY(${len(params)})")
    where = " AND ".join(conditions)

    params.append(limit)
    sql = f"""
        SELECT f.*
        FROM facts f
        JOIN public.entities e ON f.entity_id = e.id
        WHERE {where}
        ORDER BY f.importance DESC, f.created_at DESC, f.id ASC
        LIMIT ${len(params)}
    """
    try:
        rows = await pool.fetch(sql, *params)
        facts = [dict(r) for r in rows]
    except Exception:
        logger.debug("Profile facts query failed (likely missing public.entities)", exc_info=True)
        return [], 0

    if allowed_sensitivities is None:
        return facts, 0

    withheld_sql = """
        SELECT COUNT(*)
        FROM facts f
        JOIN public.entities e ON f.entity_id = e.id
        WHERE f.tenant_id = $1
          AND f.validity IN ('active', 'fading')
          AND 'owner' = ANY(e.roles)
          AND NOT (COALESCE(f.sensitivity, 'normal') = ANY($2))
    """
    try:
        withheld = await pool.fetchval(withheld_sql, tenant_id, list(allowed_sensitivities))
    except Exception:
        logger.debug("Profile facts withheld-count query failed", exc_info=True)
        withheld = 0

    return facts, int(withheld or 0)


async def _fetch_recent_episodes(
    pool: Pool,
    butler: str,
    tenant_id: str,
    *,
    limit: int = 20,
    allowed_sensitivities: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Fetch most recent episodes for the butler, ordered by created_at DESC.

    Read-ceiling filtered in SQL, same shape as ``_fetch_profile_facts``.
    """
    conditions = [
        "butler = $1",
        "tenant_id = $2",
        "metadata->>'provenance_placeholder' IS DISTINCT FROM 'true'",
    ]
    params: list[Any] = [butler, tenant_id]
    if allowed_sensitivities is not None:
        params.append(list(allowed_sensitivities))
        conditions.append(f"COALESCE(sensitivity, 'normal') = ANY(${len(params)})")
    where = " AND ".join(conditions)

    params.append(limit)
    sql = f"""
        SELECT *
        FROM episodes
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
    """
    try:
        rows = await pool.fetch(sql, *params)
        return [dict(r) for r in rows]
    except Exception:
        logger.debug("Recent episodes query failed", exc_info=True)
        return []


async def _fetch_fleet_knowledge(
    pool: Pool,
    embedding_engine,
    trigger_prompt: str,
    butler: str,
    tenant_id: str,
    read_policy,
    *,
    limit: int = _FLEET_KNOWLEDGE_QUERY_LIMIT,
) -> list[dict[str, Any]]:
    """Fetch relevant cross-butler knowledge from public.memory_catalog.

    Searches the shared discovery catalog (all butlers' facts/rules) and
    excludes rows this butler owns — Task-Relevant Facts already surfaces
    the butler's own knowledge, so this section is additive, not duplicative.

    Best-effort: any failure (catalog table absent, embedding failure, etc.)
    is logged at debug and returns an empty list rather than failing context
    assembly — matches ``_fetch_profile_facts``/``_fetch_recent_episodes``.
    """
    try:
        results = await _search.search_catalog(
            pool,
            trigger_prompt,
            embedding_engine,
            tenant_id=tenant_id,
            memory_type=None,
            limit=limit,
            mode="hybrid",
            read_policy=read_policy,
        )
    except Exception:
        logger.debug("Fleet knowledge catalog search failed", exc_info=True)
        return []
    return [r for r in results if r.get("source_butler") != butler]


def _is_confidence_renderable(row: dict[str, Any]) -> bool:
    """False when this row's effective confidence would render as a fake 0.00.

    A decaying fact (``decay_rate != 0``) that has never been confirmed
    (``last_confirmed_at IS NULL``) has *unknown* confidence, not zero
    confidence — ``_effective_confidence`` returns 0.0 for it as a scoring
    floor, but rendering that as ``(confidence: 0.00)`` misrepresents an
    unconfirmed fact as a thoroughly discredited one. Such rows are excluded
    from rendering (they remain fully visible to ``recall``/``memory_search``,
    which use the raw score for ranking, not for display).
    """
    decay_rate = row.get("decay_rate", 0.0) or 0.0
    return not (decay_rate != 0.0 and row.get("last_confirmed_at") is None)


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


def _format_fleet_knowledge_line(r: dict[str, Any]) -> str:
    source_butler = r.get("source_butler") or "?"
    title = r.get("title") or (r.get("summary") or "")[:80]
    return f"- [{source_butler}] {title}\n"


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
    include_fleet_knowledge: bool = False,
    catalog_read_policy=None,
    request_context: dict[str, Any] | None = None,
) -> str:
    """Build a deterministic, sectioned memory context block for CC system prompt injection.

    Sections (in order, empty sections omitted):
      ## Profile Facts     — 20% of budget, owner entity facts sorted by importance
      ## Task-Relevant Facts — 35% of budget, recall matches (excluding profile facts)
      ## Active Rules      — 20% of budget, sorted by maturity rank then effectiveness
      ## Recent Episodes   — 15% of budget, opt-in only via include_recent_episodes=True
      ## Fleet Knowledge   — 10% of budget, opt-in only via include_fleet_knowledge=True;
        cross-butler facts/rules discovered via public.memory_catalog, excluding
        this butler's own entries (already covered by Task-Relevant Facts)

    A single read ceiling (``catalog_read_policy``, loaded once here if not
    supplied) governs Profile Facts, Task-Relevant Facts (via ``recall``),
    Recent Episodes, and Fleet Knowledge alike — the same server-held ceiling
    ``search_catalog`` already enforces, generalized to every local fetch in
    this assembly so a mid-assembly config change can never half-govern one
    block. Facts withheld from Profile Facts by the ceiling are reported as
    a ``withheld: N`` marker rather than disappearing without a trace.

    Args:
        pool: asyncpg connection pool.
        embedding_engine: EmbeddingEngine instance for semantic search.
        trigger_prompt: The prompt/topic to search for relevant memories.
        butler: Butler name used as scope filter.
        token_budget: Maximum approximate token count (1 token ~ 4 chars).
        include_recent_episodes: If True, include Recent Episodes section.
        include_fleet_knowledge: If True, include a Fleet Knowledge section
            surfacing relevant cross-butler catalog entries. Best-effort —
            a catalog search failure degrades to an empty section rather
            than failing context assembly.
        catalog_read_policy: Server-held read policy. Module entry points pass
            the policy from the owning runtime-config pool; when omitted, it
            is loaded once from ``pool`` (fail-closed to 'normal').
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
    validate_tenant_id(tenant_id)

    # Read ceiling: loaded once per assembly so every fetch below agrees on
    # exactly one policy, even if runtime_config changes mid-assembly.
    read_policy = catalog_read_policy or await _search.load_catalog_read_policy(pool)
    allowed_sensitivities = read_policy.allowed_sensitivities

    total_chars = token_budget * 4

    profile_budget = int(total_chars * _PROFILE_FACTS_FRAC)
    task_budget = int(total_chars * _TASK_FACTS_FRAC)
    rules_budget = int(total_chars * _RULES_FRAC)
    episodes_budget = int(total_chars * _EPISODES_FRAC)
    fleet_knowledge_budget = int(total_chars * _FLEET_KNOWLEDGE_FRAC)

    # --- 1. Fetch profile facts (owner entity) ---
    profile_facts, profile_withheld = await _fetch_profile_facts(
        pool, tenant_id, allowed_sensitivities=allowed_sensitivities
    )
    profile_facts = [f for f in profile_facts if _is_confidence_renderable(f)]
    profile_ids: set = {r.get("id") for r in profile_facts if r.get("id") is not None}

    # --- 2. Fetch task-relevant facts via recall (exclude profile facts) ---
    # recall returns facts + rules sorted by composite_score DESC; the same
    # read_policy loaded above governs this fetch too.
    recall_results = await _search.recall(
        pool,
        trigger_prompt,
        embedding_engine,
        scope=butler,
        limit=30,
        tenant_id=tenant_id,
        read_policy=read_policy,
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
        recent_episodes = await _fetch_recent_episodes(
            pool, butler, tenant_id, allowed_sensitivities=allowed_sensitivities
        )

    # --- 5. Optionally fetch cross-butler fleet knowledge ---
    fleet_knowledge: list[dict[str, Any]] = []
    if include_fleet_knowledge:
        fleet_knowledge = await _fetch_fleet_knowledge(
            pool, embedding_engine, trigger_prompt, butler, tenant_id, read_policy
        )

    # --- Assemble sections ---
    preamble = "# Memory Context\n"
    sections: list[str] = [preamble]

    profile_section = _fill_section(
        "\n## Profile Facts\n",
        profile_facts,
        _format_fact_line,
        profile_budget,
    )
    if profile_withheld > 0:
        # A confidential owner fact is absent from the section above but its
        # exclusion is reported, not silent — see _fetch_profile_facts.
        if not profile_section:
            profile_section = "\n## Profile Facts\n"
        profile_section += f"_(withheld: {profile_withheld})_\n"
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

    if include_fleet_knowledge:
        fleet_knowledge_section = _fill_section(
            "\n## Fleet Knowledge (cross-butler)\n",
            fleet_knowledge,
            _format_fleet_knowledge_line,
            fleet_knowledge_budget,
        )
        if fleet_knowledge_section:
            sections.append(fleet_knowledge_section)

    return "".join(sections)
