"""Unit tests for shared helpers in butlers.jobs.home.

Covers:
- HomeJobContext.create: credential resolution from contact info
- HomeJobContext async context manager: client lifecycle, Authorization header
- _load_thresholds: stored values, absent key fallback, invalid type fallback,
  per-key fallback, partial override, type casting (int/float), key prefix, extra keys
- _read_entity_snapshot: populated table, domain filter, empty raises
- _send_notify: delegates to _notify_owner_telegram

All tests use mocked asyncpg pools — no real database or network required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.jobs.home import (
    _DEFAULT_BATTERY_THRESHOLDS,
    _DEFAULT_ENERGY_THRESHOLDS,
    _DEFAULT_OFFLINE_HOURS_THRESHOLDS,
    EmptyEntitySnapshotError,
    HomeJobContext,
    _load_thresholds,
    _read_entity_snapshot,
    _send_notify,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    fetchval_return: Any = None,
    fetchrow_return: Any = None,
    fetch_return: list[Any] | None = None,
) -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
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
    """create() populates credentials from contact info; credentials are None when absent."""
    pool = _make_pool()

    async def _resolve(pool: Any, info_type: str) -> str | None:
        return {"home_assistant_url": "http://ha.local:8123", "home_assistant_token": "secret"}[info_type]

    with patch("butlers.jobs.home.resolve_owner_entity_info", side_effect=_resolve):
        ctx = await HomeJobContext.create(pool)

    assert ctx.ha_url == "http://ha.local:8123" and ctx.ha_token == "secret"

    with patch("butlers.jobs.home.resolve_owner_entity_info", new_callable=AsyncMock, return_value=None):
        ctx2 = await HomeJobContext.create(pool)
    assert ctx2.ha_url is None and ctx2.ha_token is None


async def test_home_job_context_lifecycle():
    """Client is set inside context, None before/after; Authorization header set from token."""
    ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="mytoken")
    assert ctx.client is None  # before entering

    async with ctx as c:
        assert c is ctx
        assert isinstance(c.client, httpx.AsyncClient)
        merged = {k.lower(): v for k, v in c.client.headers.items()}
        assert "authorization" in merged
        assert "Bearer mytoken" in merged.get("authorization", merged.get("Authorization", ""))

    assert ctx.client is None  # after exiting

    # No Authorization header when token is None
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
    stored = {"critical": 5, "warning": 15, "info": 25}

    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=stored):
        result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result == {"critical": 5, "warning": 15, "info": 25}

    # Absent key → defaults
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value=None):
        result2 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result2 == dict(_DEFAULT_BATTERY_THRESHOLDS)

    # Non-dict value → defaults
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value="not-a-dict"):
        result3 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result3 == dict(_DEFAULT_BATTERY_THRESHOLDS)


async def test_load_thresholds_per_key_fallback_and_partial():
    """Per-key bad values fall back; partial dicts fill missing keys from defaults; extra keys ignored."""
    pool = _make_pool()

    # Per-key invalid value
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock,
               return_value={"critical": "bad-value", "warning": 15, "info": 25}):
        result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result["critical"] == _DEFAULT_BATTERY_THRESHOLDS["critical"]
    assert result["warning"] == 15

    # Partial stored dict
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock, return_value={"critical": 5}):
        result2 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert result2["critical"] == 5
    assert result2["warning"] == _DEFAULT_BATTERY_THRESHOLDS["warning"]

    # Extra keys ignored
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock,
               return_value={"critical": 5, "warning": 15, "info": 25, "unknown_key": 999}):
        result3 = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert "unknown_key" not in result3


async def test_load_thresholds_type_casting_and_key_prefix():
    """Float defaults cast to float; int defaults cast to int; key prefix is 'home:thresholds:<key>'."""
    pool = _make_pool()

    # Float casting
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock,
               return_value={"anomaly_pct": "30", "high_severity_pct": "150"}):
        result = await _load_thresholds(pool, "energy", _DEFAULT_ENERGY_THRESHOLDS)
    assert result["anomaly_pct"] == 30.0 and isinstance(result["anomaly_pct"], float)

    # Int casting
    with patch("butlers.jobs.home.state_get", new_callable=AsyncMock,
               return_value={"critical": 5.9, "warning": 15.1}):
        result2 = await _load_thresholds(pool, "offline_hours", _DEFAULT_OFFLINE_HOURS_THRESHOLDS)
    assert result2["critical"] == 5 and isinstance(result2["critical"], int)

    # Key prefix
    captured: list[str] = []

    async def _mock_state_get(pool: Any, key: str) -> None:
        captured.append(key)
        return None

    with patch("butlers.jobs.home.state_get", side_effect=_mock_state_get):
        await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)
    assert captured == ["home:thresholds:battery"]


# ---------------------------------------------------------------------------
# _read_entity_snapshot
# ---------------------------------------------------------------------------


async def test_read_entity_snapshot():
    """Returns all rows; domain filter adds LIKE clause; empty raises EmptyEntitySnapshotError."""
    rows = [
        _FakeRecord({"entity_id": "sensor.temp", "state": "72", "attributes": {}, "last_updated": None}),
        _FakeRecord({"entity_id": "light.living", "state": "on", "attributes": {}, "last_updated": None}),
    ]
    pool = _make_pool(fetch_return=rows)
    result = await _read_entity_snapshot(pool)
    assert len(result) == 2 and isinstance(result, list)
    assert "LIKE" not in pool.fetch.call_args[0][0]

    # Domain filter adds LIKE and passes pattern as arg
    pool2 = _make_pool(fetch_return=rows[:1])
    result2 = await _read_entity_snapshot(pool2, domain_filter="sensor")
    assert len(result2) == 1
    assert "LIKE" in pool2.fetch.call_args[0][0]
    assert pool2.fetch.call_args[0][1] == "sensor.%"

    # Empty raises — with and without filter
    pool3 = _make_pool(fetch_return=[])
    with pytest.raises(EmptyEntitySnapshotError):
        await _read_entity_snapshot(pool3)

    pool4 = _make_pool(fetch_return=[])
    with pytest.raises(EmptyEntitySnapshotError, match="sensor"):
        await _read_entity_snapshot(pool4, domain_filter="sensor")


# ---------------------------------------------------------------------------
# _send_notify
# ---------------------------------------------------------------------------


async def test_send_notify():
    """_send_notify delegates message and pool to _notify_owner_telegram unchanged."""
    pool = _make_pool()
    mock_notify = AsyncMock()

    with patch("butlers.jobs.home._notify_owner_telegram", mock_notify):
        await _send_notify(pool, "Hello, owner!")

    mock_notify.assert_awaited_once_with(pool, "Hello, owner!")

    # HTML content passes through unchanged
    html_msg = "<b>Bold</b> and <i>italic</i>"
    mock_notify2 = AsyncMock()
    with patch("butlers.jobs.home._notify_owner_telegram", mock_notify2):
        await _send_notify(pool, html_msg)
    mock_notify2.assert_awaited_once_with(pool, html_msg)
