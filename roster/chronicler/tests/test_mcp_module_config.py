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


def test_day_close_prompt_keeps_prose_human_readable() -> None:
    """Scheduled day-close prompt should not ask for raw refs in user prose."""
    config = load_config(ROSTER_DIR)
    task = next(t for t in config.schedules if t.name == "chronicler_day_close")
    prompt = task.prompt or ""

    assert 'timezone="<owner-IANA-timezone>"' in prompt
    assert "Does not print raw source_ref values" in prompt
    assert "system records provenance" in prompt
    assert "human-facing message" in prompt
    assert "episodes_truncated" in prompt
    assert "cites source_ref values" not in prompt.lower()
