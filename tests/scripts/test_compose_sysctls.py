"""Regression guard for MCP listener ports in the daemon containers.

Butler MCP listeners use fixed ports 41100-41111. Linux may otherwise allocate
one of those ports as an outbound connection's ephemeral source port before the
corresponding daemon binds its listener, causing a nondeterministic EADDRINUSE
startup failure. Keep the range reserved in both baked and hotreload daemon
containers; the default dev launcher uses hotreload while non-hotreload paths
use the baked service.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_DAEMON_SERVICES = ("butlers-up", "butlers-up-hotreload")
_MCP_PORT_RESERVATION = "41100-41111"


@pytest.fixture(scope="module")
def compose_services() -> dict:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
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
    assert sysctls.get("net.ipv4.ip_local_reserved_ports") == _MCP_PORT_RESERVATION
