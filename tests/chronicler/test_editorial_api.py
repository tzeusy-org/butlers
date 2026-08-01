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
from pydantic import ValidationError

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
    SubqueryAvailability,
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
                LaneHours(lane="butler_ops", hours=2.4),
                LaneHours(lane="play", hours=1.1),
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
                top_lane="butler_ops",
                episode_count=23,
            )
        ],
    )


def test_briefing_response_model_closes_state_and_voice_source_unions() -> None:
    """A backend value the frontend does not recognize must fail validation."""
    chronicler_mod = _load_chronicler_router()
    kwargs = {
        "date": "2026-05-08",
        "state_class": "quiet",
        "headline": "Quiet day.",
        "voice_paragraph": "Chronicler found no confirmed concerns.",
        "voice_source": "templated",
    }

    response = chronicler_mod.ChroniclesBriefing(**kwargs)
    assert response.state_class == "quiet"
    assert response.voice_source == "templated"

    with pytest.raises(ValidationError):
        chronicler_mod.ChroniclesBriefing(**{**kwargs, "state_class": "unknown"})
    with pytest.raises(ValidationError):
        chronicler_mod.ChroniclesBriefing(**{**kwargs, "voice_source": "unknown"})


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
                    "date_label": "2026-05-08",
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
    assert body["subquery_availability"] == []
    templated.assert_not_called()


async def test_briefing_exposes_named_subquery_availability(
    monkeypatch: pytest.MonkeyPatch,
):
    """The public briefing exposes the machine-readable availability ledger."""

    async def _fake_compose_with_availability(
        _pool: Any, _target: Any, _tz: str
    ) -> BriefingPayload:
        payload = _payload()
        payload.subquery_availability = [
            SubqueryAvailability(subquery="episodes", state="unavailable"),
            SubqueryAvailability(subquery="source_health", state="not_requested"),
        ]
        return payload

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose_with_availability)
    app = _make_app(_Conn())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/chronicler/briefing",
            params={"date": "2026-05-08", "tz": "Asia/Singapore"},
        )

    assert response.status_code == 200
    assert response.json()["subquery_availability"] == [
        {"subquery": "episodes", "state": "unavailable"},
        {"subquery": "source_health", "state": "not_requested"},
    ]


async def test_briefing_uses_templated_fallback_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """Missing day-close cache returns the deterministic templated voice."""

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose)
    templated = MagicMock(return_value="The day was led by butler_ops.")
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
    assert body["voice_paragraph"] == "The day was led by butler_ops."
    templated.assert_called_once()


async def test_briefing_uses_templated_fallback_when_cache_invalid(
    monkeypatch: pytest.MonkeyPatch,
):
    """An invalid (bad shape / date-mismatched) cache row is never rendered as
    prose — admission precedes staleness for the briefing endpoint too, not
    only GET /aggregate/day-close (bu-ep4ks.1)."""

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose)
    templated = MagicMock(return_value="The day was led by butler_ops.")
    monkeypatch.setattr(editorial, "templated_voice_paragraph", templated)

    conn = _Conn(
        fetchrow_returns=[
            _Row(
                {
                    "prose": '```json\n{"tool": "x"}\n```',
                    "cache_built_at": datetime(2026, 5, 8, 3, 0, tzinfo=UTC),
                    "start_at": datetime(2026, 5, 7, 16, 0, tzinfo=UTC),
                    "end_at": datetime(2026, 5, 8, 16, 0, tzinfo=UTC),
                    "invalid_reason": "inadmissible_prose",
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
    assert body["voice_source"] == "templated"
    assert body["voice_paragraph"] == "The day was led by butler_ops."
    templated.assert_called_once()
    # The staleness fetchval must not even be reached: admission is checked first.
    assert conn.fetchval_calls == []


@pytest.mark.parametrize(
    "prose",
    [
        '```json\n{"tool": "x"}\n```',
        'Tool result: {"date": "2026-05-08", "citations": []}',
        "{'tool': 'chronicler_day_close_bundle', 'result': {'date': '2026-05-08'}}",
        "tool_calls = [{'name': 'chronicler_day_close_bundle', 'result': {'date': '2026-05-08'}}]",
        "('tool', {'result': 'raw tool payload'})",
        "set()",
        "set( )",
        "set(\n)",
    ],
    ids=[
        "code-fence",
        "tool-result-header",
        "python-literal-object",
        "assignment-tool-calls",
        "python-literal-tuple",
        "empty-set",
        "empty-set-space",
        "empty-set-newline",
    ],
)
async def test_briefing_contains_legacy_malformed_cache_with_templated_copy(
    monkeypatch: pytest.MonkeyPatch, prose: str
):
    """An unmarked legacy trace cannot surface through the editorial briefing."""
    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose)
    templated = MagicMock(return_value="The day was led by butler_ops.")
    monkeypatch.setattr(editorial, "templated_voice_paragraph", templated)

    conn = _Conn(
        fetchrow_returns=[
            _Row(
                {
                    "prose": prose,
                    "cache_built_at": datetime(2026, 5, 8, 3, 0, tzinfo=UTC),
                    "start_at": datetime(2026, 5, 7, 16, 0, tzinfo=UTC),
                    "end_at": datetime(2026, 5, 8, 16, 0, tzinfo=UTC),
                    "date_label": "2026-05-08",
                    "invalid_reason": None,
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
    assert body["voice_source"] == "templated"
    assert body["voice_paragraph"] == "The day was led by butler_ops."
    templated.assert_called_once()
    assert conn.fetchval_calls == []


async def test_briefing_bypasses_cache_entirely_for_non_content_state(
    monkeypatch: pytest.MonkeyPatch,
):
    """no_data/unavailable/degraded never consult the day-close cache, fresh
    or stale — even one that exists and would otherwise be served
    (design.md decision 3)."""

    async def _fake_compose_no_data(_pool: Any, _target: Any, _tz: str) -> BriefingPayload:
        return BriefingPayload(
            state_class="no_data",
            headline="Before the chronicled archive.",
            kpi=KpiSnapshot(
                hours_by_top_lanes=[],
                longest_episode_minutes=0,
                longest_episode_title=None,
                longest_gap_minutes=0,
                sleep_minutes=0,
                streaks=Streaks(),
            ),
            attention_items=[],
            recent_days=[],
            earliest_date="2026-05-01",
            covered_and_available=False,
        )

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose_no_data)

    # A row exists in the cache — proof the state check runs BEFORE any read.
    conn = _Conn(
        fetchrow_returns=[
            _Row(
                {
                    "prose": "Would render if reached.",
                    "cache_built_at": datetime(2026, 4, 20, 3, 0, tzinfo=UTC),
                    "start_at": datetime(2026, 4, 19, 16, 0, tzinfo=UTC),
                    "end_at": datetime(2026, 4, 20, 16, 0, tzinfo=UTC),
                    "invalid_reason": None,
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
            params={"date": "2026-04-15", "tz": "Asia/Singapore"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["state_class"] == "no_data"
    assert body["voice_source"] == "templated"
    assert body["voice_paragraph"] != "Would render if reached."
    assert conn.fetchrow_calls == []


async def test_briefing_bypasses_cache_for_named_source_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    """A degraded source is not eligible for fresh or stale day-close prose."""

    async def _fake_compose_degraded(_pool: Any, _target: Any, _tz: str) -> BriefingPayload:
        return BriefingPayload(
            state_class="degraded",
            headline="Coverage for this day is degraded.",
            kpi=KpiSnapshot(
                hours_by_top_lanes=[],
                longest_episode_minutes=0,
                longest_episode_title=None,
                longest_gap_minutes=0,
                sleep_minutes=0,
                streaks=Streaks(),
            ),
            attention_items=[
                AttentionItem(
                    kind="source_error",
                    severity="high",
                    title="Episodes unavailable",
                    detail="Chronicler could not read episodes.",
                )
            ],
            recent_days=[],
            earliest_date="2026-05-01",
            covered_and_available=False,
            subquery_availability=[SubqueryAvailability(subquery="episodes", state="unavailable")],
        )

    monkeypatch.setattr(editorial, "compose_briefing_payload", _fake_compose_degraded)
    conn = _Conn(
        fetchrow_returns=[
            _Row(
                {
                    "prose": "Would render if cache admission were reached.",
                    "cache_built_at": datetime(2026, 5, 8, 3, 0, tzinfo=UTC),
                    "start_at": datetime(2026, 5, 7, 16, 0, tzinfo=UTC),
                    "end_at": datetime(2026, 5, 8, 16, 0, tzinfo=UTC),
                    "invalid_reason": None,
                }
            )
        ]
    )
    app = _make_app(conn)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/chronicler/briefing",
            params={"date": "2026-05-08", "tz": "Asia/Singapore"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["state_class"] == "degraded"
    assert body["attention_items"] == [
        {
            "kind": "source_error",
            "severity": "high",
            "title": "Episodes unavailable",
            "detail": "Chronicler could not read episodes.",
            "action_href": None,
        }
    ]
    assert body["subquery_availability"] == [{"subquery": "episodes", "state": "unavailable"}]
    assert body["voice_paragraph"] != "Would render if cache admission were reached."
    assert conn.fetchrow_calls == []


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
        "lane": "butler_ops",
        "hours": 2.4,
    }
    assert kpi.json()["data"]["streaks"] == {"sleep": 4, "exercise": 2}
