"""The Steam connector health and metrics server binds loopback only (bu-wfh3ac)."""

from __future__ import annotations

import socketserver
from typing import Any
from unittest.mock import MagicMock

import pytest

from butlers.connectors import steam

pytestmark = pytest.mark.unit


class _StopServing(Exception):
    pass


def _bound_address(monkeypatch: pytest.MonkeyPatch, port: int) -> tuple[str, int]:
    captured: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, address: tuple[str, int], handler: Any) -> None:
            captured["address"] = address

        def __enter__(self) -> _FakeServer:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def serve_forever(self) -> None:
            raise _StopServing

    monkeypatch.setattr(socketserver, "TCPServer", _FakeServer)
    connector = steam.SteamConnector(
        "http://switchboard.invalid/sse", MagicMock(), health_port=port
    )
    connector._run_health_server()
    return captured["address"]


def test_health_server_binds_loopback_on_the_configured_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _bound_address(monkeypatch, 40123) == ("127.0.0.1", 40123)


def test_health_server_never_binds_all_interfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    host, _ = _bound_address(monkeypatch, 40124)
    assert host not in {"", "0.0.0.0", "::"}
