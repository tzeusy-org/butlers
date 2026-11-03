"""Regression coverage for the Steam health and metrics listener address."""

import socketserver
from unittest.mock import MagicMock

import pytest

from butlers.connectors.steam import SteamConnector

pytestmark = pytest.mark.unit


def test_health_server_binds_loopback_on_the_configured_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = MagicMock()
    tcp_server = MagicMock(return_value=server)
    monkeypatch.setattr(socketserver, "TCPServer", tcp_server)
    connector = SteamConnector("http://switchboard.invalid/sse", MagicMock(), health_port=40123)

    connector._run_health_server()

    tcp_server.assert_called_once()
    assert tcp_server.call_args.args[0] == ("127.0.0.1", 40123)
    server.__enter__.return_value.serve_forever.assert_called_once_with()
