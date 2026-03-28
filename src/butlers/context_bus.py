"""Situational Context Bus.

Provides shared situational awareness via a ``shared.user_context`` table.
Butlers read and write context signals (traveling, sleeping, meeting, etc.)
with TTL-based expiry, confidence scoring, and per-signal write permissions.

Only the read-path and preamble formatting are implemented here; the full
write-path (set_context, clear_context, migrations) is tracked in bu-1e2p.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any


class ContextSignal(Enum):
    """Vocabulary of context signal types."""

    traveling = "traveling"
    sleeping = "sleeping"
    meeting = "meeting"
    focused = "focused"
    exercising = "exercising"
    sick = "sick"
    socializing = "socializing"
    commuting = "commuting"
    at_home = "at_home"
    away = "away"
    dnd = "dnd"


@dataclass
class ContextEntry:
    """A single active context signal from the shared.user_context table."""

    signal_type: str
    value: str | None
    set_by_butler: str
    set_at: datetime
    expires_at: datetime
    confidence: float
    metadata: dict[str, Any] | None = None


async def get_active_context(pool: Any) -> list[ContextEntry]:
    """Return all currently active context signals ordered by confidence desc, set_at desc.

    A signal is active when ``superseded_at IS NULL AND expires_at > now()``.

    Parameters
    ----------
    pool:
        An asyncpg connection pool pointed at the butler database.

    Returns
    -------
    list[ContextEntry]
        Active signals, highest confidence first. Empty list when none exist or
        the ``shared.user_context`` table is absent.
    """
    rows = await pool.fetch(
        """
        SELECT signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
        FROM shared.user_context
        WHERE superseded_at IS NULL AND expires_at > now()
        ORDER BY confidence DESC, set_at DESC
        """
    )
    return [
        ContextEntry(
            signal_type=row["signal_type"],
            value=row["value"],
            set_by_butler=row["set_by_butler"],
            set_at=row["set_at"],
            expires_at=row["expires_at"],
            confidence=row["confidence"],
            metadata=row["metadata"],
        )
        for row in rows
    ]


def _confidence_label(confidence: float) -> str:
    """Return the human-readable label for a confidence score."""
    if confidence >= 1.0:
        return "explicit"
    if confidence >= 0.8:
        return "high confidence"
    if confidence >= 0.5:
        return "medium confidence"
    return "low confidence"


def format_context_preamble(signals: list[ContextEntry]) -> str:
    """Format a list of active context signals into an LLM-ready preamble string.

    Format: ``[User Context: <signal_type> (<value>, <label>), ...]``

    When a signal has no value, the format is ``<signal_type> (<label>)``.
    Returns an empty string when *signals* is empty.

    Parameters
    ----------
    signals:
        Active ``ContextEntry`` instances to format.

    Returns
    -------
    str
        A bracketed context string, or ``""`` when no signals are present.
    """
    if not signals:
        return ""

    parts: list[str] = []
    for entry in signals:
        label = _confidence_label(entry.confidence)
        if entry.value is not None:
            parts.append(f"{entry.signal_type} ({entry.value}, {label})")
        else:
            parts.append(f"{entry.signal_type} ({label})")

    return "[User Context: " + ", ".join(parts) + "]"
