"""``location_retention.grants_ready``: the OwnTracks connector's one read of Chronicler.

RFC 0038 D7 named relay (bu-s11n0s.7 S4b-3b, ruling A2). Only the OwnTracks connector workload,
speaking for the ``owntracks`` daemon, may call it; it takes no arguments; it reaches only
Chronicler's service-only grant read, which no model session can be offered.
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.core.domain_event_contracts import load_contract_registry
from butlers.core.service_plane.registry import (
    CONNECTOR_OWNTRACKS_WORKLOAD,
    LOCATION_RETENTION_GRANTS_READY,
    build_relays,
    roster_daemons,
)
from butlers.core.service_plane.relays import (
    RelayRefused,
    ServiceCaller,
    relay_call,
    target_is_llm_presentable,
)
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME

_RELAYS = build_relays(roster_daemons(), load_contract_registry())
_OWNTRACKS = ServiceCaller(CONNECTOR_OWNTRACKS_WORKLOAD, "owntracks")


def test_the_relay_admits_only_the_owntracks_connector_and_reaches_a_service_only_read() -> None:
    endpoint = _RELAYS[LOCATION_RETENTION_GRANTS_READY]
    assert CONNECTOR_OWNTRACKS_WORKLOAD == "connector-owntracks"
    assert endpoint.allowed_callers == frozenset({_OWNTRACKS})
    assert endpoint.target_butlers == frozenset({"chronicler"})
    assert endpoint.target_tool == "location_retention_grants_ready_service"
    assert not target_is_llm_presentable(endpoint.target_tool)
    presentation = TOOL_PRESENTATION_BY_NAME[endpoint.target_tool]
    assert presentation.effect_class == "read"
    assert presentation.write_stores == ()


async def test_the_connector_reaches_chronicler_with_no_arguments() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    async def dispatch(butler: str, tool: str, args: dict[str, Any]) -> Any:
        calls.append((butler, tool, args))
        return {"grants": []}

    endpoint = _RELAYS[LOCATION_RETENTION_GRANTS_READY]
    assert await relay_call(endpoint, _OWNTRACKS, {}, dispatch) == {"grants": []}
    assert calls == [("chronicler", "location_retention_grants_ready_service", {})]


@pytest.mark.parametrize(
    ("caller", "args", "code"),
    [
        (ServiceCaller("butlers-up", "chronicler"), {}, "caller_not_allowed"),
        (ServiceCaller("butlers-up", "owntracks"), {}, "caller_not_allowed"),
        (ServiceCaller(CONNECTOR_OWNTRACKS_WORKLOAD, "chronicler"), {}, "caller_not_allowed"),
        (ServiceCaller("connector-telegram-bot", "owntracks"), {}, "caller_not_allowed"),
        (None, {}, "caller_unauthenticated"),
        (_OWNTRACKS, {"limit": 500}, "args_invalid"),
    ],
    ids=[
        "a-butler",
        "owntracks-name-on-butlers-up",
        "other-daemon",
        "other-connector",
        "anon",
        "args",
    ],
)
async def test_every_other_caller_or_argument_is_refused_before_chronicler(
    caller, args, code
) -> None:
    calls: list[Any] = []

    async def dispatch(*call: Any) -> Any:
        calls.append(call)

    with pytest.raises(RelayRefused) as refused:
        await relay_call(_RELAYS[LOCATION_RETENTION_GRANTS_READY], caller, args, dispatch)
    assert refused.value.code == code
    assert calls == []
