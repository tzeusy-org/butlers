"""Tests for delivery preferences DB operations and MCP tools — condensed.

Covers:
- §8.2  notify() priority parameter validation and timezone validation
- §8.3  Quiet hours gate: defer medium/low, bypass high
- §8.5  insert_deferred_notification: persists pending row, rejects invalid priority
- §10.1 upsert_delivery_preferences: create, update, invalid timezone
- §10.2 get_delivery_preferences: None when unconfigured, row after upsert
- §10.3 list_deferred_notifications: status filter, no filter, invalid status raises
- §10.4 cancel_deferred_notification: success, not-pending, wrong owner, invalid UUID
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = [pytest.mark.unit]

docker_available = shutil.which("docker") is not None


class TestDeliveryUnit:
    def test_priority_timezone_and_deferral(self):
        """Priority set contract; timezone validation; quiet hours deferral by priority."""
        from datetime import time

        from butlers.core.temporal.delivery import should_defer_notification
        from butlers.core.temporal.delivery_db import validate_timezone

        for tz in ("UTC", "America/New_York", "Europe/Berlin", "Asia/Tokyo"):
            assert validate_timezone(tz) == tz
        for bad_tz in ("Invalid/Zone", ""):
            with pytest.raises(ValueError, match="Unknown timezone"):
                validate_timezone(bad_tz)

        prefs_batch = {
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "timezone": "UTC",
            "batch_low_priority": True,
            "batch_delivery_time": "07:00",
            "override_channels": None,
        }
        t_night = time(23, 30)
        assert should_defer_notification(priority="medium", current_time=t_night, prefs=prefs_batch)
        assert not should_defer_notification(
            priority="high", current_time=t_night, prefs=prefs_batch
        )
        prefs_no_batch = {**prefs_batch, "batch_low_priority": False}
        assert not should_defer_notification(
            priority="medium", current_time=t_night, prefs=prefs_no_batch
        )


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
    @pytest.fixture
    async def pool(self, postgres_container):
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

    async def test_upsert_and_get_delivery_preferences(self, pool):
        """upsert creates row; second upsert updates without dup; invalid tz raises; get returns row."""
        from butlers.core.temporal.delivery_db import (
            get_delivery_preferences,
            upsert_delivery_preferences,
        )

        # None before upsert
        assert await get_delivery_preferences(pool, "nonexistent-butler") is None

        # Create
        result = await upsert_delivery_preferences(
            pool,
            butler_name="test-butler",
            timezone="America/New_York",
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
        )
        assert result["butler_name"] == "test-butler" and result["timezone"] == "America/New_York"

        # Update
        await upsert_delivery_preferences(pool, butler_name="test-butler", timezone="Europe/London")
        row_count = await pool.fetchval(
            "SELECT COUNT(*) FROM delivery_preferences WHERE butler_name = 'test-butler'"
        )
        assert row_count == 1
        prefs = await get_delivery_preferences(pool, "test-butler")
        assert prefs["timezone"] == "Europe/London"

        # Invalid tz raises
        with pytest.raises(ValueError, match="Unknown timezone"):
            await upsert_delivery_preferences(
                pool, butler_name="tz-butler", timezone="Invalid/Zone"
            )

        # Per-channel overrides stored
        overrides = {"email": {"quiet_hours_start": "20:00", "quiet_hours_end": "09:00"}}
        await upsert_delivery_preferences(
            pool, butler_name="butler-overrides", timezone="UTC", override_channels=overrides
        )
        pref2 = await get_delivery_preferences(pool, "butler-overrides")
        assert pref2["override_channels"]["email"]["quiet_hours_start"] == "20:00"

    async def test_deferred_notifications_lifecycle(self, pool):
        """insert persists pending row; list filters by status; cancel manages state; invalid inputs raise."""
        from butlers.core.temporal.delivery_db import (
            cancel_deferred_notification,
            insert_deferred_notification,
            list_deferred_notifications,
        )

        now = datetime.now(UTC)
        butler = "test-butler-notif"

        # Insert + status filter
        id1 = await insert_deferred_notification(
            pool,
            butler_name=butler,
            channel="telegram",
            message="Pending 1",
            priority="medium",
            envelope={},
            deliver_at=now + timedelta(hours=5),
        )
        id2 = await insert_deferred_notification(
            pool,
            butler_name=butler,
            channel="email",
            message="Pending 2",
            priority="low",
            envelope={},
            deliver_at=now + timedelta(hours=6),
        )
        await pool.execute(
            "UPDATE deferred_notifications SET status = 'delivered' WHERE id = $1", uuid.UUID(id2)
        )

        pending = await list_deferred_notifications(pool, butler_name=butler, status="pending")
        assert len(pending) == 1 and pending[0]["id"] == id1

        delivered = await list_deferred_notifications(pool, butler_name=butler, status="delivered")
        assert len(delivered) == 1 and delivered[0]["id"] == id2

        all_notifs = await list_deferred_notifications(pool, butler_name=butler)
        assert len(all_notifs) == 2

        # Invalid priority raises
        with pytest.raises(ValueError, match="Invalid priority"):
            await insert_deferred_notification(
                pool,
                butler_name=butler,
                channel="telegram",
                message="Bad",
                priority="urgent",
                envelope={},
                deliver_at=now + timedelta(hours=1),
            )

        # Invalid status filter raises
        with pytest.raises(ValueError, match="Invalid status filter"):
            await list_deferred_notifications(pool, butler_name="test", status="unknown")

        # Cancel: success
        id3 = await insert_deferred_notification(
            pool,
            butler_name=butler,
            channel="telegram",
            message="Cancel me",
            priority="low",
            envelope={},
            deliver_at=now + timedelta(hours=8),
        )
        assert await cancel_deferred_notification(pool, id3, butler_name=butler) is True
        row = await pool.fetchrow(
            "SELECT status FROM deferred_notifications WHERE id = $1", uuid.UUID(id3)
        )
        assert row["status"] == "cancelled"

        # Cancel: already delivered → False
        assert await cancel_deferred_notification(pool, id2, butler_name=butler) is False

        # Cancel: wrong butler → False
        id4 = await insert_deferred_notification(
            pool,
            butler_name="owner",
            channel="telegram",
            message="Owned",
            priority="medium",
            envelope={},
            deliver_at=now + timedelta(hours=3),
        )
        assert await cancel_deferred_notification(pool, id4, butler_name="other") is False

        # Cancel: invalid UUID raises
        with pytest.raises(ValueError, match="Invalid notification_id"):
            await cancel_deferred_notification(pool, "not-a-uuid", butler_name=butler)
