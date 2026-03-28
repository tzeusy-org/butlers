"""Situational Context Bus.

Provides shared situational awareness via a ``public.user_context`` table.
Butlers read and write context signals (traveling, sleeping, meeting, etc.)
with TTL-based expiry, confidence scoring, and per-signal write permissions.

Read-path: ``get_active_context``, ``is_user_in_context``, ``format_context_preamble``
Write-path: ``set_context``, ``clear_context``
Helpers: ``_check_write_permission``, ``_clamp_ttl``
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    """A single active context signal from the public.user_context table."""

    signal_type: str
    value: str | None
    set_by_butler: str
    set_at: datetime
    expires_at: datetime
    confidence: float
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Permission table: signal_type -> set of authorized butler names
# ---------------------------------------------------------------------------

_WRITE_PERMISSIONS: dict[str, frozenset[str]] = {
    "traveling": frozenset({"travel", "general"}),
    "sleeping": frozenset({"health", "general"}),
    "meeting": frozenset({"general"}),
    "focused": frozenset({"general"}),
    "exercising": frozenset({"health"}),
    "sick": frozenset({"health", "general"}),
    "socializing": frozenset({"relationship", "general"}),
    "commuting": frozenset({"travel", "general"}),
    "at_home": frozenset({"travel", "home", "general"}),
    "away": frozenset({"general"}),
    "dnd": frozenset({"general", "switchboard"}),
}

# ---------------------------------------------------------------------------
# TTL table: signal_type -> (default_timedelta, max_timedelta)
# ---------------------------------------------------------------------------

_TTL_CONFIG: dict[str, tuple[timedelta, timedelta]] = {
    "traveling": (timedelta(hours=24), timedelta(days=30)),
    "sleeping": (timedelta(hours=8), timedelta(hours=12)),
    "meeting": (timedelta(hours=1), timedelta(hours=4)),
    "focused": (timedelta(hours=2), timedelta(hours=8)),
    "exercising": (timedelta(hours=1), timedelta(hours=3)),
    "sick": (timedelta(hours=24), timedelta(days=14)),
    "socializing": (timedelta(hours=3), timedelta(hours=12)),
    "commuting": (timedelta(minutes=45), timedelta(hours=3)),
    "at_home": (timedelta(hours=12), timedelta(hours=24)),
    "away": (timedelta(hours=12), timedelta(days=30)),
    "dnd": (timedelta(hours=2), timedelta(hours=24)),
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_write_permission(butler_name: str, signal_type: str) -> None:
    """Raise ``PermissionError`` if *butler_name* is not allowed to write *signal_type*.

    Parameters
    ----------
    butler_name:
        The name of the butler attempting the write.
    signal_type:
        The signal type string being written.

    Raises
    ------
    PermissionError
        When *butler_name* is not in the authorized set for *signal_type*.
    """
    authorized = _WRITE_PERMISSIONS.get(signal_type, frozenset())
    if butler_name not in authorized:
        raise PermissionError(
            f"Butler '{butler_name}' is not authorized to write signal '{signal_type}'. "
            f"Authorized butlers: {sorted(authorized)}"
        )


def _clamp_ttl(signal_type: str, set_at: datetime, expires_at: datetime) -> datetime:
    """Clamp *expires_at* to the maximum TTL for *signal_type*.

    If the requested TTL exceeds the maximum, ``expires_at`` is returned as
    ``set_at + max_ttl``.  Otherwise *expires_at* is returned unchanged.

    Parameters
    ----------
    signal_type:
        The signal type string (used to look up the max TTL).
    set_at:
        The timestamp the signal is being set (now).
    expires_at:
        The requested expiry timestamp.

    Returns
    -------
    datetime
        The (possibly clamped) expiry timestamp.
    """
    _default_ttl, max_ttl = _TTL_CONFIG[signal_type]
    max_expires_at = set_at + max_ttl
    if expires_at > max_expires_at:
        return max_expires_at
    return expires_at


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------


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
        the ``public.user_context`` table is absent.
    """
    rows = await pool.fetch(
        """
        SELECT signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata
        FROM public.user_context
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


async def is_user_in_context(
    pool: Any,
    signal_type: str,
    min_confidence: float = 0.5,
) -> bool:
    """Return whether the user is currently in a specific context.

    Parameters
    ----------
    pool:
        An asyncpg connection pool pointed at the butler database.
    signal_type:
        The signal type to check (e.g. ``"traveling"``).
    min_confidence:
        Minimum confidence threshold (inclusive). Defaults to 0.5.

    Returns
    -------
    bool
        ``True`` if there is at least one active, non-superseded signal of the
        requested type with confidence >= *min_confidence*.
    """
    row = await pool.fetchrow(
        """
        SELECT 1
        FROM public.user_context
        WHERE signal_type = $1
          AND superseded_at IS NULL
          AND expires_at > now()
          AND confidence >= $2
        LIMIT 1
        """,
        signal_type,
        min_confidence,
    )
    return row is not None


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


async def set_context(
    pool: Any,
    butler_name: str,
    signal_type: str,
    *,
    expires_at: datetime | None = None,
    value: str | None = None,
    confidence: float = 1.0,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write or update a context signal.

    Validates write permissions and signal vocabulary, clamps the TTL to the
    maximum, then performs an upsert via ``INSERT ... ON CONFLICT DO UPDATE``.
    If the signal was previously superseded, ``superseded_at`` is cleared.

    Parameters
    ----------
    pool:
        An asyncpg connection pool.
    butler_name:
        The butler performing the write (used for permission check and as
        ``set_by_butler``).
    signal_type:
        The signal type string (must be a member of ``ContextSignal``).
    expires_at:
        Absolute expiry timestamp. If ``None``, the default TTL for the signal
        type is applied from the current time.
    value:
        Optional human-readable value (e.g. ``"Paris"`` for ``traveling``).
    confidence:
        Confidence score in [0.0, 1.0]. Defaults to 1.0.
    metadata:
        Optional JSONB metadata dict.

    Raises
    ------
    ValueError
        If *signal_type* is not a valid ``ContextSignal`` member.
    PermissionError
        If *butler_name* is not authorized to write *signal_type*.
    """
    # Validate signal type against enum vocabulary
    try:
        ContextSignal(signal_type)
    except ValueError:
        valid = [s.value for s in ContextSignal]
        raise ValueError(f"Invalid signal type '{signal_type}'. Valid types: {valid}")

    # Check write permission
    _check_write_permission(butler_name, signal_type)

    now = datetime.now(tz=UTC)

    # Apply default TTL if not provided
    if expires_at is None:
        default_ttl, _max_ttl = _TTL_CONFIG[signal_type]
        expires_at = now + default_ttl

    # Clamp to maximum TTL
    expires_at = _clamp_ttl(signal_type, now, expires_at)

    await pool.execute(
        """
        INSERT INTO public.user_context
            (signal_type, value, set_by_butler, set_at, expires_at, confidence, metadata,
             superseded_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, NULL)
        ON CONFLICT (signal_type, set_by_butler) DO UPDATE
            SET value         = EXCLUDED.value,
                set_at        = EXCLUDED.set_at,
                expires_at    = EXCLUDED.expires_at,
                confidence    = EXCLUDED.confidence,
                metadata      = EXCLUDED.metadata,
                superseded_at = NULL
        """,
        signal_type,
        value,
        butler_name,
        now,
        expires_at,
        confidence,
        metadata,
    )


async def clear_context(
    pool: Any,
    butler_name: str,
    signal_type: str,
) -> None:
    """Explicitly clear a signal before its TTL expires.

    Sets ``superseded_at = now()`` for the signal matching
    ``(signal_type, set_by_butler)``. Only the butler that set the signal can
    clear it; clearing another butler's signal is a no-op. Clearing a
    non-existent or already-cleared signal is also a no-op.

    Parameters
    ----------
    pool:
        An asyncpg connection pool.
    butler_name:
        The butler performing the clear — only clears signals it originally set.
    signal_type:
        The signal type string to clear.
    """
    await pool.execute(
        """
        UPDATE public.user_context
        SET superseded_at = now()
        WHERE signal_type = $1
          AND set_by_butler = $2
          AND superseded_at IS NULL
        """,
        signal_type,
        butler_name,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


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
