"""Tests for the Google Drive module.

Covers tasks 7.1–7.8 from openspec/changes/google-drive-integration/tasks.md §7:
  7.1  GoogleDriveConfig validation (valid, missing fields, extra rejected, account default)
  7.2  on_startup: credential resolution, scope validation failure, account not found
  7.3  Butler folder hierarchy: creation, caching, re-creation after deletion
  7.4  drive_list_files: folder listing, query filtering, pagination, root default
  7.5  drive_get_file_metadata: found, not found
  7.6  drive_read_file: text, Google Doc export, Sheet CSV, size limit, binary rejection
  7.7  drive_write_file: default butler folder, explicit folder, MIME inference
  7.8  drive_create_folder, drive_move_file, drive_search_files
  7.9  Rate-limit retry: 403 permission-denial vs quota discrimination
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.google_drive import (
    GoogleDriveConfig,
    GoogleDriveModule,
    _infer_mime_type,
    _is_retryable_403,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_httpx_response(status_code: int, json_body: Any = None, text: str = "") -> httpx.Response:
    """Build a fake httpx.Response for testing."""
    if json_body is not None:
        content = json.dumps(json_body).encode()
        headers = {"content-type": "application/json"}
    else:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    return httpx.Response(status_code, content=content, headers=headers)


def _make_mock_mcp() -> MagicMock:
    """Create a mock MCP server that captures registered tools."""
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def tool_decorator(*_args, **kwargs):
        declared_name = kwargs.get("name")

        def decorator(fn):
            tools[declared_name or fn.__name__] = fn
            return fn

        return decorator

    mcp.tool = tool_decorator
    mcp._registered_tools = tools
    return mcp


def _make_module_with_mocked_http(
    get_responses: list[httpx.Response] | None = None,
    post_responses: list[httpx.Response] | None = None,
    patch_responses: list[httpx.Response] | None = None,
) -> GoogleDriveModule:
    """Return a GoogleDriveModule with mocked _get/_post/_patch methods.

    The module is already in 'started' state (credentials_ok=True, http_client set).
    Tests that mock specific API calls should patch module._get/_post/_patch directly.
    """
    module = GoogleDriveModule()
    module._credentials_ok = True
    module._http_client = MagicMock()  # never called directly by tools
    module._config = GoogleDriveConfig()
    module._butler_name = "test-butler"

    if get_responses is not None:
        module._get = AsyncMock(side_effect=get_responses)
    else:
        module._get = AsyncMock(return_value=_make_httpx_response(200, {"files": []}))

    if post_responses is not None:
        module._post = AsyncMock(side_effect=post_responses)
    else:
        module._post = AsyncMock(return_value=_make_httpx_response(200, {"id": "new-id"}))

    if patch_responses is not None:
        module._patch = AsyncMock(side_effect=patch_responses)
    else:
        module._patch = AsyncMock(return_value=_make_httpx_response(200, {}))

    return module


# ---------------------------------------------------------------------------
# 7.1  GoogleDriveConfig validation
# ---------------------------------------------------------------------------


class TestGoogleDriveConfig:
    """Task 7.1 — Config validation, defaults, and extra-field rejection."""

    def test_defaults(self):
        config = GoogleDriveConfig()
        assert config.account is None
        assert config.max_read_size_bytes == 10_485_760
        assert config.butler_folder_name == "butlers"

    def test_valid_config_with_account(self):
        config = GoogleDriveConfig(account="work@gmail.com")
        assert config.account == "work@gmail.com"

    def test_valid_config_all_fields(self):
        config = GoogleDriveConfig(
            account="user@example.com",
            max_read_size_bytes=5_000_000,
            butler_folder_name="my-butlers",
        )
        assert config.account == "user@example.com"
        assert config.max_read_size_bytes == 5_000_000
        assert config.butler_folder_name == "my-butlers"

    def test_from_empty_dict(self):
        config = GoogleDriveConfig(**{})
        assert config.account is None
        assert config.max_read_size_bytes == 10_485_760

    def test_account_none_by_default(self):
        config = GoogleDriveConfig()
        assert config.account is None

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            GoogleDriveConfig(unknown_key="value")
        errors = exc_info.value.errors()
        assert any(e["type"] == "extra_forbidden" for e in errors)

    def test_extra_field_has_correct_loc(self):
        with pytest.raises(ValidationError) as exc_info:
            GoogleDriveConfig(extra_field="bad")
        locs = [e["loc"] for e in exc_info.value.errors()]
        assert ("extra_field",) in locs

    def test_max_read_size_bytes_custom(self):
        config = GoogleDriveConfig(max_read_size_bytes=1024)
        assert config.max_read_size_bytes == 1024

    def test_butler_folder_name_custom(self):
        config = GoogleDriveConfig(butler_folder_name="agents")
        assert config.butler_folder_name == "agents"

    def test_account_whitespace_stripped(self):
        """account field strips leading/trailing whitespace."""
        config = GoogleDriveConfig(account="  user@example.com  ")
        assert config.account == "user@example.com"

    def test_account_blank_string_becomes_none(self):
        """An account of all-whitespace is normalised to None."""
        config = GoogleDriveConfig(account="   ")
        assert config.account is None


# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """Verify GoogleDriveModule implements the Module ABC correctly."""

    def test_is_module_subclass(self):
        assert issubclass(GoogleDriveModule, Module)

    def test_instantiates(self):
        mod = GoogleDriveModule()
        assert mod is not None

    def test_isinstance_check(self):
        assert isinstance(GoogleDriveModule(), Module)

    def test_name(self):
        assert GoogleDriveModule().name == "google_drive"

    def test_config_schema(self):
        mod = GoogleDriveModule()
        assert mod.config_schema is GoogleDriveConfig

    def test_dependencies_empty(self):
        assert GoogleDriveModule().dependencies == []

    def test_migration_revisions(self):
        assert GoogleDriveModule().migration_revisions() == "google_drive"

    def test_tool_metadata_write_file_sensitive(self):
        meta = GoogleDriveModule().tool_metadata()
        assert "drive_write_file" in meta
        assert meta["drive_write_file"].arg_sensitivities.get("content") is True

    def test_tool_metadata_move_file_sensitive(self):
        meta = GoogleDriveModule().tool_metadata()
        assert "drive_move_file" in meta
        move_meta = meta["drive_move_file"]
        assert move_meta.arg_sensitivities.get("file_id") is True
        assert move_meta.arg_sensitivities.get("new_parent_id") is True

    def test_tool_metadata_read_tools_absent(self):
        meta = GoogleDriveModule().tool_metadata()
        for tool in [
            "drive_list_files",
            "drive_get_file_metadata",
            "drive_read_file",
            "drive_search_files",
        ]:
            assert tool not in meta, f"{tool} should NOT be in tool_metadata"

    def test_tool_metadata_returns_tool_meta_instances(self):
        meta = GoogleDriveModule().tool_metadata()
        for tool_meta in meta.values():
            assert isinstance(tool_meta, ToolMeta)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestToolRegistration:
    """Verify register_tools registers all 7 expected MCP tools."""

    EXPECTED_TOOLS = {
        "drive_list_files",
        "drive_get_file_metadata",
        "drive_read_file",
        "drive_write_file",
        "drive_create_folder",
        "drive_move_file",
        "drive_search_files",
    }

    async def test_registers_all_7_tools(self):
        mod = GoogleDriveModule()
        mcp = _make_mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)
        assert set(mcp._registered_tools.keys()) == self.EXPECTED_TOOLS

    async def test_tool_count_is_7(self):
        mod = GoogleDriveModule()
        mcp = _make_mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)
        assert len(mcp._registered_tools) == 7

    async def test_all_tools_callable(self):
        mod = GoogleDriveModule()
        mcp = _make_mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)
        for name, fn in mcp._registered_tools.items():
            assert callable(fn), f"{name} should be callable"

    async def test_register_tools_extracts_butler_name_from_db_schema(self):
        """register_tools derives _butler_name from db.schema."""
        mod = GoogleDriveModule()
        mcp = _make_mock_mcp()
        db = MagicMock()
        db.schema = "finance"
        await mod.register_tools(mcp=mcp, config={}, db=db)
        assert mod._butler_name == "finance"

    async def test_register_tools_keeps_default_butler_name_without_db(self):
        """Without db, _butler_name keeps default value."""
        mod = GoogleDriveModule()
        mcp = _make_mock_mcp()
        await mod.register_tools(mcp=mcp, config={}, db=None)
        assert mod._butler_name == "butler"


# ---------------------------------------------------------------------------
# 7.2  on_startup
# ---------------------------------------------------------------------------


class TestOnStartup:
    """Task 7.2 — Credential resolution, scope validation, graceful degradation."""

    async def test_startup_creates_http_client(self):
        mod = GoogleDriveModule()
        creds = MagicMock()
        creds.scope = "https://www.googleapis.com/auth/drive"
        mock_store = MagicMock()

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new=AsyncMock(return_value=creds),
        ):
            await mod.on_startup(config=GoogleDriveConfig(), db=None, credential_store=mock_store)

        assert mod._http_client is not None
        assert mod._credentials_ok is True

    async def test_startup_without_credential_store_does_not_raise(self):
        """Missing credential_store causes graceful degradation, not exception."""
        mod = GoogleDriveModule()
        # Should NOT raise — graceful degradation
        await mod.on_startup(config=GoogleDriveConfig(), db=None, credential_store=None)
        assert mod._credentials_ok is False
        assert mod._http_client is None

    async def test_startup_passes_account_to_resolver(self):
        mod = GoogleDriveModule()
        creds = MagicMock()
        creds.scope = "https://www.googleapis.com/auth/drive"
        mock_store = MagicMock()

        captured: dict[str, Any] = {}

        async def _capture_resolve(store, *, pool=None, caller="", account=None):
            captured["account"] = account
            return creds

        with patch("butlers.modules.google_drive.resolve_google_credentials", new=_capture_resolve):
            await mod.on_startup(
                config=GoogleDriveConfig(account="work@gmail.com"),
                db=None,
                credential_store=mock_store,
            )

        assert captured["account"] == "work@gmail.com"

    async def test_startup_primary_account_uses_none(self):
        mod = GoogleDriveModule()
        creds = MagicMock()
        creds.scope = "https://www.googleapis.com/auth/drive"
        mock_store = MagicMock()

        captured: dict[str, Any] = {}

        async def _capture_resolve(store, *, pool=None, caller="", account=None):
            captured["account"] = account
            return creds

        with patch("butlers.modules.google_drive.resolve_google_credentials", new=_capture_resolve):
            await mod.on_startup(
                config=GoogleDriveConfig(),
                db=None,
                credential_store=mock_store,
            )

        assert captured["account"] is None

    async def test_startup_missing_credentials_degrades_gracefully(self):
        """MissingGoogleCredentialsError causes warning + graceful degradation."""
        from butlers.google_credentials import MissingGoogleCredentialsError

        mod = GoogleDriveModule()
        mock_store = MagicMock()

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new=AsyncMock(side_effect=MissingGoogleCredentialsError("not found")),
        ):
            # Should NOT raise — logs warning and sets credentials_ok=False
            await mod.on_startup(
                config=GoogleDriveConfig(account="nobody@example.com"),
                db=None,
                credential_store=mock_store,
            )

        assert mod._credentials_ok is False

    async def test_startup_scope_validation_degrades_for_readonly_only(self):
        """drive.readonly scope causes warning + graceful degradation."""
        mod = GoogleDriveModule()
        creds = MagicMock()
        creds.scope = "https://www.googleapis.com/auth/drive.readonly"
        mock_store = MagicMock()

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new=AsyncMock(return_value=creds),
        ):
            await mod.on_startup(
                config=GoogleDriveConfig(),
                db=None,
                credential_store=mock_store,
            )

        assert mod._credentials_ok is False

    async def test_startup_full_drive_scope_passes(self):
        mod = GoogleDriveModule()
        creds = MagicMock()
        creds.scope = (
            "https://www.googleapis.com/auth/drive https://www.googleapis.com/auth/gmail.readonly"
        )
        mock_store = MagicMock()

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new=AsyncMock(return_value=creds),
        ):
            await mod.on_startup(config=GoogleDriveConfig(), db=None, credential_store=mock_store)

        assert mod._http_client is not None
        assert mod._credentials_ok is True

    async def test_startup_derives_butler_name_from_db_schema(self):
        """on_startup derives _butler_name from db.schema."""
        mod = GoogleDriveModule()
        creds = MagicMock()
        creds.scope = "https://www.googleapis.com/auth/drive"
        mock_store = MagicMock()
        db = MagicMock()
        db.schema = "finance"
        db.pool = None

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new=AsyncMock(return_value=creds),
        ):
            await mod.on_startup(config=GoogleDriveConfig(), db=db, credential_store=mock_store)

        assert mod._butler_name == "finance"

    async def test_shutdown_closes_http_client(self):
        mod = GoogleDriveModule()
        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()
        mod._http_client = mock_client

        await mod.on_shutdown()

        mock_client.aclose.assert_awaited_once()
        assert mod._http_client is None

    async def test_shutdown_no_client_is_safe(self):
        mod = GoogleDriveModule()
        mod._http_client = None
        # Should not raise
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# 7.3  Butler folder hierarchy
# ---------------------------------------------------------------------------


class TestButlerFolderHierarchy:
    """Task 7.3 — Folder creation, caching, re-creation after deletion."""

    async def test_creates_root_and_subfolder(self):
        """With no DB cache, both root and per-butler folders are created."""
        module = GoogleDriveModule()
        module._config = GoogleDriveConfig()
        module._butler_name = "my-butler"
        module._db = None
        module._credentials_ok = True
        module._http_client = MagicMock()

        root_search = _make_httpx_response(200, {"files": []})
        sub_search = _make_httpx_response(200, {"files": []})
        root_create = _make_httpx_response(200, {"id": "root-folder-id"})
        sub_create = _make_httpx_response(200, {"id": "sub-folder-id"})

        module._get = AsyncMock(side_effect=[root_search, sub_search])
        module._post = AsyncMock(side_effect=[root_create, sub_create])

        folder_id = await module._ensure_butler_folder("my-butler")
        assert folder_id == "sub-folder-id"
        assert module._post.call_count == 2

    async def test_uses_existing_root_folder(self):
        """When root folder exists, skips creation and uses its ID for subfolder."""
        module = GoogleDriveModule()
        module._config = GoogleDriveConfig()
        module._butler_name = "my-butler"
        module._db = None
        module._credentials_ok = True
        module._http_client = MagicMock()

        root_search = _make_httpx_response(200, {"files": [{"id": "existing-root"}]})
        sub_search = _make_httpx_response(200, {"files": []})
        sub_create = _make_httpx_response(200, {"id": "new-sub-id"})

        module._get = AsyncMock(side_effect=[root_search, sub_search])
        module._post = AsyncMock(return_value=sub_create)

        folder_id = await module._ensure_butler_folder("my-butler")
        assert folder_id == "new-sub-id"
        # Only one creation call (subfolder)
        assert module._post.call_count == 1

    async def test_caches_folder_id_in_memory(self):
        """Second call to _ensure_butler_folder uses in-memory cache, no new API calls."""
        module = GoogleDriveModule()
        module._config = GoogleDriveConfig()
        module._butler_name = "my-butler"
        module._db = None
        module._credentials_ok = True
        module._http_client = MagicMock()

        root_search = _make_httpx_response(200, {"files": []})
        sub_search = _make_httpx_response(200, {"files": []})
        root_create = _make_httpx_response(200, {"id": "root-id"})
        sub_create = _make_httpx_response(200, {"id": "sub-id"})
        # For DB-miss verification on second call (DB is None so no DB check)
        exists_check = _make_httpx_response(200, {"id": "sub-id", "trashed": False})

        module._get = AsyncMock(side_effect=[root_search, sub_search, exists_check])
        module._post = AsyncMock(side_effect=[root_create, sub_create])

        # First call — creates folders
        folder_id1 = await module._ensure_butler_folder("my-butler")
        # Second call — should use in-memory cache (no additional post calls)
        folder_id2 = await module._ensure_butler_folder("my-butler")

        assert folder_id1 == folder_id2 == "sub-id"

    async def test_recreates_deleted_folder(self):
        """When cached folder is trashed, re-creates and updates cache."""
        module = GoogleDriveModule()
        module._config = GoogleDriveConfig()
        module._butler_name = "my-butler"
        module._db = None
        module._credentials_ok = True
        module._http_client = MagicMock()

        # Pre-seed in-memory cache with a "deleted" folder
        module._folder_cache[("my-butler", "primary")] = "deleted-folder-id"

        # First get returns trashed=True
        trashed_check = _make_httpx_response(200, {"id": "deleted-folder-id", "trashed": True})
        root_search = _make_httpx_response(200, {"files": []})
        sub_search = _make_httpx_response(200, {"files": []})
        root_create = _make_httpx_response(200, {"id": "new-root-id"})
        sub_create = _make_httpx_response(200, {"id": "new-sub-id"})

        module._get = AsyncMock(side_effect=[trashed_check, root_search, sub_search])
        module._post = AsyncMock(side_effect=[root_create, sub_create])

        folder_id = await module._ensure_butler_folder("my-butler")
        assert folder_id == "new-sub-id"

    async def test_recreates_missing_folder_404(self):
        """When cached folder returns 404, falls through to creation."""
        module = GoogleDriveModule()
        module._config = GoogleDriveConfig()
        module._butler_name = "my-butler"
        module._db = None
        module._credentials_ok = True
        module._http_client = MagicMock()

        module._folder_cache[("my-butler", "primary")] = "gone-folder-id"

        not_found = _make_httpx_response(404, {"error": {"code": 404}})
        root_search = _make_httpx_response(200, {"files": []})
        sub_search = _make_httpx_response(200, {"files": []})
        root_create = _make_httpx_response(200, {"id": "root-id"})
        sub_create = _make_httpx_response(200, {"id": "sub-id"})

        module._get = AsyncMock(side_effect=[not_found, root_search, sub_search])
        module._post = AsyncMock(side_effect=[root_create, sub_create])

        folder_id = await module._ensure_butler_folder("my-butler")
        assert folder_id == "sub-id"


# ---------------------------------------------------------------------------
# 7.4  drive_list_files
# ---------------------------------------------------------------------------


class TestDriveListFiles:
    """Task 7.4 — Folder listing, query filtering, pagination, root default."""

    async def test_list_files_in_folder(self):
        """Basic listing with folder_id."""
        files = [
            {"id": "f1", "name": "doc.txt", "mimeType": "text/plain"},
            {"id": "f2", "name": "sheet.csv", "mimeType": "text/csv"},
        ]
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": files})]
        )

        result = await module._drive_list_files(folder_id="folder123", query=None)

        assert result["total"] == 2
        assert result["truncated"] is False
        assert len(result["files"]) == 2
        # Verify folder filter was in query
        call_params = module._get.call_args[1]["params"]
        assert "folder123" in call_params["q"]
        assert "trashed=false" in call_params["q"]

    async def test_list_files_root_default(self):
        """When folder_id is None, no parent filter is applied (list all non-trashed)."""
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": []})]
        )

        await module._drive_list_files(folder_id=None, query=None)

        call_params = module._get.call_args[1]["params"]
        # Without a folder_id, query should still include trashed=false
        assert "trashed=false" in call_params["q"]

    async def test_list_files_with_query_filter(self):
        """User query is ANDed with folder parent filter."""
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": []})]
        )

        await module._drive_list_files(folder_id="abc123", query="name contains 'report'")

        call_params = module._get.call_args[1]["params"]
        q = call_params["q"]
        assert "abc123" in q
        assert "trashed=false" in q
        assert "name contains 'report'" in q

    async def test_list_files_pagination_up_to_1000(self):
        """Pagination accumulates results up to 1000 items."""
        page1_files = [{"id": f"f{i}", "name": f"file{i}.txt"} for i in range(100)]
        page2_files = [{"id": f"g{i}", "name": f"file2_{i}.txt"} for i in range(50)]

        module = _make_module_with_mocked_http(
            get_responses=[
                _make_httpx_response(200, {"files": page1_files, "nextPageToken": "tok2"}),
                _make_httpx_response(200, {"files": page2_files}),
            ]
        )

        result = await module._drive_list_files(folder_id=None, query=None)

        assert result["total"] == 150
        assert result["truncated"] is False
        assert module._get.call_count == 2

    async def test_list_files_truncated_at_1000(self):
        """Results are capped at 1000 and truncated flag set when more exist."""
        pages = []
        for i in range(10):
            files = [{"id": f"p{i}f{j}", "name": "file.txt"} for j in range(100)]
            resp_data: dict[str, Any] = {"files": files, "nextPageToken": f"tok{i + 1}"}
            pages.append(_make_httpx_response(200, resp_data))

        module = _make_module_with_mocked_http(get_responses=pages)

        result = await module._drive_list_files(folder_id=None, query=None)

        assert result["total"] == 1000
        assert result["truncated"] is True

    async def test_list_files_returns_correct_structure(self):
        """Return structure has files, total, truncated keys."""
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": []})]
        )

        result = await module._drive_list_files(folder_id=None, query=None)

        assert "files" in result
        assert "total" in result
        assert "truncated" in result

    async def test_list_files_no_credentials_returns_error(self):
        """Without credentials, returns error dict."""
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_list_files(folder_id=None, query=None)

        assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# 7.5  drive_get_file_metadata
# ---------------------------------------------------------------------------


class TestDriveGetFileMetadata:
    """Task 7.5 — Found and not-found cases."""

    async def test_returns_metadata_for_existing_file(self):
        meta = {
            "id": "abc123",
            "name": "document.txt",
            "mimeType": "text/plain",
            "modifiedTime": "2025-01-01T00:00:00Z",
            "size": "1024",
            "webViewLink": "https://drive.google.com/file/abc123",
        }
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)]
        )

        result = await module._drive_get_file_metadata(file_id="abc123")

        assert result["id"] == "abc123"
        assert result["name"] == "document.txt"

    async def test_returns_not_found_for_missing_file(self):
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(404, {"error": {"code": 404}})]
        )

        result = await module._drive_get_file_metadata(file_id="nonexistent")

        assert result == {"status": "not_found", "file": None}

    async def test_requests_correct_fields(self):
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"id": "abc"})]
        )

        await module._drive_get_file_metadata(file_id="abc")

        call_params = module._get.call_args[1]["params"]
        fields = call_params.get("fields", "")
        # Verify all required metadata fields are requested
        for field in ["id", "name", "mimeType", "modifiedTime", "webViewLink"]:
            assert field in fields, f"Expected field '{field}' in request"

    async def test_no_credentials_returns_error(self):
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_get_file_metadata(file_id="x")

        assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# 7.6  drive_read_file
# ---------------------------------------------------------------------------


class TestDriveReadFile:
    """Task 7.6 — Text file, Google Doc export, Sheet CSV, size limit, binary."""

    async def test_read_plain_text_file(self):
        """Text files are downloaded via alt=media."""
        meta = {
            "id": "abc",
            "name": "notes.txt",
            "mimeType": "text/plain",
            "size": "100",
        }
        content_text = "Hello, world!"
        module = _make_module_with_mocked_http(
            get_responses=[
                _make_httpx_response(200, meta),
                _make_httpx_response(200, text=content_text),
            ]
        )

        result = await module._drive_read_file(file_id="abc")

        assert result["content"] == content_text
        assert result["mime_type"] == "text/plain"
        assert result["name"] == "notes.txt"
        # Verify alt=media was used for the download call
        download_call_params = module._get.call_args_list[1][1]["params"]
        assert download_call_params.get("alt") == "media"

    async def test_read_google_doc_exports_as_text(self):
        """Google Docs are exported via files.export with mimeType=text/plain."""
        meta = {
            "id": "doc1",
            "name": "My Document",
            "mimeType": "application/vnd.google-apps.document",
            "size": "0",
        }
        content_text = "Document content here"
        module = _make_module_with_mocked_http(
            get_responses=[
                _make_httpx_response(200, meta),
                _make_httpx_response(200, text=content_text),
            ]
        )

        result = await module._drive_read_file(file_id="doc1")

        assert result["content"] == content_text
        assert result["mime_type"] == "text/plain"
        # Verify export endpoint was called
        export_call_args = module._get.call_args_list[1]
        assert "export" in export_call_args[0][0]
        export_params = export_call_args[1]["params"]
        assert export_params["mimeType"] == "text/plain"

    async def test_read_google_sheet_exports_as_csv(self):
        """Google Sheets are exported via files.export with mimeType=text/csv."""
        meta = {
            "id": "sheet1",
            "name": "My Sheet",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "size": "0",
        }
        csv_content = "a,b,c\n1,2,3"
        module = _make_module_with_mocked_http(
            get_responses=[
                _make_httpx_response(200, meta),
                _make_httpx_response(200, text=csv_content),
            ]
        )

        result = await module._drive_read_file(file_id="sheet1")

        assert result["content"] == csv_content
        assert result["mime_type"] == "text/csv"
        export_params = module._get.call_args_list[1][1]["params"]
        assert export_params["mimeType"] == "text/csv"

    async def test_read_google_slides_exports_as_text(self):
        """Google Slides are exported as text/plain."""
        meta = {
            "id": "pres1",
            "name": "My Presentation",
            "mimeType": "application/vnd.google-apps.presentation",
            "size": "0",
        }
        slide_text = "Slide 1 content"
        module = _make_module_with_mocked_http(
            get_responses=[
                _make_httpx_response(200, meta),
                _make_httpx_response(200, text=slide_text),
            ]
        )

        result = await module._drive_read_file(file_id="pres1")

        assert result["content"] == slide_text
        assert result["mime_type"] == "text/plain"

    async def test_size_limit_enforcement(self):
        """Files exceeding max_read_size_bytes return an error/too_large status."""
        meta = {
            "id": "big",
            "name": "bigfile.txt",
            "mimeType": "text/plain",
            "size": "5000",  # 5KB > 1KB limit
        }
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)]
        )
        module._config = GoogleDriveConfig(max_read_size_bytes=1000)

        result = await module._drive_read_file(file_id="big")

        assert result.get("status") in ("too_large", "error")
        # Should not attempt download (only 1 get call for metadata)
        assert module._get.call_count == 1

    async def test_binary_file_rejection(self):
        """Binary files (images, etc.) return binary_file or error status."""
        meta = {
            "id": "img1",
            "name": "photo.jpg",
            "mimeType": "image/jpeg",
            "size": "500000",
            "webViewLink": "https://drive.google.com/img1",
        }
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)]
        )

        result = await module._drive_read_file(file_id="img1")

        assert result.get("status") in ("binary_file", "error")
        # Should not attempt download
        assert module._get.call_count == 1

    async def test_read_not_found_returns_not_found(self):
        """File not found returns not_found status dict."""
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(404, {"error": {"code": 404}})]
        )

        result = await module._drive_read_file(file_id="gone")

        assert result["status"] == "not_found"

    async def test_pdf_binary_rejection(self):
        """PDF files are treated as binary."""
        meta = {
            "id": "pdf1",
            "name": "report.pdf",
            "mimeType": "application/pdf",
            "size": "200000",
            "webViewLink": "https://drive.google.com/pdf1",
        }
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)]
        )

        result = await module._drive_read_file(file_id="pdf1")

        assert result.get("status") in ("binary_file", "error")

    async def test_no_credentials_returns_error(self):
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_read_file(file_id="x")

        assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# 7.7  drive_write_file
# ---------------------------------------------------------------------------


class TestDriveWriteFile:
    """Task 7.7 — Default butler folder, explicit folder, MIME type inference."""

    async def test_write_to_default_butler_folder(self):
        """When folder_id is None, auto-ensures butler folder and writes there."""
        write_response = _make_httpx_response(
            200,
            {
                "id": "new-file-id",
                "webViewLink": "https://drive.google.com/new",
            },
        )
        module = _make_module_with_mocked_http(post_responses=[write_response])

        # Mock _ensure_butler_folder to return a known ID
        module._ensure_butler_folder = AsyncMock(return_value="butler-folder-id")

        result = await module._drive_write_file(
            name="report.txt",
            content="Report content",
            folder_id=None,
            mime_type="text/plain",
        )

        assert result["file_id"] == "new-file-id"
        assert "web_view_link" in result
        module._ensure_butler_folder.assert_awaited_once()

    async def test_write_to_explicit_folder(self):
        """When folder_id is provided, skips butler folder auto-ensure."""
        write_response = _make_httpx_response(
            200, {"id": "new-file-id", "webViewLink": "https://drive.google.com/f"}
        )
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock()

        result = await module._drive_write_file(
            name="data.csv",
            content="a,b,c",
            folder_id="specific-folder-xyz",
            mime_type="text/csv",
        )

        assert result["file_id"] == "new-file-id"
        # Butler folder should NOT have been called
        module._ensure_butler_folder.assert_not_awaited()

    async def test_mime_type_inference_txt(self):
        """MIME type inferred from .txt extension."""
        write_response = _make_httpx_response(
            200, {"id": "fid", "webViewLink": ""}
        )
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock(return_value="folder-id")

        await module._drive_write_file(
            name="file.txt",
            content="hello",
            folder_id=None,
            mime_type=None,  # should infer
        )

        # Inspect the content sent — should contain text/plain
        call_content = module._post.call_args[1].get("content", b"")
        assert b"text/plain" in call_content

    async def test_mime_type_inference_csv(self):
        write_response = _make_httpx_response(200, {"id": "fid", "webViewLink": ""})
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock(return_value="folder-id")

        await module._drive_write_file(
            name="data.csv", content="a,b", folder_id=None, mime_type=None
        )

        call_content = module._post.call_args[1].get("content", b"")
        assert b"text/csv" in call_content

    async def test_mime_type_inference_json(self):
        write_response = _make_httpx_response(200, {"id": "fid", "webViewLink": ""})
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock(return_value="folder-id")

        await module._drive_write_file(
            name="config.json", content="{}", folder_id=None, mime_type=None
        )

        call_content = module._post.call_args[1].get("content", b"")
        assert b"application/json" in call_content

    async def test_mime_type_unknown_extension_defaults_to_octet_stream(self):
        write_response = _make_httpx_response(200, {"id": "fid", "webViewLink": ""})
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock(return_value="folder-id")

        await module._drive_write_file(
            name="file.xyzunknown999", content="data", folder_id=None, mime_type=None
        )

        call_content = module._post.call_args[1].get("content", b"")
        assert b"application/octet-stream" in call_content

    async def test_explicit_mime_type_overrides_inference(self):
        write_response = _make_httpx_response(200, {"id": "fid", "webViewLink": ""})
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock(return_value="folder-id")

        await module._drive_write_file(
            name="file.txt",
            content="data",
            folder_id=None,
            mime_type="text/markdown",
        )

        call_content = module._post.call_args[1].get("content", b"")
        assert b"text/markdown" in call_content

    async def test_result_contains_required_keys(self):
        write_response = _make_httpx_response(
            200,
            {
                "id": "file-123",
                "webViewLink": "https://drive.google.com/file-123",
            },
        )
        module = _make_module_with_mocked_http(post_responses=[write_response])
        module._ensure_butler_folder = AsyncMock(return_value="folder-id")

        result = await module._drive_write_file(
            name="output.txt",
            content="hello",
            folder_id=None,
            mime_type="text/plain",
        )

        assert "file_id" in result
        assert "name" in result
        assert "web_view_link" in result

    async def test_no_credentials_returns_error(self):
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_write_file(
            name="f.txt", content="x", folder_id=None, mime_type=None
        )

        assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# 7.8  drive_create_folder, drive_move_file, drive_search_files
# ---------------------------------------------------------------------------


class TestDriveCreateFolder:
    """Task 7.8 — drive_create_folder."""

    async def test_create_folder_in_butler_hierarchy(self):
        """Without parent_id, creates folder inside butler subfolder."""
        module = _make_module_with_mocked_http(
            post_responses=[_make_httpx_response(200, {"id": "new-folder", "name": "reports"})]
        )
        module._ensure_butler_folder = AsyncMock(return_value="butler-sub-id")

        result = await module._drive_create_folder(name="reports", parent_id=None)

        assert result["folder_id"] == "new-folder"
        assert result["name"] == "reports"
        module._ensure_butler_folder.assert_awaited_once()

    async def test_create_folder_in_specific_parent(self):
        """With parent_id, creates folder directly inside that parent."""
        module = _make_module_with_mocked_http(
            post_responses=[_make_httpx_response(200, {"id": "new-folder", "name": "archive"})]
        )
        module._ensure_butler_folder = AsyncMock()

        result = await module._drive_create_folder(name="archive", parent_id="xyz789")

        assert result["folder_id"] == "new-folder"
        module._ensure_butler_folder.assert_not_awaited()

    async def test_create_folder_passes_folder_mime_type(self):
        """Folder creation uses the Google Drive folder MIME type."""
        module = _make_module_with_mocked_http(
            post_responses=[_make_httpx_response(200, {"id": "f", "name": "test"})]
        )
        module._ensure_butler_folder = AsyncMock(return_value="parent-id")

        await module._drive_create_folder(name="test", parent_id=None)

        call_json = module._post.call_args[1].get("json", {})
        assert call_json.get("mimeType") == "application/vnd.google-apps.folder"

    async def test_create_folder_result_structure(self):
        module = _make_module_with_mocked_http(
            post_responses=[_make_httpx_response(200, {"id": "new-id", "name": "myfolder"})]
        )
        module._ensure_butler_folder = AsyncMock(return_value="parent")

        result = await module._drive_create_folder(name="myfolder", parent_id=None)

        assert "folder_id" in result
        assert "name" in result

    async def test_no_credentials_returns_error(self):
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_create_folder(name="test", parent_id=None)

        assert result.get("status") == "error"


class TestDriveMoveFile:
    """Task 7.8 — drive_move_file."""

    async def test_move_file_to_new_parent(self):
        """Successful move returns file info with new_parent_id."""
        meta = {"id": "file1", "name": "doc.txt", "parents": ["old-parent"]}
        move_response = {"id": "file1", "name": "doc.txt", "parents": ["new-parent"]}

        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)],
            patch_responses=[_make_httpx_response(200, move_response)],
        )

        result = await module._drive_move_file(file_id="file1", new_parent_id="new-parent")

        assert result.get("file_id") == "file1"
        assert result.get("new_parent_id") == "new-parent"

    async def test_move_file_uses_add_remove_parents(self):
        """files.update is called with addParents and removeParents params."""
        meta = {"id": "file1", "name": "doc.txt", "parents": ["old-parent-id"]}
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)],
            patch_responses=[
                _make_httpx_response(200, {"id": "file1", "name": "doc.txt"})
            ],
        )

        await module._drive_move_file(file_id="file1", new_parent_id="new-parent-id")

        patch_params = module._patch.call_args[1].get("params", {})
        assert patch_params["addParents"] == "new-parent-id"
        assert "old-parent-id" in patch_params["removeParents"]

    async def test_move_file_not_found(self):
        """Non-existent file returns not_found status."""
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(404, {"error": {"code": 404}})]
        )

        result = await module._drive_move_file(file_id="missing", new_parent_id="parent")

        assert result.get("status") == "not_found"

    async def test_move_file_patch_not_found(self):
        """If patch returns 404, returns not_found dict."""
        meta = {"id": "file1", "name": "doc.txt", "parents": ["old"]}
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, meta)],
            patch_responses=[_make_httpx_response(404, {"error": {"code": 404}})],
        )

        result = await module._drive_move_file(file_id="file1", new_parent_id="new")

        assert result["status"] == "not_found"

    async def test_no_credentials_returns_error(self):
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_move_file(file_id="f", new_parent_id="p")

        assert result.get("status") == "error"


class TestDriveSearchFiles:
    """Task 7.8 — drive_search_files."""

    async def test_search_with_query(self):
        """Search builds fullText query and returns results."""
        files = [
            {"id": "r1", "name": "tax 2025.pdf"},
            {"id": "r2", "name": "tax notes.txt"},
        ]
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": files})]
        )

        result = await module._drive_search_files(query="tax return 2025", limit=None)

        assert result["total"] == 2
        assert len(result["files"]) == 2
        call_params = module._get.call_args[1]["params"]
        assert "fullText contains" in call_params["q"]
        assert "tax return 2025" in call_params["q"]
        assert "trashed=false" in call_params["q"]

    async def test_search_with_limit(self):
        """Limit is applied to results."""
        files = [{"id": f"r{i}", "name": f"file{i}.txt"} for i in range(20)]
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": files})]
        )

        result = await module._drive_search_files(query="report", limit=10)

        assert result["total"] == 10
        assert len(result["files"]) == 10

    async def test_search_empty_results(self):
        """Empty results return files=[] and total=0."""
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": []})]
        )

        result = await module._drive_search_files(query="nonexistent stuff xyz", limit=None)

        assert result == {"files": [], "total": 0}

    async def test_search_returns_correct_structure(self):
        module = _make_module_with_mocked_http(
            get_responses=[_make_httpx_response(200, {"files": []})]
        )

        result = await module._drive_search_files(query="test", limit=None)

        assert "files" in result
        assert "total" in result

    async def test_no_credentials_returns_error(self):
        module = GoogleDriveModule()
        module._credentials_ok = False
        module._http_client = None

        result = await module._drive_search_files(query="x", limit=None)

        assert result.get("status") == "error"


# ---------------------------------------------------------------------------
# MIME type inference unit tests
# ---------------------------------------------------------------------------


class TestMimeTypeInference:
    """Verify _infer_mime_type helper."""

    def test_txt(self):
        assert _infer_mime_type("file.txt") == "text/plain"

    def test_csv(self):
        assert _infer_mime_type("data.csv") == "text/csv"

    def test_json(self):
        assert _infer_mime_type("config.json") == "application/json"

    def test_md(self):
        assert _infer_mime_type("readme.md") == "text/markdown"

    def test_html(self):
        assert _infer_mime_type("index.html") == "text/html"

    def test_htm(self):
        assert _infer_mime_type("page.htm") == "text/html"

    def test_unknown_extension(self):
        assert _infer_mime_type("file.xyzunknown999") == "application/octet-stream"

    def test_no_extension(self):
        result = _infer_mime_type("Makefile")
        # Should be octet-stream or something reasonable — not crash
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 7.9  Rate-limit retry: 403 discrimination
# ---------------------------------------------------------------------------


class TestRateLimitRetry403Discrimination:
    """Verify that 403 rate-limit errors are retried but 403 permission denials are not."""

    def test_is_retryable_403_rate_limit_exceeded(self):
        """rateLimitExceeded reason should be retried."""
        body = {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
        resp = _make_httpx_response(403, json_body=body)
        assert _is_retryable_403(resp) is True

    def test_is_retryable_403_user_rate_limit_exceeded(self):
        """userRateLimitExceeded reason should be retried."""
        body = {"error": {"errors": [{"reason": "userRateLimitExceeded"}]}}
        resp = _make_httpx_response(403, json_body=body)
        assert _is_retryable_403(resp) is True

    def test_is_retryable_403_forbidden_not_retried(self):
        """forbidden reason (permission denial) must NOT be retried."""
        body = {"error": {"errors": [{"reason": "forbidden"}]}}
        resp = _make_httpx_response(403, json_body=body)
        assert _is_retryable_403(resp) is False

    def test_is_retryable_403_domain_policy_not_retried(self):
        """domainPolicy (org restriction) must NOT be retried."""
        body = {"error": {"errors": [{"reason": "domainPolicy"}]}}
        resp = _make_httpx_response(403, json_body=body)
        assert _is_retryable_403(resp) is False

    def test_is_retryable_403_no_body_not_retried(self):
        """If body can't be parsed, treat as non-retryable (conservative)."""
        resp = httpx.Response(403, content=b"forbidden", headers={"content-type": "text/plain"})
        assert _is_retryable_403(resp) is False

    def test_is_retryable_403_empty_errors_not_retried(self):
        """Empty errors list — conservative: do not retry."""
        body = {"error": {"errors": []}}
        resp = _make_httpx_response(403, json_body=body)
        assert _is_retryable_403(resp) is False

    def test_is_retryable_403_missing_reason_not_retried(self):
        """Error entry with no reason field — conservative: do not retry."""
        body = {"error": {"errors": [{"domain": "usageLimits"}]}}
        resp = _make_httpx_response(403, json_body=body)
        assert _is_retryable_403(resp) is False
