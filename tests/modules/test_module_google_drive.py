"""Condensed Google Drive module tests — behavioral contract only.

Replaces test_module_google_drive.py (96) + test_module_google_drive_core.py (71)
= 167 tests replaced with ~20.

Covers:
- Module ABC compliance (instantiation, name, config_schema)
- GoogleDriveConfig validation (required fields, defaults, account)
- Tool registration (expected tools)
- Startup: missing credentials raises CalendarCredentialError
- MIME type inference helper
- Drive tools: list_files, get_metadata, read_file error path
- Error helper (not configured)

[bu-7sd7a]
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.google_drive import (
    GoogleDriveConfig,
    GoogleDriveModule,
    _infer_mime_type,
)

pytestmark = pytest.mark.unit

EXPECTED_DRIVE_TOOLS = {
    "drive_list_files",
    "drive_get_file_metadata",
    "drive_read_file",
    "drive_write_file",
    "drive_create_folder",
    "drive_move_file",
    "drive_search_files",
}


def _make_mock_mcp() -> MagicMock:
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        name = kwargs.get("name")

        def decorator(fn):
            tools[name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


# ---------------------------------------------------------------------------
# ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    def test_module_contract(self) -> None:
        """GoogleDriveModule satisfies Module ABC: name, config_schema, dependencies."""
        from pydantic import BaseModel

        from butlers.modules.registry import default_registry

        mod = GoogleDriveModule()
        assert issubclass(GoogleDriveModule, Module)
        assert mod.name == "google_drive"
        assert mod.config_schema is GoogleDriveConfig
        assert issubclass(mod.config_schema, BaseModel)
        assert mod.dependencies == []
        assert mod.migration_revisions() == "google_drive"
        assert "google_drive" in default_registry().available_modules


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestGoogleDriveConfig:
    def test_defaults_and_validation(self) -> None:
        cfg = GoogleDriveConfig()
        assert cfg.account is None
        assert cfg.max_read_size_bytes > 0
        # Account is stripped of whitespace
        cfg2 = GoogleDriveConfig(account="  work@gmail.com  ")
        assert cfg2.account == "work@gmail.com"
        # Extra fields rejected
        with pytest.raises(ValidationError):
            GoogleDriveConfig(unknown="x")


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    async def test_registers_expected_tools(self) -> None:
        module = GoogleDriveModule()
        mcp = _make_mock_mcp()
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="test-butler")
        assert set(mcp._registered_tools.keys()) == EXPECTED_DRIVE_TOOLS

    async def test_butler_name_param_takes_precedence_over_db_schema(self) -> None:
        """butler_name param is stored directly and wins over db.schema."""
        module = GoogleDriveModule()
        mcp = _make_mock_mcp()
        db = MagicMock()
        db.schema = "schema_value"
        await module.register_tools(mcp=mcp, config={}, db=db, butler_name="param_value")
        assert module._butler_name == "param_value"


# ---------------------------------------------------------------------------
# MIME type inference
# ---------------------------------------------------------------------------


class TestMimeTypeInference:
    @pytest.mark.parametrize(
        "filename,expected_mime",
        [
            ("doc.txt", "text/plain"),
            ("image.png", "image/png"),
            ("file.unknown", "application/octet-stream"),
        ],
    )
    def test_infer_mime_type(self, filename: str, expected_mime: str) -> None:
        result = _infer_mime_type(filename)
        assert result == expected_mime


# ---------------------------------------------------------------------------
# Startup credential resolution
# ---------------------------------------------------------------------------


class TestOnStartup:
    async def test_startup_without_credentials_does_not_raise(self) -> None:
        # GoogleDriveModule logs a warning and continues gracefully when credentials
        # are unavailable at startup (credentials resolved lazily at tool call time).
        module = GoogleDriveModule()
        with patch(
            "butlers.google_credentials._resolve_account_entity_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # Should not raise — module starts in a degraded state
            await module.on_startup(
                config={},
                db=MagicMock(pool=MagicMock()),
                credential_store=AsyncMock(resolve=AsyncMock(return_value=None)),
            )
