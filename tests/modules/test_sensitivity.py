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

    async def on_startup(self, config: Any, db: Any) -> None:
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

    async def on_startup(self, config: Any, db: Any) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {
            "bot_email_send_message": ToolMeta(
                arg_sensitivities={
                    "to": True,
                    "subject": False,
                    "body": True,
                }
            ),
            "bot_email_read_message": ToolMeta(
                arg_sensitivities={
                    "folder": False,
                }
            ),
        }


# ---------------------------------------------------------------------------
# ToolMeta dataclass tests
# ---------------------------------------------------------------------------


class TestToolMeta:
    """Tests for the ToolMeta dataclass."""

    def test_default_empty(self):
        """Default ToolMeta has an empty arg_sensitivities dict."""
        meta = ToolMeta()
        assert meta.arg_sensitivities == {}

    def test_explicit_sensitivities(self):
        """ToolMeta accepts explicit arg_sensitivities."""
        meta = ToolMeta(arg_sensitivities={"to": True, "body": False})
        assert meta.arg_sensitivities["to"] is True
        assert meta.arg_sensitivities["body"] is False


# ---------------------------------------------------------------------------
# Module.tool_metadata() default behaviour
# ---------------------------------------------------------------------------


class TestModuleToolMetadataDefault:
    """Tests for Module.tool_metadata() default implementation."""

    def test_default_returns_empty_dict(self):
        """A module that does not override tool_metadata() returns {}."""
        mod = _MinimalModule()
        assert mod.tool_metadata() == {}

    def test_existing_modules_unaffected(self):
        """Existing modules (no override) still instantiate and work normally."""
        mod = _MinimalModule()
        assert mod.name == "minimal"
        assert mod.dependencies == []
        # The default tool_metadata() is available and returns empty
        assert mod.tool_metadata() == {}

    def test_override_returns_metadata(self):
        """A module that overrides tool_metadata() returns its declarations."""
        mod = _AnnotatedModule()
        metadata = mod.tool_metadata()
        assert "bot_email_send_message" in metadata
        assert "bot_email_read_message" in metadata
        assert metadata["bot_email_send_message"].arg_sensitivities["to"] is True
        assert metadata["bot_email_send_message"].arg_sensitivities["subject"] is False


# ---------------------------------------------------------------------------
# Heuristic detection
# ---------------------------------------------------------------------------


class TestHeuristic:
    """Tests for the is_sensitive_by_heuristic function."""

    @pytest.mark.parametrize(
        "arg_name",
        [
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
        ],
    )
    def test_known_sensitive_args(self, arg_name: str):
        """All canonical sensitive argument names are detected."""
        assert is_sensitive_by_heuristic(arg_name) is True

    @pytest.mark.parametrize(
        "arg_name",
        ["To", "RECIPIENT", "Email", "URL", "Amount", "ACCOUNT"],
    )
    def test_case_insensitive(self, arg_name: str):
        """Heuristic matching is case-insensitive."""
        assert is_sensitive_by_heuristic(arg_name) is True

    @pytest.mark.parametrize(
        "arg_name",
        ["body", "subject", "text", "message", "title", "description", "limit", "offset"],
    )
    def test_non_sensitive_args(self, arg_name: str):
        """Non-sensitive argument names are not flagged."""
        assert is_sensitive_by_heuristic(arg_name) is False

    def test_sensitive_set_is_frozenset(self):
        """SENSITIVE_ARG_NAMES is immutable."""
        assert isinstance(SENSITIVE_ARG_NAMES, frozenset)

    def test_sensitive_set_contents(self):
        """SENSITIVE_ARG_NAMES contains exactly the expected names."""
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
# Resolution order
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    """Tests for resolve_arg_sensitivity and its priority chain."""

    def test_explicit_true_overrides_heuristic(self):
        """Explicit declaration of sensitive=True wins over heuristic."""
        mod = _AnnotatedModule()
        # "to" is heuristically sensitive AND explicitly marked True
        assert resolve_arg_sensitivity("bot_email_send_message", "to", mod) is True

    def test_explicit_false_overrides_heuristic(self):
        """Explicit declaration of sensitive=False wins over heuristic.

        This is the critical test: 'subject' would not be heuristically
        sensitive anyway, but more importantly, if a module explicitly says
        an arg is NOT sensitive, that must be respected even if the name
        would match a heuristic.
        """
        mod = _AnnotatedModule()
        assert resolve_arg_sensitivity("bot_email_send_message", "subject", mod) is False

    def test_explicit_false_overrides_heuristic_for_sensitive_name(self):
        """Module can explicitly mark a heuristically-sensitive name as safe."""

        class _OverrideModule(_MinimalModule):
            def tool_metadata(self) -> dict[str, ToolMeta]:
                return {
                    "transfer": ToolMeta(arg_sensitivities={"amount": False}),
                }

        mod = _OverrideModule()
        # "amount" is heuristically sensitive, but explicitly False
        assert resolve_arg_sensitivity("transfer", "amount", mod) is False

    def test_heuristic_fallback_when_no_explicit(self):
        """When no explicit declaration, heuristic kicks in."""
        mod = _MinimalModule()  # No tool_metadata override
        # "to" is heuristically sensitive
        assert resolve_arg_sensitivity("bot_email_send_message", "to", mod) is True

    def test_heuristic_fallback_when_tool_not_in_metadata(self):
        """Heuristic applies for tools not listed in module metadata."""
        mod = _AnnotatedModule()
        # "unknown_tool" is not in the metadata, but "to" is heuristic
        assert resolve_arg_sensitivity("unknown_tool", "to", mod) is True

    def test_heuristic_fallback_when_arg_not_in_tool_meta(self):
        """Heuristic applies for args not listed in tool's ToolMeta."""
        mod = _AnnotatedModule()
        # bot_email_send_message is in metadata, but "recipient" is not explicitly listed
        # "recipient" IS heuristically sensitive
        assert resolve_arg_sensitivity("bot_email_send_message", "recipient", mod) is True

    def test_default_not_sensitive(self):
        """When neither explicit nor heuristic match, default is not sensitive."""
        mod = _MinimalModule()
        assert resolve_arg_sensitivity("some_tool", "body", mod) is False

    def test_no_module_heuristic_applies(self):
        """When module is None, heuristic still applies."""
        assert resolve_arg_sensitivity("core_tool", "to", module=None) is True

    def test_no_module_default_not_sensitive(self):
        """When module is None and no heuristic match, default is not sensitive."""
        assert resolve_arg_sensitivity("core_tool", "body", module=None) is False


# ---------------------------------------------------------------------------
# classify_tool_args convenience function
# ---------------------------------------------------------------------------


class TestClassifyToolArgs:
    """Tests for the classify_tool_args batch function."""

    def test_classifies_all_args(self):
        """Returns a dict with an entry for every argument."""
        result = classify_tool_args("bot_email_send_message", ["to", "body", "subject"])
        assert set(result.keys()) == {"to", "body", "subject"}

    def test_mixed_sensitivity(self):
        """Correctly classifies a mix of sensitive and non-sensitive args."""
        result = classify_tool_args("bot_email_send_message", ["to", "body", "subject"])
        assert result["to"] is True  # heuristic
        assert result["body"] is False  # default
        assert result["subject"] is False  # default

    def test_with_explicit_module(self):
        """Uses module declarations when available."""
        mod = _AnnotatedModule()
        result = classify_tool_args("bot_email_send_message", ["to", "subject", "body"], mod)
        assert result["to"] is True  # explicit True
        assert result["subject"] is False  # explicit False
        assert result["body"] is True  # explicit True

    def test_empty_args(self):
        """Returns empty dict for empty arg list."""
        result = classify_tool_args("some_tool", [])
        assert result == {}

    def test_without_module(self):
        """Works with module=None (core tools)."""
        result = classify_tool_args("core_tool", ["url", "query"], module=None)
        assert result["url"] is True  # heuristic
        assert result["query"] is False  # default
