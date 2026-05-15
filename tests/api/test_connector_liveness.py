"""Tests for derive_liveness clock-skew guard (bu-1hs86).

Verifies that derive_liveness treats future-dated heartbeats (>5 min ahead of
server clock) as 'offline' rather than 'online', matching the SQL guard added
to the butler_registry liveness check in dashboard_briefing.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.api.models.connector import derive_liveness

pytestmark = pytest.mark.unit


class TestDeriveLivenessClockSkew:
    """derive_liveness must not report future-dated heartbeats as online."""

    def test_none_heartbeat_is_offline(self):
        """No heartbeat record -> offline."""
        assert derive_liveness(None) == "offline"

    def test_recent_heartbeat_is_online(self):
        """Heartbeat 1 minute ago -> online."""
        ts = datetime.now(UTC) - timedelta(minutes=1)
        assert derive_liveness(ts) == "online"

    def test_heartbeat_6min_ago_is_stale(self):
        """Heartbeat 6 minutes ago -> stale."""
        ts = datetime.now(UTC) - timedelta(minutes=6)
        assert derive_liveness(ts) == "stale"

    def test_heartbeat_20min_ago_is_offline(self):
        """Heartbeat 20 minutes ago -> offline."""
        ts = datetime.now(UTC) - timedelta(minutes=20)
        assert derive_liveness(ts) == "offline"

    def test_heartbeat_1min_future_is_online(self):
        """Heartbeat 1 minute in the future (minor NTP drift) -> online (within grace window)."""
        ts = datetime.now(UTC) + timedelta(minutes=1)
        assert derive_liveness(ts) == "online"

    def test_heartbeat_over_5min_future_is_offline(self):
        """Heartbeat >5 minutes in the future -> offline (clock-skew guard)."""
        ts = datetime.now(UTC) + timedelta(minutes=6)
        assert derive_liveness(ts) == "offline"

    def test_heartbeat_30min_future_is_offline(self):
        """Heartbeat 30 minutes in the future -> offline (large clock skew)."""
        ts = datetime.now(UTC) + timedelta(minutes=30)
        assert derive_liveness(ts) == "offline"
