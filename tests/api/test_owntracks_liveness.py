"""Tests for OwnTracks connection-state liveness derivation (bu-cy0zh).

``_derive_connection_state`` used to reimplement its own binary 300s-only
liveness cutoff (no distinct stale bucket, no clock-skew handling) instead
of routing through the canonical ``derive_liveness`` helper
(``butlers.api.models.connector``) used by the sibling connectors,
Google Health, and calendar-workspace routers. These tests pin the
three-state (live/stale/offline) behavior, including the clock-skew
tolerance, now that it delegates to the canonical helper.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.api.models.owntracks import OwnTracksConnectionState
from butlers.api.routers.owntracks import _derive_connection_state

pytestmark = pytest.mark.unit


def _heartbeat_row(
    *,
    last_heartbeat_at: datetime | None,
    counter_messages_ingested: int = 5,
    today_messages_ingested: int = 2,
    uptime_s: float = 120.0,
) -> dict:
    return {
        "last_heartbeat_at": last_heartbeat_at,
        "uptime_s": uptime_s,
        "counter_messages_ingested": counter_messages_ingested,
        "today_messages_ingested": today_messages_ingested,
    }


class TestDeriveConnectionStateNotConfigured:
    def test_no_token_is_not_configured_regardless_of_heartbeat(self):
        row = _heartbeat_row(last_heartbeat_at=datetime.now(UTC))
        state, running, last_event_at, events_today, uptime = _derive_connection_state(
            token_configured=False,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.not_configured
        assert running is False
        assert last_event_at is None
        assert events_today == 0
        assert uptime is None


class TestDeriveConnectionStateLiveness:
    def test_no_heartbeat_ever_received_is_offline(self):
        state, running, last_event_at, events_today, uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=None,
        )
        assert state == OwnTracksConnectionState.offline
        assert running is False
        assert last_event_at is None
        assert events_today == 0
        assert uptime is None

    def test_fresh_heartbeat_with_events_is_connected(self):
        ts = datetime.now(UTC) - timedelta(minutes=1)
        row = _heartbeat_row(last_heartbeat_at=ts)
        state, running, last_event_at, events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.connected
        assert running is True
        assert last_event_at == ts
        assert events_today == 2

    def test_fresh_heartbeat_with_no_events_yet_is_no_events(self):
        ts = datetime.now(UTC) - timedelta(minutes=1)
        row = _heartbeat_row(last_heartbeat_at=ts, counter_messages_ingested=0)
        state, running, last_event_at, _events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.no_events
        assert running is True
        assert last_event_at is None

    def test_heartbeat_10min_old_is_stale_not_offline(self):
        """A 5-15min-old heartbeat lands in the distinct stale bucket."""
        ts = datetime.now(UTC) - timedelta(minutes=10)
        row = _heartbeat_row(last_heartbeat_at=ts)
        state, running, _last_event_at, _events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.stale
        assert running is False

    def test_heartbeat_20min_old_is_offline(self):
        ts = datetime.now(UTC) - timedelta(minutes=20)
        row = _heartbeat_row(last_heartbeat_at=ts)
        state, running, _last_event_at, _events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.offline
        assert running is False

    def test_slightly_future_heartbeat_is_tolerated_as_live(self):
        """A small clock-skew (<=5min future) must not crash or read negative-age; it's live."""
        ts = datetime.now(UTC) + timedelta(minutes=1)
        row = _heartbeat_row(last_heartbeat_at=ts)
        state, running, last_event_at, _events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.connected
        assert running is True
        assert last_event_at == ts

    def test_far_future_heartbeat_is_offline_not_falsely_healthy(self):
        """Beyond the clock-skew tolerance, a future timestamp is untrustworthy, not 'live'."""
        ts = datetime.now(UTC) + timedelta(minutes=30)
        row = _heartbeat_row(last_heartbeat_at=ts)
        state, running, _last_event_at, _events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.offline
        assert running is False

    def test_naive_datetime_heartbeat_is_treated_as_utc(self):
        """cr.last_heartbeat_at may come back tz-naive from the driver; must not crash."""
        ts = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
        row = _heartbeat_row(last_heartbeat_at=ts)
        state, running, _last_event_at, _events_today, _uptime = _derive_connection_state(
            token_configured=True,
            heartbeat_row=row,
        )
        assert state == OwnTracksConnectionState.connected
        assert running is True
