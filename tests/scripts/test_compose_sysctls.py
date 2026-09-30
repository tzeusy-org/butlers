"""Regression guard for MCP listener ports in the daemon containers.

Butler MCP listeners use the fixed ports in ``roster/*/butler.toml``. Linux may
otherwise allocate one of those ports as an outbound connection's ephemeral source port before the
corresponding daemon binds its listener, causing a nondeterministic EADDRINUSE
startup failure. Keep the range reserved in both baked and hotreload daemon
containers; the default dev launcher uses hotreload while non-hotreload paths
use the baked service. The expected range is derived from the roster, so a new
butler whose port falls outside the reservation fails here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DAEMON_SERVICES = ("butlers-up", "butlers-up-hotreload")


def _roster_mcp_ports() -> dict[str, int]:
    return {
        path.parent.name: tomllib.loads(path.read_text(encoding="utf-8"))["butler"]["port"]
        for path in sorted((_REPO_ROOT / "roster").glob("*/butler.toml"))
    }


@pytest.fixture(scope="module")
def compose_services() -> dict:
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]


@pytest.mark.parametrize("service_name", _DAEMON_SERVICES)
def test_daemon_reserves_mcp_ports_from_ephemeral_allocation(
    compose_services: dict, service_name: str
) -> None:
    sysctls = compose_services[service_name].get("sysctls")

    assert sysctls is not None, (
        f"{service_name} must reserve its fixed MCP listener ports from Linux ephemeral "
        "source-port allocation"
    )
    reservation = sysctls.get("net.ipv4.ip_local_reserved_ports")
    assert isinstance(reservation, str) and "-" in reservation, reservation
    low, high = (int(bound) for bound in reservation.split("-"))

    ports = _roster_mcp_ports()
    assert ports, "no roster butler.toml found; the coverage check would pass vacuously"
    uncovered = {name: port for name, port in ports.items() if not low <= port <= high}
    assert not uncovered, (
        f"{service_name} reserves {reservation}, which misses roster MCP ports {uncovered}; "
        f"reserve {min(ports.values())}-{max(ports.values())} (and update the docs that name it)"
    )
