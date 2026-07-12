"""Tests for butlers.core.model_breaker_attention (bu-hmdqz.2).

Covers:
- _recently_notified: reads the debounce marker back from public.audit_log,
  fails open (False) on lookup error.
- _check_suppression: mirrors notify()'s quiet-hours + context-bus gate.
- _compose_message: includes alias, model_id, and failure count.
- maybe_push_breaker_open_attention:
  - debounced: a second call within the cooldown is a clean no-op.
  - onset: delivers, records the ledger row with metadata, writes the
    debounce marker.
  - high priority (the production default) skips the suppression check.
  - non-high priority IS suppressed by quiet hours / context bus.
  - no recipient configured -> deferred, no debounce marker.
  - delivery failure -> deferred, no debounce marker.
  - an unexpected exception anywhere in the body never raises.
  - pool=None is a clean no-op.

No real database required -- all pool interactions are mocked (mirrors
tests/core/test_fleet_halt_attention.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.model_breaker_attention import (
    _check_suppression,
    _compose_message,
    _recently_notified,
    maybe_push_breaker_open_attention,
)

pytestmark = pytest.mark.unit

_ENTRY_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# _recently_notified
# ---------------------------------------------------------------------------


async def test_recently_notified_true_within_cooldown():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"ts": datetime.now(UTC)})
    assert await _recently_notified(pool, _ENTRY_ID) is True


async def test_recently_notified_false_outside_cooldown():
    from datetime import timedelta

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"ts": datetime.now(UTC) - timedelta(hours=1)})
    assert await _recently_notified(pool, _ENTRY_ID) is False


async def test_recently_notified_false_when_no_row():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    assert await _recently_notified(pool, _ENTRY_ID) is False


async def test_recently_notified_fails_open_on_error():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError("boom"))
    assert await _recently_notified(pool, _ENTRY_ID) is False


# ---------------------------------------------------------------------------
# _compose_message
# ---------------------------------------------------------------------------


def test_compose_message_includes_alias_model_and_count():
    message = _compose_message("gpt-5.6-luna", "gpt-5.6-luna-model", 5, "http://x/door")
    assert "gpt-5.6-luna" in message
    assert "gpt-5.6-luna-model" in message
    assert "5 consecutive" in message
    assert "http://x/door" in message


# ---------------------------------------------------------------------------
# _check_suppression
# ---------------------------------------------------------------------------


async def test_check_suppression_quiet_hours():
    pool = AsyncMock()
    with (
        patch(
            "butlers.core.model_breaker_attention.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value={"timezone": "UTC", "quiet_hours": "22:00-08:00"}),
        ),
        patch(
            "butlers.core.model_breaker_attention.should_suppress_by_policy",
            return_value=True,
        ),
    ):
        assert await _check_suppression(pool) == "quiet_hours"


async def test_check_suppression_context_bus():
    pool = AsyncMock()
    with (
        patch(
            "butlers.core.model_breaker_attention.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.model_breaker_attention.get_suppressing_context_signal",
            new=AsyncMock(return_value="focus_mode"),
        ),
    ):
        assert await _check_suppression(pool) == "context_bus:focus_mode"


async def test_check_suppression_none_when_clear():
    pool = AsyncMock()
    with (
        patch(
            "butlers.core.model_breaker_attention.get_approvals_policy_quiet_hours",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.model_breaker_attention.get_suppressing_context_signal",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await _check_suppression(pool) is None


# ---------------------------------------------------------------------------
# maybe_push_breaker_open_attention
# ---------------------------------------------------------------------------


async def test_pool_none_is_clean_noop():
    await maybe_push_breaker_open_attention(
        None,
        catalog_entry_id=_ENTRY_ID,
        alias="a",
        model_id="m",
        consecutive_failures=5,
    )


async def test_debounced_second_call_is_a_clean_noop():
    pool = object()
    with (
        patch(
            "butlers.core.model_breaker_attention._recently_notified",
            new=AsyncMock(return_value=True),
        ),
        patch("butlers.core.model_breaker_attention.record_attention_event") as ledger_mock,
        patch(
            "butlers.core.model_breaker_attention.resolve_owner_telegram_recipient"
        ) as recipient_mock,
    ):
        await maybe_push_breaker_open_attention(
            pool,
            catalog_entry_id=_ENTRY_ID,
            alias="gpt-5.6-luna",
            model_id="gpt-5.6-luna-model",
            consecutive_failures=5,
        )

    ledger_mock.assert_not_called()
    recipient_mock.assert_not_called()


async def test_onset_delivers_records_ledger_and_writes_debounce_marker():
    pool = object()
    with (
        patch(
            "butlers.core.model_breaker_attention._recently_notified",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.model_breaker_attention.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "sent", "notification_id": "n-1"}),
        ) as deliver_mock,
        patch(
            "butlers.core.model_breaker_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append", new=AsyncMock(return_value=1)) as audit_append,
        patch("butlers.core.model_breaker_attention._check_suppression") as suppression_mock,
    ):
        await maybe_push_breaker_open_attention(
            pool,
            catalog_entry_id=_ENTRY_ID,
            alias="gpt-5.6-luna",
            model_id="gpt-5.6-luna-model",
            consecutive_failures=5,
        )

    # priority="high" (the default) skips the suppression check entirely.
    suppression_mock.assert_not_called()

    deliver_mock.assert_awaited_once()
    call_kwargs = deliver_mock.await_args.kwargs
    assert call_kwargs["channel"] == "telegram"
    assert call_kwargs["recipient"] == "12345"
    assert "gpt-5.6-luna" in call_kwargs["message"]

    ledger_mock.assert_awaited_once()
    ledger_kwargs = ledger_mock.await_args.kwargs
    assert ledger_kwargs["outcome"] == "delivered"
    assert ledger_kwargs["priority"] == "high"
    assert ledger_kwargs["dedup_key"] == f"model_breaker_open:{_ENTRY_ID}"
    assert ledger_kwargs["metadata"]["consecutive_failures"] == 5
    assert ledger_kwargs["metadata"]["catalog_entry_id"] == str(_ENTRY_ID)

    audit_append.assert_awaited_once()
    assert audit_append.await_args.args[2] == "model_breaker_open_notified"
    assert audit_append.await_args.kwargs["target"] == f"model_breaker:{_ENTRY_ID}"
    assert audit_append.await_args.kwargs["note"] == "5"


async def test_non_high_priority_is_suppressed_by_quiet_hours():
    pool = object()
    with (
        patch(
            "butlers.core.model_breaker_attention._recently_notified",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.model_breaker_attention._check_suppression",
            new=AsyncMock(return_value="quiet_hours"),
        ),
        patch(
            "butlers.core.model_breaker_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch(
            "butlers.core.model_breaker_attention.resolve_owner_telegram_recipient"
        ) as recipient_mock,
        patch("butlers.api.routers.audit.append") as audit_append,
    ):
        await maybe_push_breaker_open_attention(
            pool,
            catalog_entry_id=_ENTRY_ID,
            alias="a",
            model_id="m",
            consecutive_failures=5,
            priority="medium",
        )

    recipient_mock.assert_not_called()
    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "suppressed"
    assert ledger_mock.await_args.kwargs["reason"] == "quiet_hours"
    audit_append.assert_not_called()


async def test_no_recipient_defers_without_debounce_marker():
    pool = object()
    with (
        patch(
            "butlers.core.model_breaker_attention._recently_notified",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.model_breaker_attention.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.core.model_breaker_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append") as audit_append,
    ):
        await maybe_push_breaker_open_attention(
            pool,
            catalog_entry_id=_ENTRY_ID,
            alias="a",
            model_id="m",
            consecutive_failures=5,
        )

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "deferred"
    assert ledger_mock.await_args.kwargs["reason"] == "no_recipient_configured"
    audit_append.assert_not_called()


async def test_delivery_failure_defers_without_debounce_marker():
    pool = object()
    with (
        patch(
            "butlers.core.model_breaker_attention._recently_notified",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "butlers.core.model_breaker_attention.resolve_owner_telegram_recipient",
            new=AsyncMock(return_value="12345"),
        ),
        patch(
            "butlers.tools.switchboard.notification.deliver.deliver",
            new=AsyncMock(return_value={"status": "failed", "error": "boom"}),
        ),
        patch(
            "butlers.core.model_breaker_attention.record_attention_event",
            new=AsyncMock(return_value="r-1"),
        ) as ledger_mock,
        patch("butlers.api.routers.audit.append") as audit_append,
    ):
        await maybe_push_breaker_open_attention(
            pool,
            catalog_entry_id=_ENTRY_ID,
            alias="a",
            model_id="m",
            consecutive_failures=5,
        )

    ledger_mock.assert_awaited_once()
    assert ledger_mock.await_args.kwargs["outcome"] == "deferred"
    assert ledger_mock.await_args.kwargs["reason"] == "delivery_error:boom"
    audit_append.assert_not_called()


async def test_unexpected_error_never_raises():
    pool = object()
    with patch(
        "butlers.core.model_breaker_attention._recently_notified",
        side_effect=RuntimeError("boom"),
    ):
        await maybe_push_breaker_open_attention(
            pool,
            catalog_entry_id=_ENTRY_ID,
            alias="a",
            model_id="m",
            consecutive_failures=5,
        )
