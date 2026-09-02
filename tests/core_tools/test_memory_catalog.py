"""Authorization and routing contract for memory_catalog_fetch."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._memory_catalog import register_memory_catalog_tools

pytestmark = pytest.mark.unit


def _register(*, held_ceiling: str, pointer: dict, switchboard_client=None):
    registered: dict[str, callable] = {}

    def _core_tool(_group: str):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=held_ceiling)
    pool.fetchrow = AsyncMock(return_value=pointer)
    daemon = SimpleNamespace(switchboard_client=switchboard_client)
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=None,
        butler_name="education",
        butler_type=ButlerType.BUTLER,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_memory_catalog_tools(ctx, SimpleNamespace(), _core_tool)
    return registered["memory_catalog_fetch"], pool


async def test_fetch_above_internal_ceiling_returns_content_blind_marker() -> None:
    client = AsyncMock()
    fetch, _pool = _register(
        held_ceiling="internal",
        pointer={
            "source_butler": "finance",
            "memory_type": "fact",
            "sensitivity": "confidential",
        },
        switchboard_client=client,
    )

    result = await fetch(
        source_schema="finance",
        source_table="facts",
        source_id=str(uuid.uuid4()),
    )

    assert result == {"status": "withheld", "reason": "sensitivity"}
    assert set(result) == {"status", "reason"}
    client.call_tool.assert_not_awaited()


async def test_authorized_fetch_routes_to_owning_butler_memory_get() -> None:
    source_id = uuid.uuid4()
    client = AsyncMock()
    client.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            is_error=False,
            data={"result": {"id": str(source_id), "content": "authorized content"}},
        )
    )
    fetch, _pool = _register(
        held_ceiling="internal",
        pointer={"source_butler": "finance", "memory_type": "fact", "sensitivity": "pii"},
        switchboard_client=client,
    )

    result = await fetch(
        source_schema="finance",
        source_table="facts",
        source_id=str(source_id),
    )

    assert result["status"] == "ok"
    assert result["memory"]["content"] == "authorized content"
    route_args = client.call_tool.await_args.args
    assert route_args[0] == "route"
    assert route_args[1]["target_butler"] == "finance"
    assert route_args[1]["tool_name"] == "memory_get"
    assert route_args[1]["args"] == {"memory_type": "fact", "memory_id": str(source_id)}


async def test_fetch_has_no_caller_authority_argument() -> None:
    fetch, _pool = _register(
        held_ceiling="internal",
        pointer={"source_butler": "finance", "memory_type": "fact", "sensitivity": "pii"},
    )

    with pytest.raises(TypeError, match="max_sensitivity"):
        await fetch(
            source_schema="finance",
            source_table="facts",
            source_id=str(uuid.uuid4()),
            max_sensitivity="confidential",
        )


async def test_missing_pointer_does_not_route() -> None:
    client = AsyncMock()
    fetch, pool = _register(
        held_ceiling="normal",
        pointer=None,
        switchboard_client=client,
    )

    result = await fetch(
        source_schema="finance",
        source_table="facts",
        source_id=str(uuid.uuid4()),
    )

    assert result == {"status": "not_found"}
    assert pool.execute.await_count == 0
    client.call_tool.assert_not_awaited()


async def test_route_failure_returns_content_blind_unavailable_marker() -> None:
    client = AsyncMock()
    client.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            is_error=False,
            data={"error": "sensitive backend failure details", "retryable": True},
        )
    )
    fetch, _pool = _register(
        held_ceiling="normal",
        pointer={
            "source_butler": "finance",
            "memory_type": "fact",
            "sensitivity": "normal",
        },
        switchboard_client=client,
    )

    result = await fetch(
        source_schema="finance",
        source_table="facts",
        source_id=str(uuid.uuid4()),
    )

    assert result == {
        "status": "unavailable",
        "reason": "source_unavailable",
        "retryable": True,
    }
    assert "sensitive backend failure details" not in str(result)
