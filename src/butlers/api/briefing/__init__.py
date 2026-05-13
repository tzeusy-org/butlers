"""Dashboard briefing composition package.

Provides:
- classify(state) -> state_class
- headline_for(state_class, n) -> headline body string
- elaborate_fallback(state, state_class) -> text

Additional symbols used by the router (imported directly from submodules):
- butlers.api.briefing.prompts.elaborate_llm(pool, state, state_class) -> str | None
- butlers.api.briefing.lint.voice_lint_passes(text) -> bool
- butlers.api.briefing.cache.BriefingCache (per-owner LRU+TTL)

This package is distinct from src/butlers/jobs/briefing.py, which
handles the cross-butler daily aggregation job.
"""

from butlers.api.briefing.classify import classify, headline_for
from butlers.api.briefing.fallback import elaborate_fallback

__all__ = [
    "classify",
    "elaborate_fallback",
    "headline_for",
]
