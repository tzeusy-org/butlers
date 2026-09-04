"""Tests for shared helpers in butlers.jobs.home.

Covers:
- HomeJobContext.create: credential resolution from contact info
- HomeJobContext async context manager: client lifecycle, Authorization header
- _load_thresholds: stored values, fallbacks, per-key fallback, type casting, key prefix
- _read_entity_snapshot: populated table, domain filter, empty raises,
  unmeasurable on outage/missing health row
- _require_ha_source_healthy: healthy passes, outage/missing row raises
  HASourceUnmeasurableError
- _send_notify: routes through the notify boundary (quiet-hours/context-bus
  suppression, missing recipient, missing switchboard client, deliver()
  success/failure) with an attention_ledger row on every terminal branch
  (bu-tdd4k.3)

All tests use mocked asyncpg pools — no real database or network required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.jobs.ha_context import HomeJobContext
from butlers.jobs.home import (
    _DEFAULT_BATTERY_THRESHOLDS,
    _DEFAULT_ENERGY_THRESHOLDS,
    _DEFAULT_OFFLINE_HOURS_THRESHOLDS,
    EmptyEntitySnapshotError,
    HASourceUnmeasurableError,
    _load_thresholds,
    _read_entity_snapshot,
    _require_ha_source_healthy,
    _send_notify,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _healthy_row() -> dict[str, Any]:
    """A healthy ``ha_source_health`` row — the default across these tests."""
    return {"status": "healthy", "last_success_at": None}


def _make_pool(
    *,
    fetchval_return: Any = None,
    fetchrow_return: Any = "__default__",
    fetch_return: list[Any] | None = None,
) -> MagicMock:
    """Mocked asyncpg pool.

    ``fetchrow_return`` defaults to a healthy ``ha_source_health`` row so
    tests unrelated to source-health gating are unaffected; pass ``None`` or
    an error-status row explicitly to exercise the unmeasurable path.
    """
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(
        return_value=_healthy_row() if fetchrow_return == "__default__" else fetchrow_return
    )
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock()
    return pool


class _FakeRecord:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def keys(self) -> Any:
        return self._data.keys()


# ---------------------------------------------------------------------------
# HomeJobContext
# ---------------------------------------------------------------------------


async def test_home_job_context_create():
    """create() populates credentials from contact info; None when absent."""
    pool = _make_pool()

    async def _resolve(pool: Any, info_type: str) -> str | None:
        return {"home_assistant_url": "http://ha.local:8123", "home_assistant_token": "secret"}[
            info_type
        ]

    with patch("butlers.jobs.ha_context.resolve_owner_entity_info", side_effect=_resolve):
        ctx = await HomeJobContext.create(pool)

    assert ctx.ha_url == "http://ha.local:8123" and ctx.ha_token == "secret"

    with patch(
        "butlers.jobs.ha_context.resolve_owner_entity_info",
        new_callable=AsyncMock,
        return_value=None,
    ):
        ctx2 = await HomeJobContext.create(pool)
    assert ctx2.ha_url is None and ctx2.ha_token is None


async def test_home_job_context_lifecycle():
    """Client is set inside context, None outside; Authorization header set from token."""
    ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="mytoken")
    assert ctx.client is None

    async with ctx as c:
        assert c is ctx
        assert isinstance(c.client, httpx.AsyncClient)
        merged = {k.lower(): v for k, v in c.client.headers.items()}
        assert "authorization" in merged
        assert "Bearer mytoken" in merged.get("authorization", merged.get("Authorization", ""))

    assert ctx.client is None

    # No Authorization when token is None
    ctx2 = HomeJobContext(ha_url="http://ha.local:8123", ha_token=None)
    async with ctx2 as c2:
        merged2 = {k.lower(): v for k, v in c2.client.headers.items()}
        assert "authorization" not in merged2

    # Client cleaned up on exception
    ctx3 = HomeJobContext(ha_url="http://ha.local:8123", ha_token="tok")
    try:
        async with ctx3:
            raise ValueError("test error")
    except ValueError:
        pass
    assert ctx3.client is None


# ---------------------------------------------------------------------------
# _load_thresholds
# ---------------------------------------------------------------------------


async def test_load_thresholds_stored_and_fallbacks():
    """Returns stored values when present; falls back to defaults on missing/non-dict key."""
    pool = _make_pool()

    with patch(
        "butlers.jobs.home.state_get",
        new_callable=AsyncMock,
        return_value={"critical": 5, "warning": 15, "info": 25},
    ):
        result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result == {"critical": 5, "warning": 15, "info": 25}

    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None):
        result2 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result2 == dict(_DEFAULT_BATTERY_THRESHOLDS)

    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value="not-a-dict"):
        result3 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result3 == dict(_DEFAULT_BATTERY_THRESHOLDS)


async def test_load_thresholds_per_key_fallback_and_type_casting():
    """Per-key bad values use defaults; strings cast to correct numeric type; extra keys ignored."""
    pool = _make_pool()

    # Per-key invalid
    with patch(
        "butlers.jobs.home.state_get",
        new_callable=AsyncMock,
        return_value={"critical": "bad-value", "warning": 15, "info": 25},
    ):
        result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result["critical"] == _DEFAULT_BATTERY_THRESHOLDS["critical"]
    assert result["warning"] == 15

    # Float casting
    with patch(
        "butlers.jobs.home.state_get",
        new_callable=AsyncMock,
        return_value={"anomaly_pct": "30", "high_severity_pct": "150"},
    ):
        result2 = await _load_thresholds(pool, "energy", _DEFAULT_ENERGY_THRESHOLDS)
    assert result2["anomaly_pct"] == 30.0 and isinstance(result2["anomaly_pct"], float)

    # Int casting (offline_hours defaults are int)
    with patch(
        "butlers.jobs.home.state_get",
        new_callable=AsyncMock,
        return_value={"critical": 5.9, "warning": 1.1},
    ):
        result_int = await _load_thresholds(
            pool, "offline_hours", _DEFAULT_OFFLINE_HOURS_THRESHOLDS
        )
    assert result_int["critical"] == 5 and isinstance(result_int["critical"], int)

    # Extra keys ignored
    with patch(
        "butlers.jobs.home.state_get",
        new_callable=AsyncMock,
        return_value={"critical": 5, "warning": 15, "info": 25, "unknown_key": 999},
    ):
        result3 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert "unknown_key" not in result3


# ---------------------------------------------------------------------------
# _read_entity_snapshot
# ---------------------------------------------------------------------------


async def test_read_entity_snapshot():
    """Returns all rows; domain filter restricts results; empty raises EmptyEntitySnapshotError."""
    rows = [
        _FakeRecord(
            {"entity_id": "sensor.temp", "state": "72", "attributes": {}, "last_updated": None}
        ),
        _FakeRecord(
            {"entity_id": "light.living", "state": "on", "attributes": {}, "last_updated": None}
        ),
    ]
    pool = _make_pool(fetch_return=rows)
    result = await _read_entity_snapshot(pool)
    assert len(result) == 2

    pool2 = _make_pool(fetch_return=rows[:1])
    result2 = await _read_entity_snapshot(pool2, domain_filter="sensor")
    assert len(result2) == 1
    assert "LIKE" in pool2.fetch.call_args[0][0]
    assert pool2.fetch.call_args[0][1] == "sensor.%"

    pool3 = _make_pool(fetch_return=[])
    with pytest.raises(EmptyEntitySnapshotError):
        await _read_entity_snapshot(pool3)

    pool4 = _make_pool(fetch_return=[])
    with pytest.raises(EmptyEntitySnapshotError, match="sensor"):
        await _read_entity_snapshot(pool4, domain_filter="sensor")


async def test_read_entity_snapshot_unmeasurable_on_outage():
    """An unhealthy or missing ha_source_health row raises before querying the snapshot."""
    rows = [
        _FakeRecord(
            {"entity_id": "sensor.temp", "state": "72", "attributes": {}, "last_updated": None}
        )
    ]

    # Simulated outage: an explicit error status short-circuits before the
    # snapshot table (which may well have rows) is even queried.
    pool = _make_pool(
        fetchrow_return={"status": "error", "last_success_at": None}, fetch_return=rows
    )
    with pytest.raises(HASourceUnmeasurableError):
        await _read_entity_snapshot(pool)
    pool.fetch.assert_not_called()

    # No health record at all (never contacted) fails closed the same way.
    pool2 = _make_pool(fetchrow_return=None, fetch_return=rows)
    with pytest.raises(HASourceUnmeasurableError):
        await _read_entity_snapshot(pool2)
    pool2.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# _require_ha_source_healthy
# ---------------------------------------------------------------------------


async def test_require_ha_source_healthy_happy_path():
    """A healthy status row passes without raising."""
    pool = _make_pool(fetchrow_return={"status": "healthy", "last_success_at": None})
    await _require_ha_source_healthy(pool)  # does not raise


async def test_require_ha_source_healthy_outage_carries_last_good_timestamp():
    """An error-status row raises HASourceUnmeasurableError with the last-good timestamp."""
    from datetime import UTC, datetime

    last_good = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    pool = _make_pool(fetchrow_return={"status": "error", "last_success_at": last_good})
    with pytest.raises(HASourceUnmeasurableError) as exc_info:
        await _require_ha_source_healthy(pool)
    assert exc_info.value.last_success_at == last_good


async def test_require_ha_source_healthy_missing_row_fails_closed():
    """No ha_source_health row at all (never contacted) is treated as unmeasurable."""
    pool = _make_pool(fetchrow_return=None)
    with pytest.raises(HASourceUnmeasurableError) as exc_info:
        await _require_ha_source_healthy(pool)
    assert exc_info.value.last_success_at is None


# ---------------------------------------------------------------------------
# _send_notify — routes through the notify boundary (bu-tdd4k.3)
# ---------------------------------------------------------------------------


def _patch_no_suppression():
    """Patch the quiet-hours/context-bus gate open (not suppressed)."""
    return (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "butlers.core.attention_ledger.get_suppressing_context_signal",
            new_callable=AsyncMock,
            return_value=None,
        ),
    )


async def test_send_notify_suppressed_by_quiet_hours():
    """Quiet-hours suppression skips delivery and records a suppressed ledger row."""
    pool = _make_pool()

    with (
        patch(
            "butlers.core.attention_ledger.get_approvals_policy_quiet_hours",
            new_callable=AsyncMock,
            return_value={"timezone": "UTC", "quiet_start_hour": 0, "quiet_end_hour": 23},
        ),
        patch("butlers.core.attention_ledger.is_policy_quiet_now", return_value=True),
        patch(
            "butlers.jobs.home.resolve_owner_telegram_recipient", new_callable=AsyncMock
        ) as mock_resolve,
        patch("butlers.jobs.home.get_current_switchboard_client") as mock_client,
        patch("butlers.jobs.home.record_attention_event", new_callable=AsyncMock) as mock_ledger,
    ):
        await _send_notify(pool, "Weekly digest")

    mock_resolve.assert_not_awaited()
    mock_client.assert_not_called()
    mock_ledger.assert_awaited_once()
    assert mock_ledger.await_args.kwargs["outcome"] == "suppressed"
    assert mock_ledger.await_args.kwargs["reason"] == "quiet_hours"


async def test_send_notify_no_recipient_configured():
    """No telegram recipient configured: skip delivery, record a failed ledger row."""
    pool = _make_pool()
    p1, p2 = _patch_no_suppression()

    with (
        p1,
        p2,
        patch(
            "butlers.jobs.home.resolve_owner_telegram_recipient",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("butlers.jobs.home.get_current_switchboard_client") as mock_client,
        patch("butlers.jobs.home.record_attention_event", new_callable=AsyncMock) as mock_ledger,
    ):
        await _send_notify(pool, "Weekly digest")

    mock_client.assert_not_called()
    assert mock_ledger.await_args.kwargs["outcome"] == "failed"
    assert mock_ledger.await_args.kwargs["reason"] == "no_recipient_configured"


async def test_send_notify_no_switchboard_client():
    """Switchboard client unavailable: skip delivery, record a failed ledger row."""
    pool = _make_pool()
    p1, p2 = _patch_no_suppression()

    with (
        p1,
        p2,
        patch(
            "butlers.jobs.home.resolve_owner_telegram_recipient",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("butlers.jobs.home.get_current_switchboard_client", return_value=None),
        patch("butlers.jobs.home.record_attention_event", new_callable=AsyncMock) as mock_ledger,
    ):
        await _send_notify(pool, "Weekly digest")

    assert mock_ledger.await_args.kwargs["outcome"] == "failed"
    assert mock_ledger.await_args.kwargs["reason"] == "switchboard_client_unavailable"


async def test_send_notify_delivers_via_deliver_mcp_tool():
    """Happy path: builds a notify.v1 envelope and calls deliver() over the MCP client."""
    pool = _make_pool()
    p1, p2 = _patch_no_suppression()

    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.is_error = False
    mock_result.data = {"status": "sent", "notification_id": "abc-123"}
    mock_client.call_tool = AsyncMock(return_value=mock_result)

    with (
        p1,
        p2,
        patch(
            "butlers.jobs.home.resolve_owner_telegram_recipient",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("butlers.jobs.home.get_current_switchboard_client", return_value=mock_client),
        patch("butlers.jobs.home.record_attention_event", new_callable=AsyncMock) as mock_ledger,
    ):
        await _send_notify(pool, "Weekly digest")

    mock_client.call_tool.assert_awaited_once()
    tool_name, tool_args = mock_client.call_tool.await_args.args
    assert tool_name == "deliver"
    assert tool_args["source_butler"] == "home"
    envelope = tool_args["notify_request"]
    assert envelope["schema_version"] == "notify.v1"
    assert envelope["origin_butler"] == "home"
    assert envelope["delivery"] == {
        "intent": "send",
        "channel": "telegram",
        "message": "Weekly digest",
        "recipient": "12345",
    }
    assert mock_ledger.await_args.kwargs["outcome"] == "delivered"
    assert mock_ledger.await_args.kwargs["notification_ref"] == "abc-123"


async def test_send_notify_deliver_failure_recorded_as_failed():
    """deliver() reporting status=failed is recorded as a failed ledger row, not raised."""
    pool = _make_pool()
    p1, p2 = _patch_no_suppression()

    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_result.is_error = False
    mock_result.data = {"status": "failed", "error": "Messenger unreachable"}
    mock_client.call_tool = AsyncMock(return_value=mock_result)

    with (
        p1,
        p2,
        patch(
            "butlers.jobs.home.resolve_owner_telegram_recipient",
            new_callable=AsyncMock,
            return_value="12345",
        ),
        patch("butlers.jobs.home.get_current_switchboard_client", return_value=mock_client),
        patch("butlers.jobs.home.record_attention_event", new_callable=AsyncMock) as mock_ledger,
    ):
        await _send_notify(pool, "Weekly digest")

    assert mock_ledger.await_args.kwargs["outcome"] == "failed"
    assert "Messenger unreachable" in mock_ledger.await_args.kwargs["reason"]
