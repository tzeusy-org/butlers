"""Memory context building — generate memory context for CC system prompts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _search

logger = logging.getLogger(__name__)


async def memory_context(
    pool: Pool,
    embedding_engine,
    trigger_prompt: str,
    butler: str,
    *,
    token_budget: int = 3000,
) -> str:
    """Build a memory context block for injection into a CC system prompt.

    Retrieves the most relevant facts and rules via composite-scored recall,
    then formats them into a structured text block that fits within the
    given token budget.

    Args:
        pool: asyncpg connection pool.
        embedding_engine: EmbeddingEngine instance for semantic search.
        trigger_prompt: The prompt/topic to search for relevant memories.
        butler: Butler name used as scope filter.
        token_budget: Maximum approximate token count (1 token ~ 4 chars).

    Returns:
        Formatted memory context string with Key Facts and Active Rules sections.
    """
    results = await _search.recall(pool, trigger_prompt, embedding_engine, scope=butler, limit=20)

    # Separate facts and rules, already sorted by composite_score descending
    facts = [r for r in results if r.get("memory_type") == "fact"]
    rules = [r for r in results if r.get("memory_type") == "rule"]

    header = "# Memory Context\n"
    facts_header = "\n## Key Facts\n"
    rules_header = "\n## Active Rules\n"

    # Start building text within budget
    lines: list[str] = [header]
    budget_chars = token_budget * 4

    def _chars_used() -> int:
        return sum(len(line) for line in lines)

    # Add facts section
    if facts:
        lines.append(facts_header)
        for f in facts:
            subject = f.get("subject", "?")
            predicate = f.get("predicate", "?")
            content = f.get("content", "")
            confidence = f.get("confidence", 0.0)
            line = f"- [{subject}] [{predicate}]: {content} (confidence: {confidence:.2f})\n"
            if _chars_used() + len(line) > budget_chars:
                break
            lines.append(line)

    # Add rules section
    if rules:
        rules_hdr_candidate = rules_header
        # Check if adding the header + at least one rule fits
        if _chars_used() + len(rules_hdr_candidate) < budget_chars:
            lines.append(rules_hdr_candidate)
            for r in rules:
                content = r.get("content", "")
                maturity = r.get("maturity", "?")
                effectiveness = r.get("effectiveness_score", 0.0)
                line = f"- {content} (maturity: {maturity}, effectiveness: {effectiveness:.2f})\n"
                if _chars_used() + len(line) > budget_chars:
                    break
                lines.append(line)

    return "".join(lines)
