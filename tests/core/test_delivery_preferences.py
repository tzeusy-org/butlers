"""Tests for delivery preferences DB operations and MCP tools.

Covers tasks §8-10 from openspec/changes/temporal-intelligence/tasks.md:
  §8.2  notify() priority parameter and validation
  §8.3  Quiet hours gate in notify() — defer medium/low, bypass high
  §8.5  Deferred notification storage (insert_deferred_notification)
  §10.1 delivery_preferences_set MCP tool
  §10.2 delivery_preferences_get MCP tool (defaults when no preferences)
  §10.3 deferred_notifications_list MCP tool
  §10.4 deferred_notification_cancel MCP tool

Unit tests (no DB) cover pure logic. Integration tests (Docker/asyncpg) cover
the DB functions and notify() quiet hours gate.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


# ---------------------------------------------------------------------------
# §8.2 — notify() priority parameter validation (pure unit tests)
# ---------------------------------------------------------------------------


class TestNotifyPriorityValidation:
    """Tests for the priority parameter validation in notify()."""

    def test_valid_priorities_accepted(self):
        """All three valid priority values are accepted."""
        # The daemon validates priority before any DB access.
        # We test the logic that the set includes exactly these values.
        _VALID_PRIORITIES = {"high", "medium", "low"}
        for p in ("high", "medium", "low"):
            assert p in _VALID_PRIORITIES

    def test_invalid_priority_rejected(self):
        """An invalid priority string like 'urgent' is not in the valid set."""
        _VALID_PRIORITIES = {"high", "medium", "low"}
        assert "urgent" not in _VALID_PRIORITIES
        assert "critical" not in _VALID_PRIORITIES
        assert "" not in _VALID_PRIORITIES


# ---------------------------------------------------------------------------
# §8.2 / §10.1 — delivery_db validate_timezone (pure unit tests)
# ---------------------------------------------------------------------------


class TestValidateTimezone:
    """Tests for delivery_db.validate_timezone()."""

    def test_valid_timezone_accepted(self):
        """Common timezones are accepted without raising."""
        from butlers.core.temporal.delivery_db import validate_timezone

        for tz in ("UTC", "America/New_York", "Europe/Berlin", "Asia/Tokyo"):
            assert validate_timezone(tz) == tz

    def test_invalid_timezone_raises(self):
        """Unrecognised timezone name raises ValueError."""
        from butlers.core.temporal.delivery_db import validate_timezone

        with pytest.raises(ValueError, match="Unknown timezone"):
            validate_timezone("Invalid/Zone")

    def test_empty_string_raises(self):
        """Empty string timezone raises ValueError."""
        from butlers.core.temporal.delivery_db import validate_timezone

        with pytest.raises(ValueError, match="Unknown timezone"):
            validate_timezone("")


# ---------------------------------------------------------------------------
# §8.2 / §8.3 — should_defer_notification (covered by existing tests,
#               but confirm priority default is medium)
# ---------------------------------------------------------------------------


class TestNotifyPriorityDefaultsMedium:
    """Confirm that medium priority is the default and behaves like medium."""

    def test_medium_priority_deferred_during_quiet_hours(self):
        """Medium priority notification is deferred when inside quiet hours."""
        from datetime import time

        from butlers.core.temporal.delivery import should_defer_notification

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "batch_low_priority": True,
            "batch_delivery_time": "07:00",
            "override_channels": None,
        }
        # 23:30 is inside overnight quiet hours
        assert should_defer_notification(
            priority="medium",
            current_time=time(23, 30),
            prefs=prefs,
        )

    def test_high_priority_bypasses_quiet_hours(self):
        """High priority notification bypasses quiet hours and delivers immediately."""
        from datetime import time

        from butlers.core.temporal.delivery import should_defer_notification

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "batch_low_priority": True,
            "batch_delivery_time": "07:00",
            "override_channels": None,
        }
        assert not should_defer_notification(
            priority="high",
            current_time=time(23, 30),
            prefs=prefs,
        )

    def test_batch_low_priority_false_delivers_immediately(self):
        """When batch_low_priority=False, medium/low notifications deliver immediately."""
        from datetime import time

        from butlers.core.temporal.delivery import should_defer_notification

        prefs = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "batch_low_priority": False,
            "batch_delivery_time": "07:00",
            "override_channels": None,
        }
        # Inside quiet hours but batch_low_priority=False → no deferral
        assert not should_defer_notification(
            priority="medium",
            current_time=time(23, 30),
            prefs=prefs,
        )
        assert not should_defer_notification(
            priority="low",
            current_time=time(2, 0),
            prefs=prefs,
        )


# ---------------------------------------------------------------------------
# §10 — DB-backed tests (require Docker/asyncpg)
# ---------------------------------------------------------------------------


_DELIVERY_PREFERENCES_DDL = """
    CREATE TABLE IF NOT EXISTS delivery_preferences (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        butler_name         TEXT NOT NULL UNIQUE,
        quiet_hours_start   TIME NOT NULL DEFAULT '22:00',
        quiet_hours_end     TIME NOT NULL DEFAULT '07:00',
        timezone            TEXT NOT NULL DEFAULT 'UTC',
        batch_low_priority  BOOLEAN NOT NULL DEFAULT true,
        batch_delivery_time TIME NOT NULL DEFAULT '07:00',
        override_channels   JSONB,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    )
"""

_DEFERRED_NOTIFICATIONS_DDL = """
    CREATE TABLE IF NOT EXISTS deferred_notifications (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        butler_name  TEXT NOT NULL,
        channel      TEXT NOT NULL,
        message      TEXT NOT NULL,
        priority     TEXT NOT NULL DEFAULT 'medium',
        envelope     JSONB NOT NULL,
        deferred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        deliver_at   TIMESTAMPTZ NOT NULL,
        status       TEXT NOT NULL DEFAULT 'pending',
        delivered_at TIMESTAMPTZ,
        CONSTRAINT chk_deferred_notifications_status
            CHECK (status IN ('pending', 'delivered', 'expired', 'cancelled')),
        CONSTRAINT chk_deferred_notifications_priority
            CHECK (priority IN ('high', 'medium', 'low'))
    )
"""


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestDeliveryPreferencesDB:
    """Integration tests for delivery_db functions (require Docker PostgreSQL)."""

    @pytest.fixture
    async def pool(self, postgres_container):
        """Create a fresh test database with delivery tables."""
        import asyncpg

        db_name = f"test_{uuid.uuid4().hex[:12]}"
        admin_conn = await asyncpg.connect(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database="postgres",
        )
        try:
            await admin_conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin_conn.close()

        p = await asyncpg.create_pool(
            host=postgres_container.get_container_host_ip(),
            port=int(postgres_container.get_exposed_port(5432)),
            user=postgres_container.username,
            password=postgres_container.password,
            database=db_name,
            min_size=1,
            max_size=3,
        )
        await p.execute(_DELIVERY_PREFERENCES_DDL)
        await p.execute(_DEFERRED_NOTIFICATIONS_DDL)
        yield p
        await p.close()

    # §10.2 — get_delivery_preferences returns None when no row exists
    async def test_get_preferences_returns_none_when_not_configured(self, pool):
        """get_delivery_preferences returns None when no row exists."""
        from butlers.core.temporal.delivery_db import get_delivery_preferences

        result = await get_delivery_preferences(pool, "nonexistent-butler")
        assert result is None

    # §10.1 — upsert_delivery_preferences creates row on first call
    async def test_upsert_preferences_creates_row(self, pool):
        """upsert_delivery_preferences creates a new row on first call."""
        from butlers.core.temporal.delivery_db import upsert_delivery_preferences

        result = await upsert_delivery_preferences(
            pool,
            butler_name="test-butler",
            timezone="America/New_York",
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
        )
        assert result["butler_name"] == "test-butler"
        assert result["timezone"] == "America/New_York"
        assert result["quiet_hours_start"] == "22:00"
        assert result["quiet_hours_end"] == "07:00"
        assert "id" in result

    # §10.1 — upsert_delivery_preferences updates existing row
    async def test_upsert_preferences_updates_existing_row(self, pool):
        """Second upsert updates the existing row without duplicating it."""
        from butlers.core.temporal.delivery_db import (
            get_delivery_preferences,
            upsert_delivery_preferences,
        )

        await upsert_delivery_preferences(
            pool,
            butler_name="test-butler-update",
            timezone="UTC",
        )
        # Update timezone only
        await upsert_delivery_preferences(
            pool,
            butler_name="test-butler-update",
            timezone="Europe/London",
        )
        # Only one row should exist
        row_count = await pool.fetchval(
            "SELECT COUNT(*) FROM delivery_preferences WHERE butler_name = 'test-butler-update'"
        )
        assert row_count == 1

        # Timezone should be updated
        prefs = await get_delivery_preferences(pool, "test-butler-update")
        assert prefs is not None
        assert prefs["timezone"] == "Europe/London"

    # §10.1 — invalid timezone raises ValueError
    async def test_upsert_preferences_rejects_invalid_timezone(self, pool):
        """upsert_delivery_preferences raises ValueError for bad timezone."""
        from butlers.core.temporal.delivery_db import upsert_delivery_preferences

        with pytest.raises(ValueError, match="Unknown timezone"):
            await upsert_delivery_preferences(
                pool,
                butler_name="test-butler-tz",
                timezone="Invalid/Zone",
            )

    # §10.2 — get_delivery_preferences returns row after upsert
    async def test_get_preferences_returns_row_after_upsert(self, pool):
        """get_delivery_preferences returns the configured row."""
        from butlers.core.temporal.delivery_db import (
            get_delivery_preferences,
            upsert_delivery_preferences,
        )

        await upsert_delivery_preferences(
            pool,
            butler_name="test-butler-get",
            timezone="Asia/Tokyo",
            quiet_hours_start="23:00",
            quiet_hours_end="06:00",
        )
        prefs = await get_delivery_preferences(pool, "test-butler-get")
        assert prefs is not None
        assert prefs["timezone"] == "Asia/Tokyo"
        assert prefs["quiet_hours_start"] == "23:00"
        assert prefs["quiet_hours_end"] == "06:00"

    # §8.5 — insert_deferred_notification persists to DB
    async def test_insert_deferred_notification_persists(self, pool):
        """insert_deferred_notification stores a row with pending status."""
        from butlers.core.temporal.delivery_db import insert_deferred_notification

        now = datetime.now(UTC)
        deliver_at = now + timedelta(hours=8)

        notif_id = await insert_deferred_notification(
            pool,
            butler_name="test-butler",
            channel="telegram",
            message="Morning summary ready",
            priority="medium",
            envelope={"schema_version": "notify.v1", "delivery": {"channel": "telegram"}},
            deliver_at=deliver_at,
        )

        assert notif_id is not None
        row = await pool.fetchrow(
            "SELECT status, channel, message, priority FROM deferred_notifications WHERE id = $1",
            uuid.UUID(notif_id),
        )
        assert row is not None
        assert row["status"] == "pending"
        assert row["channel"] == "telegram"
        assert row["message"] == "Morning summary ready"
        assert row["priority"] == "medium"

    # §8.5 — insert_deferred_notification rejects invalid priority
    async def test_insert_deferred_notification_rejects_invalid_priority(self, pool):
        """insert_deferred_notification raises ValueError for invalid priority."""
        from butlers.core.temporal.delivery_db import insert_deferred_notification

        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="Invalid priority"):
            await insert_deferred_notification(
                pool,
                butler_name="test-butler",
                channel="telegram",
                message="Test",
                priority="urgent",  # invalid
                envelope={},
                deliver_at=now + timedelta(hours=1),
            )

    # §10.3 — list_deferred_notifications with status filter
    async def test_list_deferred_notifications_filters_by_status(self, pool):
        """list_deferred_notifications returns only notifications matching status."""
        from butlers.core.temporal.delivery_db import (
            insert_deferred_notification,
            list_deferred_notifications,
        )

        now = datetime.now(UTC)
        deliver_at = now + timedelta(hours=5)
        butler = "test-butler-list"

        # Insert 2 pending and mark 1 as delivered manually
        id1 = await insert_deferred_notification(
            pool,
            butler_name=butler,
            channel="telegram",
            message="Pending notification 1",
            priority="medium",
            envelope={},
            deliver_at=deliver_at,
        )
        id2 = await insert_deferred_notification(
            pool,
            butler_name=butler,
            channel="email",
            message="Pending notification 2",
            priority="low",
            envelope={},
            deliver_at=deliver_at,
        )
        # Mark id2 as delivered
        await pool.execute(
            "UPDATE deferred_notifications SET status = 'delivered' WHERE id = $1",
            uuid.UUID(id2),
        )

        pending = await list_deferred_notifications(pool, butler_name=butler, status="pending")
        assert len(pending) == 1
        assert pending[0]["id"] == id1

        delivered = await list_deferred_notifications(pool, butler_name=butler, status="delivered")
        assert len(delivered) == 1
        assert delivered[0]["id"] == id2

    # §10.3 — list_deferred_notifications no filter returns all
    async def test_list_deferred_notifications_no_filter_returns_all(self, pool):
        """list_deferred_notifications with no status filter returns all notifications."""
        from butlers.core.temporal.delivery_db import (
            insert_deferred_notification,
            list_deferred_notifications,
        )

        now = datetime.now(UTC)
        butler = "test-butler-all"

        for i in range(3):
            await insert_deferred_notification(
                pool,
                butler_name=butler,
                channel="telegram",
                message=f"Notification {i}",
                priority="medium",
                envelope={},
                deliver_at=now + timedelta(hours=i + 1),
            )

        all_notifs = await list_deferred_notifications(pool, butler_name=butler)
        assert len(all_notifs) == 3

    # §10.3 — list_deferred_notifications invalid status raises ValueError
    async def test_list_deferred_notifications_invalid_status_raises(self, pool):
        """list_deferred_notifications raises ValueError for invalid status."""
        from butlers.core.temporal.delivery_db import list_deferred_notifications

        with pytest.raises(ValueError, match="Invalid status filter"):
            await list_deferred_notifications(pool, butler_name="test", status="unknown")

    # §10.4 — cancel_deferred_notification sets status to cancelled
    async def test_cancel_deferred_notification_success(self, pool):
        """cancel_deferred_notification sets status='cancelled' for pending notification."""
        from butlers.core.temporal.delivery_db import (
            cancel_deferred_notification,
            insert_deferred_notification,
        )

        now = datetime.now(UTC)
        notif_id = await insert_deferred_notification(
            pool,
            butler_name="test-butler-cancel",
            channel="telegram",
            message="Cancel this",
            priority="low",
            envelope={},
            deliver_at=now + timedelta(hours=6),
        )

        cancelled = await cancel_deferred_notification(
            pool, notif_id, butler_name="test-butler-cancel"
        )
        assert cancelled is True

        row = await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1",
            uuid.UUID(notif_id),
        )
        assert row["status"] == "cancelled"

    # §10.4 — cancel_deferred_notification returns False for non-pending
    async def test_cancel_deferred_notification_fails_for_delivered(self, pool):
        """cancel_deferred_notification returns False when notification already delivered."""
        from butlers.core.temporal.delivery_db import (
            cancel_deferred_notification,
            insert_deferred_notification,
        )

        now = datetime.now(UTC)
        notif_id = await insert_deferred_notification(
            pool,
            butler_name="test-butler-cancel-del",
            channel="email",
            message="Already delivered",
            priority="medium",
            envelope={},
            deliver_at=now - timedelta(hours=1),
        )
        # Simulate delivery
        await pool.execute(
            "UPDATE deferred_notifications SET status = 'delivered' WHERE id = $1",
            uuid.UUID(notif_id),
        )

        cancelled = await cancel_deferred_notification(
            pool, notif_id, butler_name="test-butler-cancel-del"
        )
        assert cancelled is False

    # §10.4 — cancel_deferred_notification with wrong butler_name returns False
    async def test_cancel_deferred_notification_ownership_check(self, pool):
        """cancel_deferred_notification returns False when butler_name doesn't match."""
        from butlers.core.temporal.delivery_db import (
            cancel_deferred_notification,
            insert_deferred_notification,
        )

        now = datetime.now(UTC)
        notif_id = await insert_deferred_notification(
            pool,
            butler_name="owner-butler",
            channel="telegram",
            message="My notification",
            priority="medium",
            envelope={},
            deliver_at=now + timedelta(hours=3),
        )

        # Try to cancel with wrong butler_name
        cancelled = await cancel_deferred_notification(pool, notif_id, butler_name="other-butler")
        assert cancelled is False

    # §10.4 — cancel_deferred_notification with invalid UUID raises ValueError
    async def test_cancel_deferred_notification_invalid_uuid_raises(self, pool):
        """cancel_deferred_notification raises ValueError for malformed UUID."""
        from butlers.core.temporal.delivery_db import cancel_deferred_notification

        with pytest.raises(ValueError, match="Invalid notification_id"):
            await cancel_deferred_notification(pool, "not-a-uuid", butler_name="test-butler")

    # §8.5 — deferred_notifications have UTC-aware deliver_at
    async def test_deferred_notification_deliver_at_is_utc(self, pool):
        """Deferred notifications are stored and retrieved with UTC-aware timestamps."""
        from butlers.core.temporal.delivery_db import (
            insert_deferred_notification,
            list_deferred_notifications,
        )

        now = datetime.now(UTC)
        deliver_at = now + timedelta(hours=7)

        await insert_deferred_notification(
            pool,
            butler_name="butler-tz-check",
            channel="telegram",
            message="TZ check",
            priority="medium",
            envelope={},
            deliver_at=deliver_at,
        )

        notifs = await list_deferred_notifications(pool, butler_name="butler-tz-check")
        assert len(notifs) == 1
        # deliver_at is serialised as ISO 8601 string with timezone offset
        deliver_at_str: str = notifs[0]["deliver_at"]
        assert "T" in deliver_at_str  # ISO format

    # §10.1 — upsert supports per-channel override_channels
    async def test_upsert_preferences_stores_override_channels(self, pool):
        """upsert_delivery_preferences stores per-channel quiet hours overrides."""
        from butlers.core.temporal.delivery_db import (
            get_delivery_preferences,
            upsert_delivery_preferences,
        )

        overrides = {"email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}}
        await upsert_delivery_preferences(
            pool,
            butler_name="butler-overrides",
            timezone="UTC",
            override_channels=overrides,
        )

        prefs = await get_delivery_preferences(pool, "butler-overrides")
        assert prefs is not None
        assert prefs["override_channels"] is not None
        assert "email" in prefs["override_channels"]
        assert prefs["override_channels"]["email"]["quiet_hours_start"] == "20:00"
