"""OTel span attribute tests for chronicler aggregate and source-state handlers.

Verifies that each handler emits the correct named span with the expected
attributes:

- ``chronicler.aggregate.by_category``
  attrs: chronicler.aggregate.query_latency_ms, chronicler.aggregate.bucket_count
  optional: chronicler.aggregate.unmapped_source (set when a source has no category mapping)

- ``chronicler.aggregate.by_day``
  attrs: chronicler.aggregate.query_latency_ms, chronicler.aggregate.bucket_count
  optional: chronicler.aggregate.unmapped_source

- ``chronicler.aggregate.day_close``
  attrs: chronicler.day_close.query_latency_ms, chronicler.day_close.cache_state
  cache_state values: "fresh" | "stale" | "miss" (404 raises HTTPException before span ends)

- ``chronicler.source_state``
  attrs: chronicler.source_state.row_count, chronicler.source_state.query_latency_ms
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"

_T0 = datetime(2026, 4, 1, 0, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 2, 0, 0, 0, tzinfo=UTC)
_T_CACHE_BUILT = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# OTel fixture
# ---------------------------------------------------------------------------


def _save_otel_state():
    """Capture the current OTel global state for restoration after the test."""
    return {
        "set_once": trace._TRACER_PROVIDER_SET_ONCE,
        "provider": trace._TRACER_PROVIDER,
        "proxy": getattr(trace, "_PROXY_TRACER_PROVIDER", None),
    }


def _restore_otel_state(saved: dict) -> None:
    """Restore OTel global state to what it was before the test."""
    trace._TRACER_PROVIDER_SET_ONCE = saved["set_once"]
    trace._TRACER_PROVIDER = saved["provider"]
    if hasattr(trace, "_PROXY_TRACER_PROVIDER"):
        trace._PROXY_TRACER_PROVIDER = saved["proxy"]


def _install_fresh_provider() -> tuple[InMemorySpanExporter, TracerProvider]:
    """Reset OTel state and install a fresh in-memory provider.

    Resets _PROXY_TRACER_PROVIDER so that ProxyTracer objects re-resolve
    against the new provider on their next span-creation call.
    """
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None
    if hasattr(trace, "_PROXY_TRACER_PROVIDER"):
        trace._PROXY_TRACER_PROVIDER = None

    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter, provider


@pytest.fixture()
def otel_exporter():
    """Install an in-memory TracerProvider and yield the exporter.

    Saves and restores all OTel global state so that other tests in the same
    xdist worker process are not affected by the provider reset.
    """
    saved = _save_otel_state()
    exporter, provider = _install_fresh_provider()
    yield exporter
    provider.shutdown()
    _restore_otel_state(saved)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Row(dict):
    """dict subclass that mimics asyncpg Record subscript access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_episode_row(
    *,
    source_name: str = "core.sessions",
    episode_type: str = "work",
    start_at: datetime = _T0,
    end_at: datetime | None = None,
    precision: str = "exact",
    privacy: str = "normal",
    retention_days: int | None = None,
    tombstone_at: datetime | None = None,
) -> dict[str, Any]:
    if end_at is None:
        end_at = _T1
    return {
        "source_name": source_name,
        "episode_type": episode_type,
        "start_at": start_at,
        "end_at": end_at,
        "precision": precision,
        "privacy": privacy,
        "retention_days": retention_days,
        "tombstone_at": tombstone_at,
    }


def _make_mock_row(row: dict[str, Any]) -> MagicMock:
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: row[key])
    return m


def _make_app_with_pool(pool):
    chronicler_mod = _load_chronicler_router()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    return app


def _get_span(exporter: InMemorySpanExporter, name: str):
    """Return the finished span with the given name, or raise AssertionError."""
    spans = exporter.get_finished_spans()
    matching = [s for s in spans if s.name == name]
    assert matching, f"No span named {name!r} found. Got: {[s.name for s in spans]}"
    return matching[-1]


# ---------------------------------------------------------------------------
# aggregate/by-category span tests
# ---------------------------------------------------------------------------


class TestAggregateByCategorySpan:
    async def test_span_emitted_with_bucket_count_and_latency(self, otel_exporter):
        """Happy path: span emitted with bucket_count and query_latency_ms attributes."""
        row = _make_episode_row()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[_make_mock_row(row)])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-category",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_category")
        attrs = dict(span.attributes)
        assert "chronicler.aggregate.query_latency_ms" in attrs
        assert isinstance(attrs["chronicler.aggregate.query_latency_ms"], float)
        assert "chronicler.aggregate.bucket_count" in attrs
        assert attrs["chronicler.aggregate.bucket_count"] == 1

    async def test_span_emitted_with_empty_result(self, otel_exporter):
        """Empty episode set: span emitted with bucket_count=0."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-category",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_category")
        assert span.attributes["chronicler.aggregate.bucket_count"] == 0

    async def test_unmapped_source_attribute_set(self, otel_exporter):
        """Unmapped source_name triggers chronicler.aggregate.unmapped_source attribute."""
        # Use a source_name + episode_type combo that has no known category mapping.
        row = _make_episode_row(
            source_name="totally.unknown.source",
            episode_type="unknown_type",
        )
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[_make_mock_row(row)])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-category",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_category")
        assert "chronicler.aggregate.unmapped_source" in dict(span.attributes)
        assert span.attributes["chronicler.aggregate.unmapped_source"] == "totally.unknown.source"

    async def test_no_unmapped_attribute_for_known_source(self, otel_exporter):
        """Known source_name does NOT set chronicler.aggregate.unmapped_source."""
        row = _make_episode_row(source_name="core.sessions", episode_type="work")
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[_make_mock_row(row)])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-category",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_category")
        assert "chronicler.aggregate.unmapped_source" not in dict(span.attributes)


# ---------------------------------------------------------------------------
# aggregate/by-day span tests
# ---------------------------------------------------------------------------


class TestAggregateByDaySpan:
    async def test_span_emitted_with_bucket_count_and_latency(self, otel_exporter):
        """Happy path: span emitted with bucket_count and query_latency_ms attributes."""
        row = _make_episode_row()
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[_make_mock_row(row)])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-day",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_day")
        attrs = dict(span.attributes)
        assert "chronicler.aggregate.query_latency_ms" in attrs
        assert isinstance(attrs["chronicler.aggregate.query_latency_ms"], float)
        assert "chronicler.aggregate.bucket_count" in attrs
        assert attrs["chronicler.aggregate.bucket_count"] >= 1

    async def test_span_emitted_with_empty_result(self, otel_exporter):
        """Empty episode set: span emitted with bucket_count=0."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-day",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_day")
        assert span.attributes["chronicler.aggregate.bucket_count"] == 0

    async def test_unmapped_source_attribute_set(self, otel_exporter):
        """Unmapped source_name triggers chronicler.aggregate.unmapped_source attribute."""
        row = _make_episode_row(
            source_name="totally.unknown.source",
            episode_type="unknown_type",
        )
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[_make_mock_row(row)])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/by-day",
                params={"start_at": _T0.isoformat(), "end_at": _T1.isoformat()},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.by_day")
        assert "chronicler.aggregate.unmapped_source" in dict(span.attributes)
        assert span.attributes["chronicler.aggregate.unmapped_source"] == "totally.unknown.source"


# ---------------------------------------------------------------------------
# aggregate/day-close span tests
# ---------------------------------------------------------------------------


class TestDayCloseSpan:
    def _cache_row(self, *, stale: bool = False) -> _Row:
        return _Row(
            {
                "cache_key": "day_close:2026-04-01",
                "start_at": _T0,
                "end_at": _T1,
                "cache_built_at": _T_CACHE_BUILT,
                "prose": "Yesterday you worked for 8 hours.",
                "provenance_refs": json.dumps(["core.sessions:ref1"]),
            }
        )

    def _make_pool(self, *, stale: bool = False):
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                self._cache_row(),  # first call: fetch cache row
                _Row(
                    {
                        "last_invalidating_event_at": (
                            datetime(2026, 4, 2, 1, 0, 0, tzinfo=UTC) if stale else None
                        )
                    }
                ),  # second call: staleness row
            ]
        )
        return pool

    async def test_fresh_cache_sets_cache_state_fresh(self, otel_exporter):
        """Fresh cache hit: cache_state attribute is 'fresh'."""
        pool = self._make_pool(stale=False)
        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/day-close",
                params={"date": "2026-04-01"},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.day_close")
        attrs = dict(span.attributes)
        assert attrs.get("chronicler.day_close.cache_state") == "fresh"
        assert "chronicler.day_close.query_latency_ms" in attrs
        assert isinstance(attrs["chronicler.day_close.query_latency_ms"], float)

    async def test_stale_cache_sets_cache_state_stale(self, otel_exporter):
        """Stale cache: cache_state attribute is 'stale'."""
        pool = self._make_pool(stale=True)
        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/day-close",
                params={"date": "2026-04-01"},
            )
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.aggregate.day_close")
        assert span.attributes.get("chronicler.day_close.cache_state") == "stale"

    async def test_cache_miss_sets_cache_state_miss(self, otel_exporter):
        """Cache miss (404): cache_state attribute is 'miss' before 404 is raised."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)  # no cache entry

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/chronicler/aggregate/day-close",
                params={"date": "2026-04-01"},
            )
        assert resp.status_code == 404

        span = _get_span(otel_exporter, "chronicler.aggregate.day_close")
        assert span.attributes.get("chronicler.day_close.cache_state") == "miss"


# ---------------------------------------------------------------------------
# source-state span tests
# ---------------------------------------------------------------------------


class TestSourceStateSpan:
    async def test_span_emitted_with_row_count_and_latency(self, otel_exporter):
        """Happy path: span emitted with row_count and query_latency_ms attributes."""
        adapter_row = _Row(
            {
                "source_name": "core.sessions",
                "chronicler_compatibility": "supported",
                "read_surface": "sessions",
                "boundary_semantics": "wall_clock",
                "optional_schema": False,
                "active": True,
                "inactive_reason": None,
            }
        )
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=[[adapter_row], []])  # adapters, checkpoints

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.source_state")
        attrs = dict(span.attributes)
        assert "chronicler.source_state.row_count" in attrs
        assert attrs["chronicler.source_state.row_count"] == 1
        assert "chronicler.source_state.query_latency_ms" in attrs
        assert isinstance(attrs["chronicler.source_state.query_latency_ms"], float)

    async def test_empty_table_row_count_is_zero(self, otel_exporter):
        """Empty source_adapter_state: row_count is 0."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=[[], []])

        app = _make_app_with_pool(pool)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/chronicler/source-state")
        assert resp.status_code == 200

        span = _get_span(otel_exporter, "chronicler.source_state")
        assert span.attributes["chronicler.source_state.row_count"] == 0
