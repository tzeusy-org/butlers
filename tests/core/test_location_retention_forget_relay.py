"""``location_retention.forget_copies``: Chronicler asks Switchboard to forget its inbox copies.

RFC 0038 D7 named relay (bu-s11n0s.7 S4c-3). Switchboard answers it itself (a local relay):
there is no MCP tool behind it, so no model session or direct MCP caller can reach the
forgetter. Only Chronicler on ``butlers-up`` may call it, with exactly a grant id and that
grant's distinct OwnTracks logical keys.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from butlers.core.domain_event_contracts import load_contract_registry
from butlers.core.service_plane.registry import (
    CONNECTOR_OWNTRACKS_WORKLOAD,
    FORGET_KEYS_LIMIT,
    LOCATION_RETENTION_FORGET_COPIES,
    build_relays,
    roster_daemons,
)
from butlers.core.service_plane.relays import RelayRefused, ServiceCaller, relay_call
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME

_CONTRACTS = load_contract_registry()
_CHRONICLER = ServiceCaller("butlers-up", "chronicler")
_GRANT = "01936a00-0000-7000-8000-000000000001"
_KEY = "owntracks:owntracks:alice:1774519200:location"
_REPLY = {"outcome": "complete", "items": 1, "forgotten": 1, "already_forgotten": 0}


class _Forget:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, tuple[str, ...]]] = []

    async def __call__(self, grant_id: UUID, keys: tuple[str, ...]) -> dict[str, Any]:
        self.calls.append((grant_id, keys))
        return _REPLY


def _relays(forget: _Forget | None):
    return build_relays(roster_daemons(), _CONTRACTS, location_forget=forget)


async def _never(*call: Any) -> Any:
    raise AssertionError(f"dispatched {call}")


async def test_chronicler_reaches_switchboards_own_forgetter() -> None:
    forget = _Forget()
    endpoint = _relays(forget)[LOCATION_RETENTION_FORGET_COPIES]
    args = {"grant_id": _GRANT, "logical_source_keys": [_KEY]}

    assert await relay_call(endpoint, _CHRONICLER, args, _never) == _REPLY
    assert forget.calls == [(UUID(_GRANT), (_KEY,))]


def test_the_relay_has_no_mcp_tool_and_admits_only_chronicler() -> None:
    endpoint = _relays(_Forget())[LOCATION_RETENTION_FORGET_COPIES]
    assert endpoint.target_butlers == frozenset({"switchboard"})
    assert endpoint.local is not None
    assert endpoint.allowed_callers == frozenset({_CHRONICLER})
    assert endpoint.target_tool not in TOOL_PRESENTATION_BY_NAME


def test_without_switchboards_forgetter_the_relay_is_not_served() -> None:
    assert LOCATION_RETENTION_FORGET_COPIES not in _relays(None)


def _keys(n: int) -> list[str]:
    return [f"owntracks:owntracks:alice:{1774519200 + i}:location" for i in range(n)]


@pytest.mark.parametrize(
    ("caller", "args", "code"),
    [
        (ServiceCaller("butlers-up", "health"), None, "caller_not_allowed"),
        (ServiceCaller("butlers-up", "switchboard"), None, "caller_not_allowed"),
        (ServiceCaller(CONNECTOR_OWNTRACKS_WORKLOAD, "chronicler"), None, "caller_not_allowed"),
        (None, None, "caller_unauthenticated"),
        (_CHRONICLER, {"grant_id": _GRANT}, "args_invalid"),
        (_CHRONICLER, {"grant_id": "x", "logical_source_keys": [_KEY]}, "args_invalid"),
        (_CHRONICLER, {"grant_id": _GRANT, "logical_source_keys": []}, "args_invalid"),
        (_CHRONICLER, {"grant_id": _GRANT, "logical_source_keys": [_KEY, _KEY]}, "args_invalid"),
        (
            _CHRONICLER,
            {"grant_id": _GRANT, "logical_source_keys": ["telegram:1:2"]},
            "args_invalid",
        ),
        (
            _CHRONICLER,
            {"grant_id": _GRANT, "logical_source_keys": ["owntracks:" + "x" * 300]},
            "args_invalid",
        ),
        (
            _CHRONICLER,
            {"grant_id": _GRANT, "logical_source_keys": _keys(FORGET_KEYS_LIMIT + 1)},
            "args_invalid",
        ),
        (
            _CHRONICLER,
            {"grant_id": _GRANT, "logical_source_keys": [_KEY], "channel": "email"},
            "args_invalid",
        ),
    ],
    ids=[
        "other-daemon",
        "switchboard-name",
        "connector-workload",
        "anon",
        "no-keys-field",
        "bad-grant-id",
        "no-keys",
        "duplicate-keys",
        "other-channel-key",
        "oversized-key",
        "too-many-keys",
        "caller-chosen-field",
    ],
)
async def test_every_other_caller_or_argument_is_refused_before_anything_is_forgotten(
    caller, args, code
) -> None:
    forget = _Forget()
    endpoint = _relays(forget)[LOCATION_RETENTION_FORGET_COPIES]
    body = args if args is not None else {"grant_id": _GRANT, "logical_source_keys": [_KEY]}
    with pytest.raises(RelayRefused) as refused:
        await relay_call(endpoint, caller, body, _never)
    assert refused.value.code == code
    assert forget.calls == []


async def test_the_key_limit_is_the_grant_limit() -> None:
    from butlers.chronicler.location_retention_grant import GRANT_LIMIT

    assert FORGET_KEYS_LIMIT == GRANT_LIMIT
    forget = _Forget()
    endpoint = _relays(forget)[LOCATION_RETENTION_FORGET_COPIES]
    args = {"grant_id": _GRANT, "logical_source_keys": _keys(FORGET_KEYS_LIMIT)}
    await relay_call(endpoint, _CHRONICLER, args, _never)
    assert len(forget.calls[0][1]) == FORGET_KEYS_LIMIT


async def test_switchboards_wired_forgetter_runs_the_roster_tool(monkeypatch) -> None:
    """The wiring resolves Switchboard's roster tool and forgets on one pooled connection."""
    import importlib

    from butlers.core.service_plane import wiring

    tool = importlib.import_module("butlers.tools.switchboard.location_retention")
    seen: list[Any] = []

    async def fake(conn: Any, grant_id: UUID, keys: Any) -> Any:
        seen.append((conn, grant_id, tuple(keys)))
        return tool.ForgettingReceipt(grant_id, "blocked", 10, 1, 2, 3, 4)

    monkeypatch.setattr(tool, "forget_location_copies", fake)

    class _Pool:
        def acquire(self) -> Any:
            pool = self

            class _Ctx:
                async def __aenter__(self) -> Any:
                    return pool

                async def __aexit__(self, *exc: Any) -> None:
                    return None

            return _Ctx()

    pool = _Pool()
    reply = await wiring.location_forget_reader(pool)(UUID(_GRANT), (_KEY, "k2"))
    assert seen == [(pool, UUID(_GRANT), (_KEY, "k2"))]
    assert reply == {
        "outcome": "blocked",
        "items": 10,
        "forgotten": 1,
        "already_forgotten": 2,
        "absent": 3,
        "blocked": 4,
    }


def test_the_args_model_is_strict() -> None:
    endpoint = _relays(_Forget())[LOCATION_RETENTION_FORGET_COPIES]
    assert issubclass(endpoint.args_model, BaseModel)
    assert endpoint.args_model.model_config.get("extra") == "forbid"
