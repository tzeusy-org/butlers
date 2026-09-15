"""Tests for the dashboard-to-daemon day-close refresh boundary."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError

pytestmark = pytest.mark.unit

_ROUTER_PATH = Path(__file__).resolve().parents[2] / "roster" / "chronicler" / "api" / "router.py"
_CACHE_KEY = "day_close:2026-04-24:tz:UTC"


def _load_chronicler_router():
    module_name = "chronicler_api_router"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _ROUTER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _mcp_result(payload: dict[str, Any] | str, *, is_error: bool = False) -> MagicMock:
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    return MagicMock(content=[SimpleNamespace(text=text)], is_error=is_error)


def _make_app(
    payload: dict[str, Any] | str | None = None,
    *,
    manager_error: Exception | None = None,
) -> tuple[Any, AsyncMock, AsyncMock]:
    chronicler_mod = _load_chronicler_router()
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = AsyncMock()
    client = AsyncMock()
    if payload is not None:
        client.call_tool.return_value = _mcp_result(payload)
    manager = MagicMock()
    if manager_error is not None:
        manager.get_client = AsyncMock(side_effect=manager_error)
    else:
        manager.get_client = AsyncMock(return_value=client)

    app = create_app(api_key="")
    app.dependency_overrides[chronicler_mod._get_db_manager] = lambda: db
    app.dependency_overrides[chronicler_mod.get_mcp_manager] = lambda: manager
    chronicler_mod.emit_dashboard_audit = AsyncMock()
    return app, manager.get_client, client.call_tool


async def _post_refresh(app: Any, *, target: str = "2026-04-24", tz: Any = "UTC"):
    body = {"date": target}
    if tz is not ...:
        body["tz"] = tz
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/api/chronicler/aggregate/day-close/refresh", json=body)


async def test_refresh_proxies_exact_tuple_to_chronicler_and_returns_safe_metadata() -> None:
    built_at = "2026-04-25T06:00:00Z"
    app, get_client, call_tool = _make_app(
        {
            "status": "success",
            "cache_key": _CACHE_KEY,
            "cache_built_at": built_at,
            "quiet": False,
            "invalid": False,
            "invalid_reason": None,
        }
    )

    response = await _post_refresh(app)

    assert response.status_code == 200
    assert response.json() == {
        "cache_key": _CACHE_KEY,
        "cache_built_at": built_at,
        "invalid": False,
        "invalid_reason": None,
    }
    get_client.assert_awaited_once_with("chronicler")
    call_tool.assert_awaited_once_with(
        "chronicler_day_close_refresh",
        {"date_label": "2026-04-24", "timezone": "UTC"},
    )


async def test_refresh_preserves_quiet_and_invalid_success_shapes() -> None:
    quiet_app, _get_client, _call_tool = _make_app(
        {"status": "success", "cache_key": _CACHE_KEY, "quiet": True}
    )
    quiet_response = await _post_refresh(quiet_app)
    assert quiet_response.status_code == 200
    assert quiet_response.json() == {"cache_key": _CACHE_KEY, "quiet": True}

    invalid_app, _get_client, _call_tool = _make_app(
        {
            "status": "success",
            "cache_key": _CACHE_KEY,
            "cache_built_at": "2026-04-25T06:00:00Z",
            "quiet": False,
            "invalid": True,
            "invalid_reason": "date_mismatch",
        }
    )
    invalid_response = await _post_refresh(invalid_app)
    assert invalid_response.status_code == 200
    assert invalid_response.json()["invalid_reason"] == "date_mismatch"
    assert "prose" not in invalid_response.json()
    assert "provenance_refs" not in invalid_response.json()


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("day_close_rate_limited", 429),
        ("task_not_found", 503),
        ("cache_write_failed", 502),
        ("coverage_witness_write_failed", 502),
        ("refresh_context_forbidden", 403),
    ],
)
async def test_refresh_maps_structured_daemon_failures(code: str, expected_status: int) -> None:
    app, _get_client, _call_tool = _make_app(
        {
            "status": "error",
            "code": code,
            "message": "private daemon payload marker",
            "details": {"retry_after_seconds": 10} if code == "day_close_rate_limited" else None,
        }
    )

    response = await _post_refresh(app)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code
    assert "private daemon payload marker" not in response.text


async def test_refresh_reports_unreachable_timeout_or_malformed_daemon_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unreachable_app, _get_client, _call_tool = _make_app(
        manager_error=ButlerUnreachableError("chronicler")
    )
    unreachable = await _post_refresh(unreachable_app)
    assert unreachable.status_code == 503
    assert unreachable.json()["error"]["code"] == "dispatch_unavailable"

    timeout_app, _get_client, timeout_call = _make_app()
    chronicler_mod = _load_chronicler_router()
    monkeypatch.setattr(chronicler_mod, "_DAY_CLOSE_REFRESH_MCP_TIMEOUT_SECONDS", 0.001)

    async def never_returns(*_args, **_kwargs):
        await asyncio.Event().wait()

    timeout_call.side_effect = never_returns
    timeout_response = await _post_refresh(timeout_app)
    assert timeout_response.status_code == 504
    assert timeout_response.json()["error"]["code"] == "dispatch_timeout"

    malformed_app, _get_client, _call_tool = _make_app("not-json")
    malformed = await _post_refresh(malformed_app)
    assert malformed.status_code == 502
    assert malformed.json()["error"]["code"] == "invalid_refresh_response"

    no_content_app, _get_client, no_content_call = _make_app()
    no_content_call.return_value = MagicMock(content=None, is_error=False)
    no_content = await _post_refresh(no_content_app)
    assert no_content.status_code == 502
    assert no_content.json()["error"]["code"] == "invalid_refresh_response"

    incomplete_app, _get_client, _call_tool = _make_app({"status": "success"})
    incomplete = await _post_refresh(incomplete_app)
    assert incomplete.status_code == 502
    assert incomplete.json()["error"]["code"] == "invalid_refresh_response"

    unknown_app, _get_client, _call_tool = _make_app(
        {"status": "error", "code": "unexpected", "message": "private daemon payload marker"}
    )
    unknown = await _post_refresh(unknown_app)
    assert unknown.status_code == 502
    assert unknown.json()["error"]["code"] == "invalid_refresh_response"
    assert "private daemon payload marker" not in unknown.text

    wrong_tuple_app, _get_client, _call_tool = _make_app(
        {
            "status": "success",
            "cache_key": "day_close:2026-04-23:tz:UTC",
            "cache_built_at": "2026-04-25T06:00:00Z",
            "quiet": False,
            "invalid": False,
            "invalid_reason": None,
        }
    )
    wrong_tuple = await _post_refresh(wrong_tuple_app)
    assert wrong_tuple.status_code == 502
    assert wrong_tuple.json()["error"]["code"] == "invalid_refresh_response"

    incoherent_app, _get_client, _call_tool = _make_app(
        {
            "status": "success",
            "cache_key": _CACHE_KEY,
            "cache_built_at": "2026-04-25T06:00:00Z",
            "quiet": False,
            "invalid": False,
            "invalid_reason": "date_mismatch",
        }
    )
    incoherent = await _post_refresh(incoherent_app)
    assert incoherent.status_code == 502
    assert incoherent.json()["error"]["code"] == "invalid_refresh_response"

    malformed_details_app, _get_client, _call_tool = _make_app(
        {
            "status": "error",
            "code": "day_close_rate_limited",
            "details": ["private daemon payload marker"],
        }
    )
    malformed_details = await _post_refresh(malformed_details_app)
    assert malformed_details.status_code == 502
    assert malformed_details.json()["error"]["code"] == "invalid_refresh_response"
    assert "private daemon payload marker" not in malformed_details.text


@pytest.mark.parametrize(
    ("tz", "code"),
    [
        (..., "missing_parameter"),
        (None, "missing_parameter"),
        ("", "invalid_timezone"),
        ("Not/A/Timezone", "invalid_timezone"),
    ],
)
async def test_refresh_rejects_missing_or_invalid_timezone_before_mcp(tz: Any, code: str) -> None:
    app, get_client, _call_tool = _make_app()

    response = await _post_refresh(app, tz=tz)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    get_client.assert_not_awaited()


async def test_refresh_rejects_unsettled_date_before_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    chronicler_mod = _load_chronicler_router()
    monkeypatch.setattr(chronicler_mod, "_today_in_timezone", lambda _zone: date(2026, 1, 2))
    app, get_client, _call_tool = _make_app()

    response = await _post_refresh(app, target="2026-01-02", tz="America/Los_Angeles")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "day_close_not_settled"
    get_client.assert_not_awaited()


def test_day_close_openapi_requires_a_nonnullable_query_tuple() -> None:
    schema = create_app(api_key="").openapi()
    day_close_path = schema["paths"]["/api/chronicler/aggregate/day-close"]
    date_parameter = next(
        parameter
        for parameter in day_close_path["get"]["parameters"]
        if parameter["name"] == "date"
    )
    tz_parameter = next(
        parameter for parameter in day_close_path["get"]["parameters"] if parameter["name"] == "tz"
    )
    assert date_parameter["required"] is True
    assert tz_parameter["required"] is True
    assert tz_parameter["schema"] == {"type": "string", "minLength": 1}

    refresh_schema_ref = schema["paths"]["/api/chronicler/aggregate/day-close/refresh"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]["$ref"]
    refresh_schema = schema["components"]["schemas"][refresh_schema_ref.rsplit("/", 1)[-1]]
    assert "tz" in refresh_schema["required"]
    assert refresh_schema["properties"]["tz"] == {"type": "string", "minLength": 1}
