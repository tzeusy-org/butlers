"""Tests for tool argument sensitivity classification.

Covers:
- ToolMeta dataclass and Module.tool_metadata() default
- Heuristic detection of sensitive argument names
- Resolution order: explicit > heuristic > default
- classify_tool_args convenience function
- Backward compatibility (existing modules unaffected)
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from butlers.modules.approvals.sensitivity import (
    SENSITIVE_ARG_NAMES,
    classify_tool_args,
    is_sensitive_by_heuristic,
    resolve_arg_sensitivity,
)
from butlers.modules.base import Module, ToolMeta

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Test fixtures — concrete Module subclasses
# ---------------------------------------------------------------------------


class _EmptyConfig(BaseModel):
    pass


class _MinimalModule(Module):
    """Module with no tool_metadata override (uses default)."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass


class _AnnotatedModule(Module):
    """Module that declares explicit sensitivity metadata."""

    @property
    def name(self) -> str:
        return "annotated"

    @property
    def config_schema(self) -> type[BaseModel]:
        return _EmptyConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        pass

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {
            "email_send_message": ToolMeta(
                arg_sensitivities={
                    "to": True,
                    "subject": False,
                    "body": True,
                }
            ),
            "email_read_message": ToolMeta(
                arg_sensitivities={
                    "folder": False,
                }
            ),
        }


# ---------------------------------------------------------------------------
# ToolMeta dataclass + Module.tool_metadata() default behaviour
# ---------------------------------------------------------------------------


class TestToolMetaAndModuleDefault:
    """ToolMeta construction and Module.tool_metadata() default."""

    def test_default_empty(self):
        meta = ToolMeta()
        assert meta.arg_sensitivities == {}

    def test_explicit_sensitivities(self):
        meta = ToolMeta(arg_sensitivities={"to": True, "body": False})
        assert meta.arg_sensitivities["to"] is True
        assert meta.arg_sensitivities["body"] is False

    def test_minimal_module_defaults_and_compatibility(self):
        """Module with no override returns {} and works normally."""
        mod = _MinimalModule()
        assert mod.tool_metadata() == {}
        assert mod.name == "minimal"
        assert mod.dependencies == []

    def test_annotated_module_returns_metadata(self):
        mod = _AnnotatedModule()
        metadata = mod.tool_metadata()
        assert "email_send_message" in metadata
        assert metadata["email_send_message"].arg_sensitivities["to"] is True
        assert metadata["email_send_message"].arg_sensitivities["subject"] is False


# ---------------------------------------------------------------------------
# Heuristic detection
# ---------------------------------------------------------------------------


class TestHeuristic:
    """Tests for the is_sensitive_by_heuristic function."""

    @pytest.mark.parametrize(
        "arg_name,expected",
        [
            ("to", True),
            ("email", True),
            ("amount", True),
            ("account", True),
            ("To", True),  # case insensitive
            ("EMAIL", True),
            ("Amount", True),
            ("body", False),
            ("subject", False),
            ("limit", False),
        ],
    )
    def test_heuristic_detection(self, arg_name: str, expected: bool):
        assert is_sensitive_by_heuristic(arg_name) is expected

    def test_sensitive_set_contents(self):
        expected = {
            "to",
            "recipient",
            "recipients",
            "email",
            "address",
            "url",
            "uri",
            "amount",
            "price",
            "cost",
            "account",
            "password",
            "token",
            "secret",
            "key",
            "api_key",
            "auth",
            "credential",
            "credentials",
        }
        assert SENSITIVE_ARG_NAMES == expected


# ---------------------------------------------------------------------------
# Resolution order: explicit > heuristic > default
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    """Tests for resolve_arg_sensitivity priority chain."""

    @pytest.mark.parametrize(
        "tool,arg,module_factory,expected",
        [
            # Explicit True wins (to is both heuristic and explicitly True)
            ("email_send_message", "to", lambda: _AnnotatedModule(), True),
            # Explicit False wins (subject explicitly False)
            ("email_send_message", "subject", lambda: _AnnotatedModule(), False),
            # Heuristic fallback when no explicit (MinimalModule has no metadata)
            ("email_send_message", "to", lambda: _MinimalModule(), True),
            # Heuristic applies for tools not in metadata
            ("unknown_tool", "to", lambda: _AnnotatedModule(), True),
            # Heuristic applies for args not listed in tool's ToolMeta
            ("email_send_message", "recipient", lambda: _AnnotatedModule(), True),
            # Default not sensitive (no explicit, no heuristic)
            ("some_tool", "body", lambda: _MinimalModule(), False),
            # No module, heuristic applies
            ("core_tool", "to", lambda: None, True),
            # No module, default not sensitive
            ("core_tool", "body", lambda: None, False),
        ],
        ids=[
            "explicit-true",
            "explicit-false",
            "heuristic-fallback-no-override",
            "heuristic-unknown-tool",
            "heuristic-unlisted-arg",
            "default-not-sensitive",
            "no-module-heuristic",
            "no-module-default",
        ],
    )
    def test_resolution(self, tool, arg, module_factory, expected):
        mod = module_factory()
        assert resolve_arg_sensitivity(tool, arg, module=mod) is expected

    def test_explicit_false_overrides_heuristic_for_sensitive_name(self):
        """Module can explicitly mark a heuristically-sensitive name as safe."""

        class _OverrideModule(_MinimalModule):
            def tool_metadata(self) -> dict[str, ToolMeta]:
                return {"transfer": ToolMeta(arg_sensitivities={"amount": False})}

        mod = _OverrideModule()
        assert resolve_arg_sensitivity("transfer", "amount", mod) is False


# ---------------------------------------------------------------------------
# classify_tool_args convenience function
# ---------------------------------------------------------------------------


class TestClassifyToolArgs:
    """Tests for the classify_tool_args batch function."""

    def test_mixed_sensitivity_without_module(self):
        result = classify_tool_args("email_send_message", ["to", "body", "subject"])
        assert set(result.keys()) == {"to", "body", "subject"}
        assert result["to"] is True
        assert result["body"] is False
        assert result["subject"] is False

    def test_with_explicit_module(self):
        mod = _AnnotatedModule()
        result = classify_tool_args("email_send_message", ["to", "subject", "body"], mod)
        assert result["to"] is True
        assert result["subject"] is False
        assert result["body"] is True

    def test_empty_args_and_no_module(self):
        assert classify_tool_args("some_tool", []) == {}
        result = classify_tool_args("core_tool", ["url", "query"], module=None)
        assert result["url"] is True
        assert result["query"] is False
