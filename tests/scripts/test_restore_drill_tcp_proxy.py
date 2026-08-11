"""Unit coverage for the uncredentialed restore-drill TCP relay."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "restore_drill_tcp_proxy.py"


def _proxy_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("restore_drill_tcp_proxy", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_proxy_accepts_only_the_explicit_resolved_ipv4_route(monkeypatch) -> None:
    proxy = _proxy_module()
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_HOST", "198.51.100.42")
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_PORT", "5433")
    monkeypatch.setenv("POSTGRES_HOST", "must-not-be-used.example.test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-be-used.example.test/db")

    config = proxy.load_restore_drill_proxy_config()

    assert config.db_host == "198.51.100.42"
    assert config.db_port == 5433


@pytest.mark.parametrize(
    ("host", "port"),
    [
        ("postgres.example.test", "5432"),
        ("2001:db8::1", "5432"),
        ("198.51.100.42", "0"),
        ("198.51.100.42", "65536"),
    ],
)
def test_proxy_rejects_a_non_ipv4_or_invalid_port_route(monkeypatch, host: str, port: str) -> None:
    proxy = _proxy_module()
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_HOST", host)
    monkeypatch.setenv("RESTORE_DRILL_PROXY_DB_PORT", port)

    with pytest.raises(ValueError):
        proxy.load_restore_drill_proxy_config()
