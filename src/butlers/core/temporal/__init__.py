"""Temporal intelligence module for butlers.

Provides deadline tracking, event chains, seasonal awareness,
and time-aware delivery (quiet hours) for butler task scheduling.

Submodules:
  deadlines    — Deadline validation, countdown computation, status state machine
  event_chains — Event chain action validation, materialization, depth limit
  seasonal     — Seasonal period active detection and date validation
  delivery     — Quiet hours check, deliver_at computation, notification lifecycle
"""
