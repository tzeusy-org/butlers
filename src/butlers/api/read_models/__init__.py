"""Versioned read-models / DTOs for the dashboard API.

Each sub-module provides a typed read boundary for a specific high-churn
dashboard domain.  Dashboard API routers consume these typed DTOs rather
than constructing ad-hoc SQL queries inline, so a schema change only
requires updating the read-model — not the router.

Available read-model modules:

- ``sessions_v1`` — cross-butler session list and detail queries (v1)
- ``timeline_v1`` — cross-butler timeline fan-out queries (v1)

Version suffixes (``_v1``) are the stability contract: a breaking column or
shape change bumps to ``_v2`` rather than silently altering the existing
module.
"""

from __future__ import annotations
