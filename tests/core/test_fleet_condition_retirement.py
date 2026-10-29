"""Retired-butler legacy liveness episodes resolve only on a retirement signal. [bu-h1b5un]"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from butlers.core import fleet_conditions
from butlers.core.control_plane_identity import ShadowCycle, UnreadyDaemon

pytestmark = pytest.mark.unit


def _cycle(complete: bool = True, unready: tuple[UnreadyDaemon, ...] = ()) -> ShadowCycle:
    return ShadowCycle(complete, 3, 3 if complete else 2, 0, 3 - len(unready), unready)


def _row(butler: str | None) -> dict:
    return {"id": "1", "fingerprint": "f" * 64, "butler": butler}


async def _run(
    monkeypatch,
    *,
    butler: str | None,
    retired: bool,
    complete: bool = True,
    handoff: bool = True,
    pass_signal: bool = True,
    unready: tuple[UnreadyDaemon, ...] = (),
) -> list[str]:
    if handoff:
        monkeypatch.setenv("BUTLERS_FLEET_CONDITION_HANDOFF", "1")
    else:
        monkeypatch.delenv("BUTLERS_FLEET_CONDITION_HANDOFF", raising=False)
    resolve = AsyncMock()
    with (
        patch.object(
            fleet_conditions,
            "active_legacy_liveness_conditions",
            AsyncMock(return_value=[_row(butler)]),
        ),
        patch.object(fleet_conditions, "resolve_condition", resolve),
        patch.object(fleet_conditions, "reconcile_snapshot", AsyncMock(return_value=[])),
    ):
        await fleet_conditions.reconcile_fleet_condition(
            object(),
            _cycle(complete, unready),
            frozenset({"health", "finance"}),
            (lambda name: retired) if pass_signal else None,
        )
    return [
        call.kwargs["resolution_metadata"]["resolution_reason"] for call in resolve.call_args_list
    ]


async def test_retired_butler_resolves_with_reason(monkeypatch) -> None:
    assert await _run(monkeypatch, butler="gone", retired=True) == ["butler_retired_from_roster"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retired": False},  # skipped/failed discovery or live directory
        {"retired": True, "complete": False},
        {"retired": True, "handoff": False},
        {"retired": True, "butler": None},
        {"retired": True, "pass_signal": False},
    ],
)
async def test_no_resolution_without_every_condition(monkeypatch, kwargs) -> None:
    kwargs.setdefault("butler", "gone")
    assert await _run(monkeypatch, **kwargs) == []


async def test_expected_butler_keeps_receiver_healthy_reason(monkeypatch) -> None:
    reasons = await _run(monkeypatch, butler="health", retired=True)
    assert reasons == ["complete_receiver_snapshot_healthy"]


async def test_controller_after_cycle_passes_the_signal(monkeypatch) -> None:
    pass_fn = AsyncMock()
    seen = lambda name: True  # noqa: E731
    with patch.object(fleet_conditions, "run_controller_pass", pass_fn):
        after = fleet_conditions.controller_after_cycle(object(), [], seen)
        await after(object(), _cycle())
    assert pass_fn.call_args.args[-1] is seen


async def test_expected_but_unready_butler_never_resolves_by_retirement(monkeypatch) -> None:
    # The expected-set guard is the backstop when a roster directory name differs from
    # the butler name: an expected, unready butler must stay open whatever the signal says.
    unready = (UnreadyDaemon("health", "stale", None),)
    assert await _run(monkeypatch, butler="health", retired=True, unready=unready) == []


def test_app_wires_the_retirement_signal_into_the_controller() -> None:
    import ast
    import inspect

    from butlers.api import app

    calls = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(app)))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "controller_after_cycle"
    ]
    assert len(calls) == 1
    assert ast.unparse(calls[0].args[-1]) == "get_discovery_diagnostics().is_retired"
