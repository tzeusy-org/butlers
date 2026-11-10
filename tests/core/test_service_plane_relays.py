"""Named relays refuse before contacting any target (RFC 0038 D7, bu-2c1g7v)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from butlers.core.service_plane.relays import (
    RelayEndpoint,
    RelayRefused,
    ServiceCaller,
    relay_call,
    target_is_llm_presentable,
)

_TRAVEL = ServiceCaller(workload="butlers-up", daemon="travel")


class _SnapshotArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trip_id: str


def _endpoint(tool: str = "report_finding") -> RelayEndpoint:
    # report_finding is declared presentable=False in the presentation inventory.
    return RelayEndpoint(
        name="qa.report_finding",
        target_butler="qa",
        target_tool=tool,
        args_model=_SnapshotArgs,
        allowed_callers=frozenset({_TRAVEL}),
    )


class _Target:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, butler: str, tool: str, args: dict[str, Any]) -> Any:
        self.calls.append((butler, tool, args))
        return {"ok": True}


async def test_an_allowed_caller_reaches_the_one_fixed_target_with_validated_args() -> None:
    target = _Target()

    result = await relay_call(_endpoint(), _TRAVEL, {"trip_id": "t1"}, target)

    assert result == {"ok": True}
    assert target.calls == [("qa", "report_finding", {"trip_id": "t1"})]


@pytest.mark.parametrize(
    ("caller", "args", "tool", "code"),
    [
        (None, {"trip_id": "t1"}, "report_finding", "caller_unauthenticated"),
        (
            ServiceCaller(workload="butlers-up", daemon="finance"),
            {"trip_id": "t1"},
            "report_finding",
            "caller_not_allowed",
        ),
        (
            ServiceCaller(workload="dashboard-api", daemon="travel"),
            {"trip_id": "t1"},
            "report_finding",
            "caller_not_allowed",
        ),
        (_TRAVEL, {"trip_id": "t1"}, "medication_travel_snapshot", "target_llm_presentable"),
        (_TRAVEL, {"trip_id": "t1", "source_butler": "health"}, "report_finding", "args_invalid"),
        (_TRAVEL, {}, "report_finding", "args_invalid"),
    ],
    ids=[
        "unauthenticated",
        "wrong-daemon",
        "daemon-name-from-another-workload",
        "presentable-target",
        "extra-field-such-as-source_butler",
        "missing-field",
    ],
)
async def test_every_refusal_happens_before_the_target_is_contacted(
    caller: ServiceCaller | None, args: dict[str, Any], tool: str, code: str
) -> None:
    target = _Target()

    with pytest.raises(RelayRefused) as refused:
        await relay_call(_endpoint(tool), caller, args, target)

    assert refused.value.code == code
    assert target.calls == []


def test_an_undeclared_tool_counts_as_presentable() -> None:
    assert target_is_llm_presentable("no_such_tool_is_declared_anywhere")
    assert not target_is_llm_presentable("report_finding")
