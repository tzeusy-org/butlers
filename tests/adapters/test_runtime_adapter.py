"""Tests for RuntimeAdapter ABC and adapter registry.

Covers ABC enforcement, create_worker/reset defaults, and registry operations.
Adapter registration and subclass checks are in test_adapter_contract.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from butlers.core.runtimes import RuntimeAdapter, get_adapter, register_adapter
from butlers.core.runtimes.base import _ADAPTER_REGISTRY

pytestmark = pytest.mark.unit


class FullAdapter(RuntimeAdapter):
    """Fully concrete adapter implementation for testing."""

    @property
    def binary_name(self) -> str:
        return "full-test-binary"

    async def invoke(self, prompt, system_prompt, mcp_servers, env, cwd=None, timeout=None):
        return ("ok", [], None)

    def build_config_file(self, mcp_servers, tmp_dir):
        return tmp_dir / "config.json"

    def parse_system_prompt_file(self, config_dir):
        return "system prompt"


def test_abc_enforcement():
    """RuntimeAdapter is abstract; subclasses missing any abstract method cannot instantiate."""
    with pytest.raises(TypeError):
        RuntimeAdapter()  # type: ignore[abstract]

    for missing_method in [
        "invoke",
        "build_config_file",
        "parse_system_prompt_file",
        "binary_name",
    ]:
        attrs: dict[str, Any] = {
            "binary_name": property(lambda self: "test"),
            "invoke": lambda self, *a, **kw: ("ok", [], None),
            "build_config_file": lambda self, mcp_servers, tmp_dir: tmp_dir / "config.json",
            "parse_system_prompt_file": lambda self, config_dir: "",
        }
        del attrs[missing_method]
        cls = type("PartialAdapter", (RuntimeAdapter,), attrs)
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]


async def test_defaults_and_registry():
    """create_worker() defaults to self; reset() is a no-op; registry raises and registers."""
    adapter = FullAdapter()
    assert isinstance(adapter, RuntimeAdapter) and adapter.create_worker() is adapter
    await adapter.reset()  # should not raise

    with pytest.raises(ValueError, match="claude") as exc_info:
        get_adapter("nope")
    msg = str(exc_info.value)
    assert "codex" in msg and "gemini" in msg and "opencode" in msg

    register_adapter("custom-test", FullAdapter)
    try:
        assert get_adapter("custom-test") is FullAdapter
    finally:
        _ADAPTER_REGISTRY.pop("custom-test", None)
