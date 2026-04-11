"""Tests for runtime config API endpoints.

Covers:
- GET success with existing config
- GET 404 for unknown butler
- PATCH hot field (no restart required)
- PATCH cold field (restart required in response)
- PATCH with unknown core_group (422)
- PATCH empty body (200, no-op)
- PATCH negative concurrency (422)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from butlers.api.routers.runtime_config import KNOWN_CORE_GROUPS

pytestmark = pytest.mark.unit


def _mock_row(
    butler_name: str = "test",
    core_groups: list[str] | None = None,
    model: str | None = "gpt-5.4-mini",
    runtime_type: str = "codex",
    args: str = "[]",
    max_concurrent: int = 3,
    max_queued: int = 10,
    session_timeout_s: int = 900,
    seeded_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> MagicMock:
    data = {
        "butler_name": butler_name,
        "core_groups": core_groups,
        "model": model,
        "runtime_type": runtime_type,
        "args": args,
        "max_concurrent": max_concurrent,
        "max_queued": max_queued,
        "session_timeout_s": session_timeout_s,
        "seeded_at": seeded_at,
        "updated_at": updated_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.keys = lambda: data.keys()
    return row


def _make_app(db_manager: MagicMock):
    """Create a minimal FastAPI app with the runtime config router."""
    from fastapi import FastAPI

    from butlers.api.routers import runtime_config

    app = FastAPI()
    app.include_router(runtime_config.router)

    # Override dependency
    app.dependency_overrides[runtime_config._get_db_manager] = lambda: db_manager
    return app


def _make_db_manager(
    pool: AsyncMock | None = None,
    butler_name: str = "test",
    known: bool = True,
) -> MagicMock:
    """Create a mock DatabaseManager."""
    mgr = MagicMock()
    if known and pool is not None:
        mgr.pool.return_value = pool
    elif not known:
        mgr.pool.side_effect = KeyError(butler_name)
    return mgr


def test_get_success():
    """GET returns runtime config from DB with field_tiers."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row())
    db = _make_db_manager(pool=pool)
    app = _make_app(db)

    client = TestClient(app)
    resp = client.get("/api/butlers/test/runtime-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["butler_name"] == "test"
    assert data["model"] == "gpt-5.4-mini"
    assert data["runtime_type"] == "codex"
    assert "field_tiers" in data
    assert data["field_tiers"]["model"] == "hot"
    assert data["field_tiers"]["core_groups"] == "cold"


def test_get_404_unknown_butler():
    """GET returns 404 for unknown butler."""
    db = _make_db_manager(known=False, butler_name="nonexistent")
    app = _make_app(db)

    client = TestClient(app)
    resp = client.get("/api/butlers/nonexistent/runtime-config")
    assert resp.status_code == 404


def test_patch_hot_field():
    """PATCH of hot field returns empty restart_required."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row(session_timeout_s=1200))
    db = _make_db_manager(pool=pool)
    app = _make_app(db)

    client = TestClient(app)
    resp = client.patch(
        "/api/butlers/test/runtime-config",
        json={"session_timeout_s": 1200},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["restart_required"] == []
    assert data["config"]["session_timeout_s"] == 1200


def test_patch_cold_field():
    """PATCH of cold field includes it in restart_required."""
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row(core_groups=["infra"]))
    db = _make_db_manager(pool=pool)
    app = _make_app(db)

    client = TestClient(app)
    resp = client.patch(
        "/api/butlers/test/runtime-config",
        json={"core_groups": ["infra"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "core_groups" in data["restart_required"]


def test_patch_unknown_core_group():
    """PATCH with unknown core_group returns 422."""
    pool = AsyncMock()
    db = _make_db_manager(pool=pool)
    app = _make_app(db)

    client = TestClient(app)
    resp = client.patch(
        "/api/butlers/test/runtime-config",
        json={"core_groups": ["infra", "unknown_group"]},
    )
    assert resp.status_code == 422


def test_patch_empty_body():
    """PATCH with empty body returns 200 with no changes."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row())
    db = _make_db_manager(pool=pool)
    app = _make_app(db)

    client = TestClient(app)
    resp = client.patch(
        "/api/butlers/test/runtime-config",
        json={},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["restart_required"] == []
    # No UPDATE was executed
    pool.execute.assert_not_called()


def test_patch_negative_concurrency():
    """PATCH with negative max_concurrent returns 422."""
    pool = AsyncMock()
    db = _make_db_manager(pool=pool)
    app = _make_app(db)

    client = TestClient(app)
    resp = client.patch(
        "/api/butlers/test/runtime-config",
        json={"max_concurrent": -1},
    )
    assert resp.status_code == 422


def test_known_core_groups_constant():
    """KNOWN_CORE_GROUPS has the expected set of groups."""
    expected = {
        "infra",
        "state",
        "scheduling",
        "sessions",
        "notifications",
        "media",
        "temporal",
        "module_mgmt",
        "switchboard_routing",
        "switchboard_backfill",
    }
    assert KNOWN_CORE_GROUPS == expected
