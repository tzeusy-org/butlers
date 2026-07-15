"""Tests for Travel's Switchboard-routed Health medication consumer."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


def _health_snapshot(*, medications: list[dict] | None = None) -> dict:
    return {
        "schema_version": "health.medication-travel.v1",
        "status": "ok",
        "medications": medications if medications is not None else [],
        "error": None,
    }


def _mcp_result(data: object, *, is_error: bool = False, error_text: str = "") -> object:
    content = [SimpleNamespace(text=error_text)] if error_text else []
    return SimpleNamespace(is_error=is_error, data=data, content=content)


def _routed_health_result(payload: object, *, is_error: bool = False) -> dict:
    """Match FastMCP's serialized CallToolResult inside Switchboard's result."""
    return {
        "result": {
            "data": payload,
            "is_error": is_error,
        }
    }


async def test_consumer_routes_fixed_request_through_switchboard() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    client = AsyncMock()
    client.call_tool.return_value = _mcp_result(
        _routed_health_result(
            _health_snapshot(
                medications=[
                    {
                        "name": "Metformin",
                        "dosage": "500 mg",
                        "frequency": "twice daily",
                        "schedule": ["08:00", "20:00"],
                    }
                ]
            )
        )
    )

    result = await request_health_medication_snapshot(pool, client)

    assert result["status"] == "ok"
    assert result["medications"][0]["name"] == "Metformin"
    client.call_tool.assert_awaited_once_with(
        "route",
        {
            "target_butler": "health",
            "tool_name": "medication_travel_snapshot",
            "args": {},
            "source_butler": "travel",
        },
    )


async def test_consumer_denies_explicitly_revoked_permission_without_routing() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = {"granted": False, "reason": "owner disabled access"}
    client = AsyncMock()

    result = await request_health_medication_snapshot(pool, client)

    assert result == {
        "schema_version": "health.medication-travel.v1",
        "status": "error",
        "medications": [],
        "error": {
            "code": "permission_denied",
            "message": "Travel is not permitted to request Health medication data.",
            "retryable": False,
        },
    }
    client.call_tool.assert_not_awaited()


async def test_consumer_reports_missing_switchboard_client_as_retryable() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None

    result = await request_health_medication_snapshot(pool, None)

    assert result["status"] == "error"
    assert result["error"] == {
        "code": "switchboard_unavailable",
        "message": "Switchboard is unavailable for the Health medication request.",
        "retryable": True,
    }


@pytest.mark.parametrize(
    "call_result",
    [
        _mcp_result({}, is_error=True, error_text="route failed"),
        _mcp_result({"error": "Health is stale"}),
    ],
)
async def test_consumer_reports_health_route_failures_as_retryable(call_result: object) -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    client = AsyncMock()
    client.call_tool.return_value = call_result

    result = await request_health_medication_snapshot(pool, client)

    assert result["status"] == "error"
    assert result["error"] == {
        "code": "health_unavailable",
        "message": "Health medication data is temporarily unavailable.",
        "retryable": True,
    }


async def test_consumer_reports_inner_health_mcp_error_as_unavailable() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    client = AsyncMock()
    client.call_tool.return_value = _mcp_result(
        _routed_health_result(_health_snapshot(), is_error=True)
    )

    result = await request_health_medication_snapshot(pool, client)

    assert result["error"] == {
        "code": "health_unavailable",
        "message": "Health medication data is temporarily unavailable.",
        "retryable": True,
    }


async def test_consumer_reports_timeout_as_retryable_health_unavailability() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    client = AsyncMock()
    client.call_tool.side_effect = TimeoutError

    result = await request_health_medication_snapshot(pool, client, timeout_s=0.01)

    assert result["error"]["code"] == "health_unavailable"
    assert result["error"]["retryable"] is True


async def test_consumer_rejects_malformed_or_privacy_expanded_health_response() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    malformed = _health_snapshot(
        medications=[
            {
                "name": "Metformin",
                "dosage": "500 mg",
                "frequency": "daily",
                "schedule": ["08:00"],
                "notes": "must not cross the boundary",
            }
        ]
    )
    client = AsyncMock()
    client.call_tool.return_value = _mcp_result(_routed_health_result(malformed))

    result = await request_health_medication_snapshot(pool, client)

    assert result["status"] == "error"
    assert result["medications"] == []
    assert result["error"] == {
        "code": "invalid_health_response",
        "message": "Health returned an invalid medication response.",
        "retryable": False,
    }
    assert "notes" not in str(result)


async def test_consumer_rejects_non_call_tool_result_wrapper() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    client = AsyncMock()
    client.call_tool.return_value = _mcp_result({"result": _health_snapshot()})

    result = await request_health_medication_snapshot(pool, client)

    assert result["status"] == "error"
    assert result["medications"] == []
    assert result["error"]["code"] == "invalid_health_response"


async def test_consumer_preserves_successful_empty_response() -> None:
    from butlers.tools.travel.health import request_health_medication_snapshot

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    client = AsyncMock()
    client.call_tool.return_value = _mcp_result(_routed_health_result(_health_snapshot()))

    result = await request_health_medication_snapshot(pool, client)

    assert result == _health_snapshot()


async def test_travel_module_wires_and_registers_parameterless_consumer() -> None:
    from butlers.modules._roster_travel import TravelModule

    registered: dict[str, object] = {}

    class _Mcp:
        def tool(self):
            def decorator(fn):
                registered[fn.__name__] = fn
                return fn

            return decorator

    pool = AsyncMock()
    pool.fetchrow.return_value = None
    db = SimpleNamespace(pool=pool)
    client = AsyncMock()
    client.call_tool.return_value = _mcp_result(_routed_health_result(_health_snapshot()))
    module = TravelModule()

    await module.register_tools(_Mcp(), {}, db, butler_name="travel")
    module.wire_runtime(None, "/repo", switchboard_client=client)

    consumer = registered["health_medication_snapshot"]
    result = await consumer()  # type: ignore[operator]
    assert result == _health_snapshot()
