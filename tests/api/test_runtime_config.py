"""Tests for runtime config API endpoints.

Condensed to 3 tests (bu-2yw2d) from 9.

Keeps:
- GET success with field_tiers
- GET 404 + PATCH validation errors (parametrized)
- PATCH cold field returns restart_required
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _mock_row(
    butler_name: str = "test",
    core_groups: list[str] | None = None,
    max_concurrent: int = 3,
    max_queued: int = 10,
    seeded_at: str = "2026-01-01T00:00:00+00:00",
    updated_at: str = "2026-01-01T00:00:00+00:00",
) -> MagicMock:
    data = {
        "butler_name": butler_name,
        "core_groups": core_groups,
        "max_concurrent": max_concurrent,
        "max_queued": max_queued,
        "seeded_at": seeded_at,
        "updated_at": updated_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.keys = lambda: data.keys()
    return row


def _make_app(db_manager: MagicMock):
    from fastapi import FastAPI

    from butlers.api.routers import runtime_config

    app = FastAPI()
    app.include_router(runtime_config.router)
    app.dependency_overrides[runtime_config._get_db_manager] = lambda: db_manager
    return app


def _make_db_manager(pool=None, butler_name="test", known=True):
    mgr = MagicMock()
    if known and pool is not None:
        mgr.pool.return_value = pool
    elif not known:
        mgr.pool.side_effect = KeyError(butler_name)
    return mgr


# ---------------------------------------------------------------------------
# GET success — returns field_tiers (no hot fields exposed)
# ---------------------------------------------------------------------------


def test_get_success_returns_field_tiers():
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row())
    app = _make_app(_make_db_manager(pool=pool))
    resp = TestClient(app).get("/api/butlers/test/runtime-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["butler_name"] == "test"
    assert "field_tiers" in data
    assert data["field_tiers"]["core_groups"] == "cold"
    # Hot runtime-selection fields removed from this endpoint
    for field in ("model", "runtime_type", "args", "session_timeout_s"):
        assert field not in data


# ---------------------------------------------------------------------------
# Error / validation paths (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body,butler_name,known,expected",
    [
        ("GET", "/api/butlers/nonexistent/runtime-config", None, "nonexistent", False, 404),
        (
            "PATCH",
            "/api/butlers/test/runtime-config",
            {"core_groups": ["infra", "unknown_group"]},
            "test",
            True,
            422,
        ),
        ("PATCH", "/api/butlers/test/runtime-config", {"max_concurrent": -1}, "test", True, 422),
        (
            "PATCH",
            "/api/butlers/test/runtime-config",
            {"session_timeout_s": 1200},
            "test",
            True,
            422,
        ),
    ],
    ids=["get-404-unknown", "patch-422-bad-group", "patch-422-negative", "patch-422-removed-field"],
)
def test_runtime_config_error_paths(method, path, body, butler_name, known, expected):
    pool = AsyncMock()
    app = _make_app(_make_db_manager(pool=pool, butler_name=butler_name, known=known))
    client = TestClient(app)
    if method == "GET":
        resp = client.get(path)
    else:
        resp = client.patch(path, json=body)
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# PATCH cold field returns restart_required
# ---------------------------------------------------------------------------


def test_patch_cold_field_returns_restart_required():
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row(core_groups=["infra"], max_concurrent=5))
    app = _make_app(_make_db_manager(pool=pool))
    client = TestClient(app)

    resp_cold = client.patch("/api/butlers/test/runtime-config", json={"core_groups": ["infra"]})
    assert resp_cold.status_code == 200
    assert "core_groups" in resp_cold.json()["restart_required"]

    resp_concurrent = client.patch("/api/butlers/test/runtime-config", json={"max_concurrent": 5})
    assert resp_concurrent.status_code == 200
    assert "max_concurrent" in resp_concurrent.json()["restart_required"]
