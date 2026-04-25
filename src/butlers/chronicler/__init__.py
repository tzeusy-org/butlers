"""Chronicler — retrospective time butler.

This package hosts Chronicler-internal primitives:

- :mod:`butlers.chronicler.models` — typed dataclasses for point events,
  episodes, overrides, source state, and checkpoints.
- :mod:`butlers.chronicler.storage` — idempotent upserts, overlap and
  correction queries, checkpoint updates, source registration.
- :mod:`butlers.chronicler.contracts` — declared source compatibility
  registry (initial sources + deferred sources).
- :mod:`butlers.chronicler.adapters` — projection adapters (sessions,
  calendar, etc.) that run as scheduled jobs.
- :mod:`butlers.chronicler.aggregations` — pure deterministic helpers
  (e.g. ``category_for``) used by aggregate endpoints and episode lists.
  No I/O, no LLM.
- :mod:`butlers.chronicler.interpretation` — Tier 2 LLM entry points
  with token-bounded input guardrails.

Projection adapters NEVER invoke an LLM. Interpretation paths are
sparse, bounded, and explicit. See RFC 0014.
"""

from __future__ import annotations

__all__: list[str] = []
