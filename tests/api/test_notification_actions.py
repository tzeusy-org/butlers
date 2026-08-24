"""Tests for POST /api/notifications/{id}/retry and .../escalate.

These are the manual, human-triggered re-delivery actions for a notification
notify() already recorded as a terminal ``failed`` attempt (bu-ep4ks.4,
delivery-receipt spine): retry re-sends on the same channel right now;
escalate re-sends on the owner's alternate channel (telegram<->email).

Both endpoints re-invoke `deliver()` in-process (patched here so no real
routing/MCP call happens), flip the original row to `read` with a forward-
link metadata marker, and best-effort record an attention-ledger event.
"""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.briefing.cache import BriefingCache
from butlers.api.db import DatabaseManager
from butlers.api.routers.notifications import _get_db_manager as _notif_get_db
from butlers.api.routers.notifications import get_cache

pytestmark = pytest.mark.unit

NOTIF_ID = "5f2c1e2a-0000-4000-8000-000000000001"


def _row(**overrides) -> dict:
    base = {
        "id": NOTIF_ID,
        "source_butler": "finance",
        "channel": "telegram",
        "recipient": "12345",
        "message": "Your bill is due.",
        "metadata": {},
        "status": "failed",
        "error": "timeout contacting telegram API",
        "session_id": None,
        "trace_id": None,
        "created_at": "2026-07-25T10:00:00Z",
    }
    base.update(overrides)
    return base


def _owner_row(owner_id: str = "owner-001") -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(side_effect=lambda k: owner_id if k == "id" else None)
    return row


class _StatefulNotificationPool:
    """Minimal pool stub that enforces the same atomicity a real Postgres
    ``UPDATE ... WHERE status = 'failed'`` claim relies on.

    Unlike a canned ``side_effect`` list, this tracks one row's `status`
    across however many `fetchrow`/`execute` calls arrive, in whatever order
    the event loop interleaves concurrent requests -- exactly what the
    retry/escalate concurrency regression tests need: only the first request
    to reach the claim statement should see `status == 'failed'` and win.
    """

    def __init__(self, row: dict):
        self._row = dict(row)

    async def fetchrow(self, sql, *args, **kwargs):
        if "public.entities" in sql:
            return _owner_row()
        if "UPDATE notifications" in sql and "RETURNING" in sql:
            if self._row["status"] != "failed":
                return None
            self._row["status"] = "read"
            return dict(self._row)
        return dict(self._row)

    async def execute(self, sql, *args, **kwargs):
        return "UPDATE 1"


def _make_app(pool) -> object:
    app = create_app(api_key="")
    mock_db = MagicMock(spec=DatabaseManager)
    if pool is None:
        mock_db.pool.side_effect = KeyError("switchboard")
    else:
        mock_db.pool.return_value = pool
    app.dependency_overrides[_notif_get_db] = lambda: mock_db
    app.dependency_overrides[get_cache] = lambda: BriefingCache(ttl_seconds=300)
    return app


class TestRetryNotification:
    async def test_pool_unavailable_returns_503(self):
        app = _make_app(pool=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")
        assert resp.status_code == 503

    async def test_not_found_returns_404(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")
        assert resp.status_code == 404

    async def test_non_failed_status_returns_409(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_row(status="sent"))
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")
        assert resp.status_code == 409

    async def test_rejection_detail_is_operator_readable_and_names_the_status(self):
        """The 409 ``detail`` is the operator-facing rejection copy.

        NotificationsPage renders it verbatim as the toast description when a
        stale list still offers "Retry" on a row another tab already actioned
        (bu-6t8ix.1), so a rejection that only carried a status code would
        surface as an unexplained failure. It must say why, and never leak a
        redelivery having happened -- ``deliver()`` is not even patched here,
        so any real send attempt would raise instead of silently succeeding.
        """
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_row(status="read"))
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "Only failed notifications can be retried" in detail
        assert "'read'" in detail
        pool.execute.assert_not_awaited()

    async def test_successful_retry_marks_original_read_and_returns_new_attempt(self):
        pool = AsyncMock()
        # fetchrow order: initial status read, atomic claim, owner lookup
        # (for cache invalidation).
        pool.fetchrow = AsyncMock(side_effect=[_row(), _row(status="read"), _owner_row()])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        app = _make_app(pool)

        deliver_mock = AsyncMock(
            return_value={
                "notification_id": "299b20a0-a759-4340-82fa-98f0c8334dd4",
                "status": "sent",
            }
        )
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                deliver_mock,
            ),
            patch(
                "butlers.api.routers.notifications.record_attention_event",
                AsyncMock(return_value="ledger-row-id"),
            ) as ledger_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["status"] == "sent"
        assert body["channel"] == "telegram"
        assert body["new_notification_id"] is not None

        # deliver() was re-invoked with the original envelope's channel/message
        # and a retried_from lineage marker in metadata.
        deliver_call = deliver_mock.call_args
        assert deliver_call.kwargs["source_butler"] == "finance"
        envelope = deliver_call.kwargs["notify_request"]
        assert envelope["delivery"]["channel"] == "telegram"
        assert deliver_call.kwargs["metadata"] == {"retried_from": NOTIF_ID}

        # Original row flipped to read with a forward-link marker.
        update_call = pool.execute.call_args
        assert "SET status = 'read'" in update_call.args[0]
        assert str(update_call.args[1]) == NOTIF_ID

        ledger_mock.assert_awaited_once()
        assert ledger_mock.call_args.kwargs["outcome"] == "delivered"
        assert (
            ledger_mock.call_args.kwargs["notification_ref"]
            == "299b20a0-a759-4340-82fa-98f0c8334dd4"
        )

    async def test_retry_that_fails_again_still_marks_original_actioned(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[_row(), _row(status="read"), _owner_row()])
        pool.execute = AsyncMock(return_value="UPDATE 1")
        app = _make_app(pool)

        deliver_mock = AsyncMock(
            return_value={
                "notification_id": "073a4461-240f-47d9-87e6-6e677537e4ac",
                "status": "failed",
                "error": "still unreachable",
                "error_class": "target_unavailable",
            }
        )
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                deliver_mock,
            ),
            patch(
                "butlers.api.routers.notifications.record_attention_event",
                AsyncMock(return_value="ledger-row-id"),
            ) as ledger_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["status"] == "failed"
        assert body["error"] == "still unreachable"

        # The original is still flipped to read (a human already acted on
        # it) even though the retry itself failed too -- the new failure is
        # its own actionable row, not a reason to leave the original stuck.
        update_call = pool.execute.call_args
        assert "SET status = 'read'" in update_call.args[0]
        ledger_mock.assert_awaited_once()
        assert ledger_mock.call_args.kwargs["outcome"] == "failed"

    async def test_retry_reconstructs_envelope_from_stored_metadata_when_present(self):
        stored_envelope = {
            "schema_version": "notify.v1",
            "origin_butler": "finance",
            "delivery": {
                "intent": "send",
                "channel": "telegram",
                "recipient": "999",
                "message": "Different message than the column snapshot.",
            },
        }
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _row(metadata={"notify_request": stored_envelope}),
                _row(status="read"),
                _owner_row(),
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")
        app = _make_app(pool)

        deliver_mock = AsyncMock(return_value={"notification_id": "x", "status": "sent"})
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                deliver_mock,
            ),
            patch(
                "butlers.api.routers.notifications.record_attention_event",
                AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                await client.post(f"/api/notifications/{NOTIF_ID}/retry")

        envelope = deliver_mock.call_args.kwargs["notify_request"]
        assert envelope["delivery"]["recipient"] == "999"
        assert envelope["delivery"]["message"] == "Different message than the column snapshot."


class TestRetryEscalateConcurrencyGuard:
    """Regression coverage for the check-then-act double-delivery race:
    concurrent/replayed retry (or escalate) calls must never both redeliver,
    and a finalize failure after a successful delivery must not leave the
    row re-retryable (bu-u28pq review thread on PR #3579).
    """

    async def test_concurrent_retries_exactly_one_delivers_one_gets_409(self):
        pool = _StatefulNotificationPool(_row())
        app = _make_app(pool)

        deliver_mock = AsyncMock(
            return_value={
                "notification_id": "6b1c2e3a-0000-4000-8000-0000000000aa",
                "status": "sent",
            }
        )
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                deliver_mock,
            ),
            patch(
                "butlers.api.routers.notifications.record_attention_event",
                AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp1, resp2 = await asyncio.gather(
                    client.post(f"/api/notifications/{NOTIF_ID}/retry"),
                    client.post(f"/api/notifications/{NOTIF_ID}/retry"),
                )

        statuses = sorted([resp1.status_code, resp2.status_code])
        assert statuses == [200, 409], (
            "exactly one concurrent retry should succeed and the other should "
            f"lose the atomic claim with 409; got {statuses}"
        )
        deliver_mock.assert_awaited_once()

        # The loser can be rejected either at the early pre-check (if its
        # _fetch_notification_row read happens after the winner's claim
        # already flipped the row) or at the atomic claim itself (if both
        # reads land before either claim) -- both are honest 409s naming the
        # notification as no longer retryable, never a redelivery.
        loser = resp1 if resp1.status_code == 409 else resp2
        assert "retried" in loser.json()["detail"]

    async def test_concurrent_escalates_exactly_one_delivers_one_gets_409(self):
        pool = _StatefulNotificationPool(_row(channel="telegram", recipient="12345"))
        app = _make_app(pool)

        deliver_mock = AsyncMock(
            return_value={
                "notification_id": "6b1c2e3a-0000-4000-8000-0000000000aa",
                "status": "sent",
            }
        )
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                deliver_mock,
            ),
            patch(
                "butlers.api.routers.notifications.resolve_owner_entity_info",
                AsyncMock(return_value="owner@example.com"),
            ),
            patch(
                "butlers.api.routers.notifications.record_attention_event",
                AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp1, resp2 = await asyncio.gather(
                    client.post(f"/api/notifications/{NOTIF_ID}/escalate"),
                    client.post(f"/api/notifications/{NOTIF_ID}/escalate"),
                )

        statuses = sorted([resp1.status_code, resp2.status_code])
        assert statuses == [200, 409]
        deliver_mock.assert_awaited_once()

    async def test_finalize_failure_after_delivery_leaves_row_unretryable(self):
        # First request: status read sees 'failed', claim succeeds (flips to
        # 'read'), _redeliver() sends successfully, then
        # _finalize_manual_action's metadata-merge UPDATE raises (simulating
        # a transient DB error immediately after a real, successful send).
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=[_row(), _row(status="read")])
        pool.execute = AsyncMock(side_effect=RuntimeError("db blip"))
        app = _make_app(pool)

        deliver_mock = AsyncMock(
            return_value={
                "notification_id": "6b1c2e3a-0000-4000-8000-0000000000aa",
                "status": "sent",
            }
        )
        with patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            deliver_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/notifications/{NOTIF_ID}/retry")

        # The finalize failure surfaces as a 500 (unhandled exception) -- that
        # part of the contract is unchanged. What matters is that the row was
        # already claimed (flipped off 'failed') *before* finalize ran, so
        # the delivery itself only happened once.
        assert resp.status_code == 500
        deliver_mock.assert_awaited_once()

        # A client retry after the 500 (the exact scenario the review thread
        # flagged) hits a row that is no longer 'failed' -- the claim from
        # the first request already moved it to 'read', regardless of
        # finalize's outcome. Simulate that DB state directly: it must
        # short-circuit with 409 and never call deliver() again.
        pool_after_crash = AsyncMock()
        pool_after_crash.fetchrow = AsyncMock(return_value=_row(status="read"))
        app_after_crash = _make_app(pool_after_crash)

        deliver_mock_retry = AsyncMock()
        with patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            deliver_mock_retry,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_after_crash), base_url="http://test"
            ) as client:
                resp2 = await client.post(f"/api/notifications/{NOTIF_ID}/retry")

        assert resp2.status_code == 409
        deliver_mock_retry.assert_not_awaited()


class TestEscalateNotification:
    async def test_unsupported_channel_returns_422(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_row(channel="whatsapp"))
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/notifications/{NOTIF_ID}/escalate")
        assert resp.status_code == 422

    async def test_no_owner_alternate_channel_returns_422(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_row(channel="telegram"))
        app = _make_app(pool)
        with patch(
            "butlers.api.routers.notifications.resolve_owner_entity_info",
            AsyncMock(return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/notifications/{NOTIF_ID}/escalate")
        assert resp.status_code == 422

    async def test_non_failed_status_returns_409(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=_row(status="read"))
        app = _make_app(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(f"/api/notifications/{NOTIF_ID}/escalate")
        assert resp.status_code == 409

    async def test_successful_escalate_swaps_channel_and_recipient(self):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _row(channel="telegram", recipient="12345"),
                _row(status="read"),
                _owner_row(),
            ]
        )
        pool.execute = AsyncMock(return_value="UPDATE 1")
        app = _make_app(pool)

        deliver_mock = AsyncMock(
            return_value={
                "notification_id": "fa29d9a3-46d6-4b2e-89d0-04e56d3673de",
                "status": "sent",
            }
        )
        with (
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                deliver_mock,
            ),
            patch(
                "butlers.api.routers.notifications.resolve_owner_entity_info",
                AsyncMock(return_value="owner@example.com"),
            ) as resolve_mock,
            patch(
                "butlers.api.routers.notifications.record_attention_event",
                AsyncMock(return_value=None),
            ) as ledger_mock,
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/api/notifications/{NOTIF_ID}/escalate")

        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["channel"] == "email"
        assert body["status"] == "sent"

        resolve_mock.assert_awaited_once_with(ANY, "email")
        envelope = deliver_mock.call_args.kwargs["notify_request"]
        assert envelope["delivery"]["channel"] == "email"
        assert envelope["delivery"]["recipient"] == "owner@example.com"
        assert deliver_mock.call_args.kwargs["metadata"] == {
            "escalated_from": NOTIF_ID,
            "escalated_from_channel": "telegram",
        }

        update_call = pool.execute.call_args
        assert "SET status = 'read'" in update_call.args[0]
        ledger_mock.assert_awaited_once()
        assert ledger_mock.call_args.kwargs["channel"] == "email"
