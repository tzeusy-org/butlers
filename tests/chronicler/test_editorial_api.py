"""Tests for the Chronicles editorial API endpoints."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.chronicler import editorial
from butlers.chronicler.editorial import (
    AttentionItem,
    BriefingPayload,
    KpiSnapshot,
    LaneHours,
    RecentDay,
    Streaks,
)

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"


class _Row(dict):
    """dict subclass that mimics asyncpg Record."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class _Conn:
    def __init__(self, *, fetchrow_returns: list[_Row | None] | None = None) -> None:
        self.fetchrow_returns = list(fetchrow_returns or [])
        self.fetchrow_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.fetchval_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fetchrow(self, *args: Any, **kwargs: Any) -> _Row | None:
        self.fetchrow_calls.append((args, kwargs))
        if self.fetchrow_returns:
            return self.fetchrow_returns.pop(0)
        return None

    async def fetchval(self, *args: Any, **kwargs: Any) -> Any:
        self.fetchval_calls.append((args, kwargs))
        return None


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _mock_db(pool: _Pool):
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    return db


def _make_app(conn: _Conn):
    chronicler_mod = _load_chronicler_router()
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: _mock_db(_Pool(conn))
    return app


def _payload() -> BriefingPayload:
    return BriefingPayload(
        state_class="quiet",
        headline="Quiet day.",
        kpi=KpiSnapshot(
            hours_by_top_lanes=[
                LaneHours(lane="conversations", hours=2.4),
                LaneHours(lane="calendar", hours=1.1),
            ],
            longest_episode_minutes=95,
            longest_episode_title="Conversation with Anna",
            longest_gap_minutes=312,
            sleep_minutes=432,
            streaks=Streaks(sleep=4, exercise=2),
        ),
        attention_items=[
            AttentionItem(
                kind="anomaly",
                severity="medium",
                title="Short sleep",
                detail="Sleep was below the seven-day median.",
                action_href="/butlers-dev/chronicles",
            )
        ],
        recent_days=[
            RecentDay(
                date="2026-05-07",
                total_minutes=642,
                top_lane="conversations",
                episode_count=23,
            )
        ],
    )


async def _fake_compose(_pool: Any, _target: Any, _tz: str) -> BriefingPayload:
    return _payload()


async def test_briefing_returns_cached_voice_without_llm_call(monkeypatch: pytest.MonkeyPatch):
    """Fresh day-close cache supplies the voice paragraph."""

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose)
    templated = MagicMock(return_value="templated fallback")
    monkeypatch.setattr(editorial, "templated_voice_paragraph", templated)

    conn = _Conn(
        fetchrow_returns=[
            _Row(
                {
                    "prose": "Cached day-close prose.",
                    "cache_built_at": datetime(2026, 5, 8, 3, 0, tzinfo=UTC),
                    "start_at": datetime(2026, 5, 7, 16, 0, tzinfo=UTC),
                    "end_at": datetime(2026, 5, 8, 16, 0, tzinfo=UTC),
                }
            )
        ]
    )
    app = _make_app(conn)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/briefing",
            params={"date": "2026-05-08", "tz": "Asia/Singapore"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["date"] == "2026-05-08"
    assert body["voice_paragraph"] == "Cached day-close prose."
    assert body["voice_source"] == "llm·cached"
    assert body["kpi"]["sleep_minutes"] == 432
    assert body["attention_items"][0]["title"] == "Short sleep"
    templated.assert_not_called()


async def test_briefing_uses_templated_fallback_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Missing day-close cache returns the deterministic templated voice."""

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose)
    templated = MagicMock(return_value="The day was led by conversations.")
    monkeypatch.setattr(editorial, "templated_voice_paragraph", templated)

    app = _make_app(_Conn(fetchrow_returns=[None]))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/chronicler/briefing",
            params={"date": "2026-05-08", "tz": "Asia/Singapore"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["voice_source"] == "templated"
    assert body["voice_paragraph"] == "The day was led by conversations."
    templated.assert_called_once()


async def test_attention_and_kpi_endpoints_wrap_payload(monkeypatch: pytest.MonkeyPatch):
    """Standalone endpoints expose the same attention and KPI data as briefing."""

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose)

    app = _make_app(_Conn())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        attention = await client.get(
            "/api/chronicler/attention",
            params={"date": "2026-05-08", "tz": "Asia/Singapore"},
        )
        kpi = await client.get(
            "/api/chronicler/kpi",
            params={"date": "2026-05-08", "tz": "Asia/Singapore"},
        )

    assert attention.status_code == 200
    assert attention.json()["data"][0]["kind"] == "anomaly"
    assert attention.json()["data"][0]["severity"] == "medium"
    assert kpi.status_code == 200
    assert kpi.json()["data"]["hours_by_top_lanes"][0] == {
        "lane": "conversations",
        "hours": 2.4,
    }
    assert kpi.json()["data"]["streaks"] == {"sleep": 4, "exercise": 2}
