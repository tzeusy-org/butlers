"""Typed read-models / DTOs for the dashboard API.

Each sub-module provides a typed read boundary for a specific high-churn
dashboard domain. Dashboard API routers consume these typed DTOs rather
than constructing ad-hoc SQL queries inline, so a schema change only
requires updating the read-model.

Available read-model modules:

- ``activity``: activity feed queries
- ``butlers``: fleet status queries
- ``calendar_workspace``: calendar workspace queries
- ``insights``: insight delivery queries
- ``search``: cross-butler search queries
- ``sessions``: cross-butler session list and detail queries
- ``timeline``: cross-butler timeline fan-out queries

Each module's explicit ``READ_MODEL_VERSION`` retains its versioned query
and DTO contract independently of the internal module filename. Breaking
column or shape changes require an explicit contract-version change.
"""

from __future__ import annotations
