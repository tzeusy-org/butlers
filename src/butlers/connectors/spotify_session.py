"""Spotify listening session state machine.

Implements ``ListeningSessionTracker``: a lightweight in-memory state machine
that aggregates Spotify poll results into logical listening sessions and emits
batched events to minimise downstream token costs.

Event model (batched)
---------------------
- ``spotify.context_start`` — emitted once when playback begins from idle.
  Carries the first track's metadata.
- ``spotify.listening_digest`` — emitted periodically (default every 60 min of
  active listening) with all tracks accumulated since the last digest or
  context_start.
- ``spotify.session_summary`` — emitted when a session ends (drain timeout
  after playback stops, or forced shutdown).

State machine transitions:

    idle ──(playback detected)──────────────────────────────→ active (emit context_start)
    active ──(same track)───────────────────────────────────→ active (no event)
    active ──(new track, any context)───────────────────────→ active (accumulate silently)
    active ──(playback stopped)─────────────────────────────→ draining
    draining ──(idle timeout exceeded)──────────────────────→ idle (emit session_summary)
    draining ──(playback resumed)───────────────────────────→ active (continue session)

Context changes during continuous playback (autoplay, radio, DJ) are NOT
session boundaries — they are accumulated silently.  Sessions end ONLY when
playback stops long enough for the drain timeout to expire.

At the end of every ``on_poll()`` call, the tracker checks whether a digest is
due and appends a ``spotify.listening_digest`` event if so.

Usage::

    tracker = ListeningSessionTracker(idle_timeout_s=300, digest_interval_s=3600)

    # inside connector poll loop:
    events = tracker.on_poll(poll_result, now=time.time())
    for ev in events:
        if ev.event_type == "spotify.context_start":
            ...
        elif ev.event_type == "spotify.listening_digest":
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

# Default interval between listening_digest events (seconds).
DEFAULT_DIGEST_INTERVAL_S = 3600


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


class SessionState(Enum):
    """States of the listening session state machine."""

    IDLE = "idle"
    ACTIVE = "active"
    DRAINING = "draining"


@dataclass(frozen=True)
class TrackDetail:
    """Lightweight record of a single track observation for digest batching."""

    track_id: str
    track_name: str | None
    artist_names: list[str]
    album_name: str | None
    duration_ms: int | None
    played_at_ms: int


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
        event_type: One of ``"spotify.context_start"``,
            ``"spotify.listening_digest"``, or ``"spotify.session_summary"``.
        track_id: First track ID (context_start events).
        track_name: First track name (context_start events).
        artist_names: Artist names (context_start events).
        album_name: Album name (context_start events).
        duration_ms: Track duration in ms (context_start events).
        context_uri: Playlist/album URI (all event types).
        context_name: Human-readable context name (all event types).
        device_name: Playback device (context_start events).
        played_at_ms: Timestamp of the poll observation in milliseconds.
        raw_payload: Raw Spotify API response (context_start events).
        session_start_ms: Session start time in ms (session_summary events).
        session_end_ms: Session end time in ms (session_summary events).
        session_duration_ms: Total session duration in ms (session_summary events).
        track_count: Number of unique tracks heard (summary/digest events).
        tracks_played: Ordered list of track names heard (summary/digest events).
        digest_start_ms: Start of the digest window in ms (digest events).
        digest_end_ms: End of the digest window in ms (digest events).
        digest_tracks: Full track details for the digest period (digest events).
    """

    event_type: str
    # context_start fields (first track in new context)
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
    # Digest fields
    digest_start_ms: int | None = None
    digest_end_ms: int | None = None
    digest_tracks: list[TrackDetail] = field(default_factory=list)


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
    # Digest tracking: tracks accumulated since last digest (or context_start)
    last_digest_ms: int = 0  # set to start_ms at creation
    _digest_tracks: list[TrackDetail] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.last_digest_ms == 0:
            self.last_digest_ms = self.start_ms

    def record_track(
        self,
        track_id: str,
        track_name: str | None,
        played_at_ms: int,
        *,
        detail: TrackDetail | None = None,
    ) -> None:
        """Add a track to the session accumulator."""
        self._seen_track_ids.add(track_id)
        if track_name:
            self.tracks_played.append(track_name)
        self.last_active_ms = played_at_ms
        if detail is not None:
            self._digest_tracks.append(detail)

    @property
    def track_count(self) -> int:
        """Number of unique tracks observed in this session."""
        return len(self._seen_track_ids)

    @property
    def digest_track_count(self) -> int:
        """Number of tracks accumulated since the last digest."""
        return len(self._digest_tracks)

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

    def build_digest_event(self, now_ms: int) -> SessionEvent:
        """Produce a listening_digest SessionEvent and reset digest state."""
        tracks = list(self._digest_tracks)
        names = [t.track_name for t in tracks if t.track_name]
        event = SessionEvent(
            event_type="spotify.listening_digest",
            context_uri=self.context_uri,
            context_name=self.context_name,
            played_at_ms=now_ms,
            track_count=len(tracks),
            tracks_played=names,
            digest_start_ms=self.last_digest_ms,
            digest_end_ms=now_ms,
            digest_tracks=tracks,
        )
        self._digest_tracks.clear()
        self.last_digest_ms = now_ms
        return event


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class ListeningSessionTracker:
    """State machine that aggregates Spotify poll results into listening sessions.

    Args:
        idle_timeout_s: Seconds of no playback before the draining state ends
            the current session and transitions back to idle.  Defaults to
            ``DEFAULT_IDLE_TIMEOUT_S`` (300 s).
        digest_interval_s: Seconds between ``listening_digest`` events during
            active playback.  Defaults to ``DEFAULT_DIGEST_INTERVAL_S`` (3600 s).
    """

    def __init__(
        self,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        digest_interval_s: float = DEFAULT_DIGEST_INTERVAL_S,
    ) -> None:
        self._idle_timeout_s = idle_timeout_s
        self._digest_interval_ms = int(digest_interval_s * 1000)
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
            in emission order: context_start / session_summary first, then
            any listening_digest that became due.
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

        # Check if a periodic digest is due
        events.extend(self._check_digest(played_at_ms))

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
        detail = self._make_track_detail(result, played_at_ms)
        self._session = _SessionAccumulator(
            context_uri=result.context_uri,
            context_name=result.context_name,
            start_ms=played_at_ms,
            last_active_ms=played_at_ms,
        )
        self._session.record_track(result.track_id, result.track_name, played_at_ms, detail=detail)
        self._current_track_id = result.track_id
        self._state = SessionState.ACTIVE
        logger.debug(
            "idle→active: track=%s context=%s",
            result.track_id,
            result.context_uri,
        )

        # Emit a context_start event for the new listening context.
        return [self._make_context_start_event(result, played_at_ms)]

    def _handle_active(
        self,
        result: PollResult,
        played_at_ms: int,
        now: float,
    ) -> list[SessionEvent]:
        """Handle a poll result while in ACTIVE state.

        All track and context changes during continuous playback are accumulated
        silently.  Context changes (autoplay, radio, DJ) are NOT session
        boundaries — sessions end only on playback gaps (drain timeout).
        """
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

        # Accumulate silently regardless of context change.
        if result.track_id != self._current_track_id:
            assert self._session is not None
            detail = self._make_track_detail(result, played_at_ms)
            self._session.record_track(
                result.track_id, result.track_name, played_at_ms, detail=detail
            )
            self._current_track_id = result.track_id
            logger.debug(
                "active: track changed → %s (accumulated, no event)",
                result.track_id,
            )
        else:
            # Same track — update last_active timestamp only.
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
            # Playback resumed — continue existing session regardless of context.
            # The drain timeout hasn't expired, so the session is still alive.
            assert self._session is not None
            if result.track_id != self._current_track_id:
                detail = self._make_track_detail(result, played_at_ms)
                self._session.record_track(
                    result.track_id, result.track_name, played_at_ms, detail=detail
                )
                self._current_track_id = result.track_id
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

    def _transition_to_idle(self) -> None:
        """Reset machine state to IDLE."""
        self._state = SessionState.IDLE
        self._session = None
        self._current_track_id = None
        self._drain_started_at = None

    def _check_digest(self, now_ms: int) -> list[SessionEvent]:
        """Emit a listening_digest if enough time has elapsed since the last one."""
        if self._state != SessionState.ACTIVE or self._session is None:
            return []
        if self._session.digest_track_count == 0:
            return []
        elapsed_ms = now_ms - self._session.last_digest_ms
        if elapsed_ms >= self._digest_interval_ms:
            return [self._session.build_digest_event(now_ms)]
        return []

    @staticmethod
    def _make_context_start_event(result: PollResult, played_at_ms: int) -> SessionEvent:
        """Build a context_start SessionEvent from a PollResult."""
        return SessionEvent(
            event_type="spotify.context_start",
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

    @staticmethod
    def _make_track_detail(result: PollResult, played_at_ms: int) -> TrackDetail:
        """Build a TrackDetail from a PollResult for digest accumulation."""
        return TrackDetail(
            track_id=result.track_id or "",
            track_name=result.track_name,
            artist_names=list(result.artist_names),
            album_name=result.album_name,
            duration_ms=result.duration_ms,
            played_at_ms=played_at_ms,
        )
