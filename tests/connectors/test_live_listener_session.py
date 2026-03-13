"""Tests for live-listener conversation session manager.

Covers task 6.2 from the connector-live-listener openspec:
- Session creation on first utterance
- Session continuity within the gap window
- New session started when gap is exceeded
- Session ID format: 'voice:{device_name}:{session_start_unix_ms}'
- Checkpoint restore
"""

from __future__ import annotations

import pytest

from butlers.connectors.live_listener.session import (
    DEFAULT_SESSION_GAP_S,
    ConversationSession,
)

pytestmark = pytest.mark.unit

_DEVICE = "kitchen"
_GAP_S = 120  # default gap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ms(seconds: float) -> int:
    """Convert seconds to integer milliseconds."""
    return int(seconds * 1000)


# ---------------------------------------------------------------------------
# Session ID format
# ---------------------------------------------------------------------------


def test_session_id_format_on_first_utterance() -> None:
    """Session ID should be 'voice:{device_name}:{start_unix_ms}'."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    start_ms = _ms(1000)
    sid = session.get_or_create_session(start_ms)
    assert sid == f"voice:{_DEVICE}:{start_ms}"


def test_session_id_contains_device_name() -> None:
    """Session ID must embed the device name."""
    session = ConversationSession("bedroom", session_gap_s=_GAP_S)
    sid = session.get_or_create_session(_ms(500))
    assert "bedroom" in sid


# ---------------------------------------------------------------------------
# First utterance creates a new session
# ---------------------------------------------------------------------------


def test_first_utterance_creates_session() -> None:
    """The very first utterance should always create a fresh session."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    assert session.session_id is None
    sid = session.get_or_create_session(_ms(1000))
    assert sid is not None
    assert session.session_id == sid


def test_first_utterance_sets_session_last_ts() -> None:
    """After first utterance, session_last_ts_ms should be set."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    ts = _ms(1000)
    session.get_or_create_session(ts)
    assert session.session_last_ts_ms == ts


# ---------------------------------------------------------------------------
# Session continuity within the gap window
# ---------------------------------------------------------------------------


def test_utterance_within_gap_reuses_session() -> None:
    """Utterances within the gap window must share the same session ID."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    start_ms = _ms(1000)
    sid1 = session.get_or_create_session(start_ms)

    # 60 s later — within 120 s gap
    later_ms = _ms(1000 + 60)
    sid2 = session.get_or_create_session(later_ms)

    assert sid1 == sid2


def test_last_ts_updated_on_each_utterance() -> None:
    """session_last_ts_ms should track the most recent utterance timestamp."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    session.get_or_create_session(_ms(1000))
    t2 = _ms(1000 + 60)
    session.get_or_create_session(t2)
    assert session.session_last_ts_ms == t2


def test_utterance_exactly_at_gap_boundary_reuses_session() -> None:
    """An utterance at exactly the gap boundary (not exceeding) reuses the session."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    start_ms = _ms(1000)
    session.get_or_create_session(start_ms)

    # Exactly 120 000 ms later (= 120 s, not > 120 s)
    exactly_at_boundary = start_ms + _ms(_GAP_S)
    sid_first = session.session_id

    sid_boundary = session.get_or_create_session(exactly_at_boundary)
    # gap_s == _GAP_S is NOT > _GAP_S so session continues
    assert sid_boundary == sid_first


# ---------------------------------------------------------------------------
# Gap exceeded starts a new session
# ---------------------------------------------------------------------------


def test_gap_exceeded_starts_new_session() -> None:
    """When the gap exceeds SESSION_GAP_S, a new session must be created."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    start_ms = _ms(1000)
    sid1 = session.get_or_create_session(start_ms)

    # 121 s later — exceeds 120 s gap
    after_gap_ms = start_ms + _ms(_GAP_S + 1)
    sid2 = session.get_or_create_session(after_gap_ms)

    assert sid2 != sid1
    assert sid2 == f"voice:{_DEVICE}:{after_gap_ms}"


def test_new_session_id_uses_new_start_timestamp() -> None:
    """The new session ID's timestamp component must be the new utterance time."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    session.get_or_create_session(_ms(1000))

    new_start = _ms(1000) + _ms(_GAP_S + 10)
    sid = session.get_or_create_session(new_start)
    # Extract the timestamp from the session ID
    ts_str = sid.split(":")[-1]
    assert int(ts_str) == new_start


def test_multiple_gaps_create_distinct_sessions() -> None:
    """Multiple silence gaps should produce distinct session IDs."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    t0 = _ms(1000)
    t1 = t0 + _ms(_GAP_S + 1)
    t2 = t1 + _ms(_GAP_S + 1)

    sid0 = session.get_or_create_session(t0)
    sid1 = session.get_or_create_session(t1)
    sid2 = session.get_or_create_session(t2)

    assert len({sid0, sid1, sid2}) == 3


# ---------------------------------------------------------------------------
# Custom session_gap_s
# ---------------------------------------------------------------------------


def test_custom_session_gap_shorter() -> None:
    """A shorter custom gap should trigger session creation earlier."""
    session = ConversationSession(_DEVICE, session_gap_s=30)
    start_ms = _ms(1000)
    sid1 = session.get_or_create_session(start_ms)

    # 31 s later — exceeds 30 s gap
    sid2 = session.get_or_create_session(start_ms + _ms(31))
    assert sid2 != sid1


def test_custom_session_gap_longer() -> None:
    """A longer custom gap should allow longer sessions without restarting."""
    session = ConversationSession(_DEVICE, session_gap_s=300)
    start_ms = _ms(1000)
    sid1 = session.get_or_create_session(start_ms)

    # 200 s later — within 300 s gap
    sid2 = session.get_or_create_session(start_ms + _ms(200))
    assert sid2 == sid1


# ---------------------------------------------------------------------------
# DEFAULT_SESSION_GAP_S constant
# ---------------------------------------------------------------------------


def test_default_session_gap_is_120() -> None:
    """The spec mandates a default gap of 120 s."""
    assert DEFAULT_SESSION_GAP_S == 120


def test_default_gap_used_when_none_specified() -> None:
    """ConversationSession with no gap arg should use DEFAULT_SESSION_GAP_S."""
    session = ConversationSession(_DEVICE)
    start_ms = _ms(1000)
    session.get_or_create_session(start_ms)

    # 121 s = just over default 120 s gap
    sid_after_gap = session.get_or_create_session(start_ms + _ms(121))
    assert f"voice:{_DEVICE}:{start_ms + _ms(121)}" == sid_after_gap


# ---------------------------------------------------------------------------
# Checkpoint restore
# ---------------------------------------------------------------------------


def test_restore_sets_session_state() -> None:
    """restore() should set session_id and session_last_ts_ms."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    persisted_sid = f"voice:{_DEVICE}:{_ms(500)}"
    persisted_ts = _ms(500)

    session.restore(session_id=persisted_sid, session_last_ts_ms=persisted_ts)

    assert session.session_id == persisted_sid
    assert session.session_last_ts_ms == persisted_ts


def test_restore_allows_session_continuity_within_gap() -> None:
    """After restoring state within the gap, the next utterance reuses the session."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    original_ts = _ms(1000)
    original_sid = f"voice:{_DEVICE}:{original_ts}"

    session.restore(session_id=original_sid, session_last_ts_ms=original_ts)

    # 60 s after the persisted timestamp — within gap
    next_ts = original_ts + _ms(60)
    sid = session.get_or_create_session(next_ts)
    assert sid == original_sid


def test_restore_triggers_new_session_when_gap_exceeded() -> None:
    """After restoring state beyond the gap, the next utterance starts a new session."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    original_ts = _ms(1000)
    original_sid = f"voice:{_DEVICE}:{original_ts}"

    session.restore(session_id=original_sid, session_last_ts_ms=original_ts)

    # 300 s after the persisted timestamp — exceeds 120 s gap
    next_ts = original_ts + _ms(300)
    sid = session.get_or_create_session(next_ts)
    assert sid != original_sid
    assert sid == f"voice:{_DEVICE}:{next_ts}"


def test_restore_with_none_state_starts_fresh() -> None:
    """Restoring with all-None state is a no-op (fresh start)."""
    session = ConversationSession(_DEVICE, session_gap_s=_GAP_S)
    session.restore(session_id=None, session_last_ts_ms=None)

    start_ms = _ms(1000)
    sid = session.get_or_create_session(start_ms)
    assert sid == f"voice:{_DEVICE}:{start_ms}"
