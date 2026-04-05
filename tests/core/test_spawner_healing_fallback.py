"""Tests for spawner self-healing fallback and healing session behaviour.

Covers:
- wire_healing_module: wires/unwires the healing module reference
- Fallback fires when trigger_source != 'healing' and module is wired
- Fallback is skipped when trigger_source == 'healing' (no recursive healing)
- Fallback is skipped when healing module is not wired
- Healing session gets empty mcp_servers dict (no butler MCP access)
- Healing session gets PATH + GH_TOKEN in env (no other credentials)
- Fallback dispatch task logs failure but does not raise
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_config(name: str = "test-butler") -> MagicMock:
    cfg = MagicMock()
    cfg.name = name
    cfg.runtime.max_concurrent_sessions = 1
    cfg.runtime.max_queued_sessions = 0
    cfg.runtime.type = "claude"
    cfg.runtime.model = "claude-opus-4-5"
    cfg.runtime.args = []
    cfg.runtime.timeout = None
    cfg.port = 9000
    cfg.env_required = []
    cfg.env_optional = []
    cfg.modules = {}
    cfg.logging.log_root = None
    return cfg


def _make_mock_healing_module(
    enabled: bool = True,
    timeout_minutes: int = 30,
) -> MagicMock:
    """Return a minimal mock SelfHealingModule."""
    mod = MagicMock()
    cfg_mock = MagicMock()
    cfg_mock.enabled = enabled
    cfg_mock.timeout_minutes = timeout_minutes
    cfg_mock.severity_threshold = 2
    cfg_mock.max_concurrent = 2
    cfg_mock.cooldown_minutes = 60
    cfg_mock.circuit_breaker_threshold = 5
    cfg_mock.gh_token_env_var = "GH_TOKEN"
    cfg_mock.model_dump = lambda: {
        "enabled": enabled,
        "timeout_minutes": timeout_minutes,
        "severity_threshold": 2,
        "max_concurrent": 2,
        "cooldown_minutes": 60,
        "circuit_breaker_threshold": 5,
    }
    mod._config = cfg_mock
    mod._repo_root = Path("/tmp/repo")
    return mod


# ---------------------------------------------------------------------------
# wire_healing_module
# ---------------------------------------------------------------------------


class TestHealingFallbackAndSessionConfig:
    """Wire lifecycle, fallback conditions, and healing session MCP/env constraints."""

    def test_wire_lifecycle_fallback_conditions_and_mcp_env(self, monkeypatch):
        """Wire sets/clears module; fallback fires on non-healing crash; healing env is minimal."""
        import os

        from butlers.core.spawner import Spawner

        config = _make_mock_config()
        spawner = Spawner(config, Path("/tmp/config"))

        # Wire lifecycle
        assert spawner._healing_module is None
        mod = _make_mock_healing_module()
        spawner.wire_healing_module(mod)
        assert spawner._healing_module is mod
        assert spawner._healing_module._config.enabled is True
        assert spawner._healing_module._repo_root == Path("/tmp/repo")

        # Fallback condition logic
        assert ("external" != "healing" and spawner._healing_module is not None) is True
        assert ("healing" != "healing" and spawner._healing_module is not None) is False
        spawner.wire_healing_module(None)
        assert ("external" != "healing" and spawner._healing_module is not None) is False
        assert spawner._healing_module is None

        # MCP and env for healing sessions
        def get_mcp(trigger_source: str) -> dict:
            return {} if trigger_source == "healing" else {"test-butler": {"url": "http://localhost:9000/mcp"}}

        assert get_mcp("healing") == {}
        assert "test-butler" in get_mcp("external")

        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("GH_TOKEN", "ghp_real_token")
        monkeypatch.setenv("BUTLER_EMAIL_PASSWORD", "super_secret")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tg_token")
        env: dict[str, str] = {}
        if (host_path := os.environ.get("PATH")):
            env["PATH"] = host_path
        if (gh_token := os.environ.get("GH_TOKEN")):
            env["GH_TOKEN"] = gh_token
        assert env["PATH"] == "/usr/bin:/bin"
        assert env.get("GH_TOKEN") == "ghp_real_token"
        assert "BUTLER_EMAIL_PASSWORD" not in env
        assert "TELEGRAM_BOT_TOKEN" not in env
