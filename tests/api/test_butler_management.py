"""Tests for butler management API endpoints (Phase 7 fold-in).

§9.4 of the settings-redesign OpenSpec.

Covers:
- GET /api/butlers/{name}/prompt — returns version 0 empty when no history
- PUT /api/butlers/{name}/prompt — inserts new version, increments monotonically
- GET /api/butlers/{name}/prompt/history — version DESC ordering
- GET /api/butlers/{name}/tools — lists grants
- PUT /api/butlers/{name}/tools/{tool} — upserts grant with audit
- GET /api/butlers/{name}/memory-access — offline fallback returns empty
- POST /api/butlers/{name}/kill — audit entry + 503 on unreachable
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerConnectionInfo, get_butler_configs
from butlers.api.routers.butler_management import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_record(data: dict) -> MagicMock:
    """Return a mock asyncpg Record backed by ``data``."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda k, _r=data: _r[k])
    return m


def _make_records(rows: list[dict]) -> list[MagicMock]:
    return [_make_record(r) for r in rows]


def _now_str() -> str:
    return str(datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC))


def _make_pool(
    fetchrow_return=None,
    fetch_return: list[dict] | None = None,
    fetchval_return=None,
) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    pool.fetch = AsyncMock(return_value=_make_records(fetch_return or []))
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock(return_value=None)
    return pool


def _make_db(pool: AsyncMock) -> MagicMock:
    db = MagicMock(spec=DatabaseManager)
    db.credential_shared_pool = MagicMock(return_value=pool)
    return db


def _stub_configs(names: list[str] = None) -> list[ButlerConnectionInfo]:
    if names is None:
        names = ["qa"]
    return [ButlerConnectionInfo(name=n, port=8000) for n in names]


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/prompt
# ---------------------------------------------------------------------------


async def test_get_prompt_no_history_returns_empty_version_zero(app):
    """When no history exists, returns version 0 with empty prompt."""
    pool = _make_pool(fetchrow_return=None)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/prompt")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["version"] == 0
    assert data["prompt"] == ""
    assert data["butler_name"] == "qa"


async def test_get_prompt_returns_latest_version(app):
    """Returns the most-recent version from system_prompt_history."""
    pool = _make_pool(
        fetchrow_return=_make_record(
            {
                "butler_name": "qa",
                "prompt": "You are QA.",
                "version": 3,
                "updated_at": datetime(2026, 5, 16, tzinfo=UTC),
                "updated_by": "owner",
            }
        )
    )
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/prompt")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["version"] == 3
    assert data["prompt"] == "You are QA."


async def test_get_prompt_404_unknown_butler(app):
    """Returns 404 for unknown butler names."""
    pool = _make_pool()
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/unknown/prompt")

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/prompt
# ---------------------------------------------------------------------------


async def test_put_prompt_inserts_new_version(app):
    """PUT prompt: next version = max(existing) + 1."""
    inserted_row = _make_record(
        {
            "butler_name": "qa",
            "prompt": "Updated prompt text.",
            "version": 4,
            "updated_at": datetime(2026, 5, 16, tzinfo=UTC),
            "updated_by": "owner",
        }
    )
    pool = _make_pool(fetchrow_return=inserted_row, fetchval_return=3)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    with patch(
        "butlers.api.routers.butler_management.audit_append", new_callable=AsyncMock
    ) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/butlers/qa/prompt",
                json={"prompt": "Updated prompt text.", "actor": "owner"},
            )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["version"] == 4
    assert data["prompt"] == "Updated prompt text."

    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args
    assert call_kwargs.args[2] == "butler.prompt_set"
    assert call_kwargs.kwargs["target"] == "qa"
    assert call_kwargs.kwargs["note"] == "v4"


async def test_put_prompt_version_increments_monotonically(app):
    """When current_version=0 (first PUT), new version is 1."""
    inserted_row = _make_record(
        {
            "butler_name": "qa",
            "prompt": "First prompt.",
            "version": 1,
            "updated_at": datetime(2026, 5, 16, tzinfo=UTC),
            "updated_by": "owner",
        }
    )
    pool = _make_pool(fetchrow_return=inserted_row, fetchval_return=0)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    with patch("butlers.api.routers.butler_management.audit_append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/butlers/qa/prompt",
                json={"prompt": "First prompt.", "actor": "owner"},
            )

    assert resp.status_code == 200
    assert resp.json()["data"]["version"] == 1


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/prompt/history
# ---------------------------------------------------------------------------


async def test_get_prompt_history_ordered_desc(app):
    """History endpoint returns versions in DESC order."""
    rows = [
        {
            "butler_name": "qa",
            "prompt": "Prompt v3",
            "version": 3,
            "updated_at": datetime(2026, 5, 14, tzinfo=UTC),
            "updated_by": "owner",
        },
        {
            "butler_name": "qa",
            "prompt": "Prompt v2",
            "version": 2,
            "updated_at": datetime(2026, 5, 13, tzinfo=UTC),
            "updated_by": "owner",
        },
        {
            "butler_name": "qa",
            "prompt": "Prompt v1",
            "version": 1,
            "updated_at": datetime(2026, 5, 12, tzinfo=UTC),
            "updated_by": "owner",
        },
    ]
    pool = _make_pool(fetch_return=rows, fetchval_return=3)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/prompt/history")

    assert resp.status_code == 200
    body = resp.json()
    versions = [v["version"] for v in body["data"]]
    assert versions == [3, 2, 1]
    assert body["meta"]["total"] == 3


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/tools
# ---------------------------------------------------------------------------


async def test_get_butler_tools_returns_list(app):
    """GET /tools returns tool grants for the butler."""
    rows = [
        {"tool_name": "log.tail", "description": "Tail logs", "allowed": True, "scope": "all"},
        {"tool_name": "shell.exec", "description": "Exec shell", "allowed": False, "scope": None},
    ]
    pool = _make_pool(fetch_return=rows)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/tools")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    names = [t["name"] for t in data]
    assert "log.tail" in names
    assert "shell.exec" in names


async def test_get_butler_tools_empty(app):
    """Returns empty list when no tools configured."""
    pool = _make_pool(fetch_return=[])
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/tools")

    assert resp.status_code == 200
    assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/tools/{tool}
# ---------------------------------------------------------------------------


async def test_put_butler_tool_upserts_and_audits(app):
    """PUT /tools/{tool} upserts the row and calls audit.append."""
    updated_row = _make_record(
        {
            "tool_name": "log.tail",
            "description": "Tail logs",
            "allowed": False,
            "scope": None,
        }
    )
    pool = _make_pool(fetchrow_return=updated_row)
    db = _make_db(pool)
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    with patch(
        "butlers.api.routers.butler_management.audit_append", new_callable=AsyncMock
    ) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                "/api/butlers/qa/tools/log.tail",
                json={"allowed": False, "actor": "owner"},
            )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["allowed"] is False
    assert data["name"] == "log.tail"

    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args
    assert call_kwargs.args[2] == "butler.tool_set"
    assert call_kwargs.kwargs["target"] == "qa.log.tail"
    assert "allowed=False" in call_kwargs.kwargs["note"]


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/memory-access
# ---------------------------------------------------------------------------


async def test_get_memory_access_offline_butler_returns_empty(app):
    """When butler is offline, returns empty read/write lists (no 503)."""
    from butlers.api.deps import get_mcp_manager

    mock_manager = MagicMock()
    mock_manager.get_client = AsyncMock(side_effect=Exception("unreachable"))
    app.dependency_overrides[get_mcp_manager] = lambda: mock_manager
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/memory-access")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["read"] == []
    assert data["write"] == []


async def test_get_memory_access_online_butler_returns_real_data(app):
    """When butler is online and memory_access tool responds, returns real data."""
    import json

    from butlers.api.deps import get_mcp_manager

    payload = {
        "read": ["episodes", "facts", "rules"],
        "write": ["episodes", "facts", "rules"],
        "namespace": "qa",
        "embedding_model": "all-MiniLM-L6-v2",
        "drops_7d": 5,
    }
    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps(payload))]
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=mock_result)
    mock_manager = MagicMock()
    mock_manager.get_client = AsyncMock(return_value=mock_client)

    app.dependency_overrides[get_mcp_manager] = lambda: mock_manager
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/butlers/qa/memory-access")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["read"] == ["episodes", "facts", "rules"]
    assert data["write"] == ["episodes", "facts", "rules"]
    assert data["namespace"] == "qa"
    assert data["embedding_model"] == "all-MiniLM-L6-v2"
    assert data["drops_7d"] == 5


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/kill
# ---------------------------------------------------------------------------


async def test_kill_butler_audits_and_returns_shutdown_initiated(app):
    """POST /kill appends audit and returns shutdown_initiated."""
    import json

    from butlers.api.deps import get_mcp_manager

    pool = _make_pool()
    db = _make_db(pool)

    mock_result = MagicMock()
    mock_result.content = [MagicMock(text=json.dumps({"status": "shutting_down"}))]
    mock_client = MagicMock()
    mock_client.call_tool = AsyncMock(return_value=mock_result)
    mock_manager = MagicMock()
    mock_manager.get_client = AsyncMock(return_value=mock_client)

    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_manager
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    with patch(
        "butlers.api.routers.butler_management.audit_append", new_callable=AsyncMock
    ) as mock_audit:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/butlers/qa/kill",
                json={"grace_seconds": 30, "actor": "owner"},
            )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "shutdown_initiated"
    assert data["grace_seconds"] == 30
    assert data["butler_name"] == "qa"

    mock_audit.assert_called_once()
    call_kwargs = mock_audit.call_args
    assert call_kwargs.args[2] == "butler.kill"
    assert call_kwargs.kwargs["target"] == "qa"
    assert "grace=30s" in call_kwargs.kwargs["note"]


async def test_kill_butler_503_when_unreachable(app):
    """POST /kill returns 503 when butler MCP is unreachable."""
    from butlers.api.deps import ButlerUnreachableError, get_mcp_manager

    pool = _make_pool()
    db = _make_db(pool)

    mock_manager = MagicMock()
    mock_manager.get_client = AsyncMock(side_effect=ButlerUnreachableError("qa"))

    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_manager
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    with patch("butlers.api.routers.butler_management.audit_append", new_callable=AsyncMock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/butlers/qa/kill",
                json={"grace_seconds": 30},
            )

    assert resp.status_code == 503


async def test_kill_butler_invalid_grace_seconds(app):
    """POST /kill with negative grace_seconds returns 422."""
    from butlers.api.deps import get_mcp_manager

    pool = _make_pool()
    db = _make_db(pool)
    mock_manager = MagicMock()
    app.dependency_overrides[_get_db_manager] = lambda: db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_manager
    app.dependency_overrides[get_butler_configs] = lambda: _stub_configs()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/butlers/qa/kill",
            json={"grace_seconds": -1},
        )

    assert resp.status_code == 422
