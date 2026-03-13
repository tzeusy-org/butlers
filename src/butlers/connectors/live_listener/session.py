"""Conversation session manager for the live-listener voice connector.

Groups temporally related utterances into conversation sessions for thread
context (``event.external_thread_id``).

Session lifecycle per-mic:
- A new session is created on the first forwarded utterance, or when the
  silence gap since the last forwarded utterance exceeds SESSION_GAP_S.
- All utterances within the gap share the same session ID.
- Session expiry is implicit: no explicit close action is taken. The session
  is simply not updated, and the next utterance past the gap creates a new one.

Session ID format: ``"voice:{device_name}:{session_start_unix_ms}"``

Checkpoint state exposed by :class:`ConversationSession` for persistence:
- ``session_id``: current session ID or ``None``
- ``session_last_ts_ms``: unix_ms of the last forwarded utterance in this session
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Default silence gap in seconds before starting a new session.
DEFAULT_SESSION_GAP_S: int = 120


class ConversationSession:
    """Per-mic conversation session state.

    Tracks the active session ID and the timestamp of the last forwarded
    utterance. When an utterance arrives more than ``session_gap_s`` seconds
    after the previous one, a new session is started.

    Args:
        device_name: The device name from LIVE_LISTENER_DEVICES config.
        session_gap_s: Seconds of silence that trigger a new session.
            Defaults to :data:`DEFAULT_SESSION_GAP_S` (120 s).
    """

    def __init__(
        self,
        device_name: str,
        session_gap_s: int = DEFAULT_SESSION_GAP_S,
    ) -> None:
        self._device_name = device_name
        self._session_gap_s = session_gap_s
        self._session_id: str | None = None
        self._session_last_ts_ms: int | None = None

    # ------------------------------------------------------------------
    # State accessors (for checkpoint persistence)
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        """Current session ID, or ``None`` if no session has started."""
        return self._session_id

    @property
    def session_last_ts_ms(self) -> int | None:
        """Unix ms of the last forwarded utterance, or ``None``."""
        return self._session_last_ts_ms

    # ------------------------------------------------------------------
    # Session resolution
    # ------------------------------------------------------------------

    def get_or_create_session(self, utterance_unix_ms: int) -> str:
        """Return the active session ID for an utterance, creating a new one
        if needed.

        A new session is created when:
        - No session exists yet (first utterance for this mic), OR
        - The silence gap since the last forwarded utterance exceeds
          ``session_gap_s``.

        Args:
            utterance_unix_ms: Unix ms timestamp of the current utterance
                (speech segment offset time).

        Returns:
            The session ID string to use as ``event.external_thread_id``.
        """
        if self._session_last_ts_ms is not None:
            gap_s = (utterance_unix_ms - self._session_last_ts_ms) / 1000.0
            if gap_s > self._session_gap_s:
                # Gap exceeded — start a fresh session
                new_id = self._make_session_id(utterance_unix_ms)
                logger.debug(
                    "live-listener: new session for mic=%s (gap=%.1fs > %ds): %s",
                    self._device_name,
                    gap_s,
                    self._session_gap_s,
                    new_id,
                )
                self._session_id = new_id
            # else: reuse existing session
        else:
            # First utterance on this mic — create initial session
            new_id = self._make_session_id(utterance_unix_ms)
            logger.debug(
                "live-listener: initial session for mic=%s: %s",
                self._device_name,
                new_id,
            )
            self._session_id = new_id

        # Always update the last-seen timestamp
        self._session_last_ts_ms = utterance_unix_ms
        return self._session_id  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Checkpoint restore
    # ------------------------------------------------------------------

    def restore(self, session_id: str | None, session_last_ts_ms: int | None) -> None:
        """Restore session state from a persisted checkpoint.

        Called on connector startup to resume an active session if the
        connector restarted within the gap window.

        Args:
            session_id: Previously persisted session ID (or ``None``).
            session_last_ts_ms: Unix ms of the last forwarded utterance
                from the persisted checkpoint (or ``None``).
        """
        self._session_id = session_id
        self._session_last_ts_ms = session_last_ts_ms
        if session_id is not None:
            logger.info(
                "live-listener: restored session state for mic=%s: session_id=%s, last_ts_ms=%s",
                self._device_name,
                session_id,
                session_last_ts_ms,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_session_id(self, start_unix_ms: int) -> str:
        """Build a session ID from the device name and start timestamp.

        Format: ``"voice:{device_name}:{session_start_unix_ms}"``
        """
        return f"voice:{self._device_name}:{start_unix_ms}"
