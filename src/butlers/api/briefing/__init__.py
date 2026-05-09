"""Dashboard briefing composition package.

Provides:
- classify(state) -> state_class
- headline_for(state_class, n) -> headline body string
- elaborate_llm(state, state_class) -> (text, source)
- elaborate_fallback(state, state_class) -> text
- voice_lint(text) -> bool (True = passes)
- BriefingCache (per-owner LRU+TTL)

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
