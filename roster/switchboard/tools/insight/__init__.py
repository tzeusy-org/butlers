"""Insight broker — proactive insight candidate management and delivery.

Public symbols re-exported here for convenience.
"""

from __future__ import annotations

from butlers.tools.switchboard.insight.broker import (
    cleanup_old_rows,
    compute_effective_budget,
    create_insight_tables,
    deduplicate_candidates,
    delivery_cycle,
    expire_candidates,
    filter_by_cooldown,
    propose_insight_candidate,
    record_cooldowns,
    record_engagement_rows,
)
from butlers.tools.switchboard.insight.models import InsightCandidate

__all__ = [
    "InsightCandidate",
    "cleanup_old_rows",
    "compute_effective_budget",
    "create_insight_tables",
    "deduplicate_candidates",
    "delivery_cycle",
    "expire_candidates",
    "filter_by_cooldown",
    "propose_insight_candidate",
    "record_cooldowns",
    "record_engagement_rows",
]
