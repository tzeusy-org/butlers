"""Unit tests for shared helpers in butlers.jobs.home.

Covers:
- HomeJobContext.create: credential resolution from contact info
- HomeJobContext async context manager: client lifecycle, Authorization header
- HomeJobContext: missing credentials (None ha_url / ha_token)
- _load_thresholds: key present (all values valid), key absent fallback,
  non-dict value fallback, per-key invalid value fallback (int and float types)
- _read_entity_snapshot: populated table (no filter), populated table with domain
  filter, empty table raises EmptyEntitySnapshotError, domain filter empty raises
- _send_notify: delegates to _notify_owner_telegram

All tests use mocked asyncpg pools — no real database or network required.

Issue: bu-o7fq
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
    """Return a minimal mock asyncpg pool."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.execute = AsyncMock()
    return pool


def _make_state_row(key: str, value: Any) -> dict:
    """Simulate a state store row dict."""
    import json

    return {"key": key, "value": json.dumps(value)}


class _FakeRecord:
    """Minimal asyncpg Record stub for entity snapshot rows."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def keys(self) -> Any:
        return self._data.keys()


# ---------------------------------------------------------------------------
# HomeJobContext tests
# ---------------------------------------------------------------------------


class TestHomeJobContextCreate:
    """Tests for HomeJobContext.create factory method."""

    @pytest.mark.asyncio
    async def test_resolves_ha_url_and_token(self) -> None:
        """create() populates ha_url and ha_token from owner contact info."""
        pool = _make_pool()

        async def _resolve(pool: Any, info_type: str) -> str | None:
            return {"home_assistant_url": "http://ha.local:8123", "home_assistant_token": "secret"}[
                info_type
            ]

        with patch("butlers.jobs.home.resolve_owner_entity_info", side_effect=_resolve):
            ctx = await HomeJobContext.create(pool)

        assert ctx.ha_url == "http://ha.local:8123"
        assert ctx.ha_token == "secret"

    @pytest.mark.asyncio
    async def test_missing_credentials_returns_none(self) -> None:
        """create() sets ha_url and ha_token to None when contact info is absent."""
        pool = _make_pool()

        with patch(
            "butlers.jobs.home.resolve_owner_entity_info",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ctx = await HomeJobContext.create(pool)

        assert ctx.ha_url is None
        assert ctx.ha_token is None

    @pytest.mark.asyncio
    async def test_client_is_none_before_entering_context(self) -> None:
        """HomeJobContext.client is None before the async with block."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="tok")
        assert ctx.client is None


class TestHomeJobContextAsyncContextManager:
    """Tests for HomeJobContext used as async context manager."""

    @pytest.mark.asyncio
    async def test_client_is_set_inside_context(self) -> None:
        """client is an open httpx.AsyncClient inside the async with block."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="tok")
        async with ctx as c:
            assert c.client is not None
            assert isinstance(c.client, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_client_is_none_after_exit(self) -> None:
        """client is set back to None after the async with block exits."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="tok")
        async with ctx:
            pass
        assert ctx.client is None

    @pytest.mark.asyncio
    async def test_authorization_header_set_when_token_present(self) -> None:
        """httpx.AsyncClient has Authorization header pre-set from ha_token."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="mytoken")
        async with ctx as c:
            assert c.client is not None
            # httpx.AsyncClient stores merged headers; check Authorization is present
            merged = dict(c.client.headers)
            assert "authorization" in {k.lower() for k in merged}
            assert "Bearer mytoken" in merged.get("authorization", merged.get("Authorization", ""))

    @pytest.mark.asyncio
    async def test_no_authorization_header_when_token_missing(self) -> None:
        """No Authorization header is added when ha_token is None."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token=None)
        async with ctx as c:
            assert c.client is not None
            merged = {k.lower(): v for k, v in c.client.headers.items()}
            assert "authorization" not in merged

    @pytest.mark.asyncio
    async def test_context_manager_returns_self(self) -> None:
        """__aenter__ returns the HomeJobContext itself."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="tok")
        async with ctx as c:
            assert c is ctx

    @pytest.mark.asyncio
    async def test_client_closed_on_exception(self) -> None:
        """Client is closed even when an exception is raised inside the block."""
        ctx = HomeJobContext(ha_url="http://ha.local:8123", ha_token="tok")
        try:
            async with ctx:
                raise ValueError("test error")
        except ValueError:
            pass
        # After exception, client should be cleaned up
        assert ctx.client is None


# ---------------------------------------------------------------------------
# _load_thresholds tests
# ---------------------------------------------------------------------------


class TestLoadThresholds:
    """Tests for the generic _load_thresholds helper."""

    @pytest.mark.asyncio
    async def test_returns_stored_values_when_key_present(self) -> None:
        """Returns stored values merged with defaults when state store has the key."""
        pool = _make_pool()
        stored = {"critical": 5, "warning": 15, "info": 25}

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=stored,
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert result == {"critical": 5, "warning": 15, "info": 25}

    @pytest.mark.asyncio
    async def test_returns_defaults_when_key_absent(self) -> None:
        """Returns defaults when state_get returns None (key not in store)."""
        pool = _make_pool()

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert result == dict(_DEFAULT_BATTERY_THRESHOLDS)

    @pytest.mark.asyncio
    async def test_logs_warning_on_absent_key(self, caplog: Any) -> None:
        """Logs a WARNING when the key is not found."""
        import logging

        pool = _make_pool()

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with caplog.at_level(logging.WARNING, logger="butlers.jobs.home"):
                await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert any("not found in state store" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_returns_defaults_when_value_not_dict(self) -> None:
        """Returns defaults when stored value is not a dict (e.g. a string)."""
        pool = _make_pool()

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value="not-a-dict",
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert result == dict(_DEFAULT_BATTERY_THRESHOLDS)

    @pytest.mark.asyncio
    async def test_returns_defaults_when_value_is_list(self) -> None:
        """Returns defaults when stored value is a list (wrong type)."""
        pool = _make_pool()

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=[10, 20],
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert result == dict(_DEFAULT_BATTERY_THRESHOLDS)

    @pytest.mark.asyncio
    async def test_per_key_invalid_value_falls_back_to_default(self) -> None:
        """Individual keys with non-castable values fall back to their defaults."""
        pool = _make_pool()
        # "critical" has a bad value; "warning" and "info" are valid
        stored = {"critical": "bad-value", "warning": 15, "info": 25}

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=stored,
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert result["critical"] == _DEFAULT_BATTERY_THRESHOLDS["critical"]
        assert result["warning"] == 15
        assert result["info"] == 25

    @pytest.mark.asyncio
    async def test_partial_override_fills_missing_keys_from_defaults(self) -> None:
        """Stored dict with only some keys: missing keys come from defaults."""
        pool = _make_pool()
        stored = {"critical": 5}  # "warning" and "info" missing

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=stored,
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert result["critical"] == 5
        assert result["warning"] == _DEFAULT_BATTERY_THRESHOLDS["warning"]
        assert result["info"] == _DEFAULT_BATTERY_THRESHOLDS["info"]

    @pytest.mark.asyncio
    async def test_float_type_casting_for_float_defaults(self) -> None:
        """Values are cast to float when defaults are float (not int)."""
        pool = _make_pool()
        stored = {"anomaly_pct": "30", "high_severity_pct": "150"}

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=stored,
        ):
            result = await _load_thresholds(pool, "energy", _DEFAULT_ENERGY_THRESHOLDS)

        assert result["anomaly_pct"] == 30.0
        assert isinstance(result["anomaly_pct"], float)
        assert result["high_severity_pct"] == 150.0

    @pytest.mark.asyncio
    async def test_int_type_casting_for_int_defaults(self) -> None:
        """Values are cast to int when defaults are int."""
        pool = _make_pool()
        stored = {"critical": 5.9, "warning": 15.1}  # floats in store, but defaults are int

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=stored,
        ):
            result = await _load_thresholds(
                pool, "offline_hours", _DEFAULT_OFFLINE_HOURS_THRESHOLDS
            )

        # int(5.9) == 5
        assert result["critical"] == 5
        assert isinstance(result["critical"], int)

    @pytest.mark.asyncio
    async def test_uses_correct_full_key_prefix(self) -> None:
        """state_get is called with 'home:thresholds:<key>'."""
        pool = _make_pool()
        captured: list[str] = []

        async def _mock_state_get(pool: Any, key: str) -> None:
            captured.append(key)
            return None

        with patch("butlers.jobs.home.state_get", side_effect=_mock_state_get):
            await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert captured == ["home:thresholds:battery"]

    @pytest.mark.asyncio
    async def test_extra_keys_in_store_are_ignored(self) -> None:
        """Extra keys in the stored dict that are not in defaults are ignored."""
        pool = _make_pool()
        stored = {"critical": 5, "warning": 15, "info": 25, "unknown_key": 999}

        with patch(
            "butlers.jobs.home.state_get",
            new_callable=AsyncMock,
            return_value=stored,
        ):
            result = await _load_thresholds(pool, "battery", _DEFAULT_BATTERY_THRESHOLDS)

        assert "unknown_key" not in result


# ---------------------------------------------------------------------------
# _read_entity_snapshot tests
# ---------------------------------------------------------------------------


class TestReadEntitySnapshot:
    """Tests for the generic _read_entity_snapshot helper."""

    @pytest.mark.asyncio
    async def test_returns_all_rows_when_no_domain_filter(self) -> None:
        """Returns all rows from ha_entity_snapshot when domain_filter is None."""
        rows = [
            _FakeRecord(
                {"entity_id": "sensor.temp", "state": "72", "attributes": {}, "last_updated": None}
            ),  # noqa: E501
            _FakeRecord(
                {"entity_id": "light.living", "state": "on", "attributes": {}, "last_updated": None}
            ),  # noqa: E501
        ]
        pool = _make_pool(fetch_return=rows)

        result = await _read_entity_snapshot(pool)

        assert len(result) == 2
        pool.fetch.assert_awaited_once()
        # No LIKE clause when no domain filter
        call_sql = pool.fetch.call_args[0][0]
        assert "LIKE" not in call_sql

    @pytest.mark.asyncio
    async def test_returns_filtered_rows_with_domain_filter(self) -> None:
        """Returns only rows matching domain_filter when provided."""
        rows = [
            _FakeRecord(
                {"entity_id": "sensor.temp", "state": "72", "attributes": {}, "last_updated": None}
            ),  # noqa: E501
        ]
        pool = _make_pool(fetch_return=rows)

        result = await _read_entity_snapshot(pool, domain_filter="sensor")

        assert len(result) == 1
        # LIKE clause should be in the SQL
        call_sql = pool.fetch.call_args[0][0]
        assert "LIKE" in call_sql
        # Verify the pattern argument is correct
        call_args = pool.fetch.call_args[0]
        assert call_args[1] == "sensor.%"

    @pytest.mark.asyncio
    async def test_raises_on_empty_table(self) -> None:
        """Raises EmptyEntitySnapshotError when ha_entity_snapshot returns no rows."""
        pool = _make_pool(fetch_return=[])

        with pytest.raises(EmptyEntitySnapshotError):
            await _read_entity_snapshot(pool)

    @pytest.mark.asyncio
    async def test_raises_on_empty_filtered_result(self) -> None:
        """Raises EmptyEntitySnapshotError when filtered query returns no rows."""
        pool = _make_pool(fetch_return=[])

        with pytest.raises(EmptyEntitySnapshotError):
            await _read_entity_snapshot(pool, domain_filter="binary_sensor")

    @pytest.mark.asyncio
    async def test_error_message_includes_domain(self) -> None:
        """EmptyEntitySnapshotError message includes the domain_filter."""
        pool = _make_pool(fetch_return=[])

        with pytest.raises(EmptyEntitySnapshotError, match="sensor"):
            await _read_entity_snapshot(pool, domain_filter="sensor")

    @pytest.mark.asyncio
    async def test_returns_list_not_asyncpg_result(self) -> None:
        """Return value is a plain Python list."""
        rows = [
            _FakeRecord(
                {"entity_id": "sensor.temp", "state": "72", "attributes": {}, "last_updated": None}
            ),  # noqa: E501
        ]
        pool = _make_pool(fetch_return=rows)

        result = await _read_entity_snapshot(pool)

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# _send_notify tests
# ---------------------------------------------------------------------------


class TestSendNotify:
    """Tests for the _send_notify shared helper."""

    @pytest.mark.asyncio
    async def test_delegates_to_notify_owner_telegram(self) -> None:
        """_send_notify forwards pool and message to _notify_owner_telegram."""
        pool = _make_pool()
        captured: list[tuple[Any, str]] = []

        async def _mock_notify(pool: Any, message: str) -> None:
            captured.append((pool, message))

        with patch("butlers.jobs.home._notify_owner_telegram", side_effect=_mock_notify):
            await _send_notify(pool, "Hello, owner!")

        assert len(captured) == 1
        assert captured[0][0] is pool
        assert captured[0][1] == "Hello, owner!"

    @pytest.mark.asyncio
    async def test_passes_html_message_through(self) -> None:
        """Message content including HTML tags is passed through unchanged."""
        pool = _make_pool()
        msg = "<b>Bold</b> and <i>italic</i>"
        received: list[str] = []

        async def _mock_notify(pool: Any, message: str) -> None:
            received.append(message)

        with patch("butlers.jobs.home._notify_owner_telegram", side_effect=_mock_notify):
            await _send_notify(pool, msg)

        assert received[0] == msg

    @pytest.mark.asyncio
    async def test_awaits_underlying_call(self) -> None:
        """_send_notify properly awaits the underlying coroutine."""
        pool = _make_pool()
        mock_notify = AsyncMock()

        with patch("butlers.jobs.home._notify_owner_telegram", mock_notify):
            await _send_notify(pool, "test")

        mock_notify.assert_awaited_once_with(pool, "test")
