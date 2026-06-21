"""Unit tests: insight delivery cycle wiring in scheduled_jobs.

Verifies that ``_run_switchboard_insight_delivery_cycle_job`` wires the
production ``notify_fn`` into ``delivery_cycle`` (i.e. notify_fn is NOT None)
so that the v1 proactive-insight promise is actually met.

Coverage goals (per bu-dl98i.3.1 acceptance):
- A non-None notify_fn is passed to delivery_cycle → delivery is not skipped.
- The wired notify_fn resolves the owner's telegram chat_id and calls deliver().
- When deliver() returns status="failed", the fn translates to status="error"
  so the broker's failure-detection fires correctly.
- When no telegram chat_id is configured, the fn returns status="error".
- Channel selection from candidates: majority vote; None → default to telegram.

All tests are unit-level (no Docker required) — pool, deliver(), and
resolve_owner_entity_info() are all mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> AsyncMock:
    """Return a minimal asyncpg.Pool mock."""
    return AsyncMock()


class _FakeRecord:
    """Minimal asyncpg.Record-alike that supports dict(row) via keys() + __getitem__."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def keys(self) -> Any:
        return self._data.keys()

    def __iter__(self) -> Any:
        return iter(self._data)


# ---------------------------------------------------------------------------
# Tests: _build_switchboard_insight_notify_fn
# ---------------------------------------------------------------------------


class TestBuildSwitchboardInsightNotifyFn:
    """Tests for the notify_fn factory used by the insight delivery job."""

    @pytest.mark.asyncio
    async def test_telegram_delivery_success(self):
        """notify_fn resolves telegram chat_id and calls deliver(); returns deliver result."""
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        deliver_return = {"status": "sent", "notification_id": "abc-123"}

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(return_value="12345678"),
            ) as mock_resolve,
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value=deliver_return),
            ) as mock_deliver,
        ):
            result = await notify_fn("Test insight", {"channel": "telegram", "intent": "insight"})

        assert result == deliver_return
        # Must resolve the deliverable numeric chat id, not the @username handle.
        mock_resolve.assert_awaited_once_with(pool, "telegram_chat_id")
        mock_deliver.assert_awaited_once()
        call_kwargs = mock_deliver.call_args
        assert call_kwargs.kwargs["channel"] == "telegram"
        assert call_kwargs.kwargs["message"] == "Test insight"
        assert call_kwargs.kwargs["recipient"] == "12345678"
        assert call_kwargs.kwargs["source_butler"] == "switchboard"

    @pytest.mark.asyncio
    async def test_failed_status_translated_to_error(self):
        """deliver() returning status='failed' is translated to status='error' for the broker."""
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        deliver_failed = {
            "status": "failed",
            "error": "Telegram unavailable",
            "notification_id": "x",
        }

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(return_value="12345"),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value=deliver_failed),
            ),
        ):
            result = await notify_fn("msg", {"channel": "telegram"})

        assert result["status"] == "error"
        assert "Telegram unavailable" in result["error"]

    @pytest.mark.asyncio
    async def test_no_telegram_chat_id_returns_error(self):
        """When no telegram_chat_id is configured, notify_fn returns status='error'."""
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        with patch(
            "butlers.credential_store.resolve_owner_entity_info",
            new=AsyncMock(return_value=None),
        ) as mock_resolve:
            result = await notify_fn("msg", {"channel": "telegram"})

        # Both the numeric chat id and the username fallback are exhausted.
        from unittest.mock import call

        assert mock_resolve.await_args_list == [
            call(pool, "telegram_chat_id"),
            call(pool, "telegram"),
        ]
        assert result["status"] == "error"
        assert "telegram" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_email_channel_resolves_email_recipient(self):
        """When channel='email', notify_fn resolves 'email' info_type."""
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(return_value="owner@example.com"),
            ) as mock_resolve,
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value={"status": "sent"}),
            ) as mock_deliver,
        ):
            result = await notify_fn("Email insight", {"channel": "email"})

        # Verify we looked up "email" info type
        mock_resolve.assert_awaited_once_with(pool, "email")
        assert result["status"] == "sent"
        assert mock_deliver.call_args.kwargs["channel"] == "email"
        assert mock_deliver.call_args.kwargs["recipient"] == "owner@example.com"

    @pytest.mark.asyncio
    async def test_telegram_prefers_numeric_chat_id_over_username(self):
        """Regression (bu-1q9wh): when both the numeric chat id and the @username
        handle are stored, notify_fn must deliver to the numeric chat id.

        Sending to a bare @username is undeliverable (Telegram addresses private
        users by numeric id only) and trips the approval gate's owner-primacy
        check, parking the owner's own notifications forever.
        """
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        async def fake_resolve(_pool, info_type):
            return {"telegram_chat_id": "206570151", "telegram": "@Tzeusy"}.get(info_type)

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(side_effect=fake_resolve),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value={"status": "sent"}),
            ) as mock_deliver,
        ):
            await notify_fn("msg", {"channel": "telegram"})

        assert mock_deliver.call_args.kwargs["recipient"] == "206570151"

    @pytest.mark.asyncio
    async def test_telegram_falls_back_to_username_when_no_chat_id(self):
        """When only the @username handle is stored (no numeric chat id), notify_fn
        falls back to it rather than failing outright.
        """
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        async def fake_resolve(_pool, info_type):
            return {"telegram": "@Tzeusy"}.get(info_type)

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(side_effect=fake_resolve),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value={"status": "sent"}),
            ) as mock_deliver,
        ):
            await notify_fn("msg", {"channel": "telegram"})

        assert mock_deliver.call_args.kwargs["recipient"] == "@Tzeusy"

    @pytest.mark.asyncio
    async def test_unsupported_channel_falls_back_to_telegram(self):
        """Unknown channel falls back to telegram with a warning."""
        from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

        pool = _make_mock_pool()
        notify_fn = _build_switchboard_insight_notify_fn(pool)

        with (
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(return_value="55554444"),
            ) as mock_resolve,
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value={"status": "sent"}),
            ) as mock_deliver,
        ):
            await notify_fn("msg", {"channel": "whatsapp"})

        # Should have fallen back to telegram (resolving the numeric chat id).
        mock_resolve.assert_awaited_once_with(pool, "telegram_chat_id")
        assert mock_deliver.call_args.kwargs["channel"] == "telegram"


# ---------------------------------------------------------------------------
# Tests: _run_switchboard_insight_delivery_cycle_job wiring
# ---------------------------------------------------------------------------


class TestSwitchboardInsightDeliveryJobWiring:
    """Verify the scheduled job wires a real notify_fn into delivery_cycle."""

    @pytest.mark.asyncio
    async def test_job_passes_non_none_notify_fn_to_delivery_cycle(self):
        """The scheduled job must NOT pass notify_fn=None to delivery_cycle."""
        from butlers.scheduled_jobs import _run_switchboard_insight_delivery_cycle_job

        pool = _make_mock_pool()
        captured: dict[str, Any] = {}

        async def fake_delivery_cycle(
            p: Any,
            *,
            notify_fn: Any = None,
            now: Any = None,
        ) -> dict[str, Any]:
            captured["notify_fn"] = notify_fn
            return {"skipped": False, "delivered": [], "expired": 0, "effective_budget": 1}

        with patch(
            "butlers.tools.switchboard.insight.broker.delivery_cycle",
            side_effect=fake_delivery_cycle,
        ):
            await _run_switchboard_insight_delivery_cycle_job(pool, None)

        assert "notify_fn" in captured, "delivery_cycle was not called"
        assert captured["notify_fn"] is not None, (
            "notify_fn must NOT be None — delivery would be skipped"
        )
        assert callable(captured["notify_fn"]), "notify_fn must be callable"

    @pytest.mark.asyncio
    async def test_job_delivers_when_candidate_exists(self):
        """End-to-end: a pending candidate is delivered, not skipped, when notify_fn is wired."""
        from butlers.scheduled_jobs import _run_switchboard_insight_delivery_cycle_job

        pool = _make_mock_pool()
        notify_calls: list[tuple[str, dict]] = []

        async def fake_delivery_cycle(
            p: Any,
            *,
            notify_fn: Any = None,
            now: Any = None,
        ) -> dict[str, Any]:
            # Simulate actual delivery by calling notify_fn
            assert notify_fn is not None
            result = await notify_fn(
                "Daily Insights (1):\n1. [Health] Test insight",
                {
                    "insight_count": 1,
                    "insight_ids": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"],
                    "intent": "insight",
                    "channel": None,
                },
            )
            notify_calls.append(("called", result))
            return {"skipped": False, "delivered": ["aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"]}

        with (
            patch(
                "butlers.tools.switchboard.insight.broker.delivery_cycle",
                side_effect=fake_delivery_cycle,
            ),
            patch(
                "butlers.credential_store.resolve_owner_entity_info",
                new=AsyncMock(return_value="12345678"),
            ),
            patch(
                "butlers.tools.switchboard.notification.deliver.deliver",
                new=AsyncMock(return_value={"status": "sent", "notification_id": "n-1"}),
            ),
        ):
            result = await _run_switchboard_insight_delivery_cycle_job(pool, None)

        assert not result.get("skipped"), "Delivery should not be skipped when notify_fn is wired"
        assert len(notify_calls) == 1, "notify_fn should have been called once"
        _, notify_result = notify_calls[0]
        assert notify_result.get("status") == "sent"


# ---------------------------------------------------------------------------
# Tests: broker channel selection (majority vote in notify_metadata)
# ---------------------------------------------------------------------------


class TestBrokerChannelSelectionInNotifyMetadata:
    """Verify the broker computes the majority delivery channel and includes it in metadata."""

    @pytest.mark.parametrize(
        ("channels", "expected_channel"),
        [
            # 2 telegram, 1 email → majority is telegram
            (["telegram", "telegram", "email"], "telegram"),
            # all-None candidate set → no candidate specifies a channel, so the
            # broker must yield channel=None (notify_fn resolves the owner's
            # primary channel). Guards against a crash on empty Counter input or
            # emitting a non-None channel for an all-None set.
            ([None, None, None], None),
        ],
    )
    @pytest.mark.asyncio
    async def test_majority_channel_for_digest(self, channels, expected_channel):
        """The broker computes meta['channel'] from candidate channels:
        majority vote when present, None when no candidate specifies one.
        """
        from butlers.tools.switchboard.insight.broker import delivery_cycle  # noqa: PLC0415

        captured_metadata: list[dict] = []

        async def _capture_notify(message: str, metadata: dict) -> dict:
            captured_metadata.append(dict(metadata))
            return {"status": "sent"}

        import asyncpg  # noqa: PLC0415

        mock_pool = AsyncMock(spec=asyncpg.Pool)

        cid1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        cid2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        cid3 = "cccccccc-cccc-cccc-cccc-cccccccccccc"

        candidates = [
            {
                "id": cid1,
                "origin_butler": "health",
                "priority": 80,
                "category": "test",
                "dedup_key": "health:test:a:2026",
                "cooldown_days": None,
                "message": "Insight 1",
                "channel": channels[0],
                "metadata": None,
            },
            {
                "id": cid2,
                "origin_butler": "finance",
                "priority": 70,
                "category": "test",
                "dedup_key": "finance:test:b:2026",
                "cooldown_days": None,
                "message": "Insight 2",
                "channel": channels[1],
                "metadata": None,
            },
            {
                "id": cid3,
                "origin_butler": "relationship",
                "priority": 60,
                "category": "test",
                "dedup_key": "relationship:test:c:2026",
                "cooldown_days": None,
                "message": "Insight 3",
                "channel": channels[2],
                "metadata": None,
            },
        ]
        all_ids = [cid1, cid2, cid3]

        with (
            patch(
                "butlers.tools.switchboard.insight.broker.get_insight_settings",
                new=AsyncMock(
                    return_value={
                        "verbosity": "normal",  # budget=3
                        "custom_budget": None,
                        "quiet_start": None,
                        "quiet_end": None,
                        "quiet_timezone": None,
                    }
                ),
            ),
            patch("butlers.tools.switchboard.insight.broker._is_quiet_hours", return_value=False),
            patch(
                "butlers.tools.switchboard.insight.broker.expire_candidates",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.filter_by_cooldown",
                new=AsyncMock(return_value=all_ids),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.deduplicate_candidates",
                new=AsyncMock(return_value=all_ids),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.compute_effective_budget",
                new=AsyncMock(return_value=3),
            ),
            patch("butlers.tools.switchboard.insight.broker.record_cooldowns", new=AsyncMock()),
            patch(
                "butlers.tools.switchboard.insight.broker.record_engagement_rows", new=AsyncMock()
            ),
            patch("butlers.tools.switchboard.insight.broker.cleanup_old_rows", new=AsyncMock()),
            patch(
                "butlers.tools.switchboard.insight.broker.check_total_disengagement_auto_off",
                new=AsyncMock(return_value=False),
            ),
        ):

            async def mock_fetch(query: str, *args: Any) -> list:
                if "insight_candidates" in query and "LIMIT" not in query:
                    return [_FakeRecord({"id": c["id"]}) for c in candidates]
                if "insight_candidates" in query and "LIMIT" in query:
                    return [_FakeRecord(c) for c in candidates]
                return []

            mock_pool.fetch = mock_fetch
            mock_pool.execute = AsyncMock(return_value="UPDATE 3")
            mock_pool.executemany = AsyncMock()

            await delivery_cycle(mock_pool, notify_fn=_capture_notify)

        assert len(captured_metadata) >= 1, "notify_fn should have been called"
        meta = captured_metadata[0]
        assert meta.get("channel") == expected_channel, (
            f"Expected channel {expected_channel!r}, got {meta.get('channel')!r}"
        )
