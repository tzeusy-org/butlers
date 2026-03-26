"""Insight candidate model for the proactive insight engine.

Provides the ``InsightCandidate`` dataclass that butler insight-scan jobs
use to construct well-formed candidates for submission via the Switchboard's
``propose_insight_candidate()`` MCP tool.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# Compiled regex for dedup_key format validation:
#   {segment}:{segment}:{segment}          (3 segments)
#   {segment}:{segment}:{segment}:{segment} (4 segments)
# Each segment must be non-empty and contain no colons.
_DEDUP_KEY_PATTERN = re.compile(r"^[^:]+:[^:]+:[^:]+(?::[^:]+)?$")


def _validate_dedup_key(dedup_key: str) -> None:
    """Raise ValueError if dedup_key does not match the required format."""
    if not dedup_key:
        raise ValueError("dedup_key is required and must be non-empty")
    if not _DEDUP_KEY_PATTERN.match(dedup_key):
        raise ValueError(
            "dedup_key must match format {category}:{entity}:{time-scope} "
            "or {butler}:{category}:{entity}:{time-scope}"
        )


@dataclass
class InsightCandidate:
    """A structured insight candidate for submission to the insight broker.

    Used by butler insight-scan jobs to construct well-formed candidates
    before calling ``propose_insight_candidate()`` on the Switchboard.

    Attributes
    ----------
    priority:
        Delivery priority, 1-100. Higher = more important.
        90-100: time-critical (action within 24-48 hours)
        70-89:  actionable-soon (action within 7 days)
        50-69:  informational (summaries, milestones, trends)
        30-49:  low-urgency nudges (suggestions, reconnections)
        1-29:   background observations (verbose mode only)
    category:
        Domain category string (e.g. "birthday", "spending-anomaly").
    dedup_key:
        Semantic deduplication key. Format: ``{category}:{entity}:{time-scope}``
        for cross-butler dedup, or ``{butler}:{category}:{entity}:{time-scope}``
        for butler-specific insights.
    message:
        Human-readable insight message.
    expires_at:
        Candidate expires at this UTC datetime if not delivered.
    cooldown_days:
        Optional override for the default cooldown period.
    channel:
        Optional preferred delivery channel (e.g. "telegram", "email").
    metadata:
        Optional butler-specific structured data.
    """

    priority: int
    category: str
    dedup_key: str
    message: str
    expires_at: datetime
    cooldown_days: int | None = None
    channel: str | None = None
    metadata: dict | None = None

    def __post_init__(self) -> None:
        if not (1 <= self.priority <= 100):
            raise ValueError("priority must be between 1 and 100")
        if not self.message:
            raise ValueError("message must be non-empty")
        _validate_dedup_key(self.dedup_key)

    def to_mcp_args(self) -> dict:
        """Return a dict suitable for passing to ``propose_insight_candidate()``."""
        args: dict = {
            "priority": self.priority,
            "category": self.category,
            "dedup_key": self.dedup_key,
            "message": self.message,
            "expires_at": self.expires_at.isoformat(),
        }
        if self.cooldown_days is not None:
            args["cooldown_days"] = self.cooldown_days
        if self.channel is not None:
            args["channel"] = self.channel
        if self.metadata is not None:
            args["metadata"] = self.metadata
        return args
