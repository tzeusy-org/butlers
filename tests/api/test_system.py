"""Tests for the /api/system/* endpoints.

Condensed: 28 → ~14 tests [bu-gg4y1].
Keeps: instance shape, DB happy-path + 503, backups graceful-degrade,
egress 403 gate + happy path + actor mapping + aggregation,
heartbeat happy + null-heartbeat + schema_unreachable + 503.
OTel span system.egress.read: emitted + actor_count attribute + per-request.

Backup tests cover: BUTLERS_BACKUP_DIR unset (degraded), dir missing (degraded),
dir empty (reachable, no history), dir with files (reachable, history populated).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path
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
from butlers.api.deps import ButlerConnectionInfo, get_butler_configs
from butlers.api.routers.system import (
    _get_db_manager,
    _read_backup_facts_from_dir,
    system_egress_reads_total,
    system_instance_reads_total,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 3, 10, 0, 0, tzinfo=UTC)


def _make_app_with_db(mock_db):
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_record(row: dict) -> MagicMock:
    rec = MagicMock()
    rec.__getitem__ = MagicMock(side_effect=lambda k: row[k])
    rec.get = MagicMock(side_effect=lambda k, default=None: row.get(k, default))
    for k, v in row.items():
        setattr(rec, k, v)
    return rec


# ---------------------------------------------------------------------------
# GET /api/system/instance
# ---------------------------------------------------------------------------


async def test_instance_endpoint_returns_required_fields():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/instance")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data["version"], str) and data["version"]
    assert data["uptime_seconds"] >= 0
    parsed = datetime.fromisoformat(data["started_at"])
    assert parsed.tzinfo is not None


# ---------------------------------------------------------------------------
# GET /api/system/database
# ---------------------------------------------------------------------------


def _make_db_endpoint_mock(*, fail_at=None, schema_rows=None, table_rows=None):
    schema_rows = schema_rows or [
        {"schema_name": "general", "size_bytes": 512 * 1024, "table_count": 5},
        {"schema_name": "health", "size_bytes": 256 * 1024, "table_count": 3},
    ]
    table_rows = table_rows or [
        {"schema_name": "general", "table_name": "sessions", "size_bytes": 400 * 1024}
    ]
    pool = AsyncMock()

    async def _fetchval(sql, *args):
        if fail_at == "total":
            raise RuntimeError("permission denied")
        return 1024 * 1024

    async def _fetch(sql, *args):
        if fail_at in ("schema", "table"):
            raise RuntimeError("permission denied")
        if "GROUP BY" in sql:
            return [_make_record(r) for r in schema_rows]
        return [_make_record(r) for r in table_rows]

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    return mock_db


async def test_database_happy_path():
    mock_db = _make_db_endpoint_mock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/database")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "total_size_bytes" in data
    assert len(data["schemas"]) == 2
    for s in data["schemas"]:
        assert "schema_name" in s and "size_bytes" in s and "table_count" in s


async def test_database_503_on_query_failure():
    mock_db = _make_db_endpoint_mock(fail_at="total")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/database")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/system/backups
# ---------------------------------------------------------------------------


async def test_backups_degraded_when_env_var_unset(monkeypatch: pytest.MonkeyPatch):
    """No BUTLERS_BACKUP_DIR → backup_source_reachable=false, null fields."""
    monkeypatch.delenv("BUTLERS_BACKUP_DIR", raising=False)
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/backups")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["last_backup_at"] is None
    assert data["backup_source_reachable"] is False
    assert data["backup_history"] == []


async def test_backups_degraded_when_dir_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """BUTLERS_BACKUP_DIR points to a non-existent path → degraded."""
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(tmp_path / "nonexistent"))
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/backups")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["backup_source_reachable"] is False
    assert data["last_backup_at"] is None


async def test_backups_reachable_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """BUTLERS_BACKUP_DIR exists but contains no dumps → reachable, empty history."""
    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(tmp_path))
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/backups")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["backup_source_reachable"] is True
    assert data["last_backup_at"] is None
    assert data["backup_history"] == []


async def test_backups_reachable_with_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """BUTLERS_BACKUP_DIR contains dump files → reachable with populated history."""
    # Create two fake dump files with distinct mtimes
    older = tmp_path / "butlers_2026-05-01T01-00-00.sql.gz"
    newer = tmp_path / "butlers_2026-05-07T02-00-00.sql.gz"
    older.write_bytes(b"x" * 1024)
    newer.write_bytes(b"x" * 2048)
    # Ensure distinct mtimes by touching them
    older_ts = time.time() - 100
    newer_ts = time.time()
    os.utime(older, (older_ts, older_ts))
    os.utime(newer, (newer_ts, newer_ts))

    monkeypatch.setenv("BUTLERS_BACKUP_DIR", str(tmp_path))
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/backups")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["backup_source_reachable"] is True
    assert data["last_backup_at"] is not None
    assert data["last_backup_size_bytes"] == 2048
    assert len(data["backup_history"]) == 2
    # Most-recent file first
    assert data["backup_history"][0]["size_bytes"] == 2048
    assert data["backup_history"][1]["size_bytes"] == 1024


# ---------------------------------------------------------------------------
# _read_backup_facts_from_dir unit tests (no HTTP stack needed)
#
# The missing/empty/with-files paths are covered through the /backups endpoint
# tests above; only the file-glob filtering invariant is unique here.
# ---------------------------------------------------------------------------


def test_read_backup_facts_ignores_non_matching_files(tmp_path: Path):
    """Files that don't match butlers_*.sql.gz are not counted."""
    (tmp_path / "other_dump.sql.gz").write_bytes(b"x" * 100)
    (tmp_path / "butlers_latest.tar.gz").write_bytes(b"x" * 100)
    facts = _read_backup_facts_from_dir(tmp_path)
    assert facts.backup_source_reachable is True
    assert facts.backup_history == []


# ---------------------------------------------------------------------------
# GET /api/system/egress
# ---------------------------------------------------------------------------


def _make_egress_db(*, has_owner=True, audit_rows=None, owner_fails=False, audit_fails=False):
    if audit_rows is None and has_owner:
        audit_rows = [
            {
                "operation": "llm_api_call",
                "last_seen_at": _NOW,
                "total_calls": 42,
                "first_seen_at": _NOW,
            }
        ]
    elif audit_rows is None:
        audit_rows = []

    pool = AsyncMock()

    async def _fetchrow(sql, *args):
        if owner_fails:
            raise RuntimeError("DB error")
        # Owner-assertion query now reads public.entities directly (bu-jnaa3):
        # WHERE 'owner' = ANY(roles) — no contacts join, no 'e.' alias.
        if "ANY(roles)" in sql:
            if has_owner:
                rec = MagicMock()
                rec.__getitem__ = MagicMock(return_value="fake-uuid")
                return rec
            return None
        return None

    async def _fetch(sql, *args):
        if audit_fails:
            raise RuntimeError("DB error")
        return [_make_record(r) for r in audit_rows]

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(side_effect=_fetch)
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = pool
    return mock_db


async def test_egress_403_when_no_owner():
    mock_db = _make_egress_db(has_owner=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/egress")
    assert resp.status_code == 403
    assert "actors" not in resp.json()  # no partial data leak


async def test_egress_403_when_owner_query_fails():
    mock_db = _make_egress_db(has_owner=True, owner_fails=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/egress")
    assert resp.status_code == 403


async def test_egress_happy_path_actor_mapping_and_aggregation():
    """llm_api_call maps to anthropic.claude; duplicate rows aggregate total_calls."""
    audit_rows = [
        {
            "operation": "llm_api_call",
            "last_seen_at": _NOW,
            "total_calls": 5,
            "first_seen_at": _NOW,
        },
        {
            "operation": "llm_api_call",
            "last_seen_at": _NOW,
            "total_calls": 3,
            "first_seen_at": _NOW,
        },
        {
            "operation": "some_exotic_op",
            "last_seen_at": _NOW,
            "total_calls": 1,
            "first_seen_at": _NOW,
        },
    ]
    mock_db = _make_egress_db(has_owner=True, audit_rows=audit_rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/egress")
    assert resp.status_code == 200
    actors = resp.json()["data"]["actors"]
    claude = next((a for a in actors if a["actor_id"] == "anthropic.claude"), None)
    assert claude is not None
    assert claude["total_calls"] == 8  # aggregated
    other = next((a for a in actors if a["actor_id"] == "other"), None)
    assert other is not None


async def test_egress_catalog_covers_from_oldest():
    earliest = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    audit_rows = [
        {
            "operation": "llm_api_call",
            "last_seen_at": _NOW,
            "total_calls": 1,
            "first_seen_at": earliest,
        }
    ]
    mock_db = _make_egress_db(has_owner=True, audit_rows=audit_rows)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/egress")
    assert "2025" in resp.json()["data"]["catalog_covers_from"]


async def test_egress_503_when_audit_fails():
    mock_db = _make_egress_db(has_owner=True, audit_fails=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_app_with_db(mock_db)), base_url="http://test"
    ) as client:
        resp = await client.get("/api/system/egress")
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/system/butlers/heartbeat
# ---------------------------------------------------------------------------


def _make_heartbeat_db(
    *,
    registry_rows=None,
    butler_names=None,
    active_count=0,
    registry_fails=False,
    session_fails=False,
):
    registry_rows = registry_rows or [{"name": "general", "last_seen_at": _NOW}]
    butler_names = butler_names or ["general"]

    sw_pool = AsyncMock()

    async def _sw_fetch(sql, *args):
        if registry_fails:
            raise RuntimeError("DB error")
        return [_make_record(r) for r in registry_rows]

    sw_pool.fetch = AsyncMock(side_effect=_sw_fetch)
    butler_pool = AsyncMock()

    async def _butler_fetchrow(sql, *args):
        if session_fails:
            raise RuntimeError("schema not found")
        return None

    async def _butler_fetchval(sql, *args):
        if session_fails:
            raise RuntimeError("schema not found")
        return active_count

    butler_pool.fetchrow = AsyncMock(side_effect=_butler_fetchrow)
    butler_pool.fetchval = AsyncMock(side_effect=_butler_fetchval)

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = butler_names
    mock_db.pool.side_effect = lambda name: sw_pool if name == "switchboard" else butler_pool
    return mock_db


def _make_heartbeat_app(mock_db, roster_names):
    """Build a test app with both DB manager and butler configs overridden.

    The heartbeat endpoint uses get_butler_configs() as the canonical butler
    source. Tests must supply roster_names so the dependency override matches
    the scenario under test.
    """
    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    configs = [ButlerConnectionInfo(name=n, port=40000) for n in roster_names]
    app.dependency_overrides[get_butler_configs] = lambda: configs
    return app


async def test_heartbeat_happy_path_fields():
    mock_db = _make_heartbeat_db(active_count=3)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_heartbeat_app(mock_db, ["general"])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/butlers/heartbeat")
    assert resp.status_code == 200
    butlers = resp.json()["data"]["butlers"]
    general = next(b for b in butlers if b["name"] == "general")
    assert general["active_session_count"] == 3
    assert general["heartbeat_age_seconds"] >= 0
    for field in (
        "last_heartbeat_at",
        "last_session_at",
        "active_session_count",
        "heartbeat_age_seconds",
    ):
        assert field in general


async def test_heartbeat_schema_unreachable_sets_error():
    mock_db = _make_heartbeat_db(session_fails=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_heartbeat_app(mock_db, ["general"])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/butlers/heartbeat")
    assert resp.status_code == 200
    general = next(b for b in resp.json()["data"]["butlers"] if b["name"] == "general")
    assert general["error"] == "schema_unreachable"


async def test_heartbeat_503_when_registry_fails():
    mock_db = _make_heartbeat_db(registry_fails=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_heartbeat_app(mock_db, ["general"])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/butlers/heartbeat")
    assert resp.status_code == 503


async def test_heartbeat_null_last_heartbeat_when_not_in_registry():
    """Butlers known to the DB manager but absent from the registry have null heartbeat fields.

    Regression: verifies that a butler present in db.butler_names but not in the
    switchboard registry appears in results with last_heartbeat_at=None,
    heartbeat_age_seconds=None, and no error (session facts still populated).
    """
    mock_db = _make_heartbeat_db(
        registry_rows=[{"name": "other-butler", "last_seen_at": _NOW}],  # general not in registry
        butler_names=["general"],
        active_count=2,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_heartbeat_app(mock_db, ["general"])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/butlers/heartbeat")
    assert resp.status_code == 200
    butlers = resp.json()["data"]["butlers"]
    names = [b["name"] for b in butlers]
    assert "general" in names, "butler in db.butler_names must appear even if absent from registry"
    general = next(b for b in butlers if b["name"] == "general")
    assert general["last_heartbeat_at"] is None
    assert general["heartbeat_age_seconds"] is None
    assert general["active_session_count"] == 2
    assert general["error"] is None


async def test_heartbeat_includes_roster_butler_with_failed_pool_as_schema_unreachable():
    """Corrected behavior: roster butlers with failed DB pool appear with error='schema_unreachable'.

    The heartbeat endpoint uses get_butler_configs() as the canonical butler source.
    A butler in the roster whose DB pool failed to initialize at startup (absent from
    db.butler_names) and has never pinged the switchboard (absent from registry) must
    still appear in the heartbeat response with error='schema_unreachable' and null/0
    session facts — not be silently omitted.

    Also verifies the registry-union path: a roster-absent butler that HAS pinged the
    switchboard still appears via the registry union.
    """
    # Part 1: ghost is in the registry (union path) but not in butler_names or roster.
    # It should still appear via registry union even without a roster entry.
    mock_db_with_registry = _make_heartbeat_db(
        registry_rows=[
            {"name": "general", "last_seen_at": _NOW},
            {"name": "ghost", "last_seen_at": _NOW},
        ],
        butler_names=["general"],  # ghost pool never initialized
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_make_heartbeat_app(mock_db_with_registry, ["general"])),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/butlers/heartbeat")
    assert resp.status_code == 200
    names_with_registry = [b["name"] for b in resp.json()["data"]["butlers"]]
    assert "ghost" in names_with_registry, (
        "ghost appears via registry union path when it has pinged the switchboard"
    )

    # Part 2: ghost is in the roster but NOT in butler_names and NOT in registry.
    # FIX VERIFIED: ghost must appear with error='schema_unreachable', not be omitted.
    mock_db_no_registry = _make_heartbeat_db(
        registry_rows=[{"name": "general", "last_seen_at": _NOW}],
        butler_names=["general"],  # ghost pool never initialized, never pinged
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=_make_heartbeat_app(mock_db_no_registry, ["general", "ghost"])
        ),
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/system/butlers/heartbeat")
    assert resp.status_code == 200
    butlers_no_registry = resp.json()["data"]["butlers"]
    names_no_registry = [b["name"] for b in butlers_no_registry]
    assert "ghost" in names_no_registry, (
        "ghost must appear in heartbeat when in roster even if pool init failed "
        "and it has never pinged the switchboard registry"
    )
    ghost = next(b for b in butlers_no_registry if b["name"] == "ghost")
    assert ghost["error"] == "schema_unreachable"
    assert ghost["last_session_at"] is None
    assert ghost["active_session_count"] == 0
    assert ghost["last_heartbeat_at"] is None


# ---------------------------------------------------------------------------
# OTel span: system.egress.read
# ---------------------------------------------------------------------------


def _save_otel_state() -> dict:
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


def _install_fresh_otel_provider() -> tuple[InMemorySpanExporter, TracerProvider]:
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
    exporter, provider = _install_fresh_otel_provider()
    yield exporter
    provider.shutdown()
    _restore_otel_state(saved)


class TestEgressSpan:
    """Verify that GET /api/system/egress emits the system.egress.read OTel span."""

    def _make_db_with_owner(
        self,
        *,
        audit_rows: list[dict] | None = None,
    ) -> DatabaseManager:
        """Wire a mock DatabaseManager for the egress endpoint (owner present)."""
        if audit_rows is None:
            audit_rows = [
                {
                    "operation": "llm_api_call",
                    "last_seen_at": _NOW,
                    "total_calls": 5,
                    "first_seen_at": _NOW,
                },
                {
                    "operation": "telegram_send",
                    "last_seen_at": _NOW,
                    "total_calls": 3,
                    "first_seen_at": _NOW,
                },
            ]

        pool = AsyncMock()

        async def _fetchrow(sql, *args):
            # Owner-assertion query now reads public.entities directly (bu-jnaa3).
            if "ANY(roles)" in sql:
                rec = MagicMock()
                rec.__getitem__ = MagicMock(return_value="fake-uuid")
                return rec
            return None

        async def _fetch(sql, *args):
            return [_make_record(r) for r in audit_rows]

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        pool.fetch = AsyncMock(side_effect=_fetch)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = pool
        return mock_db

    async def test_span_emitted_on_successful_read(self, otel_exporter: InMemorySpanExporter):
        """system.egress.read span is emitted with actor_count on every successful read."""
        mock_db = self._make_db_with_owner()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 200

        spans = otel_exporter.get_finished_spans()
        egress_spans = [s for s in spans if s.name == "system.egress.read"]
        assert len(egress_spans) == 1, (
            f"expected 1 system.egress.read span, got {len(egress_spans)}"
        )

        span = egress_spans[0]
        assert "actor_count" in span.attributes, "span must carry actor_count attribute"
        # Two distinct operations map to two distinct actors (anthropic.claude, telegram.api)
        assert span.attributes["actor_count"] == 2

    async def test_span_actor_count_reflects_empty_catalog(
        self, otel_exporter: InMemorySpanExporter
    ):
        """actor_count is 0 when the audit log has no rows."""
        mock_db = self._make_db_with_owner(audit_rows=[])
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        assert resp.status_code == 200

        spans = otel_exporter.get_finished_spans()
        egress_spans = [s for s in spans if s.name == "system.egress.read"]
        assert len(egress_spans) == 1
        assert egress_spans[0].attributes["actor_count"] == 0

    async def test_span_emitted_on_each_request(self, otel_exporter: InMemorySpanExporter):
        """A span is emitted on every call to the endpoint, not just the first."""
        mock_db = self._make_db_with_owner()
        app = _make_app_with_db(mock_db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/system/egress")
            await client.get("/api/system/egress")

        spans = otel_exporter.get_finished_spans()
        egress_spans = [s for s in spans if s.name == "system.egress.read"]
        assert len(egress_spans) == 2


# ---------------------------------------------------------------------------
# Prometheus counter tests
# ---------------------------------------------------------------------------


class TestPrometheusCounters:
    """Verify that each /api/system/* endpoint registers a counter and bumps it."""

    def _counter_value(self, counter) -> float:
        """Read the current value of an unlabelled prometheus_client Counter.

        Uses the public collect() API rather than internal _value so the helper
        stays compatible across prometheus_client versions and multiprocess mode.
        The _total sample holds the monotonic counter value.
        """
        for metric_family in counter.collect():
            for sample in metric_family.samples:
                if sample.name.endswith("_total"):
                    return sample.value
        return 0.0

    async def test_instance_counter_exists_and_increments(self):
        """system_instance_reads_total increments on GET /api/system/instance."""
        app = create_app()
        before = self._counter_value(system_instance_reads_total)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/instance")
        assert resp.status_code == 200
        assert self._counter_value(system_instance_reads_total) == before + 1

    async def test_egress_counter_increments_even_on_403(self):
        """system_egress_reads_total bumps on GET /api/system/egress even when it 403s.

        Retained alongside the instance counter test as the representative
        observability-wiring guard for the error-path branch (counter must bump
        before the 403 gate). The other per-endpoint counter tests were redundant
        copies of this same increment-on-read pattern.
        """
        mock_db = MagicMock(spec=DatabaseManager)
        pool = AsyncMock()
        # Simulate missing owner -> 403, but counter still bumps
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[])
        mock_db.pool.return_value = pool
        app = _make_app_with_db(mock_db)
        before = self._counter_value(system_egress_reads_total)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/system/egress")
        # 403 is expected (no owner), but the counter must still have bumped
        assert resp.status_code == 403
        assert self._counter_value(system_egress_reads_total) == before + 1
