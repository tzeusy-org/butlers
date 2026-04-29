"""Regression coverage for Chronicler MCP module startup configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from butlers.config import load_config
from butlers.daemon import ButlerDaemon
from butlers.modules.registry import default_registry

pytestmark = pytest.mark.unit

ROSTER_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_chronicler_mcp_module_is_selected_for_startup() -> None:
    """The day-close prompt depends on the ``chronicler_day_close_bundle`` MCP tool."""
    config_dir = ROSTER_DIR
    config = load_config(config_dir)

    assert config.modules.get("chronicler") == {}

    daemon = ButlerDaemon(config_dir=config_dir)
    daemon.config = config
    loaded_modules = default_registry().load_all(config.modules)
    selected_names = {module.name for module in daemon._select_startup_modules(loaded_modules)}

    assert "chronicler" in selected_names
