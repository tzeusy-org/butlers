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

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from butlers.testing.fake_pool import AsyncCtx
from butlers.testing.records import row

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_PINNED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool() -> AsyncMock:
    """Return a minimal asyncpg.Pool mock."""
    return AsyncMock()


class _FakeConn:
    """Minimal connection stand-in for the post-delivery bookkeeping transaction.

    delivery_cycle()'s deliver_success path acquires a connection and marks
    candidates delivered on it directly; record_cooldowns()/
    record_engagement_rows() are patched out wholesale in these tests, so this
    only needs to support conn.execute() and conn.transaction().
    """

    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="UPDATE 3")

    def transaction(self) -> AsyncCtx:
        return AsyncCtx(None)


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

        Sending to a bare @username is undeliverable because Telegram addresses
        private users by numeric id only; choosing the numeric endpoint avoids
        provider rejection for an otherwise owner-directed notification.
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
            daily_hold_mode: bool = False,
            amend_fn: Any = None,
        ) -> dict[str, Any]:
            captured["notify_fn"] = notify_fn
            captured["amend_fn"] = amend_fn
            captured["daily_hold_mode"] = daily_hold_mode
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
        assert callable(captured["amend_fn"]), "amend_fn must be wired for in-place amendment"
        assert captured["daily_hold_mode"] is True, (
            "the daily job must pass daily_hold_mode=True (bu-ep4ks.9 slice 5) "
            "or the windowed cron degenerates into a plain fixed-time cycle"
        )

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
            daily_hold_mode: bool = False,
            amend_fn: Any = None,
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
# Tests: _run_switchboard_insight_urgent_subcycle_job wiring (bu-o8233)
# ---------------------------------------------------------------------------


class TestSwitchboardInsightUrgentSubcycleJobWiring:
    """Verify the hourly urgent-subcycle job wires notify_fn AND urgent_only=True."""

    @pytest.mark.asyncio
    async def test_job_passes_urgent_only_true_and_non_none_notify_fn(self):
        """The scheduled job must call delivery_cycle(urgent_only=True, notify_fn=...)."""
        from butlers.scheduled_jobs import _run_switchboard_insight_urgent_subcycle_job

        pool = _make_mock_pool()
        captured: dict[str, Any] = {}

        async def fake_delivery_cycle(
            p: Any,
            *,
            notify_fn: Any = None,
            now: Any = None,
            urgent_only: bool = False,
            amend_fn: Any = None,
        ) -> dict[str, Any]:
            captured["notify_fn"] = notify_fn
            captured["amend_fn"] = amend_fn
            captured["urgent_only"] = urgent_only
            return {"skipped": False, "delivered": [], "expired": 0, "effective_budget": 0}

        with patch(
            "butlers.tools.switchboard.insight.broker.delivery_cycle",
            side_effect=fake_delivery_cycle,
        ):
            await _run_switchboard_insight_urgent_subcycle_job(pool, None)

        assert "notify_fn" in captured, "delivery_cycle was not called"
        assert captured["notify_fn"] is not None, (
            "notify_fn must NOT be None — delivery would be skipped"
        )
        assert callable(captured["notify_fn"]), "notify_fn must be callable"
        assert callable(captured["amend_fn"]), "amend_fn must be wired for in-place amendment"
        assert captured["urgent_only"] is True, (
            "the hourly job must pass urgent_only=True or it degenerates into "
            "a second daily-budgeted cycle"
        )

    @pytest.mark.asyncio
    async def test_job_uses_same_notify_fn_builder_as_daily_job(self):
        """The urgent subcycle shares the exact same production notify_fn factory
        as the daily cycle — same channel resolution, same delivery path."""
        from butlers.scheduled_jobs import (
            _build_switchboard_insight_notify_fn,
            _run_switchboard_insight_urgent_subcycle_job,
        )

        pool = _make_mock_pool()

        with (
            patch(
                "butlers.scheduled_jobs._build_switchboard_insight_notify_fn",
                wraps=_build_switchboard_insight_notify_fn,
            ) as wrapped_builder,
            patch(
                "butlers.tools.switchboard.insight.broker.delivery_cycle",
                new=AsyncMock(return_value={"skipped": True}),
            ),
        ):
            await _run_switchboard_insight_urgent_subcycle_job(pool, None)

        wrapped_builder.assert_called_once_with(pool)


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
                "expires_at": _PINNED_NOW + timedelta(days=2),
                "created_at": _PINNED_NOW,
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
                "expires_at": _PINNED_NOW + timedelta(days=2),
                "created_at": _PINNED_NOW,
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
                "expires_at": _PINNED_NOW + timedelta(days=2),
                "created_at": _PINNED_NOW,
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
                    }
                ),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.get_approvals_policy_quiet_hours",
                new=AsyncMock(
                    return_value={
                        "quiet_start_hour": None,
                        "quiet_end_hour": None,
                        "timezone": "UTC",
                    }
                ),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.is_policy_quiet_now", return_value=False
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.expire_candidates",
                new=AsyncMock(return_value=0),
            ),
            # The mock pool answers every fetch() with candidate rows, which the
            # premise-revalidation and fold-line queries cannot read.
            patch(
                "butlers.tools.switchboard.insight.broker._revalidate_premises",
                new=AsyncMock(return_value=({}, 0)),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker._pending_fold_lines",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.filter_by_cooldown",
                new=AsyncMock(return_value=all_ids),
            ),
            patch(
                "butlers.tools.switchboard.insight.broker.deduplicate_candidates",
                new=AsyncMock(return_value=all_ids),
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
                if "SELECT id, origin_butler" in query:
                    return [row(**c) for c in candidates]
                if "insight_candidates" in query:
                    return [row(**{"id": c["id"]}) for c in candidates]
                return []

            mock_pool.fetch = mock_fetch
            mock_pool.execute = AsyncMock(return_value="UPDATE 3")
            mock_pool.executemany = AsyncMock()
            mock_pool.acquire = lambda: AsyncCtx(_FakeConn())

            await delivery_cycle(mock_pool, notify_fn=_capture_notify, now=_PINNED_NOW)

        assert len(captured_metadata) >= 1, "notify_fn should have been called"
        meta = captured_metadata[0]
        assert meta.get("channel") == expected_channel, (
            f"Expected channel {expected_channel!r}, got {meta.get('channel')!r}"
        )


async def test_actual_insight_notifier_preserves_terminal_partial_flag():
    from butlers.scheduled_jobs import _build_switchboard_insight_notify_fn

    notify = _build_switchboard_insight_notify_fn(_make_mock_pool())
    with (
        patch("butlers.credential_store.resolve_owner_entity_info", AsyncMock(return_value="123")),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            AsyncMock(
                return_value={
                    "status": "failed",
                    "error": "telegram_partial_delivery",
                    "partial_delivery": True,
                    "retryable": False,
                }
            ),
        ),
    ):
        result = await notify("x" * 5000, {"channel": "telegram"})
    assert result["status"] == "error"
    assert result["partial_delivery"] is True
    assert result["retryable"] is False


@pytest.mark.parametrize("terminal_field", ["partial_delivery", "telegram_effect_uncertain"])
async def test_actual_insight_cycle_quarantines_partial_through_failed_terminal_write(
    terminal_field,
):
    import uuid
    from contextlib import ExitStack
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from butlers.tools.switchboard.insight import broker

    candidate_id = str(uuid.uuid4())
    candidate = {
        "id": candidate_id,
        "origin_butler": "health",
        "priority": 20,
        "category": "controlled",
        "dedup_key": "controlled:partial",
        "cooldown_days": None,
        "message": "x" * 5000,
        "channel": "telegram",
        "metadata": {},
        "expires_at": _PINNED_NOW + timedelta(days=2),
        "created_at": _PINNED_NOW,
    }
    state, attempts, fail_once = "pending", 0, True

    async def fetch(sql, *_):
        if "delivery_attempt_count" in sql:
            return [{**candidate, "status": state, "delivery_attempt_count": attempts}]
        return [candidate] if state == "pending" else []

    async def execute(sql, *args):
        nonlocal state, attempts, fail_once
        if "metadata = COALESCE(metadata" in sql and args[1].get("delivery_failure"):
            if fail_once:
                fail_once = False
                raise RuntimeError("controlled terminal write failure")
            state = "filtered"
        elif "delivery_attempt_count = delivery_attempt_count + 1" in sql:
            attempts += 1

    pool = MagicMock(fetch=fetch, execute=execute)
    notify = AsyncMock(return_value={"status": "error", terminal_field: True, "retryable": False})
    ledger = AsyncMock()
    with ExitStack() as stack:
        values = {
            "get_insight_settings": {"verbosity": "normal", "custom_budget": None},
            "get_approvals_policy_quiet_hours": {},
            "get_suppressing_context_signal": None,
            "expire_candidates": 0,
            "_revalidate_premises": ({}, 0),
            "filter_by_cooldown": [candidate_id],
            "deduplicate_candidates": [candidate_id],
            "compute_effective_budget": 1,
            "compute_category_budget_weights": {},
            "_pending_fold_lines": [],
            "cleanup_old_rows": None,
            "check_total_disengagement_auto_off": False,
        }
        for name, value in values.items():
            stack.enter_context(patch.object(broker, name, AsyncMock(return_value=value)))
        stack.enter_context(patch.object(broker, "is_policy_quiet_now", return_value=False))
        stack.enter_context(patch.object(broker, "record_attention_event", ledger))
        stack.enter_context(
            patch(
                "butlers.core.attention_urgency.candidate_interruption",
                AsyncMock(return_value=SimpleNamespace(interrupts=False)),
            )
        )
        with pytest.raises(RuntimeError, match="controlled terminal"):
            await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)
        assert broker._PARTIAL_INSIGHTS.contains(pool, candidate_id)
        second = await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)
        third = await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)
    assert notify.await_count == 1
    assert second["delivered"] == third["delivered"] == []
    assert state == "filtered" and attempts == 0
    assert ledger.await_count == 1
    assert ledger.await_args.kwargs["outcome"] == "failed"
    assert ledger.await_args.kwargs["metadata"]["retryable"] is False
    assert ledger.await_args.kwargs["metadata"][terminal_field] is True
    assert not broker._PARTIAL_INSIGHTS.contains(pool, candidate_id)

    # A settled old send must not turn into a pool-wide permanent mute.
    old_id = candidate_id
    fresh_id = str(uuid.uuid4())
    fresh = {**candidate, "id": fresh_id, "dedup_key": "controlled:fresh", "message": "fresh"}
    fresh_state = "pending"
    conn = _FakeConn()

    async def fresh_fetch(sql, *_):
        return [fresh] if fresh_state == "pending" else []

    async def fresh_write(sql, *args):
        nonlocal fresh_state
        if "SET status = 'delivered'" in sql:
            assert args[1] == [fresh_id]
            fresh_state = "delivered"
        return "UPDATE 1"

    conn.execute.side_effect = fresh_write
    pool.fetch = AsyncMock(side_effect=fresh_fetch)
    pool.acquire.return_value = AsyncCtx(conn)
    notify.return_value = {"status": "sent"}
    values.update(
        {
            "filter_by_cooldown": [fresh_id],
            "deduplicate_candidates": [fresh_id],
            "record_cooldowns": None,
            "record_engagement_rows": None,
        }
    )
    with ExitStack() as stack:
        for name, value in values.items():
            stack.enter_context(patch.object(broker, name, AsyncMock(return_value=value)))
        stack.enter_context(patch.object(broker, "is_policy_quiet_now", return_value=False))
        stack.enter_context(patch.object(broker, "record_attention_event", ledger))
        stack.enter_context(
            patch(
                "butlers.core.attention_urgency.candidate_interruption",
                AsyncMock(return_value=SimpleNamespace(interrupts=False)),
            )
        )
        fourth = await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)
        fifth = await broker.delivery_cycle(pool, notify_fn=notify, now=_PINNED_NOW)
    assert fourth["delivered"] == [fresh_id] and fifth["delivered"] == []
    assert notify.await_count == 2 and fresh_state == "delivered"
    assert state == "filtered" and attempts == 0
    assert [c.kwargs["notification_ref"] for c in ledger.await_args_list] == [old_id, fresh_id]
    assert [c.kwargs["outcome"] for c in ledger.await_args_list] == ["failed", "delivered"]
    assert not broker._PARTIAL_INSIGHTS.keys(pool)


@pytest.mark.parametrize(
    "job_name",
    ["_run_switchboard_insight_delivery_cycle_job", "_run_switchboard_insight_urgent_subcycle_job"],
)
async def test_actual_daily_and_urgent_assembly_supplies_only_installed_pool_reader(job_name):
    from types import SimpleNamespace
    from uuid import UUID

    from butlers import scheduled_jobs
    from butlers.core import finance_warning_control as control

    pool = object()
    daemon = SimpleNamespace(
        db=SimpleNamespace(pool=pool),
        config=SimpleNamespace(name="switchboard"),
        _tool_catalog=None,
        _boot_instance_id=UUID(int=1),
        _boot_epoch=1,
    )
    owner = control.FinanceWarningControlOwner(daemon)
    daemon._finance_warning_control_owner = owner
    cycle = AsyncMock(return_value={})
    try:
        with patch("butlers.tools.switchboard.insight.broker.delivery_cycle", cycle):
            await getattr(scheduled_jobs, job_name)(pool, None)
        callback = cycle.call_args.kwargs["finance_warning_reader"]
        assert callback.__self__ is owner
        assert cycle.call_args.kwargs["notify_fn"] is not None
        assert (
            await callback({"subject": str(UUID(int=1)), "selected_date": "2026-10-27", "epoch": 0})
            is None
        )
    finally:
        await owner.close(1)


@pytest.mark.parametrize("final_verdict", [False, None])
async def test_real_delivery_cycle_rechecks_finance_after_format_and_preserves_unknown(
    final_verdict,
):
    from contextlib import ExitStack
    from types import SimpleNamespace
    from uuid import UUID

    from butlers.tools.switchboard.insight import broker

    cid = UUID(int=1)
    candidate = {
        "id": cid,
        "origin_butler": "finance",
        "priority": 75,
        "category": "subscription-renewal",
        "dedup_key": "finance:subscription-renewal:synthetic:2026-01-16",
        "cooldown_days": None,
        "message": "Synthetic unverified renewal",
        "channel": None,
        "metadata": {},
        "expires_at": _PINNED_NOW + timedelta(days=1),
        "created_at": _PINNED_NOW,
        "premise": {
            "kind": "probe",
            "butler": "finance",
            "probe": "subscription_warning_still_relevant",
            "args": {"subject": str(cid), "selected_date": "2026-01-16", "epoch": 0},
        },
    }
    pool = AsyncMock()

    async def fetch(query, *args):
        return [row(**candidate)]

    pool.fetch = fetch
    pool.execute = AsyncMock(return_value="UPDATE 1")
    pool.acquire = lambda: AsyncCtx(_FakeConn())
    read = AsyncMock(side_effect=[True, final_verdict])
    notify = AsyncMock(return_value={"status": "sent"})
    replacements = {
        "get_insight_settings": {"verbosity": "normal", "custom_budget": None},
        "get_approvals_policy_quiet_hours": {
            "quiet_start_hour": None,
            "quiet_end_hour": None,
            "timezone": "UTC",
        },
        "get_suppressing_context_signal": None,
        "expire_candidates": 0,
        "_pending_fold_lines": [],
        "filter_by_cooldown": [str(cid)],
        "deduplicate_candidates": [str(cid)],
        "compute_effective_budget": 1,
        "compute_category_budget_weights": {},
        "record_cooldowns": None,
        "record_engagement_rows": None,
        "cleanup_old_rows": None,
        "check_total_disengagement_auto_off": False,
        "record_attention_event": None,
    }
    with ExitStack() as stack:
        for name, value in replacements.items():
            stack.enter_context(patch.object(broker, name, AsyncMock(return_value=value)))
        stack.enter_context(
            patch(
                "butlers.core.attention_urgency.candidate_interruption",
                AsyncMock(
                    return_value=SimpleNamespace(interrupts=False, cause="routine", ref=None)
                ),
            )
        )
        result = await broker.delivery_cycle(
            pool, notify_fn=notify, now=_PINNED_NOW, finance_warning_reader=read
        )
    assert read.await_count == 2
    if final_verdict is False:
        assert result["reason"] == "finance_warning_superseded"
        notify.assert_not_awaited()
        assert any("status = 'withdrawn'" in call.args[0] for call in pool.execute.call_args_list)
    else:
        assert result["delivered"] == [str(cid)]
        wire = notify.call_args.args[0]
        assert "as of 2026-01-15 12:00 UTC" in wire and "Unverified Finance report" in wire
