"""Tests for RuntimeAdapter ABC and adapter registry.

Covers ABC enforcement, create_worker/reset defaults, and registry operations.
Adapter registration and subclass checks are in test_adapter_contract.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any



import pytest

from butlers.core.runtimes import RuntimeAdapter, get_adapter, register_adapter

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


# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------


def test_cannot_instantiate_runtime_adapter_abc():
    """RuntimeAdapter is abstract and cannot be instantiated directly."""
    with pytest.raises(TypeError):
        RuntimeAdapter()  # type: ignore[abstract]


@pytest.mark.parametrize(
    "missing_method",
    ["invoke", "build_config_file", "parse_system_prompt_file", "binary_name"],
)
def test_missing_abstract_method_raises(missing_method: str):
    """A subclass missing any abstract method cannot be instantiated."""
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


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


def test_runtime_adapter_default_create_worker_returns_self():
    """A fully concrete subclass instantiates; create_worker() defaults to returning self."""
    adapter = FullAdapter()
    assert isinstance(adapter, RuntimeAdapter)
    assert adapter.create_worker() is adapter


async def test_runtime_adapter_default_reset_is_noop():
    """RuntimeAdapter.reset() default implementation is a no-op."""
    await FullAdapter().reset()


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------


def test_get_adapter_unknown_raises_with_available_list():
    """get_adapter() raises ValueError listing available adapters."""
    with pytest.raises(ValueError, match="claude") as exc_info:
        get_adapter("nope")
    msg = str(exc_info.value)
    assert "codex" in msg
    assert "gemini" in msg
    assert "opencode" in msg


def test_register_custom_adapter():
    """register_adapter() allows adding new runtime types at runtime."""
    register_adapter("custom-test", FullAdapter)
    assert get_adapter("custom-test") is FullAdapter


