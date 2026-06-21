"""Tests for POST /api/chronicler/episodes/{id}/explain.

Covers:
- Happy path: dispatch succeeds, cache row is written, response is 200.
- Rate-limit: 429 with code=episode_explain_rate_limited and retry_after_seconds.
- Rate-limit not triggered when existing row is outside 24 h window.
- Sensitive episode: 403 with code=episode_explain_excluded.
- Restricted episode: 403 with code=episode_explain_excluded.
- Episode not found: 404.
- No dispatch wired: 503.

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
from uuid import UUID

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_EPISODE_ID = UUID("00000000-0000-0000-0000-000000000001")
_EPISODE_ID_STR = str(_EPISODE_ID)
_CACHE_KEY = f"episode_explain:{_EPISODE_ID}"

_T_WITHIN_24H = datetime.now(UTC) - timedelta(hours=2)  # live: always within rate-limit window
_T_OUTSIDE_24H = datetime.now(UTC) - timedelta(hours=25)  # live: always outside rate-limit window


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


def _make_episode_row(
    *,
    privacy: str = "normal",
    canonical_privacy: str = "normal",
) -> _Row:
    _now = datetime.now(UTC)
    return _row(
        {
            "id": _EPISODE_ID,
            "source_name": "work",
            "source_ref": "ref-ep-1",
            "episode_type": "session",
            "start_at": _now - timedelta(hours=2),
            "end_at": _now - timedelta(hours=1),
            "precision": "minute",
            "title": "Deep work block",
            "payload": {},
            "privacy": privacy,
            "retention_days": None,
            "tombstone_at": None,
            "canonical_start_at": _now - timedelta(hours=2),
            "canonical_end_at": _now - timedelta(hours=1),
            "canonical_title": "Deep work block",
            "canonical_privacy": canonical_privacy,
            "corrected_at": None,
            "correction_note": None,
            "created_at": _now - timedelta(days=1),
            "updated_at": _now - timedelta(hours=1),
        }
    )


def _mock_pool(
    *,
    fetchrow_side_effect: list | None = None,
    fetchrow_returns: Any = None,
    fetch_returns: list | None = None,
) -> AsyncMock:
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        pool.fetchrow = AsyncMock(return_value=fetchrow_returns)
    pool.fetch = AsyncMock(return_value=fetch_returns or [])
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _mock_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


def _make_spawner_result(
    *,
    success: bool = True,
    output: str = "This episode represents a focused 1-hour deep work block.",
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
    """App with no dispatch function wired (returns 503 for explain)."""
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


async def _post_explain(app: Any, episode_id: str = _EPISODE_ID_STR) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(f"/api/chronicler/episodes/{episode_id}/explain")


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


class TestEpisodeExplainHappyPath:
    async def test_happy_path_returns_200_with_cache_info(self):
        """Happy path: dispatch succeeds and cache row is written.

        Acceptance criterion 1 & 2: new backend route + LLM dispatch.
        """
        fresh_built_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),  # episode lookup
                None,  # rate-limit check: no existing cache row
                _row({"cache_built_at": fresh_built_at}),  # final row fetch after write
            ],
            fetch_returns=[],  # no linked events, no corrections
        )
        dispatch_result = _make_spawner_result()
        dispatch_fn = AsyncMock(return_value=dispatch_result)

        with patch(
            "chronicler_api_router.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = None
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_explain(app)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["episode_id"] == _EPISODE_ID_STR
        assert body["cache_key"] == _CACHE_KEY
        assert "cache_built_at" in body

    async def test_dispatch_called_with_episode_bundle_prompt(self):
        """Dispatch receives a prompt containing the episode bundle."""
        fresh_built_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),
                None,  # no existing rate-limit row
                _row({"cache_built_at": fresh_built_at}),
            ],
            fetch_returns=[],
        )
        dispatch_fn = AsyncMock(return_value=_make_spawner_result())

        with patch(
            "chronicler_api_router.upsert_tier2_cache",
            new_callable=AsyncMock,
        ):
            app = _make_app_with_dispatch(pool, dispatch_fn)
            await _post_explain(app)

        dispatch_fn.assert_awaited_once()
        call_kwargs = dispatch_fn.call_args.kwargs
        assert "prompt" in call_kwargs
        assert "trigger_source" in call_kwargs
        # Prompt must contain the episode bundle
        assert "episode" in call_kwargs["prompt"].lower()
        # trigger_source must identify this as an episode explain
        assert f"api:episode_explain:{_EPISODE_ID}" in call_kwargs["trigger_source"]

    async def test_cache_key_is_episode_scoped(self):
        """Cache key is scoped to the episode ID, not the date."""
        fresh_built_at = datetime.now(UTC)
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),
                None,
                _row({"cache_built_at": fresh_built_at}),
            ],
            fetch_returns=[],
        )
        dispatch_fn = AsyncMock(return_value=_make_spawner_result())

        with patch(
            "chronicler_api_router.upsert_tier2_cache",
            new_callable=AsyncMock,
        ) as mock_upsert:
            mock_upsert.return_value = None
            app = _make_app_with_dispatch(pool, dispatch_fn)
            resp = await _post_explain(app)

        assert resp.status_code == 200
        upsert_kwargs = mock_upsert.call_args.kwargs
        assert upsert_kwargs["cache_key"] == _CACHE_KEY
        assert "episode_explain:" in _CACHE_KEY


# ---------------------------------------------------------------------------
# Tests: rate-limit (429)
# ---------------------------------------------------------------------------


class TestEpisodeExplainRateLimit:
    async def test_rate_limit_returns_429_within_24h(self):
        """If the last explain for this episode was < 24 h ago, return 429.

        Acceptance criterion 3: rate-limit middleware.
        """
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),  # episode lookup
                _row({"cache_built_at": _T_WITHIN_24H}),  # existing recent cache row
            ]
        )
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        assert resp.status_code == 429
        body = resp.json()
        assert "error" in body
        err = body["error"]
        assert err["code"] == "episode_explain_rate_limited"
        assert err["butler"] == "chronicler"
        assert "retry_after_seconds" in err["details"]
        assert isinstance(err["details"]["retry_after_seconds"], int)
        assert err["details"]["retry_after_seconds"] > 0

    async def test_rate_limit_not_triggered_when_row_outside_24h(self):
        """If the last explain was > 24 h ago, rate-limit is NOT triggered.

        Falls through to dispatch guard → 503 (no dispatch wired).
        """
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),  # episode lookup
                _row({"cache_built_at": _T_OUTSIDE_24H}),  # old cache row
            ]
        )
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        # Rate-limit should NOT trigger; falls through to dispatch guard → 503
        assert resp.status_code == 503

    async def test_rate_limit_not_triggered_when_no_existing_row(self):
        """If no cache row exists, rate-limit is not triggered."""
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),  # episode lookup
                None,  # no existing cache row
            ]
        )
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        # Falls through to dispatch guard → 503
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: sensitive episode excluded (403)
# ---------------------------------------------------------------------------


class TestEpisodeExplainSensitiveExcluded:
    async def test_sensitive_episode_returns_403(self):
        """Sensitive episodes are excluded from LLM drilldown (403).

        Acceptance criterion 6 (backend sensitive-episode excluded test).
        """
        pool = _mock_pool(fetchrow_returns=_make_episode_row(canonical_privacy="sensitive"))
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        assert resp.status_code == 403
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "episode_explain_excluded"
        assert body["error"]["butler"] == "chronicler"

    async def test_restricted_episode_returns_403(self):
        """Restricted episodes are also excluded from LLM drilldown (403)."""
        pool = _mock_pool(fetchrow_returns=_make_episode_row(canonical_privacy="restricted"))
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "episode_explain_excluded"

    async def test_normal_episode_not_excluded(self):
        """Normal privacy episodes are NOT excluded.

        Falls through to dispatch guard → 503 (confirming 403 was not returned).
        """
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(canonical_privacy="normal"),  # episode lookup
                None,  # rate-limit check
            ]
        )
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        # Should NOT be 403; dispatch guard returns 503 instead
        assert resp.status_code != 403
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Tests: episode not found (404)
# ---------------------------------------------------------------------------


class TestEpisodeExplainNotFound:
    async def test_returns_404_when_episode_not_found(self):
        """Returns 404 if the episode does not exist."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app, episode_id=_EPISODE_ID_STR)

        assert resp.status_code == 404

    async def test_returns_422_on_invalid_uuid(self):
        """Returns 422 (Unprocessable Entity) if the episode_id is not a valid UUID."""
        pool = _mock_pool(fetchrow_returns=None)
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app, episode_id="not-a-uuid")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests: no dispatch wired (503)
# ---------------------------------------------------------------------------


class TestEpisodeExplainNoDispatch:
    async def test_returns_503_when_dispatch_not_wired(self):
        """Returns 503 when no dispatch callable is wired."""
        pool = _mock_pool(
            fetchrow_side_effect=[
                _make_episode_row(),  # episode lookup
                None,  # no existing cache row → rate-limit not triggered
            ]
        )
        app = _make_app_no_dispatch(pool)
        resp = await _post_explain(app)

        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "dispatch_unavailable"


# The no-LLM-import guardrail for router.py is authoritative in
# tests/contracts/test_chronicler_no_llm.py. The /episodes/{episode_id}/explain
# route is exercised behaviorally by the 200/403/404/422/503 tests above.
