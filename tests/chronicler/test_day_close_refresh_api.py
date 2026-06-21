"""Tests for POST /api/chronicler/aggregate/day-close/refresh.

Covers:
- Rate-limit triggers 429 with ErrorResponse envelope (code=day_close_rate_limited,
  details.retry_after_seconds).
- Successful refresh writes fresh cache row via write_day_close_cache().
- 404-equivalent: no cached row does NOT trigger rate-limit (falls through to dispatch).
- 503 when no dispatch callable is wired.
- 400 on invalid timezone.

(The no-LLM-import guardrail for router.py is authoritative in
tests/contracts/test_chronicler_no_llm.py.)
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T_OUTSIDE_24H = datetime(2026, 4, 25, 6, 0, 0, tzinfo=UTC) - timedelta(
    hours=25
)  # built 25 hours ago (outside limit)

_CACHE_KEY = "day_close:2026-04-24"


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(key, default)


def _row(data: dict) -> _Row:
    return _Row(data)


def _mock_pool(
    *,
    fetchrow_side_effect: list | None = None,
    fetchrow_returns: Any = None,
    execute_returns: str = "OK",
) -> AsyncMock:
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_returns)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchval = AsyncMock(return_value=0)
    pool.execute = AsyncMock(return_value=execute_returns)
    return pool


def _mock_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


def _make_spawner_result(
    *,
    success: bool = True,
    output: str = "Refreshed day-close summary prose.",
) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.output = output
    r.tool_calls = []
    return r


# ---------------------------------------------------------------------------
# Dynamic module loading for the chronicler router
# ---------------------------------------------------------------------------


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def _make_app_no_dispatch(pool: AsyncMock) -> Any:
    """App with no dispatch function wired (returns 503 for refresh)."""
    chronicler_mod = _load_chronicler_router()
    db = _mock_db(pool)
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    # _get_day_close_dispatch_fn left as-is → returns None
    return app


def _make_app_with_dispatch(pool: AsyncMock, dispatch_fn: Any) -> Any:
    """App with a dispatch function wired."""
    chronicler_mod = _load_chronicler_router()
    db = _mock_db(pool)
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    app.dependency_overrides[chronicler_mod._get_day_close_dispatch_fn] = lambda: dispatch_fn
    return app


async def _post_refresh(app: Any, date: str = "2026-04-24", tz: str = "UTC") -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/chronicler/aggregate/day-close/refresh",
            json={"date": date, "tz": tz},
        )


# ---------------------------------------------------------------------------
# Tests: rate-limit (429)
# ---------------------------------------------------------------------------


class TestDayCloseRefreshRateLimit:
    async def test_rate_limit_returns_429_when_cache_built_within_24h(self):
        """If cache_built_at is within the last 24 h, endpoint returns 429."""
        # Use a live timestamp so the age stays within the 24h window regardless of when the test
        # runs. A fixed past timestamp (e.g. _T_WITHIN_24H) becomes stale as time passes and
        # causes the rate-limit check to fall through, producing a 503 instead of 429.
        cache_built_at = datetime.now(UTC) - timedelta(hours=1)
        pool = _mock_pool(fetchrow_returns=_row({"cache_built_at": cache_built_at}))
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app)
        assert resp.status_code == 429
        body = resp.json()
        assert "error" in body
        err = body["error"]
        assert err["code"] == "day_close_rate_limited"
        assert err["butler"] == "chronicler"
        assert "retry_after_seconds" in err["details"]
        assert isinstance(err["details"]["retry_after_seconds"], int)
        assert err["details"]["retry_after_seconds"] > 0

    async def test_rate_limit_retry_after_is_positive(self):
        """retry_after_seconds reflects the remaining window, rounded down."""
        # cache_built_at = 23 hours ago → 1 hour left in the 24h window
        cache_built_at = datetime.now(UTC) - timedelta(hours=23)
        pool = _mock_pool(fetchrow_returns=_row({"cache_built_at": cache_built_at}))
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app)
        assert resp.status_code == 429
        retry_after = resp.json()["error"]["details"]["retry_after_seconds"]
        # ~1 hour left: between 3500 s and 3601 s (allow drift)
        assert 3500 <= retry_after <= 3601

    async def test_rate_limit_not_triggered_when_cache_outside_24h(self):
        """If cache_built_at is > 24 h ago, rate-limit is NOT triggered.

        In this test no dispatch function is wired, so we expect 503 (not 429).
        This confirms the rate-limit gate passed and execution continued.
        """
        pool = _mock_pool(fetchrow_returns=_row({"cache_built_at": _T_OUTSIDE_24H}))
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app)
        # No rate-limit → falls through to dispatch guard → 503 (no dispatch wired)
        assert resp.status_code == 503

    async def test_rate_limit_not_triggered_when_no_cache_row(self):
        """If no cache row exists, rate-limit check is skipped entirely.

        Execution continues past rate-limit to dispatch guard → 503.
        """
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: successful refresh (writes fresh cache row)
# ---------------------------------------------------------------------------


class TestDayCloseRefreshSuccess:
    async def test_successful_refresh_writes_fresh_cache_row(self):
        """Refresh dispatches and writes a fresh tier2_cache row.

        Acceptance: successful refresh writes fresh cache row (via write_day_close_cache).
        """
        # No existing row → no rate-limit.
        # scheduled_tasks row for the prompt lookup.
        # Final fetchrow returns fresh cache_built_at.
        fresh_built_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                None,  # rate-limit check: no existing cache row
                _row({"prompt": "Run the Chronicler day-close interpretation for yesterday."}),
                _row({"cache_built_at": fresh_built_at}),  # final row fetch after write
            ]
        )

        dispatch_result = _make_spawner_result()
        dispatch_fn = AsyncMock(return_value=dispatch_result)

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = None
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_refresh(app)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["cache_key"] == _CACHE_KEY
        assert "cache_built_at" in body

        # Verify the dispatch was called with trigger_source indicating API origin.
        dispatch_fn.assert_awaited_once()
        call_kwargs = dispatch_fn.call_args.kwargs
        assert "prompt" in call_kwargs
        assert "trigger_source" in call_kwargs
        assert "api:day_close_refresh" in call_kwargs["trigger_source"]

        # Verify upsert_tier2_cache was called (fresh cache row written).
        mock_upsert.assert_awaited_once()
        upsert_kwargs = mock_upsert.call_args.kwargs
        assert upsert_kwargs["cache_key"] == _CACHE_KEY
        assert upsert_kwargs["prose"] == "Refreshed day-close summary prose."

    async def test_successful_refresh_outside_rate_limit_window(self):
        """Refresh bypasses rate-limit when existing row is older than 24h."""
        fresh_built_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _row({"cache_built_at": _T_OUTSIDE_24H}),  # old row → no rate-limit
                _row({"prompt": "Day close prompt."}),
                _row({"cache_built_at": fresh_built_at}),
            ]
        )
        dispatch_result = _make_spawner_result(output="Fresh prose after stale row.")
        dispatch_fn = AsyncMock(return_value=dispatch_result)

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = None
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_refresh(app)

        assert resp.status_code == 200
        mock_upsert.assert_awaited_once()

    async def test_dispatch_called_with_prompt_from_scheduled_tasks(self):
        """Dispatch fn receives the prompt from scheduled_tasks (not a new prompt)."""
        fresh_built_at = datetime.now(UTC)
        expected_prompt = "The chronicler_day_close cron prompt text."
        pool = _mock_pool(
            fetchrow_side_effect=[
                None,  # no rate-limit
                _row({"prompt": expected_prompt}),
                _row({"cache_built_at": fresh_built_at}),
            ]
        )
        dispatch_fn = AsyncMock(return_value=_make_spawner_result(output="prose"))

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ):
            app = _make_app_with_dispatch(pool, dispatch_fn)
            await _post_refresh(app)

        dispatch_fn.assert_awaited_once()
        assert dispatch_fn.call_args.kwargs["prompt"] == expected_prompt


# ---------------------------------------------------------------------------
# Tests: 503 when no dispatch wired
# ---------------------------------------------------------------------------


class TestDayCloseRefreshNoDispatch:
    async def test_returns_503_when_dispatch_not_wired(self):
        """When no dispatch fn is wired, endpoint returns 503."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app)
        assert resp.status_code == 503

    async def test_no_cache_write_when_no_dispatch(self):
        """No DB write occurs when dispatch is unavailable (no side-effects)."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            await _post_refresh(app)

        mock_upsert.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: invalid timezone
# ---------------------------------------------------------------------------


class TestDayCloseRefreshValidation:
    async def test_invalid_timezone_returns_400(self):
        """Invalid IANA timezone returns 400 with error envelope."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, tz="Not/A/Timezone")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "invalid_timezone"

    async def test_valid_non_utc_timezone_accepted(self):
        """A valid non-UTC timezone does not trigger the tz validation error."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, tz="America/New_York")
        # Should proceed past tz validation → hit dispatch guard → 503
        assert resp.status_code == 503


# The no-LLM-import guardrail for router.py is authoritative in
# tests/contracts/test_chronicler_no_llm.py.
