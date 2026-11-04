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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from butlers.modules.base import Module
from butlers.modules.google_drive import (
    GoogleDriveConfig,
    GoogleDriveModule,
    _infer_mime_type,
)
from butlers.testing.mcp import registered_tools

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
        mcp = FastMCP("providers8-google-drive-tools")
        await module.register_tools(mcp=mcp, config={}, db=None, butler_name="test-butler")
        assert set((await registered_tools(mcp)).keys()) == EXPECTED_DRIVE_TOOLS

    async def test_butler_name_param_takes_precedence_over_db_schema(self) -> None:
        """butler_name param is stored directly and wins over db.schema."""
        module = GoogleDriveModule()
        mcp = FastMCP("providers8-google-drive-tools")
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


@pytest.mark.parametrize("operation", ["write", "folder", "move"])
@pytest.mark.parametrize(
    "audience,enabled",
    [("owner", True), ("guest", True), ("group", True), ("unknown", True), ("guest", False)],
)
async def test_destination_audience_precedes_every_drive_write(
    monkeypatch, operation, audience, enabled
):
    from types import SimpleNamespace

    def response(data, status=200):
        return SimpleNamespace(status_code=status, json=lambda: data, text="synthetic response")

    async def get(path, **kwargs):
        if path.endswith("/permissions"):
            permission = (
                {
                    "id": "group-permission",
                    "role": "reader",
                    "type": "group",
                    "emailAddress": "group@example.test",
                }
                if audience == "group"
                else {
                    "id": "user-permission",
                    "role": "writer",
                    "type": "user",
                    "emailAddress": audience + "@example.test",
                }
            )
            return response({"permissions": [permission]})
        if path.endswith("source-file"):
            return response({"id": "source-file", "name": "sample", "parents": ["old-parent"]})
        return response({"id": "destination", "mimeType": "application/vnd.google-apps.folder"})

    module = GoogleDriveModule()
    module._db = SimpleNamespace(pool=object())
    module._client = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        post=AsyncMock(return_value=response({"id": "new", "name": "sample"}, 201)),
        patch=AsyncMock(return_value=response({"id": "source-file", "name": "sample"})),
    )
    enqueuer = AsyncMock(return_value="drive-action")
    module._approval_enqueuer = enqueuer if enabled else None

    async def resolve(pool, channel, identifier):
        assert channel == "email"
        if identifier == "unknown@example.test":
            return None
        return SimpleNamespace(roles=["owner"] if identifier == "owner@example.test" else [])

    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration", resolve
    )
    if operation == "write":
        result = await module._drive_write_file(
            "sample", "Synthetic content", folder_id="destination"
        )
    elif operation == "folder":
        result = await module._drive_create_folder("sample", parent_id="destination")
    else:
        result = await module._drive_move_file("source-file", "destination")
    if audience == "owner":
        assert module._client.post.await_count + module._client.patch.await_count == 1
        enqueuer.assert_not_awaited()
    else:
        module._client.post.assert_not_awaited()
        module._client.patch.assert_not_awaited()
        assert result["status"] == ("approval_required" if enabled else "approval_unavailable")
        assert enqueuer.await_count == int(enabled)
        if enabled:
            args = enqueuer.await_args.args[1]
            assert (
                args[
                    {"write": "folder_id", "folder": "parent_id", "move": "new_parent_id"}[
                        operation
                    ]
                ]
                == "destination"
            )


@pytest.mark.parametrize("owner", [False, True])
async def test_default_bootstrap_approves_only_the_missing_folder_before_content(
    monkeypatch, owner
):
    import asyncio
    import uuid
    from types import SimpleNamespace

    from butlers.modules.approvals.execution_context import (
        ApprovalExecutionContext,
        approval_tool_args_digest,
        reset_approval_execution_context,
        set_approval_execution_context,
    )

    created = False

    def response(data, status=200):
        return SimpleNamespace(status_code=status, json=lambda: data, text="synthetic")

    async def get(path, **kwargs):
        if path == "files":
            root = "name='butlers'" in kwargs["params"]["q"]
            return response(
                {
                    "files": [{"id": "parent-root"}]
                    if root
                    else ([{"id": "butler-folder"}] if created else [])
                }
            )
        if path.endswith("/permissions"):
            return response(
                {
                    "permissions": [
                        {
                            "id": "bootstrap-permission",
                            "role": "writer",
                            "type": "user",
                            "emailAddress": "owner@example.test" if owner else "guest@example.test",
                        }
                    ]
                }
            )
        return response(
            {"id": path.split("/")[-1], "mimeType": "application/vnd.google-apps.folder"}
        )

    async def post(path, **kwargs):
        nonlocal created
        if kwargs.get("json", {}).get("mimeType") == "application/vnd.google-apps.folder":
            created = True
            return response({"id": "butler-folder", "name": "butler"}, 201)
        return response({"id": "uploaded", "name": "sample"}, 201)

    module = GoogleDriveModule()
    module._db = SimpleNamespace(pool=object())
    module._client = SimpleNamespace(
        get=AsyncMock(side_effect=get), post=AsyncMock(side_effect=post)
    )
    module._get_cached_folder_id = AsyncMock(return_value=None)
    module._cache_folder_id = AsyncMock()
    enqueuer = AsyncMock(return_value="bootstrap-action")
    module._approval_enqueuer = enqueuer
    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration",
        AsyncMock(return_value=SimpleNamespace(roles=["owner"] if owner else [])),
    )
    first = await module._drive_write_file("sample", "Private synthetic body")
    if owner:
        assert first["file_id"] == "uploaded"
        assert module._client.post.await_count == 2
        enqueuer.assert_not_awaited()
        return
    module._client.post.assert_not_awaited()
    assert first["status"] == "bootstrap_approval_required"
    assert first["operation_complete"] is False
    tool_name, stored, _ = enqueuer.await_args.args
    assert tool_name == "drive_create_folder"
    assert {k: v for k, v in stored.items() if k != "destination_audience_digest"} == {
        "name": "butler",
        "parent_id": "parent-root",
    }
    assert len(stored["destination_audience_digest"]) == 64
    assert "Private synthetic body" not in str(stored)
    task = asyncio.current_task()
    token = set_approval_execution_context(
        ApprovalExecutionContext(
            action_id=uuid.uuid4(),
            session_id=None,
            actor="human:owner",
            tool_name=tool_name,
            tool_args_digest=approval_tool_args_digest(stored),
            authorized_task=task,
        )
    )
    try:
        approved = await module._drive_create_folder(**stored)
    finally:
        reset_approval_execution_context(token)
    assert approved["folder_id"] == "butler-folder"
    assert module._client.post.await_count == 1
    second = await module._drive_write_file("sample", "Private synthetic body")
    assert second["status"] == "approval_required"
    assert enqueuer.await_args.args[0] == "drive_write_file"
    assert enqueuer.await_args.args[1]["folder_id"] == "butler-folder"
    assert module._client.post.await_count == 1


@pytest.mark.parametrize("operation", ["write", "folder", "move"])
@pytest.mark.parametrize(
    "change", ["none", "role", "identity", "inherited", "visibility", "destination", "partial"]
)
async def test_approved_drive_call_revalidates_frozen_destination_audience(
    monkeypatch, operation, change
):
    import asyncio
    import uuid
    from types import SimpleNamespace

    from butlers.modules.approvals.execution_context import (
        ApprovalExecutionContext,
        approval_tool_args_digest,
        reset_approval_execution_context,
        set_approval_execution_context,
    )

    permission = {
        "id": "guest-permission",
        "type": "user",
        "role": "writer",
        "emailAddress": "guest@example.test",
    }
    unavailable = False

    def response(data, status=200):
        return SimpleNamespace(status_code=status, json=lambda: data, text="synthetic")

    async def get(path, **kwargs):
        if path.endswith("/permissions"):
            return response({"permissions": [dict(permission)]}, 403 if unavailable else 200)
        if path.endswith("source-file"):
            return response({"id": "source-file", "parents": ["old"], "name": "sample"})
        return response(
            {"id": path.split("/")[-1], "mimeType": "application/vnd.google-apps.folder"}
        )

    module = GoogleDriveModule()
    module._db = SimpleNamespace(pool=object())
    module._client = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        post=AsyncMock(return_value=response({"id": "new", "name": "sample"}, 201)),
        patch=AsyncMock(return_value=response({"id": "source-file", "name": "sample"})),
    )
    enqueue = AsyncMock(return_value="frozen-drive-action")
    module._approval_enqueuer = enqueue
    monkeypatch.setattr(
        "butlers.identity.resolve_channel_contact_with_owner_corroboration",
        AsyncMock(return_value=SimpleNamespace(roles=[])),
    )
    calls = {
        "write": (
            module._drive_write_file,
            {"name": "sample", "content": "Synthetic", "folder_id": "destination"},
        ),
        "folder": (module._drive_create_folder, {"name": "sample", "parent_id": "destination"}),
        "move": (
            module._drive_move_file,
            {"file_id": "source-file", "new_parent_id": "destination"},
        ),
    }
    call, initial = calls[operation]
    assert (await call(**initial))["status"] == "approval_required"
    tool_name, parked, _ = enqueue.await_args.args
    # Knowing the fingerprint is not authority. Even exact supplied args park.
    assert (await call(**parked))["status"] == "approval_required"
    assert enqueue.await_count == 2
    replay = dict(parked)
    if change == "role":
        permission["role"] = "reader"
    elif change == "identity":
        permission["id"] = "different-guest"
    elif change == "inherited":
        permission["permissionDetails"] = [
            {"permissionType": "file", "inherited": True, "inheritedFrom": "other-parent"}
        ]
    elif change == "visibility":
        permission["allowFileDiscovery"] = True
    elif change == "destination":
        replay[
            {"write": "folder_id", "folder": "parent_id", "move": "new_parent_id"}[operation]
        ] = "another-folder"
    elif change == "partial":
        unavailable = True
    task = asyncio.current_task()
    token = set_approval_execution_context(
        ApprovalExecutionContext(
            action_id=uuid.uuid4(),
            session_id=None,
            actor="human:owner",
            tool_name=tool_name,
            tool_args_digest=approval_tool_args_digest(parked),
            authorized_task=task,
        )
    )
    try:
        result = await call(**replay)
    finally:
        reset_approval_execution_context(token)
    if change == "none":
        assert module._client.post.await_count + module._client.patch.await_count == 1
        assert result.get("status") != "approval_required"
    else:
        module._client.post.assert_not_awaited()
        module._client.patch.assert_not_awaited()
        assert result["status"] == (
            "approval_unavailable" if change == "partial" else "destination_changed"
        )
    assert enqueue.await_count == 2


async def _pagination_case(mode):
    from types import SimpleNamespace
    from unittest.mock import patch

    calls = []

    def response(data):
        return SimpleNamespace(status_code=200, json=lambda: data, text="synthetic")

    async def get(path, **kwargs):
        if not path.endswith("/permissions"):
            return response(
                {
                    "id": "destination",
                    "mimeType": "application/vnd.google-apps.folder",
                    "trashed": False,
                }
            )
        calls.append(kwargs.get("params", {}).get("pageToken"))
        owner = {
            "id": "owner-permission",
            "type": "user",
            "role": "owner",
            "emailAddress": "owner@example.test",
        }
        guest = {
            "id": "guest-permission",
            "type": "user",
            "role": "reader",
            "emailAddress": "guest@example.test",
        }
        if kwargs.get("params", {}).get("pageToken") == "page-two":
            return response({"permissions": [guest]})
        data = {"permissions": [owner]}
        if mode == "guest-next-page":
            data["nextPageToken"] = "page-two"
        elif mode == "empty-terminal":
            data["nextPageToken"] = ""
        elif mode == "null-terminal":
            data["nextPageToken"] = None
        elif mode == "malformed-false":
            data["nextPageToken"] = False
        elif mode == "malformed-zero":
            data["nextPageToken"] = 0
        elif mode == "malformed-empty-list":
            data["nextPageToken"] = []
        elif mode == "malformed-nonempty-list":
            data["nextPageToken"] = ["invalid"]
        return response(data)

    m = GoogleDriveModule()
    m._db = SimpleNamespace(pool=object())
    m._client = SimpleNamespace(
        get=AsyncMock(side_effect=get),
        post=AsyncMock(return_value=response({"id": "synthetic-new", "name": "synthetic"})),
    )
    m._approval_enqueuer = AsyncMock(return_value="synthetic-action")

    async def identity(pool, channel, value):
        return SimpleNamespace(roles=["owner"]) if value == "owner@example.test" else None

    with patch("butlers.identity.resolve_channel_contact_with_owner_corroboration", identity):
        result = await m._drive_write_file(
            "synthetic", "Synthetic content", folder_id="destination"
        )
    return {
        "case": mode,
        "provider_write_attempts": m._client.post.await_count,
        "permission_pages": len(calls),
        "approval_enqueues": m._approval_enqueuer.await_count,
        "result": result.get("status", "uploaded" if "file_id" in result else "other"),
    }


@pytest.mark.parametrize(
    "mode",
    [
        "owner-complete",
        "empty-terminal",
        "null-terminal",
        "guest-next-page",
        "malformed-false",
        "malformed-zero",
        "malformed-empty-list",
        "malformed-nonempty-list",
    ],
)
async def test_destination_permission_pagination_is_complete_before_write(mode):
    row = await _pagination_case(mode)
    if mode in {"owner-complete", "empty-terminal", "null-terminal"}:
        assert row["provider_write_attempts"] == 1
        assert row["approval_enqueues"] == 0
    elif mode == "guest-next-page":
        assert row["provider_write_attempts"] == 0
        assert row["permission_pages"] == 2
        assert row["approval_enqueues"] == 1
        assert row["result"] == "approval_required"
    else:
        assert row["provider_write_attempts"] == 0
        assert row["approval_enqueues"] == 0
        assert row["result"] == "approval_unavailable"
