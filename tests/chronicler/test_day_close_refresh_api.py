"""Tests for POST /api/chronicler/aggregate/day-close/refresh.

Covers:
- Rate-limit triggers 429 with ErrorResponse envelope (code=day_close_rate_limited,
  details.retry_after_seconds).
- Successful refresh writes fresh cache row via write_day_close_cache().
- Invalid candidates are distinguishable whether they are contained behind an
  admissible cache row or retained as an audit-only invalid row.
- 404-equivalent: no cached row does NOT trigger rate-limit (falls through to dispatch).
- 503 when no dispatch callable is wired.
- 400 on invalid timezone.

(The no-LLM-import guardrail for router.py is authoritative in
tests/contracts/test_chronicler_no_llm.py.)
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler.day_close_writer import DayCloseCacheWriteOutcome

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T_OUTSIDE_24H = datetime(2026, 4, 25, 6, 0, 0, tzinfo=UTC) - timedelta(
    hours=25
)  # built 25 hours ago (outside limit)

_CACHE_KEY = "day_close:2026-04-24:tz:UTC"


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


class _AcquireCM:
    """Minimal async context manager mimicking asyncpg's ``pool.acquire()``,
    used by ``editorial.record_coverage_witness`` (called on every
    successful refresh dispatch, independent of admission)."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _TransactionCM:
    """Minimal async context manager mimicking ``asyncpg.Connection.transaction``."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _mock_pool(
    *,
    fetchrow_side_effect: list | None = None,
    fetchrow_returns: Any = None,
    conn_fetchrow_side_effect: list | None = None,
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
    conn = AsyncMock()
    conn.transaction = MagicMock(side_effect=_TransactionCM)
    if conn_fetchrow_side_effect is not None:
        conn.fetchrow = AsyncMock(side_effect=conn_fetchrow_side_effect)
    else:
        conn.fetchrow = AsyncMock(return_value=None)
    pool.acquire = MagicMock(side_effect=lambda: _AcquireCM(conn))
    return pool


def _mock_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


def _make_spawner_result(
    *,
    success: bool = True,
    output: str | None = "Refreshed day-close summary prose.",
    date_label: str | None = "2026-04-24",
    episodes: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.output = output
    r.tool_calls = (
        [
            {
                "name": "chronicler_day_close_bundle",
                "input": {"date_label": date_label, "timezone": "UTC"},
                "outcome": "success",
                "result": {
                    "date": date_label,
                    "citations": [],
                    "episodes": episodes if episodes is not None else [],
                    "events": events if events is not None else [],
                },
            }
        ]
        if date_label
        else []
    )
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
        assert body["invalid"] is False
        assert body["invalid_reason"] is None

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

    async def test_contained_invalid_candidate_is_externally_distinguishable(self):
        """Invalid prose cannot replace an admissible row but is reported to the caller."""
        preserved_built_at = datetime.now(UTC) - timedelta(hours=25)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _row({"cache_built_at": _T_OUTSIDE_24H}),
                _row({"prompt": "Day close prompt."}),
                _row({"cache_built_at": preserved_built_at}),
            ],
            conn_fetchrow_side_effect=[
                _row(
                    {
                        "prose": "A valid earlier retrospective.",
                        "date_label": "2026-04-24",
                        "invalid_reason": None,
                    }
                )
            ],
        )
        dispatch_fn = AsyncMock(
            return_value=_make_spawner_result(
                output='```json\n{"tool": "chronicler_list_episodes"}\n```'
            )
        )

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_refresh(app)

        assert resp.status_code == 200, resp.text
        assert resp.json() == {
            "cache_key": _CACHE_KEY,
            "cache_built_at": preserved_built_at.isoformat().replace("+00:00", "Z"),
            "invalid": True,
            "invalid_reason": "inadmissible_prose",
        }
        mock_upsert.assert_not_awaited()

    async def test_audit_only_invalid_candidate_returns_no_prose(self):
        """An invalid candidate without an admissible row remains audit-only."""
        invalid_built_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                None,
                _row({"prompt": "Day close prompt."}),
                _row({"cache_built_at": invalid_built_at}),
            ],
            conn_fetchrow_side_effect=[None],
        )
        dispatch_fn = AsyncMock(
            return_value=_make_spawner_result(
                date_label="2026-04-23",
                output="This prose is bound to the wrong day.",
            )
        )

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_refresh(app)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["invalid"] is True
        assert body["invalid_reason"] == "date_mismatch"
        assert body["cache_key"] == _CACHE_KEY
        assert "prose" not in body
        assert "provenance_refs" not in body
        mock_upsert.assert_awaited_once()

    async def test_dispatch_called_with_prompt_from_scheduled_tasks(self):
        """Dispatch keeps the scheduled prompt and appends only its trusted target."""
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
        prompt = dispatch_fn.call_args.kwargs["prompt"]
        assert prompt.startswith(expected_prompt)
        assert "Trusted refresh target:" in prompt

    async def test_refresh_appends_trusted_target_and_binds_writer_to_it(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Refresh retains the scheduled path while binding it to the request target."""
        scheduled_prompt = "The chronicler_day_close cron prompt text."
        refreshed_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                None,
                _row({"prompt": scheduled_prompt}),
                _row({"cache_built_at": refreshed_at}),
            ]
        )
        dispatch_fn = AsyncMock(return_value=_make_spawner_result(output="prose"))
        chronicler_mod = _load_chronicler_router()
        writer = AsyncMock(return_value=DayCloseCacheWriteOutcome())
        monkeypatch.setattr(chronicler_mod, "write_day_close_cache", writer)
        app = _make_app_with_dispatch(pool, dispatch_fn)

        resp = await _post_refresh(
            app,
            date="2024-02-03",
            tz="America/Los_Angeles",
        )

        assert resp.status_code == 200, resp.text
        dispatch_fn.assert_awaited_once()
        prompt = dispatch_fn.call_args.kwargs["prompt"]
        assert prompt.startswith(scheduled_prompt)
        assert "trusted refresh target" in prompt.lower()
        assert "date_label=2024-02-03" in prompt
        assert "timezone=America/Los_Angeles" in prompt
        assert "exactly once" in prompt.lower()
        assert dispatch_fn.call_args.kwargs["trigger_source"] == "api:day_close_refresh:2024-02-03"

        writer.assert_awaited_once()
        assert writer.call_args.kwargs["target_date"] == date(2024, 2, 3)
        assert writer.call_args.kwargs["tz"] == "America/Los_Angeles"


# ---------------------------------------------------------------------------
# Tests: executed quiet close (200 without a cache row)
# ---------------------------------------------------------------------------


class TestDayCloseRefreshQuiet:
    async def test_quiet_empty_bundle_returns_200_without_cache_row(self):
        """A validated empty bundle is a successful quiet close, not a write failure."""
        pool = _mock_pool(
            fetchrow_side_effect=[
                None,
                _row({"prompt": "Day close prompt."}),
            ]
        )
        dispatch_fn = AsyncMock(return_value=_make_spawner_result(output=""))

        with patch(
            "butlers.chronicler.day_close_writer.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_refresh(app)

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cache_key": _CACHE_KEY, "quiet": True}
        mock_upsert.assert_not_awaited()
        # The response must branch before a post-write lookup because no row exists.
        assert pool.fetchrow.await_count == 2

    async def test_quiet_empty_bundle_never_returns_old_cache_timestamp(self):
        """A quiet rerun cannot reuse the stale row it intentionally did not replace."""
        old_built_at = datetime.now(UTC) - timedelta(hours=25)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _row({"cache_built_at": old_built_at}),
                _row({"prompt": "Day close prompt."}),
            ]
        )
        dispatch_fn = AsyncMock(return_value=_make_spawner_result(output="  \n"))
        app = _make_app_with_dispatch(pool, dispatch_fn)

        resp = await _post_refresh(app)

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"cache_key": _CACHE_KEY, "quiet": True}
        assert "cache_built_at" not in resp.json()
        assert pool.fetchrow.await_count == 2

    @pytest.mark.parametrize(
        "result",
        [
            _make_spawner_result(output="", date_label=None),
            _make_spawner_result(output=None, date_label=None),
            _make_spawner_result(output="", episodes=[{"id": "episode-1"}]),
        ],
        ids=[
            "missing-canonical-capture",
            "missing-canonical-capture-and-prose",
            "nonempty-bundle-with-blank-prose",
        ],
    )
    async def test_blank_output_without_a_valid_empty_bundle_returns_502(self, result: MagicMock):
        """Quiet is reserved for an executed canonical bundle that proves emptiness."""
        old_built_at = datetime.now(UTC) - timedelta(hours=25)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _row({"cache_built_at": old_built_at}),
                _row({"prompt": "Day close prompt."}),
            ]
        )
        app = _make_app_with_dispatch(pool, AsyncMock(return_value=result))

        resp = await _post_refresh(app)

        assert resp.status_code == 502
        assert resp.json()["error"]["code"] == "cache_write_failed"
        assert pool.fetchrow.await_count == 2


# ---------------------------------------------------------------------------
# Tests: no cache write when dispatch is unavailable
# ---------------------------------------------------------------------------


class TestDayCloseRefreshNoDispatch:
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
    async def test_missing_timezone_returns_400_before_rate_limit_or_dispatch(self):
        """Refresh has no UTC default: tz is required before all cache work."""
        pool = _mock_pool(fetchrow_returns=None)
        dispatch_fn = AsyncMock()
        app = _make_app_with_dispatch(pool, dispatch_fn)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/chronicler/aggregate/day-close/refresh",
                json={"date": "2026-04-24"},
            )

        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "missing_parameter"
        assert body["error"]["butler"] == "chronicler"
        pool.fetchrow.assert_not_awaited()
        dispatch_fn.assert_not_awaited()

    async def test_null_timezone_returns_the_same_structured_400(self):
        """OpenAPI is non-nullable, but a malformed runtime body stays a 400 envelope."""
        pool = _mock_pool(fetchrow_returns=None)
        dispatch_fn = AsyncMock()
        app = _make_app_with_dispatch(pool, dispatch_fn)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/chronicler/aggregate/day-close/refresh",
                json={"date": "2026-04-24", "tz": None},
            )

        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "missing_parameter"
        pool.fetchrow.assert_not_awaited()
        dispatch_fn.assert_not_awaited()

    async def test_invalid_timezone_returns_400(self):
        """Invalid IANA timezone returns 400 with error envelope."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, tz="Not/A/Timezone")
        assert resp.status_code == 400
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "invalid_timezone"

    async def test_empty_timezone_returns_400(self):
        """An empty string is not an exact IANA timezone."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, tz="")

        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_timezone"
        pool.fetchrow.assert_not_awaited()

    async def test_valid_non_utc_timezone_accepted(self):
        """A valid non-UTC timezone does not trigger the tz validation error."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, tz="America/New_York")
        # Should proceed past tz validation → hit dispatch guard → 503
        assert resp.status_code == 503


def test_day_close_openapi_requires_a_nonnullable_query_tuple() -> None:
    """OpenAPI must describe the same valid-call contract as the API boundary."""
    schema = create_app(api_key="").openapi()
    day_close_path = schema["paths"]["/api/chronicler/aggregate/day-close"]
    date_parameter = next(
        parameter
        for parameter in day_close_path["get"]["parameters"]
        if parameter["name"] == "date"
    )
    tz_parameter = next(
        parameter for parameter in day_close_path["get"]["parameters"] if parameter["name"] == "tz"
    )

    assert date_parameter["required"] is True
    assert date_parameter["schema"] == {"type": "string", "format": "date", "minLength": 1}
    assert tz_parameter["required"] is True
    assert tz_parameter["schema"] == {"type": "string", "minLength": 1}
    assert [parameter["name"] for parameter in day_close_path["get"]["parameters"]].count(
        "date"
    ) == 1

    refresh_path = schema["paths"]["/api/chronicler/aggregate/day-close/refresh"]
    request_schema = refresh_path["post"]["requestBody"]["content"]["application/json"]["schema"]
    request_ref = request_schema["$ref"].rsplit("/", 1)[-1]
    refresh_schema = schema["components"]["schemas"][request_ref]
    assert "tz" in refresh_schema["required"]
    assert refresh_schema["properties"]["tz"] == {"type": "string", "minLength": 1}


class TestDayCloseRefreshTimezoneIdentity:
    async def test_rate_limit_uses_date_and_exact_timezone(self):
        """A recent cache row only throttles the same requested local-day tuple."""
        cache_built_at = datetime.now(UTC) - timedelta(hours=1)
        pool = _mock_pool(fetchrow_returns=_row({"cache_built_at": cache_built_at}))
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, tz="America/Los_Angeles")

        assert resp.status_code == 429
        lookup = pool.fetchrow.await_args.args
        assert lookup[1] == "day_close:2026-04-24:tz:America/Los_Angeles"


class TestDayCloseRefreshSettledDate:
    def test_today_in_timezone_uses_the_request_timezone_at_utc_boundary(self):
        """A local date can still be yesterday while UTC has already crossed midnight."""
        chronicler_mod = _load_chronicler_router()

        assert chronicler_mod._today_in_timezone(
            ZoneInfo("America/Los_Angeles"),
            now=datetime(2026, 1, 2, 0, 30, tzinfo=UTC),
        ) == date(2026, 1, 1)

    async def test_today_and_future_are_rejected_before_rate_limit_or_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """Unsettled local dates must not consume a rate-limit lookup or dispatch."""
        chronicler_mod = _load_chronicler_router()
        monkeypatch.setattr(
            chronicler_mod,
            "_today_in_timezone",
            lambda _zone: date(2026, 1, 2),
            raising=False,
        )
        pool = _mock_pool(fetchrow_returns=None)
        dispatch_fn = AsyncMock()
        app = _make_app_with_dispatch(pool, dispatch_fn)

        for target in ("2026-01-02", "2026-01-03"):
            resp = await _post_refresh(app, date=target, tz="America/Los_Angeles")
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "day_close_not_settled"

        pool.fetchrow.assert_not_awaited()
        dispatch_fn.assert_not_awaited()

    async def test_historical_local_date_reaches_the_existing_dispatch_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The request-local day before today remains eligible for refresh."""
        chronicler_mod = _load_chronicler_router()
        monkeypatch.setattr(
            chronicler_mod,
            "_today_in_timezone",
            lambda _zone: date(2026, 1, 2),
            raising=False,
        )
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)

        resp = await _post_refresh(app, date="2026-01-01", tz="America/Los_Angeles")

        assert resp.status_code == 503
        pool.fetchrow.assert_awaited_once()


# The no-LLM-import guardrail for router.py is authoritative in
# tests/contracts/test_chronicler_no_llm.py.
