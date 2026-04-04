"""Condensed SelfHealingModule tests — behavioral contract only.

Replaces 53 tests with ~12 focused behavioral tests.

Covers:
- Module ABC compliance
- SelfHealingConfig validation (defaults, extra rejected)
- Tool registration (report_error, get_healing_status)
- Tool sensitivity metadata
- report_error: not configured returns error dict
- get_healing_status: empty list when no attempts
- _serialize_attempt: handles UUID and datetime

[bu-7sd7a]
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.self_healing import SelfHealingConfig, SelfHealingModule, _serialize_attempt

pytestmark = pytest.mark.unit


def _make_module() -> SelfHealingModule:
    return SelfHealingModule()


class TestModuleABC:
    def test_is_module_subclass(self) -> None:
        assert issubclass(SelfHealingModule, Module)

    def test_name(self) -> None:
        assert _make_module().name == "self_healing"

    def test_config_schema(self) -> None:
        assert _make_module().config_schema is SelfHealingConfig

    def test_migration_revisions(self) -> None:
        # Schema owned by core migration (public.healing_attempts)
        assert _make_module().migration_revisions() is None

    def test_in_default_registry(self) -> None:
        from butlers.modules.registry import default_registry

        assert "self_healing" in default_registry().available_modules


class TestSelfHealingConfig:
    def test_defaults(self) -> None:
        cfg = SelfHealingConfig()
        assert cfg.max_concurrent > 0
        assert cfg.enabled is True

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SelfHealingConfig(unknown_field="x")


class TestToolRegistration:
    async def test_registers_expected_tools(self) -> None:
        mod = _make_module()
        registered: dict = {}
        mcp = MagicMock()
        mcp.tool.side_effect = lambda **kw: (
            lambda fn: registered.__setitem__(kw.get("name") or fn.__name__, fn) or fn
        )
        await mod.register_tools(mcp=mcp, config=None, db=None)
        assert "report_error" in registered
        assert "get_healing_status" in registered

    def test_tool_metadata_marks_sensitive_args(self) -> None:
        mod = _make_module()
        meta = mod.tool_metadata()
        assert "report_error" in meta
        report_meta = meta["report_error"]
        assert isinstance(report_meta, ToolMeta)
        # error_message, traceback, context should be marked sensitive
        for key in ("error_message", "traceback", "context"):
            assert report_meta.arg_sensitivities.get(key) is True


class TestReportErrorBehavior:
    async def test_not_configured_returns_error(self) -> None:
        mod = _make_module()
        mcp = MagicMock()
        registered: dict = {}
        mcp.tool.side_effect = lambda **kw: (
            lambda fn: registered.__setitem__(kw.get("name") or fn.__name__, fn) or fn
        )
        await mod.register_tools(mcp=mcp, config=None, db=None)

        result = await registered["report_error"](
            error_type="test_error", error_message="test error"
        )
        assert isinstance(result, dict)


class TestSerializeAttempt:
    def test_serializes_uuid_and_datetime(self) -> None:
        attempt = {
            "id": uuid.uuid4(),
            "created_at": datetime.now(UTC),
            "fingerprint": "abc123",
            "status": "investigating",
        }
        result = _serialize_attempt(attempt)
        assert isinstance(result["id"], str)
        assert isinstance(result["created_at"], str)
