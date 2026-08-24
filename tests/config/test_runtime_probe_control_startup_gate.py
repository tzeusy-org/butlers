"""Canonical full-stack startup stays one ordinary ``up``.

Covers REQ-core-credentials-002 (and the control plane
REQ-dashboard-model-settings-001 rides on) acceptance criteria 3 and 9.  The readiness
gate exists because the launcher starts Dashboard *before* all-butlers: the
signing side comes up first, so it cannot assume the verifying side can check
what it is about to sign.  The behavioural half of that --- ``200``/``ready``,
the byte-identical ``503``, the latch --- is proven in
``tests/api/test_runtime_probe_control_endpoint.py``.  This file proves the
deployment half: that the order the gate was designed around is the order
Compose actually starts, in both the default and the hotreload profile, and
that adding the gate did not buy it with a dependency cycle or a second launch
stage.

A cycle is the specific failure to be afraid of.  The tempting way to make
Dashboard wait for a verifier is ``dashboard-api: depends_on: butlers-up``, and
since all-butlers already waits on ``oauth-gate``, which waits on Dashboard's
health, that edge deadlocks the entire stack at boot --- including services
that have nothing to do with model verification.  The gate is a runtime
question precisely so this graph can stay a line.

These tests read ``docker-compose.yml``.  They never render, start, or inspect
a live stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"

_SIGNING_SERVICE = "dashboard-api"
_VERIFYING_SERVICE = "butlers-up"
_GATE = "oauth-gate"

#: Hotreload counterparts, which take over the same network aliases.
_HOTRELOAD = {
    _SIGNING_SERVICE: "dashboard-api-hotreload",
    _VERIFYING_SERVICE: "butlers-up-hotreload",
}


def _compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _depends_on(service: dict) -> dict[str, dict]:
    """A service's dependencies, normalised out of Compose's two syntaxes."""
    declared = service.get("depends_on") or {}
    if isinstance(declared, list):
        return {name: {} for name in declared}
    return declared


def test_dashboard_starts_before_the_verifying_stack() -> None:
    """The exact ordering the readiness gate is written for.

    Dashboard needs only migrations; ``oauth-gate`` waits for Dashboard to be
    *healthy*; all-butlers waits for the gate to finish.  So the signing side
    is up and serving while the verifying side is still starting, which is why
    the client may not assume it can sign.
    """
    services = _compose()["services"]

    assert set(_depends_on(services[_SIGNING_SERVICE])) == {"migrations"}
    assert _depends_on(services[_GATE])[_SIGNING_SERVICE]["condition"] == "service_healthy"
    assert _depends_on(services[_VERIFYING_SERVICE])[_GATE]["condition"] == (
        "service_completed_successfully"
    )


def test_dashboard_health_does_not_wait_on_the_verifying_stack() -> None:
    """Criterion 3: Dashboard becomes ordinarily healthy with no verifier at all.

    Its healthcheck is its own ``/health`` on its own port.  A check that
    reached the control plane would turn a not-yet-started Switchboard into an
    unhealthy Dashboard, which would stall ``oauth-gate``, which would stall
    Switchboard --- the deadlock spelled out in the module docstring, arrived
    at through a healthcheck instead of a ``depends_on``.
    """
    for name in (_SIGNING_SERVICE, _HOTRELOAD[_SIGNING_SERVICE]):
        probe = " ".join(_compose()["services"][name]["healthcheck"]["test"])

        assert "localhost:41200/health" in probe
        assert "_control" not in probe
        assert "41100" not in probe


def test_the_signing_side_never_depends_on_the_verifying_side() -> None:
    """Criterion 9: no dependency cycle, asserted at the edge that would cause one."""
    services = _compose()["services"]
    verifying = {_VERIFYING_SERVICE, _HOTRELOAD[_VERIFYING_SERVICE]}

    for name in (_SIGNING_SERVICE, _HOTRELOAD[_SIGNING_SERVICE]):
        assert not set(_depends_on(services[name])) & verifying


def test_the_whole_startup_graph_is_acyclic() -> None:
    """And no cycle anywhere else either, since the mounts touched four services."""
    services = _compose()["services"]
    graph = {name: set(_depends_on(service)) for name, service in services.items()}

    resolved: set[str] = set()
    while True:
        ready = {
            name
            for name, dependencies in graph.items()
            if name not in resolved and dependencies <= resolved
        }
        if not ready:
            break
        resolved |= ready

    assert sorted(set(graph) - resolved) == []


def test_hotreload_starts_in_the_same_order_as_the_default_profile() -> None:
    """Criterion 3 asks for both profiles, so both are asserted, not assumed."""
    services = _compose()["services"]
    hot_dashboard = services[_HOTRELOAD[_SIGNING_SERVICE]]
    hot_butlers = services[_HOTRELOAD[_VERIFYING_SERVICE]]

    assert hot_dashboard["profiles"] == ["hotreload"]
    assert hot_butlers["profiles"] == ["hotreload"]
    assert set(_depends_on(hot_dashboard)) == {"migrations"}
    assert _depends_on(hot_butlers)[_GATE]["condition"] == "service_completed_successfully"


def test_no_service_runs_a_second_launch_stage_for_the_control_plane() -> None:
    """Criterion 9: activation added mounts, not a provisioning entrypoint.

    A wrapper that generated, chmodded, or waited on a key document would make
    the launcher two-stage in everything but name, and would put key handling
    inside the stack instead of in the operator procedure.
    """
    for service in _compose()["services"].values():
        launch = " ".join(
            str(part) for key in ("entrypoint", "command") for part in (service.get(key) or [])
        )

        assert "runtime_probe_control" not in launch
        assert "runtime-probe" not in launch
