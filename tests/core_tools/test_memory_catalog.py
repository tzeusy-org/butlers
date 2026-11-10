"""Authorization and routing contract for memory_catalog_fetch."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from butlers.config import ButlerType
from butlers.core_tools._base import ToolContext
from butlers.core_tools._memory_catalog import register_memory_catalog_tools
from butlers.testing.fake_pool import fake_pool

pytestmark = pytest.mark.unit


def _register(
    *,
    held_ceiling: str,
    pointer: dict | None,
    switchboard_client=None,
    butler_name: str = "education",
):
    registered: dict[str, callable] = {}

    def _core_tool(_group: str):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    pool = fake_pool(fetchval=held_ceiling, fetchrow=pointer)
    daemon = SimpleNamespace(switchboard_client=switchboard_client)
    ctx = ToolContext(
        daemon=daemon,
        pool=pool,
        spawner=None,
        butler_name=butler_name,
        butler_type=ButlerType.BUTLER,
        is_switchboard=butler_name == "switchboard",
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


# --- Switch on: the memory.catalog_fetch named relay (RFC 0038 D7, bu-2c1g7v) ---

_POINTER_ROW = {"source_butler": "finance", "memory_type": "fact", "sensitivity": "normal"}


class _RelayedSwitchboard:
    """A domain daemon's Switchboard client; with the switch on only its URL is used."""

    def __init__(self, url: str | None = "http://butlers-control:41100/mcp") -> None:
        self.transport = SimpleNamespace(url=url)
        self.call_tool = AsyncMock(side_effect=AssertionError("generic route used"))


@pytest.fixture
def relay_on(monkeypatch):
    """Switch on; record call_relay."""
    import butlers.core.service_plane.client as relay_client

    monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
    relay = SimpleNamespace(calls=[], reply=None, failure=None)

    async def call_relay(name, args, *, daemon, switchboard_url, **kwargs):
        relay.calls.append((name, args, daemon, switchboard_url))
        if relay.failure is not None:
            raise relay.failure
        return relay.reply

    monkeypatch.setattr(relay_client, "call_relay", call_relay)
    return relay


async def _fetch_with_relay(source_id: str, **register) -> dict:
    fetch, _pool = _register(held_ceiling="normal", pointer=_POINTER_ROW, **register)
    return await fetch(source_schema="finance", source_table="facts", source_id=source_id)


async def test_switch_on_fetches_through_the_named_relay_with_only_the_pointer(relay_on) -> None:
    source_id = str(uuid.uuid4())
    relay_on.reply = {"result": {"id": source_id, "content": "authorized"}, "transport": "mcp"}

    result = await _fetch_with_relay(source_id, switchboard_client=_RelayedSwitchboard())

    assert result == {
        "status": "ok",
        "source_schema": "finance",
        "source_table": "facts",
        "source_id": source_id,
        "memory": {"id": source_id, "content": "authorized"},
    }
    # Switchboard picks the owner and the memory type from its own catalog read.
    assert relay_on.calls == [
        (
            "memory.catalog_fetch",
            {"source_schema": "finance", "source_table": "facts", "source_id": source_id},
            "education",
            "http://butlers-control:41100/mcp",
        )
    ]


async def test_switch_on_an_absent_owner_row_is_not_found(relay_on) -> None:
    relay_on.reply = {"result": None, "transport": "mcp"}

    result = await _fetch_with_relay(str(uuid.uuid4()), switchboard_client=_RelayedSwitchboard())

    assert result == {"status": "not_found"}


async def test_switch_on_the_held_ceiling_still_withholds_before_any_relay(relay_on) -> None:
    fetch, _pool = _register(
        held_ceiling="internal",
        pointer={**_POINTER_ROW, "sensitivity": "confidential"},
        switchboard_client=_RelayedSwitchboard(),
    )

    result = await fetch(source_schema="finance", source_table="facts", source_id=str(uuid.uuid4()))

    assert result == {"status": "withheld", "reason": "sensitivity"}
    assert relay_on.calls == []


def _refused(code: str, status: int):
    from butlers.core.service_plane.relays import RelayRefused

    return RelayRefused(code, status=status)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (_refused("catalog_pointer_not_found", 403), {"status": "not_found"}),
        (
            _refused("target_not_allowed", 403),
            {"status": "unavailable", "reason": "source_unavailable"},
        ),
        (
            _refused("unknown_relay", 404),
            {"status": "unavailable", "reason": "source_unavailable", "retryable": True},
        ),
        (
            _refused("auth_unavailable", 503),
            {"status": "unavailable", "reason": "source_unavailable", "retryable": True},
        ),
        (
            OSError("connect"),
            {"status": "unavailable", "reason": "source_unavailable", "retryable": True},
        ),
    ],
    ids=["pointer-gone", "permanent-refusal", "not-served", "auth-unavailable", "oserror"],
)
async def test_switch_on_relay_failures_are_content_blind(relay_on, failure, expected) -> None:
    relay_on.failure = failure

    result = await _fetch_with_relay(str(uuid.uuid4()), switchboard_client=_RelayedSwitchboard())

    assert result == expected


async def test_switch_on_relay_unavailable_is_retryable(relay_on) -> None:
    from butlers.core.service_plane.client import RelayUnavailable

    relay_on.failure = RelayUnavailable("http_500")

    result = await _fetch_with_relay(str(uuid.uuid4()), switchboard_client=_RelayedSwitchboard())

    assert result == {"status": "unavailable", "reason": "source_unavailable", "retryable": True}


async def test_switch_on_a_route_error_in_the_relay_reply_is_unavailable(relay_on) -> None:
    relay_on.reply = {"error": "TargetUnavailable: stale", "retryable": True}

    result = await _fetch_with_relay(str(uuid.uuid4()), switchboard_client=_RelayedSwitchboard())

    assert result == {"status": "unavailable", "reason": "source_unavailable", "retryable": True}


async def test_switch_on_without_a_switchboard_url_sends_nothing(relay_on) -> None:
    result = await _fetch_with_relay(
        str(uuid.uuid4()), switchboard_client=_RelayedSwitchboard(url=None)
    )

    assert result == {"status": "unavailable", "reason": "source_unavailable", "retryable": True}
    assert relay_on.calls == []


async def test_switch_on_switchboard_itself_reads_in_process_not_through_a_relay(
    relay_on, monkeypatch
) -> None:
    from butlers.core_tools import _memory_catalog

    source_id = str(uuid.uuid4())
    route = AsyncMock(return_value=({"id": source_id}, None, False))
    monkeypatch.setattr(_memory_catalog, "dispatch_via_switchboard_route", route)

    result = await _fetch_with_relay(source_id, butler_name="switchboard")

    assert result["status"] == "ok"
    assert relay_on.calls == []
    assert route.await_args.kwargs["tool_name"] == "memory_get"
