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
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

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

    async def test_get_delivery_preferences_missing_table_returns_none(self):
        """Older schema-scoped DBs may not have delivery tables yet."""
        import asyncpg

        from butlers.core.temporal.delivery_db import get_delivery_preferences

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=asyncpg.exceptions.UndefinedTableError(
                'relation "delivery_preferences" does not exist'
            )
        )

        assert await get_delivery_preferences(pool, "chronicler") is None


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.mark.integration
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.asyncio(loop_scope="session")
class TestDeliveryPreferencesDB:
    @pytest.fixture
    async def pool(self, migrated_db_url: str):
        """Return an asyncpg pool with delivery tables cleared between tests."""
        p = await asyncpg.create_pool(
            migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
        )
        await p.execute("TRUNCATE deferred_notifications, delivery_preferences CASCADE")
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

    async def test_cancel_pending_matching_line_boundaries_and_supersede(self, pool):
        """bu-id0fh: cancel_pending_notifications_matching_line supersedes prior
        pending retry envelopes sharing a state-independent dedup token, with
        line-boundary anchoring so a shorter key never collides with a longer
        sibling, and only PENDING rows for the given butler are touched."""
        from butlers.core.temporal.delivery_db import (
            cancel_pending_notifications_matching_line,
            insert_deferred_notification,
        )
        from butlers.jobs.secrets_lifecycle import _focus_fragment

        now = datetime.now(UTC)
        spotify = _focus_fragment("s:SPOTIFY")  # /secrets?focus=s%3ASPOTIFY
        access = _focus_fragment("s:SPOTIFY_ACCESS_TOKEN")  # longer sibling
        assert spotify in access, "test premise: one fragment is a prefix of the other"
        base = "http://localhost:41200"

        async def _insert(butler, message, deliver_offset_h=1):
            return await insert_deferred_notification(
                pool,
                butler_name=butler,
                channel="telegram",
                message=message,
                priority="medium",
                envelope={"schema_version": "notify.v1"},
                deliver_at=now + timedelta(hours=deliver_offset_h),
            )

        # Fragment at end-of-message (non-OAuth credential shape).
        id_eos = await _insert("switchboard", f"Credential 'SPOTIFY' has expired.\n{base}{spotify}")
        # Fragment mid-message, followed by a newline (OAuth re-authorize line).
        id_mid = await _insert(
            "switchboard",
            f"Credential 'SPOTIFY' is expiring soon.\n{base}{spotify}\nRe-authorize: {base}/x",
        )
        # Longer sibling — MUST NOT be superseded by the shorter token.
        id_sibling = await _insert(
            "switchboard", f"Credential 'SPOTIFY_ACCESS_TOKEN' has expired.\n{base}{access}"
        )
        # A different butler's row with the same token — butler isolation.
        id_other = await _insert("finance", f"Credential 'SPOTIFY' has expired.\n{base}{spotify}")
        # An already-delivered switchboard row — only pending rows are cancellable.
        id_delivered = await _insert("switchboard", f"done.\n{base}{spotify}")
        await pool.execute(
            "UPDATE deferred_notifications SET status = 'delivered' WHERE id = $1",
            uuid.UUID(id_delivered),
        )

        cancelled = await cancel_pending_notifications_matching_line(
            pool, butler_name="switchboard", line_token=spotify
        )
        assert cancelled == 2  # id_eos + id_mid only

        async def _status(nid):
            return await pool.fetchval(
                "SELECT status FROM deferred_notifications WHERE id = $1", uuid.UUID(nid)
            )

        assert await _status(id_eos) == "cancelled"
        assert await _status(id_mid) == "cancelled"
        assert await _status(id_sibling) == "pending", "longer sibling must survive"
        assert await _status(id_other) == "pending", "other butler must be isolated"
        assert await _status(id_delivered) == "delivered", "delivered rows untouched"

        # Empty token is a no-op — never a mass-cancel footgun.
        assert (
            await cancel_pending_notifications_matching_line(
                pool, butler_name="switchboard", line_token=""
            )
            == 0
        )
        assert await _status(id_sibling) == "pending"

    async def test_multi_tick_outage_bounds_pending_to_one_then_zero(self, pool):
        """bu-id0fh reproduction: emulate the secrets_lifecycle enqueue loop over
        a persistent multi-tick outage. Each tick supersedes-then-inserts, so the
        queue holds exactly ONE pending envelope (carrying the latest state) no
        matter how many ticks the outage spans — not N. On recovery the direct
        delivery's own supersede drains it, leaving zero: one delivery, not N+1."""
        from butlers.core.temporal.delivery_db import (
            cancel_pending_notifications_matching_line,
            insert_deferred_notification,
        )
        from butlers.jobs.secrets_lifecycle import _focus_fragment

        now = datetime.now(UTC)
        marker = _focus_fragment("s:SPOTIFY_ACCESS_TOKEN")
        base = "http://localhost:41200"
        states = ["expiring", "expiring", "failing", "expired"]  # incl. a mid-outage change

        async def _pending_count():
            return await pool.fetchval(
                "SELECT COUNT(*) FROM deferred_notifications "
                "WHERE butler_name = 'switchboard' AND status = 'pending'"
            )

        for tick, state in enumerate(states):
            # supersede then insert, mirroring _enqueue_delivery_retry
            await cancel_pending_notifications_matching_line(
                pool, butler_name="switchboard", line_token=marker
            )
            await insert_deferred_notification(
                pool,
                butler_name="switchboard",
                channel="telegram",
                message=f"Credential 'SPOTIFY_ACCESS_TOKEN' is now '{state}'.\n{base}{marker}",
                priority="medium",
                envelope={"schema_version": "notify.v1"},
                deliver_at=now + timedelta(minutes=30),
            )
            assert await _pending_count() == 1, f"tick {tick}: queue must never exceed one pending"

        # The single surviving envelope carries the LATEST state (expired).
        surviving = await pool.fetchval(
            "SELECT message FROM deferred_notifications "
            "WHERE butler_name = 'switchboard' AND status = 'pending'"
        )
        assert "'expired'" in surviving

        # Recovery: the direct delivery's post-success supersede drains it.
        drained = await cancel_pending_notifications_matching_line(
            pool, butler_name="switchboard", line_token=marker
        )
        assert drained == 1
        assert await _pending_count() == 0
