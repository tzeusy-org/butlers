"""Tests for runtime config API endpoints.

Condensed to 3 tests (bu-2yw2d) from 9.

Keeps:
- GET success with field_tiers
- GET 404 + PATCH validation errors (parametrized)
- PATCH cold field returns restart_required
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _mock_row(
    butler_name: str = "test",
    core_groups: list[str] | None = None,
    catalog_read_sensitivity: str = "normal",
    include_catalog_read_sensitivity: bool = True,
    max_concurrent: int = 3,
    max_queued: int = 10,
    tool_exposure_policy: str = "eager_filtered",
    include_tool_exposure_policy: bool = True,
    core_groups_narrowing_reason: str | None = None,
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
        "core_groups_narrowing_reason": core_groups_narrowing_reason,
    }
    if include_catalog_read_sensitivity:
        data["catalog_read_sensitivity"] = catalog_read_sensitivity
    if include_tool_exposure_policy:
        data["tool_exposure_policy"] = tool_exposure_policy
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    row.keys = lambda: data.keys()
    return row


def _make_app(db_manager: MagicMock, roster_dir: Path):
    from fastapi import FastAPI

    from butlers.api.routers import runtime_config

    app = FastAPI()
    app.include_router(runtime_config.router)
    app.dependency_overrides[runtime_config._get_db_manager] = lambda: db_manager
    app.dependency_overrides[runtime_config._get_roster_dir] = lambda: roster_dir
    app.dependency_overrides[runtime_config._get_mcp_client_manager] = lambda: None
    return app


def _write_roster(
    tmp_path: Path,
    core_groups: tuple[str, ...] = ("infra", "delegation", "graph"),
) -> Path:
    roster = tmp_path / "roster"
    butler = roster / "test"
    butler.mkdir(parents=True)
    groups = ", ".join(f'"{group}"' for group in core_groups)
    (butler / "butler.toml").write_text(
        f'[butler]\nname = "test"\nport = 9000\n[butler.runtime_seed]\ncore_groups = [{groups}]\n'
    )
    return roster


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


def test_get_success_returns_field_tiers(tmp_path: Path):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row())
    app = _make_app(_make_db_manager(pool=pool), _write_roster(tmp_path))
    resp = TestClient(app).get("/api/butlers/test/runtime-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["butler_name"] == "test"
    assert "field_tiers" in data
    assert data["field_tiers"]["core_groups"] == "cold"
    assert data["field_tiers"]["catalog_read_sensitivity"] == "hot"
    assert data["field_tiers"]["tool_exposure_policy"] == "hot"
    assert data["tool_exposure_policy"] == "eager_filtered"
    assert data["declared_core_groups"] == ["infra", "delegation", "graph"]
    assert data["effective_core_groups"] == ["infra", "delegation", "graph"]
    assert data["core_groups_source"] == "git"
    assert data["tool_snapshot_status"] == "unavailable"
    # Hot runtime-selection fields removed from this endpoint
    for field in ("model", "runtime_type", "args", "session_timeout_s"):
        assert field not in data


async def test_tool_surface_snapshot_preserves_comparable_tool_names():
    from butlers.api.routers.runtime_config import _tool_surface_snapshot

    client = AsyncMock()
    client.call_tool.return_value = SimpleNamespace(
        content=[
            SimpleNamespace(
                text=(
                    '{"tool_surface":{"declared_names":["delegate_ask","status"],'
                    '"effective_names":["status"],"registered_names":["status"],'
                    '"registration_failures":[{"tool_name":"delegate_ask",'
                    '"module_name":"pipeline","error_type":"RuntimeError"}],'
                    '"declaration_complete":false}}'
                )
            )
        ]
    )
    manager = AsyncMock()
    manager.get_client.return_value = client

    snapshot = await _tool_surface_snapshot(manager, "relationship")

    assert snapshot == {
        "declared_tool_names": ["delegate_ask", "status"],
        "effective_tool_names": ["status"],
        "tool_registration_failures": [
            {
                "tool_name": "delegate_ask",
                "module_name": "pipeline",
                "error_type": "RuntimeError",
            }
        ],
        "tool_declaration_complete": False,
        "tool_snapshot_status": "available",
    }


async def test_tool_surface_snapshot_names_unavailable_evidence():
    from butlers.api.routers.runtime_config import _tool_surface_snapshot

    manager = AsyncMock()
    manager.get_client.side_effect = RuntimeError("daemon offline")

    assert await _tool_surface_snapshot(manager, "relationship") == {
        "tool_snapshot_status": "unavailable"
    }


def test_get_legacy_row_missing_catalog_authority_fails_closed_normal(tmp_path: Path):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row(include_catalog_read_sensitivity=False))
    app = _make_app(_make_db_manager(pool=pool), _write_roster(tmp_path))

    resp = TestClient(app).get("/api/butlers/test/runtime-config")

    assert resp.status_code == 200
    assert resp.json()["catalog_read_sensitivity"] == "normal"


def test_get_legacy_row_missing_exposure_policy_fails_closed_eager_filtered(tmp_path: Path):
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row(include_tool_exposure_policy=False))
    app = _make_app(_make_db_manager(pool=pool), _write_roster(tmp_path))

    resp = TestClient(app).get("/api/butlers/test/runtime-config")

    assert resp.status_code == 200
    assert resp.json()["tool_exposure_policy"] == "eager_filtered"


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
            {"catalog_read_sensitivity": "caller-invented"},
            "test",
            True,
            422,
        ),
        (
            "PATCH",
            "/api/butlers/test/runtime-config",
            {"session_timeout_s": 1200},
            "test",
            True,
            422,
        ),
        (
            "PATCH",
            "/api/butlers/test/runtime-config",
            {"tool_exposure_policy": "caller-invented"},
            "test",
            True,
            422,
        ),
        (
            "PATCH",
            "/api/butlers/test/runtime-config",
            {
                "core_groups": ["infra", "state"],
                "core_groups_narrowing_reason": "Cannot broaden Git authority",
            },
            "test",
            True,
            422,
        ),
    ],
    ids=[
        "get-404-unknown",
        "patch-422-bad-group",
        "patch-422-negative",
        "patch-422-bad-catalog-authority",
        "patch-422-removed-field",
        "patch-422-bad-exposure-policy",
        "patch-422-runtime-broadening",
    ],
)
def test_runtime_config_error_paths(
    method, path, body, butler_name, known, expected, tmp_path: Path
):
    pool = AsyncMock()
    app = _make_app(
        _make_db_manager(pool=pool, butler_name=butler_name, known=known),
        _write_roster(tmp_path),
    )
    client = TestClient(app)
    if method == "GET":
        resp = client.get(path)
    else:
        resp = client.patch(path, json=body)
    assert resp.status_code == expected


# ---------------------------------------------------------------------------
# PATCH cold field returns restart_required
# ---------------------------------------------------------------------------


def test_patch_cold_field_returns_restart_required(tmp_path: Path):
    pool = AsyncMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=_mock_row(core_groups=["infra"], max_concurrent=5))
    app = _make_app(_make_db_manager(pool=pool), _write_roster(tmp_path))
    client = TestClient(app)

    resp_cold = client.patch(
        "/api/butlers/test/runtime-config",
        json={
            "core_groups": ["infra"],
            "core_groups_narrowing_reason": "Temporary incident containment",
        },
    )
    assert resp_cold.status_code == 200
    assert "core_groups" in resp_cold.json()["restart_required"]

    resp_concurrent = client.patch("/api/butlers/test/runtime-config", json={"max_concurrent": 5})
    assert resp_concurrent.status_code == 200
    assert "max_concurrent" in resp_concurrent.json()["restart_required"]

    resp_authority = client.patch(
        "/api/butlers/test/runtime-config", json={"catalog_read_sensitivity": "internal"}
    )
    assert resp_authority.status_code == 200
    assert resp_authority.json()["restart_required"] == []

    # bu-ondtw.2: tool_exposure_policy is hot — a policy-only PATCH applies to
    # subsequent sessions and must never claim a restart is needed.
    resp_policy = client.patch(
        "/api/butlers/test/runtime-config", json={"tool_exposure_policy": "auto"}
    )
    assert resp_policy.status_code == 200
    assert resp_policy.json()["restart_required"] == []

    # bu-27dxl.5.3: "delegation" is a known group — PATCH accepts it like any
    # other, instead of the 422 unknown-group rejection it got previously.
    # Graph is likewise a selectable core group for the entity graph tools.
    resp_known_groups = client.patch(
        "/api/butlers/test/runtime-config",
        json={"core_groups": ["infra", "delegation", "graph"]},
    )
    assert resp_known_groups.status_code == 200

    # Clearing the explicit narrowing reason restores Git authority in the
    # same PATCH instead of leaving a stale subset until a later restart.
    pool.fetchrow = AsyncMock(
        side_effect=[
            _mock_row(
                core_groups=["infra"],
                core_groups_narrowing_reason="Temporary incident containment",
            ),
            _mock_row(core_groups=["infra", "delegation", "graph"]),
        ]
    )
    resp_clear_reason = client.patch(
        "/api/butlers/test/runtime-config",
        json={"core_groups_narrowing_reason": None},
    )
    assert resp_clear_reason.status_code == 200
    assert resp_clear_reason.json()["config"]["core_groups_source"] == "git"
    assert set(resp_clear_reason.json()["restart_required"]) == {
        "core_groups",
        "core_groups_narrowing_reason",
    }
