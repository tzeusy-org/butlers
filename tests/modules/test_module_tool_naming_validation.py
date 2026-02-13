"""Tests for module I/O descriptors and tool-name validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from butlers.daemon import ButlerDaemon, ModuleToolValidationError, _SpanWrappingMCP
from butlers.modules.base import Module, ToolIODescriptor
from butlers.modules.registry import ModuleRegistry

pytestmark = pytest.mark.unit


class EmptyConfig(BaseModel):
    """Minimal module config for tests."""


class DescriptorModule(Module):
    """Module that declares user and bot I/O descriptors."""

    @property
    def name(self) -> str:
        return "descriptor_mod"

    @property
    def config_schema(self) -> type[BaseModel]:
        return EmptyConfig

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

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_email_receive"),)

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="user_email_send"),)

    def bot_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_email_send"),)


class BadDescriptorModule(DescriptorModule):
    """Module with invalid descriptor naming."""

    def user_inputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="email_receive"),)


class WrongPrefixDescriptorModule(DescriptorModule):
    """Module with wrong identity prefix in descriptor group."""

    def user_outputs(self) -> tuple[ToolIODescriptor, ...]:
        return (ToolIODescriptor(name="bot_email_send"),)


class MissingRegistrationDescriptorModule(DescriptorModule):
    """Module that intentionally skips registering all declared descriptors."""

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        @mcp.tool(name="user_email_send")
        async def _user_email_send() -> dict[str, str]:
            return {"status": "ok"}


class FakeMCP:
    """Minimal FastMCP stand-in for decorator registration tests."""

    def __init__(self) -> None:
        self.registered: list[str] = []

    def tool(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        explicit_name = kwargs.get("name")

        def decorator(fn):  # noqa: ANN001, ANN202
            self.registered.append(explicit_name or fn.__name__)
            return fn

        return decorator


def _daemon() -> ButlerDaemon:
    return ButlerDaemon(config_dir=Path("."), registry=ModuleRegistry())


def test_validate_module_io_descriptors_accepts_valid_descriptors():
    """Declared user/bot I/O descriptors are collected into a tool-name set."""
    daemon = _daemon()
    names = daemon._validate_module_io_descriptors(DescriptorModule())  # noqa: SLF001
    assert names == {"user_email_receive", "user_email_send", "bot_email_send"}


def test_validate_module_io_descriptors_rejects_invalid_name():
    """Descriptor names must match user_<channel>_<action> / bot_<channel>_<action>."""
    daemon = _daemon()
    with pytest.raises(ModuleToolValidationError, match="descriptor in user_inputs"):
        daemon._validate_module_io_descriptors(BadDescriptorModule())  # noqa: SLF001


def test_validate_module_io_descriptors_rejects_wrong_group_prefix():
    """Descriptor identity prefix must match the descriptor group."""
    daemon = _daemon()
    with pytest.raises(ModuleToolValidationError, match="must start with 'user_'"):
        daemon._validate_module_io_descriptors(WrongPrefixDescriptorModule())  # noqa: SLF001


def test_wrapping_mcp_rejects_non_prefixed_registered_tool_names():
    """Registration fails when a declared module registers a non-prefixed tool."""
    wrapped = _SpanWrappingMCP(
        FakeMCP(),
        butler_name="switchboard",
        module_name="descriptor_mod",
        declared_tool_names={"user_email_send"},
    )

    with pytest.raises(ModuleToolValidationError, match="Expected 'user_<channel>_<action>'"):

        @wrapped.tool(name="send_email")
        async def _send_email() -> dict[str, str]:
            return {"status": "ok"}


def test_wrapping_mcp_rejects_undeclared_registered_tool_names():
    """Registration fails when a module registers a prefixed name not declared in descriptors."""
    wrapped = _SpanWrappingMCP(
        FakeMCP(),
        butler_name="switchboard",
        module_name="descriptor_mod",
        declared_tool_names={"user_email_send"},
    )

    with pytest.raises(ModuleToolValidationError, match="registered undeclared tool"):

        @wrapped.tool(name="user_email_reply")
        async def _user_email_reply() -> dict[str, str]:
            return {"status": "ok"}


def test_wrapping_mcp_keeps_legacy_modules_compatible_without_descriptors():
    """Legacy modules with no descriptors keep registering tools unchanged."""
    fake = FakeMCP()
    wrapped = _SpanWrappingMCP(
        fake,
        butler_name="switchboard",
        module_name="legacy_mod",
        declared_tool_names=set(),
    )

    @wrapped.tool()
    async def send_email() -> dict[str, str]:
        return {"status": "ok"}

    assert fake.registered == ["send_email"]


@pytest.mark.asyncio
async def test_register_module_tools_rejects_declared_descriptors_not_registered():
    """Daemon rejects modules that declare descriptors but skip registration."""
    module = MissingRegistrationDescriptorModule()
    daemon = _daemon()
    daemon._modules = [module]  # noqa: SLF001
    daemon._module_configs = {module.name: None}  # noqa: SLF001
    daemon.config = SimpleNamespace(name="switchboard")
    daemon.db = object()
    daemon.mcp = FakeMCP()

    with pytest.raises(
        ModuleToolValidationError,
        match=(
            "declared tool descriptors that were not registered: bot_email_send, user_email_receive"
        ),
    ):
        await daemon._register_module_tools()  # noqa: SLF001
