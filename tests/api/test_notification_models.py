"""Tests for notification Pydantic models."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from butlers.api.models import NotificationStats, NotificationSummary

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# NotificationSummary
# ---------------------------------------------------------------------------


class TestNotificationSummary:
    def test_full(self):
        nid = uuid4()
        now = datetime.now(tz=UTC)
        n = NotificationSummary(
            id=nid,
            source_butler="switchboard",
            channel="telegram",
            recipient="12345",
            message="Hello!",
            status="sent",
            error=None,
            created_at=now,
        )
        assert n.id == nid
        assert n.source_butler == "switchboard"
        assert n.channel == "telegram"
        assert n.recipient == "12345"
        assert n.message == "Hello!"
        assert n.status == "sent"
        assert n.error is None
        assert n.created_at == now

    def test_minimal_with_defaults(self):
        nid = uuid4()
        now = datetime.now(tz=UTC)
        n = NotificationSummary(
            id=nid,
            source_butler="health",
            channel="email",
            message="Reminder",
            status="sent",
            created_at=now,
        )
        assert n.recipient is None
        assert n.error is None

    def test_failed_notification(self):
        nid = uuid4()
        now = datetime.now(tz=UTC)
        n = NotificationSummary(
            id=nid,
            source_butler="health",
            channel="telegram",
            recipient="99999",
            message="Alert!",
            status="failed",
            error="Connection timeout",
            created_at=now,
        )
        assert n.status == "failed"
        assert n.error == "Connection timeout"

    def test_json_round_trip(self):
        nid = uuid4()
        now = datetime.now(tz=UTC)
        n = NotificationSummary(
            id=nid,
            source_butler="switchboard",
            channel="telegram",
            recipient="12345",
            message="test",
            status="sent",
            created_at=now,
        )
        json_str = n.model_dump_json()
        restored = NotificationSummary.model_validate_json(json_str)
        assert restored.id == nid
        assert restored.source_butler == "switchboard"
        assert restored.created_at == now

    def test_rejects_missing_required_field(self):
        with pytest.raises(Exception):
            NotificationSummary(
                id=uuid4(),
                source_butler="switchboard",
                # missing channel, message, status, created_at
            )  # type: ignore[call-arg]

    def test_rejects_invalid_uuid(self):
        with pytest.raises(Exception):
            NotificationSummary(
                id="not-a-uuid",  # type: ignore[arg-type]
                source_butler="switchboard",
                channel="email",
                message="test",
                status="sent",
                created_at=datetime.now(tz=UTC),
            )

    def test_model_dump_structure(self):
        nid = uuid4()
        now = datetime.now(tz=UTC)
        n = NotificationSummary(
            id=nid,
            source_butler="switchboard",
            channel="telegram",
            recipient="12345",
            message="Hello!",
            status="sent",
            created_at=now,
        )
        payload = n.model_dump()
        assert set(payload.keys()) == {
            "id",
            "source_butler",
            "channel",
            "recipient",
            "message",
            "metadata",
            "status",
            "error",
            "session_id",
            "trace_id",
            "created_at",
        }


# ---------------------------------------------------------------------------
# NotificationStats
# ---------------------------------------------------------------------------


class TestNotificationStats:
    def test_zero_stats(self):
        stats = NotificationStats(
            total=0,
            sent=0,
            failed=0,
            by_channel={},
            by_butler={},
        )
        assert stats.total == 0
        assert stats.sent == 0
        assert stats.failed == 0
        assert stats.by_channel == {}
        assert stats.by_butler == {}

    def test_populated_stats(self):
        stats = NotificationStats(
            total=150,
            sent=140,
            failed=10,
            by_channel={"telegram": 100, "email": 50},
            by_butler={"switchboard": 80, "health": 70},
        )
        assert stats.total == 150
        assert stats.sent == 140
        assert stats.failed == 10
        assert stats.by_channel["telegram"] == 100
        assert stats.by_butler["health"] == 70

    def test_json_round_trip(self):
        stats = NotificationStats(
            total=10,
            sent=8,
            failed=2,
            by_channel={"telegram": 10},
            by_butler={"switchboard": 10},
        )
        json_str = stats.model_dump_json()
        restored = NotificationStats.model_validate_json(json_str)
        assert restored.total == 10
        assert restored.by_channel == {"telegram": 10}

    def test_model_dump_json_parseable(self):
        stats = NotificationStats(
            total=5,
            sent=3,
            failed=2,
            by_channel={"email": 5},
            by_butler={"general": 5},
        )
        parsed = json.loads(stats.model_dump_json())
        assert parsed["total"] == 5
        assert parsed["by_channel"]["email"] == 5

    def test_rejects_missing_field(self):
        with pytest.raises(Exception):
            NotificationStats(total=10, sent=8)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Re-export from models package
# ---------------------------------------------------------------------------


class TestModelsReExport:
    def test_notification_summary_importable_from_package(self):
        from butlers.api.models import NotificationSummary as NS

        assert NS is NotificationSummary

    def test_notification_stats_importable_from_package(self):
        from butlers.api.models import NotificationStats as NSt

        assert NSt is NotificationStats
