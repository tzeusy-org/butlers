"""Tests for the calendar cross-source duplicate review surface (bu-tjo2m1).

Covers three things the bead requires:
- the ``GET /duplicates`` endpoint exposes the clusters the read-model collapses,
- ``PATCH /dedup-rules`` persists the match-strategy / noisy-threshold,
- a keep-separate override prevents the dedup from collapsing a chosen cluster,

plus the pure-function dedup/cluster logic and fail-open behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.read_models import calendar_workspace_v1 as rm
from butlers.api.routers.calendar_workspace import (
    _cluster_key,
    _dedup_workspace_rows,
    _get_db_manager,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Row + fake-pool helpers
# ---------------------------------------------------------------------------


def _row(*, origin_ref: str, title: str, start: datetime, instance_id=None) -> dict:
    """A full workspace row dict (the shape ``_normalize_entry`` consumes)."""
    iid = instance_id or uuid4()
    return {
        "instance_id": iid,
        "source_id": uuid4(),
        "source_key": "provider:google:primary",
        "source_kind": "provider_event",
        "lane": "user",
        "provider": "google",
        "calendar_id": "primary",
        "butler_name": None,
        "display_name": "Primary",
        "writable": True,
        "source_metadata": {},
        "event_id": uuid4(),
        "origin_ref": origin_ref,
        "origin_instance_ref": str(uuid4()),
        "title": title,
        "description": "",
        "location": "",
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": {"source_type": "provider_event"},
        "source_butler": None,
        "source_session_id": None,
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(hours=1),
        "instance_status": "confirmed",
        "instance_metadata": {},
        "cursor_name": "provider_sync",
        "last_synced_at": start,
        "last_success_at": start,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


class _FakePool:
    """In-memory stand-in for an asyncpg pool over the two dedup tables."""

    def __init__(self) -> None:
        self.rules: dict | None = None
        self.overrides: dict[str, tuple[str, str | None]] = {}

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())
        if "calendar_dedup_rules" in q and q.upper().startswith("SELECT"):
            return dict(self.rules) if self.rules else None
        if "INSERT INTO public.calendar_dedup_rules" in q:
            cols = [c.strip() for c in q.split("(", 1)[1].split(")", 1)[0].split(",")]
            values = dict(zip(cols, args, strict=True))
            if self.rules is None:
                self.rules = {"match_strategy": "balanced", "noisy_threshold": 2}
            for col in cols:
                if col != "id":
                    self.rules[col] = values[col]
            return dict(self.rules)
        raise AssertionError(f"unexpected fetchrow: {q}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())
        if "calendar_dedup_overrides" in q and q.upper().startswith("SELECT"):
            return [{"cluster_key": k} for k in self.overrides]
        raise AssertionError(f"unexpected fetch: {q}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())
        if "INSERT INTO public.calendar_dedup_overrides" in q:
            self.overrides[args[0]] = (args[1], args[2])
        elif "DELETE FROM public.calendar_dedup_overrides" in q:
            self.overrides.pop(args[0], None)
        else:
            raise AssertionError(f"unexpected execute: {q}")


def _build_app(app, *, workspace_rows: dict[str, list[dict]], pool: _FakePool):
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=["general", "relationship"])
    mock_db.pool = MagicMock(return_value=pool)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            rows = workspace_rows or {}
            if butler_names is not None:
                rows = {k: v for k, v in rows.items() if k in butler_names}
            return rows
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)
    mock_mgr = AsyncMock(spec=MCPClientManager)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db


_DUP_PARAMS = {
    "view": "user",
    "start": "2026-02-22T00:00:00Z",
    "end": "2026-02-23T00:00:00Z",
}


# ---------------------------------------------------------------------------
# Pure dedup / cluster logic
# ---------------------------------------------------------------------------


def test_dedup_collapses_cross_source_copies_and_exposes_cluster():
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    # Two cross-calendar copies: different origin_ref, same title+start → pass2.
    a = _row(origin_ref="ref-a", title="Standup", start=start)
    b = _row(origin_ref="ref-b", title="Standup", start=start)
    deduped, clusters = _dedup_workspace_rows([a, b], strategy="balanced")

    assert len(deduped) == 1  # collapsed to the lowest-keyset survivor
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.match_pass == "title"
    assert len(cluster.members) == 2
    assert cluster.keep_separate is False


def test_exact_strategy_does_not_run_title_pass():
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    a = _row(origin_ref="ref-a", title="Standup", start=start)
    b = _row(origin_ref="ref-b", title="Standup", start=start)
    deduped, clusters = _dedup_workspace_rows([a, b], strategy="exact")

    # Distinct origin_refs ⇒ the title pass never runs ⇒ nothing collapses.
    assert len(deduped) == 2
    assert clusters == []


def test_keep_separate_prevents_collapse_but_still_reports_cluster():
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    a = _row(origin_ref="ref-a", title="Standup", start=start)
    b = _row(origin_ref="ref-b", title="Standup", start=start)
    ck = _cluster_key(a, "title", aggressive=False)

    deduped, clusters = _dedup_workspace_rows([a, b], strategy="balanced", keep_separate={ck})

    assert len(deduped) == 2  # not collapsed — both survive
    assert len(clusters) == 1
    assert clusters[0].keep_separate is True


# ---------------------------------------------------------------------------
# GET /duplicates
# ---------------------------------------------------------------------------


async def test_duplicates_endpoint_exposes_collapsed_clusters(app):
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    # Same Google event synced into two butler schemas: identical origin_ref+start.
    shared_ref = "google-evt-1"
    g = _row(origin_ref=shared_ref, title="Sync", start=start)
    r = _row(origin_ref=shared_ref, title="Sync", start=start)
    app, _ = _build_app(app, workspace_rows={"general": [g], "relationship": [r]}, pool=_FakePool())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/duplicates", params=_DUP_PARAMS)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is True
    assert len(data["clusters"]) == 1
    cluster = data["clusters"][0]
    assert cluster["match_pass"] == "origin_ref"
    assert cluster["member_count"] == 2
    assert len(cluster["duplicate_entries"]) == 1
    assert data["rules"]["match_strategy"] == "balanced"


async def test_duplicates_endpoint_fails_open_on_pool_error(app, monkeypatch):
    # Force the flattened-rows fetch to raise → degraded envelope, never a 500.
    async def _boom(*a, **k):
        raise RuntimeError("pool down")

    monkeypatch.setattr(
        "butlers.api.routers.calendar_workspace._fetch_flattened_workspace_rows", _boom
    )
    app, _ = _build_app(app, workspace_rows={}, pool=_FakePool())

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/workspace/duplicates", params=_DUP_PARAMS)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["available"] is False
    assert data["clusters"] == []


# ---------------------------------------------------------------------------
# PATCH /dedup-rules
# ---------------------------------------------------------------------------


async def test_patch_dedup_rules_persists(app, monkeypatch):
    monkeypatch.setattr("butlers.api.routers.calendar_workspace.log_audit_entry", AsyncMock())
    pool = _FakePool()
    app, _ = _build_app(app, workspace_rows={}, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/calendar/workspace/dedup-rules",
            json={"match_strategy": "aggressive", "noisy_threshold": 3},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["match_strategy"] == "aggressive"
    assert data["noisy_threshold"] == 3
    # Persisted to the singleton row.
    assert pool.rules == {"match_strategy": "aggressive", "noisy_threshold": 3}


async def test_patch_dedup_rules_rejects_bad_strategy(app):
    app, _ = _build_app(app, workspace_rows={}, pool=_FakePool())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/calendar/workspace/dedup-rules",
            json={"match_strategy": "nonsense"},
        )
    assert resp.status_code == 422  # rejected by the Literal request model


# ---------------------------------------------------------------------------
# POST /duplicates/keep-separate
# ---------------------------------------------------------------------------


async def test_keep_separate_toggle_persists_and_unpins(app, monkeypatch):
    monkeypatch.setattr("butlers.api.routers.calendar_workspace.log_audit_entry", AsyncMock())
    pool = _FakePool()
    app, _ = _build_app(app, workspace_rows={}, pool=pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Pin
        resp = await client.post(
            "/api/calendar/workspace/duplicates/keep-separate",
            json={"cluster_key": "title\x01standup\x011000", "keep_separate": True},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["keep_separate"] is True
        assert "title\x01standup\x011000" in pool.overrides

        # Unpin
        resp = await client.post(
            "/api/calendar/workspace/duplicates/keep-separate",
            json={"cluster_key": "title\x01standup\x011000", "keep_separate": False},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["keep_separate"] is False
        assert pool.overrides == {}


# ---------------------------------------------------------------------------
# Store-level fail-open
# ---------------------------------------------------------------------------


async def test_load_dedup_rules_defaults_when_no_pool():
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butlers_with_module = MagicMock(return_value=[])
    mock_db.butler_names = []
    rules = await rm.load_dedup_rules(mock_db)
    assert rules.match_strategy == "balanced"
    assert rules.noisy_threshold == 2


async def test_load_keep_separate_keys_fail_open_on_query_error():
    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError("missing table"))
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butlers_with_module = MagicMock(return_value=["general"])
    mock_db.butler_names = ["general"]
    mock_db.pool = MagicMock(return_value=pool)
    keys = await rm.load_keep_separate_keys(mock_db)
    assert keys == set()
