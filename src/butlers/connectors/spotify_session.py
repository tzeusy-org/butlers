"""Spotify listening session state machine.

Implements ``ListeningSessionTracker``: a lightweight in-memory state machine
that aggregates Spotify poll results into logical listening sessions and emits
``spotify.session_summary`` envelopes on session end.

State machine transitions (per openspec connector-spotify design D7):

    idle ──(playback detected)──────────────────────────────→ active
    active ──(same track, same context)─────────────────────→ active (no event)
    active ──(new track, same context)──────────────────────→ active (emit track_change)
    active ──(context changed)──────────────→ active (emit session_summary, start new session)
    active ──(playback stopped)─────────────────────────────→ draining
    draining ──(idle timeout exceeded)──────────────────────→ idle (emit session_summary)
    draining ──(playback resumed, same context)──────────────→ active (continue session)
    draining ──(playback resumed, different context)─────────→ active (emit summary, new session)

Poll result protocol
--------------------
Callers pass a ``PollResult`` dataclass to ``on_poll()``.  The method returns
a list of ``SessionEvent`` objects, in emission order, describing what happened
during the poll.  Each poll may emit zero or more ``track_change`` events and
at most one ``session_summary`` event.  On context changes (or when draining
playback resumes with a different context), a ``spotify.session_summary`` is
emitted for the previous session before the ``spotify.track_change`` event for
the new context.

Usage::

    tracker = ListeningSessionTracker(idle_timeout_s=300)

    # inside connector poll loop:
    events = tracker.on_poll(poll_result, now=time.time())
    for ev in events:
        if ev.event_type == "spotify.track_change":
            ...
        elif ev.event_type == "spotify.session_summary":
            ...
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Default time in seconds of no playback before the draining state closes
# a session.  Matches SPOTIFY_SESSION_IDLE_TIMEOUT_S env var default.
DEFAULT_IDLE_TIMEOUT_S = 300


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


class SessionState(Enum):
    """States of the listening session state machine."""

    IDLE = "idle"
    ACTIVE = "active"
    DRAINING = "draining"


@dataclass(frozen=True)
class PollResult:
    """Normalised output of one Spotify poll cycle.

    Attributes:
        is_playing: True when Spotify reports active playback.
        track_id: Spotify track identifier, or None when nothing is playing.
        track_name: Human-readable track title, or None.
        artist_names: List of artist display names.
        album_name: Album title, or None.
        duration_ms: Track duration in milliseconds, or None.
        context_uri: Playlist or album URI (the session boundary key), or None.
        context_name: Human-readable name of the context (playlist/album), or None.
        device_name: Name of the Spotify device, or None.
        played_at_ms: Unix timestamp in milliseconds when playback was observed.
            Defaults to the current wall-clock time if not supplied.
        raw_payload: Full Spotify API response dict for envelope construction.
    """

    is_playing: bool
    track_id: str | None = None
    track_name: str | None = None
    artist_names: list[str] = field(default_factory=list)
    album_name: str | None = None
    duration_ms: int | None = None
    context_uri: str | None = None
    context_name: str | None = None
    device_name: str | None = None
    played_at_ms: int | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionEvent:
    """An event emitted by the state machine during a poll cycle.

    Attributes:
        event_type: ``"spotify.track_change"`` or ``"spotify.session_summary"``.
        track_id: Relevant for track_change events; the new track ID.
        track_name: Human-readable track name (track_change events).
        artist_names: Artist names (track_change events).
        album_name: Album name (track_change events).
        duration_ms: Track duration in ms (track_change events).
        context_uri: Playlist/album URI (both event types).
        context_name: Human-readable context name (both event types).
        device_name: Playback device (track_change events).
        played_at_ms: Timestamp of the poll observation in milliseconds.
        raw_payload: Raw Spotify API response (track_change events).
        session_start_ms: Session start time in ms (session_summary events).
        session_end_ms: Session end time in ms (session_summary events).
        session_duration_ms: Total session duration in ms (session_summary events).
        track_count: Number of unique tracks heard (session_summary events).
        tracks_played: Ordered list of track names heard (session_summary events).
    """

    event_type: str
    # Track change fields
    track_id: str | None = None
    track_name: str | None = None
    artist_names: list[str] = field(default_factory=list)
    album_name: str | None = None
    duration_ms: int | None = None
    context_uri: str | None = None
    context_name: str | None = None
    device_name: str | None = None
    played_at_ms: int | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
    # Session summary fields
    session_start_ms: int | None = None
    session_end_ms: int | None = None
    session_duration_ms: int | None = None
    track_count: int = 0
    tracks_played: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal session accumulator
# ---------------------------------------------------------------------------


@dataclass
class _SessionAccumulator:
    """Mutable in-session state.

    Accumulated while the state machine is in ACTIVE or DRAINING state.
    """

    context_uri: str | None
    context_name: str | None
    start_ms: int
    last_active_ms: int
    # Ordered track names for inclusion in session_summary
    tracks_played: list[str] = field(default_factory=list)
    # Set of track IDs for deduplication
    _seen_track_ids: set[str] = field(default_factory=set)

    def record_track(self, track_id: str, track_name: str | None, played_at_ms: int) -> None:
        """Add a track to the session accumulator."""
        self._seen_track_ids.add(track_id)
        if track_name:
            self.tracks_played.append(track_name)
        self.last_active_ms = played_at_ms

    @property
    def track_count(self) -> int:
        """Number of unique tracks observed in this session."""
        return len(self._seen_track_ids)

    def build_summary_event(self, end_ms: int) -> SessionEvent:
        """Produce a session_summary SessionEvent."""
        duration_ms = max(0, end_ms - self.start_ms)
        return SessionEvent(
            event_type="spotify.session_summary",
            context_uri=self.context_uri,
            context_name=self.context_name,
            played_at_ms=end_ms,
            session_start_ms=self.start_ms,
            session_end_ms=end_ms,
            session_duration_ms=duration_ms,
            track_count=self.track_count,
            tracks_played=list(self.tracks_played),
        )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class ListeningSessionTracker:
    """State machine that aggregates Spotify poll results into listening sessions.

    Args:
        idle_timeout_s: Seconds of no playback before the draining state ends
            the current session and transitions back to idle.  Defaults to
            ``DEFAULT_IDLE_TIMEOUT_S`` (300 s).
    """

    def __init__(self, idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S) -> None:
        self._idle_timeout_s = idle_timeout_s
        self._state: SessionState = SessionState.IDLE
        self._current_track_id: str | None = None
        self._session: _SessionAccumulator | None = None
        # Timestamp when playback was last seen stopped (for drain timeout)
        self._drain_started_at: float | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """Current state machine state."""
        return self._state

    @property
    def current_track_id(self) -> str | None:
        """Track ID of the currently-playing track, or None."""
        return self._current_track_id

    def on_poll(
        self,
        result: PollResult,
        now: float | None = None,
    ) -> list[SessionEvent]:
        """Process one poll result and return any emitted events.

        Args:
            result: Normalised output of a Spotify API poll cycle.
            now: Current wall-clock time as a Unix timestamp (seconds).  If
                not provided, ``time.time()`` is used.  Exposed for testing.

        Returns:
            A list of ``SessionEvent`` objects (may be empty).  Events appear
            in emission order: track_change events before session_summary.
        """
        if now is None:
            now = time.time()

        played_at_ms = result.played_at_ms if result.played_at_ms is not None else int(now * 1000)

        events: list[SessionEvent] = []

        if self._state == SessionState.IDLE:
            events.extend(self._handle_idle(result, played_at_ms, now))
        elif self._state == SessionState.ACTIVE:
            events.extend(self._handle_active(result, played_at_ms, now))
        elif self._state == SessionState.DRAINING:
            events.extend(self._handle_draining(result, played_at_ms, now))

        return events

    def force_end_session(self, now: float | None = None) -> list[SessionEvent]:
        """Forcibly end the current session (e.g., on connector shutdown).

        If the state machine is in ACTIVE or DRAINING state, a session_summary
        event is emitted and the machine transitions to IDLE.

        Args:
            now: Current wall-clock time.  Defaults to ``time.time()``.

        Returns:
            List containing zero or one ``session_summary`` event.
        """
        if now is None:
            now = time.time()
        events: list[SessionEvent] = []
        if self._state in (SessionState.ACTIVE, SessionState.DRAINING) and self._session:
            end_ms = int(now * 1000)
            events.append(self._session.build_summary_event(end_ms))
            logger.debug(
                "force_end_session: emitted session_summary (tracks=%d, duration_ms=%d)",
                self._session.track_count,
                max(0, end_ms - self._session.start_ms),
            )
        self._transition_to_idle()
        return events

    # ------------------------------------------------------------------
    # State handlers
    # ------------------------------------------------------------------

    def _handle_idle(
        self,
        result: PollResult,
        played_at_ms: int,
        now: float,  # noqa: ARG002
    ) -> list[SessionEvent]:
        """Handle a poll result while in IDLE state."""
        if not result.is_playing or not result.track_id:
            # Still idle — nothing to do.
            return []

        # Playback detected → start new session, transition to ACTIVE.
        self._session = _SessionAccumulator(
            context_uri=result.context_uri,
            context_name=result.context_name,
            start_ms=played_at_ms,
            last_active_ms=played_at_ms,
        )
        self._session.record_track(result.track_id, result.track_name, played_at_ms)
        self._current_track_id = result.track_id
        self._state = SessionState.ACTIVE
        logger.debug(
            "idle→active: track=%s context=%s",
            result.track_id,
            result.context_uri,
        )

        # Emit a track_change event for the newly-started track.
        return [self._make_track_change_event(result, played_at_ms)]

    def _handle_active(
        self,
        result: PollResult,
        played_at_ms: int,
        now: float,
    ) -> list[SessionEvent]:
        """Handle a poll result while in ACTIVE state."""
        events: list[SessionEvent] = []

        if not result.is_playing or not result.track_id:
            # Playback stopped → transition to DRAINING.
            self._drain_started_at = now
            self._state = SessionState.DRAINING
            logger.debug(
                "active→draining: last_track=%s",
                self._current_track_id,
            )
            return events

        context_changed = self._context_changed(result.context_uri)

        if context_changed:
            # Context changed → end current session, emit summary, start new session.
            assert self._session is not None
            summary = self._session.build_summary_event(played_at_ms)
            events.append(summary)
            logger.debug(
                "active: context changed (%s→%s), emitting session_summary",
                self._session.context_uri,
                result.context_uri,
            )

            # Start fresh session for the new context.
            self._session = _SessionAccumulator(
                context_uri=result.context_uri,
                context_name=result.context_name,
                start_ms=played_at_ms,
                last_active_ms=played_at_ms,
            )
            self._session.record_track(result.track_id, result.track_name, played_at_ms)
            self._current_track_id = result.track_id
            # Emit track_change for the first track of the new context.
            events.append(self._make_track_change_event(result, played_at_ms))
            return events

        # Same context — check for track change.
        if result.track_id != self._current_track_id:
            assert self._session is not None
            self._session.record_track(result.track_id, result.track_name, played_at_ms)
            self._current_track_id = result.track_id
            logger.debug(
                "active: track changed → %s",
                result.track_id,
            )
            events.append(self._make_track_change_event(result, played_at_ms))
        else:
            # Same track, same context — update last_active timestamp only.
            assert self._session is not None
            self._session.last_active_ms = played_at_ms

        return events

    def _handle_draining(
        self,
        result: PollResult,
        played_at_ms: int,
        now: float,
    ) -> list[SessionEvent]:
        """Handle a poll result while in DRAINING state."""
        events: list[SessionEvent] = []

        if result.is_playing and result.track_id:
            # Playback resumed.
            context_changed = self._context_changed(result.context_uri)

            if context_changed:
                # Different context → end current session, emit summary, start new session.
                assert self._session is not None
                end_ms = played_at_ms
                summary = self._session.build_summary_event(end_ms)
                events.append(summary)
                logger.debug(
                    "draining: resumed with different context (%s→%s), emitting session_summary",
                    self._session.context_uri,
                    result.context_uri,
                )
                self._session = _SessionAccumulator(
                    context_uri=result.context_uri,
                    context_name=result.context_name,
                    start_ms=played_at_ms,
                    last_active_ms=played_at_ms,
                )
                self._session.record_track(result.track_id, result.track_name, played_at_ms)
                self._current_track_id = result.track_id
                self._drain_started_at = None
                self._state = SessionState.ACTIVE
                events.append(self._make_track_change_event(result, played_at_ms))
                return events

            # Same context → resume, no event.
            assert self._session is not None
            if result.track_id != self._current_track_id:
                self._session.record_track(result.track_id, result.track_name, played_at_ms)
                self._current_track_id = result.track_id
                events.append(self._make_track_change_event(result, played_at_ms))
            else:
                self._session.last_active_ms = played_at_ms

            self._drain_started_at = None
            self._state = SessionState.ACTIVE
            logger.debug(
                "draining→active: resumed track=%s context=%s",
                result.track_id,
                result.context_uri,
            )
            return events

        # Still not playing — check idle timeout.
        if self._drain_started_at is not None:
            elapsed = now - self._drain_started_at
            if elapsed >= self._idle_timeout_s:
                assert self._session is not None
                end_ms = int(now * 1000)
                summary = self._session.build_summary_event(end_ms)
                events.append(summary)
                logger.debug(
                    "draining→idle: timeout after %.1fs, emitting session_summary (tracks=%d)",
                    elapsed,
                    self._session.track_count,
                )
                self._transition_to_idle()

        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _context_changed(self, new_context_uri: str | None) -> bool:
        """Return True when the context URI has changed since the active session started."""
        if self._session is None:
            return False
        return new_context_uri != self._session.context_uri

    def _transition_to_idle(self) -> None:
        """Reset machine state to IDLE."""
        self._state = SessionState.IDLE
        self._session = None
        self._current_track_id = None
        self._drain_started_at = None

    @staticmethod
    def _make_track_change_event(result: PollResult, played_at_ms: int) -> SessionEvent:
        """Build a track_change SessionEvent from a PollResult."""
        return SessionEvent(
            event_type="spotify.track_change",
            track_id=result.track_id,
            track_name=result.track_name,
            artist_names=list(result.artist_names),
            album_name=result.album_name,
            duration_ms=result.duration_ms,
            context_uri=result.context_uri,
            context_name=result.context_name,
            device_name=result.device_name,
            played_at_ms=played_at_ms,
            raw_payload=result.raw_payload,
        )
