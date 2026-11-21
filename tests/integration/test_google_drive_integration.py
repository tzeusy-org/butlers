"""Integration tests for Google Drive module and connector.

Covers:
- 15.3: OAuth account connection with Drive scope, connector discovery, module
  credential resolution.
- 15.4: Module butler folder hierarchy creation, file write, and read-back.

All external Google Drive API calls are mocked — no network access required.
The google_drive module and connector are imported lazily so these tests can
co-exist with the rest of the suite while the implementation is being merged
from parallel feature branches.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Lazy imports — skip entire module if google_drive implementation is absent.
# ---------------------------------------------------------------------------

google_drive_module = pytest.importorskip(
    "butlers.modules.google_drive",
    reason="google_drive module not yet implemented (tasks 2-7 pending merge)",
)
google_drive_connector = pytest.importorskip(
    "butlers.connectors.google_drive",
    reason="google_drive connector not yet implemented (tasks 8-14 pending merge)",
)

from butlers.connectors.google_drive import DriveAccountSource  # noqa: E402
from butlers.core.goal_lineage import lineage_scope  # noqa: E402
from butlers.modules.google_drive import GoogleDriveConfig, GoogleDriveModule  # noqa: E402

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_DRIVE_FULL_SCOPE = "https://www.googleapis.com/auth/drive"

_FAKE_ACCOUNT_EMAIL = "user@example.com"
_FAKE_ACCOUNT_ID = uuid.uuid4()
_FAKE_ENTITY_ID = uuid.uuid4()

_FAKE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
_FAKE_CLIENT_SECRET = "test-client-secret"
_FAKE_REFRESH_TOKEN = "1//fake-refresh-token"
_FAKE_ACCESS_TOKEN = "ya29.fake-access-token"

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_google_account(
    email: str = _FAKE_ACCOUNT_EMAIL,
    *,
    granted_scopes: list[str] | None = None,
    status: str = "active",
) -> MagicMock:
    """Create a mock GoogleAccount row."""
    account = MagicMock()
    account.id = _FAKE_ACCOUNT_ID
    account.entity_id = _FAKE_ENTITY_ID
    account.email = email
    account.is_primary = True
    account.status = status
    account.granted_scopes = granted_scopes or [_DRIVE_FULL_SCOPE, _DRIVE_READONLY_SCOPE]
    account.connected_at = datetime(2026, 1, 1, tzinfo=UTC)
    account.last_token_refresh_at = None
    return account


def _make_google_credentials(
    email: str = _FAKE_ACCOUNT_EMAIL,
) -> MagicMock:
    """Create mock GoogleCredentials."""
    creds = MagicMock()
    creds.client_id = _FAKE_CLIENT_ID
    creds.client_secret = _FAKE_CLIENT_SECRET
    creds.refresh_token = _FAKE_REFRESH_TOKEN
    creds.scope = f"openid email profile {_DRIVE_FULL_SCOPE} {_DRIVE_READONLY_SCOPE}"
    return creds


def _make_mock_db_pool() -> MagicMock:
    """Create a minimal mock asyncpg pool."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="UPDATE 1")

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


def _make_mock_credential_store() -> MagicMock:
    """Create a mock CredentialStore."""
    store = MagicMock()
    return store


# ---------------------------------------------------------------------------
# Test 15.3: OAuth Drive scope → connector discovery → module credential resolution
# ---------------------------------------------------------------------------


class TestDriveScopeOAuthFlow:
    """Task 15.3: Verify drive scopes flow from OAuth to connector and module."""

    def test_drive_scopes_present_in_default_oauth_scopes(self) -> None:
        """The OAuth start endpoint scope builder includes both drive scopes.

        Regression guard for task 15.1 — ensures the scopes added to
        _DEFAULT_SCOPES are discoverable at test time.
        """
        from butlers.api.routers.oauth import _DEFAULT_SCOPES  # noqa: PLC0415

        assert _DRIVE_READONLY_SCOPE in _DEFAULT_SCOPES, (
            f"drive.readonly scope missing from _DEFAULT_SCOPES: {_DEFAULT_SCOPES!r}"
        )
        assert _DRIVE_FULL_SCOPE in _DEFAULT_SCOPES, (
            f"drive (full) scope missing from _DEFAULT_SCOPES: {_DEFAULT_SCOPES!r}"
        )

    def test_drive_readonly_scope_sufficient_for_connector_discovery(self) -> None:
        """An account with only drive.readonly is discovered by the connector."""
        account = _make_google_account(granted_scopes=[_DRIVE_READONLY_SCOPE])

        # Connector discovery logic checks granted_scopes for drive.readonly OR drive.
        has_drive_scope = any(
            s in (account.granted_scopes or []) for s in (_DRIVE_READONLY_SCOPE, _DRIVE_FULL_SCOPE)
        )
        assert has_drive_scope, "Account with drive.readonly should qualify for connector discovery"

    def test_drive_full_scope_sufficient_for_connector_discovery(self) -> None:
        """An account with drive (full access) is discovered by the connector."""
        account = _make_google_account(granted_scopes=[_DRIVE_FULL_SCOPE])

        has_drive_scope = any(
            s in (account.granted_scopes or []) for s in (_DRIVE_READONLY_SCOPE, _DRIVE_FULL_SCOPE)
        )
        assert has_drive_scope

    def test_account_without_drive_scope_not_discovered(self) -> None:
        """An account missing drive scopes is NOT discovered by the connector."""
        account = _make_google_account(
            granted_scopes=["https://www.googleapis.com/auth/gmail.modify"]
        )

        has_drive_scope = any(
            s in (account.granted_scopes or []) for s in (_DRIVE_READONLY_SCOPE, _DRIVE_FULL_SCOPE)
        )
        assert not has_drive_scope, (
            "Account without drive scope should NOT qualify for connector discovery"
        )

    async def test_connector_discovers_drive_accounts_at_startup(self) -> None:
        """DriveAccountSource discovers accounts with drive scope at startup."""
        mock_pool = _make_mock_db_pool()
        mock_store = _make_mock_credential_store()

        # Simulate DB returning one active account with drive scope.
        drive_account_row = MagicMock()
        drive_account_row.__getitem__ = lambda self, k: {
            "id": _FAKE_ACCOUNT_ID,
            "entity_id": _FAKE_ENTITY_ID,
            "email": _FAKE_ACCOUNT_EMAIL,
            "granted_scopes": [_DRIVE_FULL_SCOPE, _DRIVE_READONLY_SCOPE],
            "status": "active",
            "is_primary": True,
            "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
            "last_token_refresh_at": None,
            "display_name": "Test User",
        }.get(k)

        mock_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[drive_account_row]
        )

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=_make_google_credentials(),
        ):
            manager = DriveAccountSource(
                db_pool=mock_pool,
                credential_store=mock_store,
                switchboard_mcp_url="http://localhost:41100/sse",
            )

            # Discover accounts — should return the one with drive scope.
            discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 1
        assert discovered[0]["email"] == _FAKE_ACCOUNT_EMAIL

    async def test_connector_skips_accounts_without_drive_scope(self) -> None:
        """DriveAccountSource skips accounts that lack drive scopes."""
        mock_pool = _make_mock_db_pool()
        mock_store = _make_mock_credential_store()

        no_drive_row = MagicMock()
        no_drive_row.__getitem__ = lambda self, k: {
            "id": _FAKE_ACCOUNT_ID,
            "entity_id": _FAKE_ENTITY_ID,
            "email": _FAKE_ACCOUNT_EMAIL,
            "granted_scopes": ["https://www.googleapis.com/auth/gmail.modify"],
            "status": "active",
            "is_primary": True,
            "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
            "last_token_refresh_at": None,
            "display_name": "Test User",
        }.get(k)

        mock_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[no_drive_row]
        )

        manager = DriveAccountSource(
            db_pool=mock_pool,
            credential_store=mock_store,
            switchboard_mcp_url="http://localhost:41100/sse",
        )

        discovered = await manager.discover_drive_accounts()
        assert len(discovered) == 0, "Account without drive scope should be skipped"

    async def test_module_resolves_drive_credentials_for_primary_account(self) -> None:
        """GoogleDriveModule on_startup resolves credentials for the primary account."""
        mock_store = _make_mock_credential_store()
        fake_creds = _make_google_credentials()

        config = GoogleDriveConfig()  # No account specified → uses primary.

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ) as mock_resolve:
            module = GoogleDriveModule()
            await module.on_startup(
                config=config,
                db=None,
                credential_store=mock_store,
            )

        mock_resolve.assert_awaited_once()
        call_kwargs = mock_resolve.call_args[1]
        assert call_kwargs.get("caller") == "google_drive", (
            "on_startup must identify itself as 'google_drive' for traceability"
        )
        assert call_kwargs.get("account") is None, (
            "No account in config should resolve the primary account"
        )

    async def test_module_resolves_drive_credentials_for_specific_account(self) -> None:
        """GoogleDriveModule on_startup resolves credentials for a specific account."""
        mock_store = _make_mock_credential_store()
        fake_creds = _make_google_credentials(_FAKE_ACCOUNT_EMAIL)

        config = GoogleDriveConfig(account=_FAKE_ACCOUNT_EMAIL)

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ) as mock_resolve:
            module = GoogleDriveModule()
            await module.on_startup(
                config=config,
                db=None,
                credential_store=mock_store,
            )

        mock_resolve.assert_awaited_once()
        call_kwargs = mock_resolve.call_args[1]
        assert call_kwargs.get("account") == _FAKE_ACCOUNT_EMAIL

    async def test_module_startup_degrades_missing_drive_scope(self) -> None:
        """Module on_startup warns and skips when 'drive' scope is absent."""
        mock_store = _make_mock_credential_store()

        # Credentials with only read-only scope — insufficient for the module (needs write).
        readonly_creds = _make_google_credentials()
        readonly_creds.scope = (
            "openid email profile "
            "https://www.googleapis.com/auth/gmail.modify "
            "https://www.googleapis.com/auth/drive.readonly"
        )

        config = GoogleDriveConfig()

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=readonly_creds,
        ):
            module = GoogleDriveModule()
            await module.on_startup(
                config=config,
                db=None,
                credential_store=mock_store,
            )
            # Module degrades gracefully — no credentials stored.
            assert not getattr(module, "_credentials_ok", False)


# ---------------------------------------------------------------------------
# Test 15.4: Module creates butler folder hierarchy, writes file, reads it back
# ---------------------------------------------------------------------------

_FAKE_BUTLERS_FOLDER_ID = "gdrive-folder-butlers-root"
_FAKE_BUTLER_SUBFOLDER_ID = "gdrive-folder-general-subfolder"
_FAKE_FILE_ID = "gdrive-file-test-123"
_FAKE_FILE_CONTENT = "Hello from Butler!"


class TestDriveButlerFolderHierarchy:
    """Task 15.4: Module folder hierarchy creation, file write, and read-back."""

    def _make_drive_module_with_mocked_http(
        self,
        *,
        butlers_folder_id: str = _FAKE_BUTLERS_FOLDER_ID,
        butler_subfolder_id: str = _FAKE_BUTLER_SUBFOLDER_ID,
    ) -> tuple[GoogleDriveModule, MagicMock]:
        """Create a GoogleDriveModule with a mocked HTTP client and DB pool."""
        from butlers.modules.google_drive import _DriveHTTPClient  # noqa: PLC0415

        mock_pool = _make_mock_db_pool()

        # Mock the DB calls for the butler_folders table.
        conn = mock_pool.acquire.return_value.__aenter__.return_value
        conn.fetchrow = AsyncMock(return_value=None)  # No cached folder IDs initially.
        conn.execute = AsyncMock(return_value="INSERT 1")

        # Build the module and inject mocked internals.
        module = GoogleDriveModule()
        module._butler_name = "general"
        module._config = GoogleDriveConfig()

        # Wire a mock db with pool so _get_cached_folder/_cache_folder work.
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        module._db = mock_db

        # Mock HTTP client responses.
        mock_client = MagicMock(spec=_DriveHTTPClient)

        # files.list response (folder search returns empty → triggers creation).
        empty_list_response = MagicMock()
        empty_list_response.status_code = 200
        empty_list_response.json.return_value = {"files": []}

        # files.create response for butlers/ root folder.
        root_folder_create_response = MagicMock()
        root_folder_create_response.status_code = 200
        root_folder_create_response.json.return_value = {
            "id": butlers_folder_id,
            "name": "butlers",
            "mimeType": "application/vnd.google-apps.folder",
        }

        # files.create response for general/ subfolder.
        sub_folder_create_response = MagicMock()
        sub_folder_create_response.status_code = 200
        sub_folder_create_response.json.return_value = {
            "id": butler_subfolder_id,
            "name": "general",
            "mimeType": "application/vnd.google-apps.folder",
        }

        mock_client.get = AsyncMock(return_value=empty_list_response)
        mock_client.post = AsyncMock(
            side_effect=[root_folder_create_response, sub_folder_create_response]
        )

        module._client = mock_client
        return module, mock_client

    @staticmethod
    def _private_destination_http(module, mock_client, monkeypatch):
        """Supply the metadata/ACL protocol separately from existing search/read doubles."""
        original_get = mock_client.get

        async def owner_identity(pool, channel, identifier):
            assert channel == "email" and identifier == "owner@example.test"
            return SimpleNamespace(roles=["owner"])

        monkeypatch.setattr(
            "butlers.identity.resolve_channel_contact_with_owner_corroboration", owner_identity
        )

        async def get(path, **kwargs):
            if path.endswith("/permissions"):
                payload = {
                    "permissions": [
                        {
                            "id": "synthetic-owner-permission",
                            "type": "user",
                            "role": "owner",
                            "emailAddress": "owner@example.test",
                        }
                    ]
                }
            elif kwargs.get("params", {}).get("fields") == "id,mimeType,trashed":
                folder = path.removeprefix("files/")
                payload = {
                    "id": "synthetic-private-root" if folder == "root" else folder,
                    "mimeType": "application/vnd.google-apps.folder",
                    "trashed": False,
                }
            else:
                # Preserve the cache404/search/read/media branches and sequence.
                return await original_get(path, **kwargs)
            return MagicMock(status_code=200, json=MagicMock(return_value=payload))

        mock_client.get = AsyncMock(side_effect=get)

    async def test_drive_write_creates_root_butler_folder_when_missing(self, monkeypatch) -> None:
        """_drive_write_file creates the 'butlers/' root folder if it doesn't exist."""
        module, mock_client = self._make_drive_module_with_mocked_http()

        # Simulate the file create response.
        file_create_response = MagicMock()
        file_create_response.status_code = 200
        file_create_response.json.return_value = {
            "id": _FAKE_FILE_ID,
            "name": "test.txt",
            "webViewLink": "https://drive.google.com/file/d/fake",
        }
        mock_client.post = AsyncMock(
            side_effect=[
                # Calls: root folder create, subfolder create, file create.
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_BUTLERS_FOLDER_ID,
                            "name": "butlers",
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    ),
                ),
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_BUTLER_SUBFOLDER_ID,
                            "name": "general",
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    ),
                ),
                file_create_response,
            ]
        )

        self._private_destination_http(module, mock_client, monkeypatch)

        result = await module._drive_write_file(
            name="test.txt",
            content="Hello from Butler!",
            folder_id=None,
            mime_type="text/plain",
        )

        assert result["file_id"] == _FAKE_FILE_ID
        # Root folder and subfolder should have been created.
        assert mock_client.post.call_count >= 2, (
            "Expected at least two POST calls: root folder + subfolder creation"
        )

    async def test_drive_write_reuses_cached_folder_ids(self, monkeypatch) -> None:
        """_drive_write_file uses cached folder IDs without re-querying Drive."""
        module, mock_client = self._make_drive_module_with_mocked_http()

        # Pre-populate DB-cache (simulates a prior write that stored IDs).
        conn = module._db.pool.acquire.return_value.__aenter__.return_value

        folder_cache_row = MagicMock()
        folder_cache_row.__getitem__ = lambda self, k: {
            "folder_id": _FAKE_BUTLER_SUBFOLDER_ID,
            "folder_path": "butlers/general",
        }.get(k)
        conn.fetchrow = AsyncMock(return_value=folder_cache_row)

        # Simulate files.get (folder existence check) returning a valid folder.
        folder_get_response = MagicMock()
        folder_get_response.status_code = 200
        folder_get_response.json.return_value = {
            "id": _FAKE_BUTLER_SUBFOLDER_ID,
            "name": "general",
            "trashed": False,
        }
        mock_client.get = AsyncMock(return_value=folder_get_response)

        file_create_response = MagicMock()
        file_create_response.status_code = 200
        file_create_response.json.return_value = {
            "id": _FAKE_FILE_ID,
            "name": "test.txt",
            "webViewLink": "https://drive.google.com/file/d/fake",
        }
        mock_client.post = AsyncMock(return_value=file_create_response)

        self._private_destination_http(module, mock_client, monkeypatch)

        result = await module._drive_write_file(
            name="test.txt",
            content="Hello from Butler!",
            folder_id=None,
            mime_type="text/plain",
        )

        assert result["file_id"] == _FAKE_FILE_ID
        # Folder creation should NOT be called (only one POST: the file create).
        assert mock_client.post.call_count == 1, (
            "Expected exactly one POST (file create) when folder IDs are cached"
        )

    async def test_drive_read_file_returns_text_content(self) -> None:
        """_drive_read_file returns file content for a text file."""
        module, mock_client = self._make_drive_module_with_mocked_http()

        # Simulate files.get?alt=media response for a text file.
        file_meta_response = MagicMock()
        file_meta_response.status_code = 200
        file_meta_response.json.return_value = {
            "id": _FAKE_FILE_ID,
            "name": "test.txt",
            "mimeType": "text/plain",
            "size": "19",
        }

        file_content_response = MagicMock()
        file_content_response.status_code = 200
        file_content_response.text = _FAKE_FILE_CONTENT

        mock_client.get = AsyncMock(side_effect=[file_meta_response, file_content_response])

        result = await module._drive_read_file(file_id=_FAKE_FILE_ID)

        assert result["content"] == _FAKE_FILE_CONTENT
        # The read result does NOT include file_id; check name and mime_type instead.
        assert result["name"] == "test.txt"
        assert result["mime_type"] == "text/plain"

    async def test_drive_write_then_read_roundtrip(self, monkeypatch) -> None:
        """Full write → read-back roundtrip via mocked Drive API."""
        module, mock_client = self._make_drive_module_with_mocked_http()
        written_content = "Butler output: monthly report summary."

        # Write responses: root folder, subfolder, file create.
        mock_client.post = AsyncMock(
            side_effect=[
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_BUTLERS_FOLDER_ID,
                            "name": "butlers",
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    ),
                ),
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_BUTLER_SUBFOLDER_ID,
                            "name": "general",
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    ),
                ),
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_FILE_ID,
                            "name": "report.txt",
                            "webViewLink": "https://drive.google.com/file/d/fake",
                        }
                    ),
                ),
            ]
        )

        # GET calls in order:
        #   1. Write: folder search for root (empty → will create)
        #   2. Write: folder search for subfolder (empty → will create)
        #   3. Read: files.get metadata
        #   4. Read: files.get?alt=media content download
        mock_client.get = AsyncMock(
            side_effect=[
                # Folder search for root 'butlers/': no existing folders.
                MagicMock(status_code=200, json=MagicMock(return_value={"files": []})),
                # Folder search for 'general/' subfolder: no existing folders.
                MagicMock(status_code=200, json=MagicMock(return_value={"files": []})),
                # files.get metadata for read_file.
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_FILE_ID,
                            "name": "report.txt",
                            "mimeType": "text/plain",
                            "size": str(len(written_content)),
                        }
                    ),
                ),
                # files.get?alt=media for content download.
                MagicMock(status_code=200, text=written_content),
            ]
        )

        self._private_destination_http(module, mock_client, monkeypatch)

        write_result = await module._drive_write_file(
            name="report.txt",
            content=written_content,
            folder_id=None,
            mime_type="text/plain",
        )
        assert write_result["file_id"] == _FAKE_FILE_ID

        read_result = await module._drive_read_file(file_id=_FAKE_FILE_ID)

        assert read_result["content"] == written_content, (
            "Content read back from Drive must match what was written"
        )

    async def test_drive_write_recreates_deleted_folder(self, monkeypatch) -> None:
        """_drive_write_file re-creates a butler folder when the cached ID is stale (deleted)."""
        module, mock_client = self._make_drive_module_with_mocked_http()

        # Cache returns a folder ID.
        conn = module._db.pool.acquire.return_value.__aenter__.return_value
        stale_row = MagicMock()
        stale_row.__getitem__ = lambda self, k: {
            "folder_id": "stale-deleted-folder-id",
            "folder_path": "butlers/general",
        }.get(k)
        conn.fetchrow = AsyncMock(return_value=stale_row)

        # files.get returns 404 → folder was deleted.
        not_found_response = MagicMock()
        not_found_response.status_code = 404
        not_found_response.json.return_value = {
            "error": {"code": 404, "message": "File not found."}
        }

        new_folder_response = MagicMock()
        new_folder_response.status_code = 200
        new_folder_response.json.return_value = {
            "id": "new-folder-id-after-recreate",
            "name": "butlers",
            "mimeType": "application/vnd.google-apps.folder",
        }

        new_sub_folder_response = MagicMock()
        new_sub_folder_response.status_code = 200
        new_sub_folder_response.json.return_value = {
            "id": "new-sub-folder-id-after-recreate",
            "name": "general",
            "mimeType": "application/vnd.google-apps.folder",
        }

        file_create_response = MagicMock()
        file_create_response.status_code = 200
        file_create_response.json.return_value = {
            "id": _FAKE_FILE_ID,
            "name": "test.txt",
            "webViewLink": "https://drive.google.com/file/d/fake",
        }

        # get: 1st call = check cached folder (404), then folder searches (empty), then...
        # the _find_or_create_folder will search then create
        search_empty = MagicMock()
        search_empty.status_code = 200
        search_empty.json.return_value = {"files": []}

        mock_client.get = AsyncMock(side_effect=[not_found_response, search_empty, search_empty])
        mock_client.post = AsyncMock(
            side_effect=[new_folder_response, new_sub_folder_response, file_create_response]
        )

        self._private_destination_http(module, mock_client, monkeypatch)

        result = await module._drive_write_file(
            name="test.txt",
            content="content after folder re-creation",
            folder_id=None,
            mime_type="text/plain",
        )

        assert result["file_id"] == _FAKE_FILE_ID, (
            "Write must succeed after re-creating the deleted folder"
        )
        # Folder creation POST must have been called (at least root + sub + file).
        assert mock_client.post.call_count >= 2


# ---------------------------------------------------------------------------
# Native Docs and Sheets (owner-private tools, RFC 0038 switch on)
# ---------------------------------------------------------------------------

_OWNER = "owner@example.test"
_FOLDER = "folder-private"
_DOC_MIME = "application/vnd.google-apps.document"
_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_OWN_GRANT = {"type": "user", "role": "owner", "emailAddress": _OWNER}


class _FakeGoogle:
    """A recording Drive + Sheets fake behind ``httpx.MockTransport``."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.files: dict[str, dict] = {
            _FOLDER: {
                "id": _FOLDER,
                "mimeType": _FOLDER_MIME,
                "ownedByMe": True,
                "permissions": [_OWN_GRANT],
            }
        }
        self.sheets: dict[str, dict[str, list]] = {}
        self.import_formats = {"text/markdown": [_DOC_MIME], "text/plain": [_DOC_MIME]}
        self.sheet_write_status = 200
        self.folders_by_name: dict[str, str] = {}  # default-folder lookups by name
        self.sheet_write_body: dict = {}
        self.export_body = b"%PDF-1.7 exported"
        self.export_status = 200
        self.export_error = ""
        self._n = 0

    def add(self, file_id: str, mime: str, **meta) -> None:
        self.files[file_id] = {
            "id": file_id,
            "mimeType": mime,
            "ownedByMe": True,
            "permissions": [_OWN_GRANT],
            "version": "7",
            **meta,
        }

    @property
    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET" and "/tokeninfo" not in r.url.path]

    @property
    def exports(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/export")]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        query = dict(request.url.params.multi_items())
        if path == "/drive/v3/about":
            if "importFormats" in query.get("fields", ""):
                return httpx.Response(200, json={"importFormats": self.import_formats})
            return httpx.Response(200, json={"user": {"emailAddress": _OWNER}})
        if path.startswith("/upload/drive/v3/files"):
            self._n += 1
            file_id = path.rsplit("/", 1)[-1] if path.count("/") > 4 else f"new-doc-{self._n}"
            return httpx.Response(
                200, json={"id": file_id, "name": "T", "webViewLink": f"https://docs/{file_id}"}
            )
        if path == "/drive/v3/files" and method == "GET":
            name = query.get("q", "").split("'")[1] if "'" in query.get("q", "") else ""
            found = self.folders_by_name.get(name)
            return httpx.Response(200, json={"files": [{"id": found}] if found else []})
        if path == "/drive/v3/files" and method == "POST":
            self._n += 1
            return httpx.Response(
                200, json={"id": f"new-sheet-{self._n}", "name": "S", "webViewLink": "https://s"}
            )
        if path.startswith("/drive/v3/files/") and path.endswith("/export"):
            if self.export_status != 200:
                return httpx.Response(
                    self.export_status,
                    json={"error": {"errors": [{"reason": self.export_error}]}},
                )
            return httpx.Response(
                200, content=self.export_body, headers={"Content-Type": query["mimeType"]}
            )
        if path.startswith("/drive/v3/files/"):
            file_id = path.rsplit("/", 1)[-1]
            if method == "PATCH":
                return httpx.Response(200, json={"id": file_id})
            meta = self.files.get(file_id)
            return httpx.Response(200, json=meta) if meta else httpx.Response(404, json={})
        if path.startswith("/v4/spreadsheets/"):
            return self._sheets(request, path, query)
        return httpx.Response(500, json={"error": f"unrouted {method} {path}"})

    def _sheets(self, request: httpx.Request, path: str, query: dict) -> httpx.Response:
        rest = path.removeprefix("/v4/spreadsheets/")
        file_id = rest.split("/", 1)[0]
        if request.method != "GET" and self.sheet_write_status != 200:
            return httpx.Response(self.sheet_write_status, json=self.sheet_write_body)
        if rest.endswith("values:batchGet"):
            tabs = self.sheets.get(file_id, {})
            return httpx.Response(
                200,
                json={"valueRanges": [{"range": f"{t}!A1", "values": v} for t, v in tabs.items()]},
            )
        if "/" not in rest:
            tabs = self.sheets.get(file_id, {"Sheet1": []})
            return httpx.Response(
                200, json={"sheets": [{"properties": {"title": t}} for t in tabs]}
            )
        return httpx.Response(
            200, json={"updates": {"updatedRange": "Sheet1!A2:B2", "updatedRows": 1}}
        )


class _FakeReceiptPool:
    def __init__(self) -> None:
        self.rows: dict = {}
        self.inserts = 0

    def acquire(self):
        pool = self

        class _Conn:
            async def fetchrow(self, sql, effect_id):
                row = pool.rows.get(effect_id)
                return {"receipt": row} if row is not None else None

            async def execute(self, sql, effect_id, tool, file_id, receipt):
                pool.inserts += 1
                pool.rows.setdefault(effect_id, receipt)

        class _CM:
            async def __aenter__(self):
                return _Conn()

            async def __aexit__(self, *exc):
                return None

        return _CM()


class _Token:
    async def get_token(self, **kwargs) -> str:
        return "tok"


def _native_module(fake: _FakeGoogle, *, pool=None, blob_store=None):
    from butlers.modules.google_drive import _DriveHTTPClient

    module = GoogleDriveModule()
    module._butler_name = "home"
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    module._http_client = http
    module._client = _DriveHTTPClient(
        http, _Token(), SimpleNamespace(client_id="c", client_secret="s", refresh_token="r")
    )
    module._db = SimpleNamespace(pool=pool) if pool is not None else None
    module._blob_store = blob_store
    return module


def _lineage(*, exposed: bool, effect: uuid.UUID | None = None):
    from butlers.core.goal_lineage import VerifiedLineage

    return VerifiedLineage(
        goal_id=uuid.uuid4(),
        invocation_id=uuid.uuid4(),
        depth=0,
        origin="owner",
        exposed=exposed,
        capability="drive_create_doc",
        args_digest="x",
        call_id=uuid.uuid4(),
        effect_id=effect,
    )


def _upload_text(request: httpx.Request) -> str:
    return request.content.decode()


def _sent_values(request: httpx.Request):
    return json.loads(request.content)["values"]


class TestNativeDocsAndSheets:
    async def test_create_doc_sends_native_mime_and_sanitised_markdown(self) -> None:
        fake = _FakeGoogle()
        module = _native_module(fake)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_create_doc(
                "Plan", "# Hi\n![x](https://evil.test/p.png)\n<b>bold</b>", folder_id=_FOLDER
            )
        assert result["status"] == "ok" and result["mime_type"] == _DOC_MIME
        (upload,) = fake.writes
        text = _upload_text(upload)
        assert '"mimeType": "application/vnd.google-apps.document"' in text
        assert '"butlers_created_by": "home"' in text
        assert "Content-Type: text/markdown" in text
        assert "![" not in text and "[x](https://evil.test/p.png)" in text
        assert "\\<b>bold\\</b>" in text

    async def test_markdown_falls_back_to_text_when_not_importable(self) -> None:
        fake = _FakeGoogle()
        fake.import_formats = {"text/plain": [_DOC_MIME]}
        module = _native_module(fake)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_create_doc("T", "body", folder_id=_FOLDER)
        assert result["format_degraded"] == "markdown_imported_as_text"
        assert "Content-Type: text/plain" in _upload_text(fake.writes[0])

    async def test_sheet_tools_send_raw_values(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        module = _native_module(fake)
        with lineage_scope(_lineage(exposed=False)):
            created = await module._drive_create_sheet(
                "S", [['=IMPORTXML("http://x")', 1]], folder_id=_FOLDER
            )
            appended = await module._drive_append_sheet_rows("sheet-1", [["a", "b"]])
            updated = await module._drive_update_sheet_range(
                "sheet-1", "Sheet1!A1:B1", [["c", "d"]]
            )
        assert created["status"] == appended["status"] == updated["status"] == "ok"
        value_writes = [r for r in fake.writes if "/values/" in r.url.path]
        assert len(value_writes) == 3
        assert all(r.url.params["valueInputOption"] == "RAW" for r in value_writes)
        assert _sent_values(value_writes[0]) == [['=IMPORTXML("http://x")', 1]]
        assert value_writes[2].method == "PUT"

    async def test_read_sheet_returns_every_tab(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        fake.sheets["sheet-1"] = {"One": [["a"]], "Two": [["b", "c"]]}
        module = _native_module(fake)
        result = await module._drive_read_sheet("sheet-1")
        assert result["status"] == "ok"
        assert [t["values"] for t in result["tabs"]] == [[["a"]], [["b", "c"]]]

    @pytest.mark.parametrize(
        "tool", ["create_doc", "create_sheet", "append", "update_range", "update_doc"]
    )
    @pytest.mark.parametrize(
        "shared",
        [
            {
                "permissions": [
                    _OWN_GRANT,
                    {"type": "user", "role": "reader", "emailAddress": "b@x"},
                ]
            },
            {
                "permissions": [
                    _OWN_GRANT,
                    {"type": "group", "role": "writer", "emailAddress": "g@x"},
                ]
            },
            {"permissions": [_OWN_GRANT, {"type": "domain", "role": "reader", "domain": "x.test"}]},
            {"permissions": [_OWN_GRANT, {"type": "anyone", "role": "reader"}]},
            {"ownedByMe": False},
            {"driveId": "drive-1"},
        ],
    )
    async def test_shared_target_is_refused_and_nothing_is_written(self, tool, shared) -> None:
        fake = _FakeGoogle()
        fake.files[_FOLDER].update(shared)
        fake.add("doc-1", _DOC_MIME, appProperties={"butlers_created_by": "home"}, **shared)
        fake.add("sheet-1", _SHEET_MIME, **shared)
        module = _native_module(fake)
        calls = {
            "create_doc": lambda: module._drive_create_doc("T", "b", folder_id=_FOLDER),
            "create_sheet": lambda: module._drive_create_sheet("T", [["a"]], folder_id=_FOLDER),
            "append": lambda: module._drive_append_sheet_rows("sheet-1", [["a"]]),
            "update_range": lambda: module._drive_update_sheet_range("sheet-1", "A1", [["a"]]),
            "update_doc": lambda: module._drive_update_doc("doc-1", "b"),
        }
        with lineage_scope(_lineage(exposed=False)):
            result = await calls[tool]()
        assert result["status"] == "target_shared"
        assert "@" not in json.dumps(result["reach"])
        assert fake.writes == []

    async def test_deleted_grants_do_not_count_as_shared(self) -> None:
        fake = _FakeGoogle()
        fake.files[_FOLDER]["permissions"].append(
            {"type": "anyone", "role": "reader", "deleted": True}
        )
        module = _native_module(fake)
        with lineage_scope(_lineage(exposed=False)):
            assert (await module._drive_create_doc("T", "b", folder_id=_FOLDER))["status"] == "ok"

    @pytest.mark.parametrize(
        ("exposed", "keeps_link"),
        [(True, False), (None, False), (False, True)],
    )
    async def test_urls_inert_under_exposed_or_missing_lineage(self, exposed, keeps_link) -> None:
        fake = _FakeGoogle()
        module = _native_module(fake)
        body = "see [site](https://example.test/a) or https://example.test/b"
        if exposed is None:
            result = await module._drive_create_doc("T", body, folder_id=_FOLDER)
        else:
            with lineage_scope(_lineage(exposed=exposed)):
                result = await module._drive_create_doc("T", body, folder_id=_FOLDER)
        assert result["status"] == "ok"
        text = _upload_text(fake.writes[0])
        assert ("https://example.test/a" in text) is keeps_link
        assert ("https://example.test/b" in text) is keeps_link
        if not keeps_link:
            assert "https[:]//example[.]test/b" in text

    async def test_sheet_cells_defang_urls_when_exposed(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        module = _native_module(fake)
        await module._drive_append_sheet_rows("sheet-1", [["https://example.test/x"]])
        (write,) = [r for r in fake.writes if "/values/" in r.url.path]
        assert _sent_values(write) == [["https[:]//example[.]test/x"]]

    async def test_update_doc_refuses_a_doc_without_the_butlers_mark(self) -> None:
        fake = _FakeGoogle()
        fake.add("doc-1", _DOC_MIME)
        fake.add("doc-2", _DOC_MIME, appProperties={"butlers_created_by": "health"})
        module = _native_module(fake)
        for file_id in ("doc-1", "doc-2"):
            result = await module._drive_update_doc(file_id, "new")
            assert result == {"status": "not_created_by_butlers"}
        assert fake.writes == []

    async def test_update_doc_keeps_the_file_in_place(self) -> None:
        fake = _FakeGoogle()
        fake.add("doc-1", _DOC_MIME, appProperties={"butlers_created_by": "home"})
        module = _native_module(fake)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_update_doc("doc-1", "# new")
        assert result["status"] == "ok" and result["file_id"] == "doc-1"
        assert result["pre_write_version"] == "7"
        (write,) = fake.writes
        assert write.method == "PATCH" and write.url.path.endswith("/files/doc-1")
        assert write.headers["content-type"] == "text/markdown"

    async def test_wrong_type_and_missing_file(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        module = _native_module(fake)
        assert (await module._drive_update_doc("sheet-1", "x"))["status"] == "wrong_type"
        assert (await module._drive_append_sheet_rows("doc-x", [["a"]]))["status"] == "not_found"
        fake.add("doc-1", _DOC_MIME)
        assert (await module._drive_append_sheet_rows("doc-1", [["a"]]))["status"] == "wrong_type"

    async def test_replay_returns_the_first_receipt_and_sends_nothing(self) -> None:
        fake = _FakeGoogle()
        pool = _FakeReceiptPool()
        module = _native_module(fake, pool=pool)
        effect = uuid.uuid4()
        with lineage_scope(_lineage(exposed=False, effect=effect)):
            first = await module._drive_create_doc("T", "b", folder_id=_FOLDER)
            sent = len(fake.requests)
            second = await module._drive_create_doc("T", "b", folder_id=_FOLDER)
        assert first["status"] == "ok" and pool.inserts == 1
        assert second["file_id"] == first["file_id"] and second["replayed"] is True
        assert len(fake.requests) == sent

    async def test_unreadable_receipt_store_refuses_a_keyed_write(self) -> None:
        fake = _FakeGoogle()
        module = _native_module(fake)  # no pool
        with lineage_scope(_lineage(exposed=False, effect=uuid.uuid4())):
            result = await module._drive_create_doc("T", "b", folder_id=_FOLDER)
        assert result["status"] == "error" and fake.requests == []

    async def test_failed_value_write_trashes_the_new_sheet(self) -> None:
        fake = _FakeGoogle()
        fake.sheet_write_status = 500
        module = _native_module(fake)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_create_sheet("S", [["a"]], folder_id=_FOLDER)
        assert result["status"] == "error" and result["trashed"] is True
        trash = [r for r in fake.writes if r.method == "PATCH"]
        assert len(trash) == 1 and json.loads(trash[0].content) == {"trashed": True}

    async def test_sheets_api_disabled_names_the_api_and_is_not_retried(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        fake.sheet_write_status = 403
        fake.sheet_write_body = {"error": {"errors": [{"reason": "accessNotConfigured"}]}}
        module = _native_module(fake)
        result = await module._drive_append_sheet_rows("sheet-1", [["a"]])
        assert "Google Sheets API" in result["error"]
        assert len([r for r in fake.writes if "/values/" in r.url.path]) == 1

    async def test_oversize_content_is_refused_without_calling_google(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        module = _native_module(fake)
        assert (await module._drive_create_doc("T", "x" * 1_000_001, folder_id=_FOLDER))[
            "status"
        ] == "too_large"
        big = [[0] * 1000 for _ in range(51)]
        assert (await module._drive_append_sheet_rows("sheet-1", big))["status"] == "too_large"
        assert fake.requests == []

    async def test_post_write_hook_is_the_single_seam(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        module = _native_module(fake)
        seen = []

        async def hook(tool, file_id, detail):
            seen.append((tool, file_id))

        module.set_post_write_hook(hook)
        await module._drive_append_sheet_rows("sheet-1", [["a"]])
        await module._drive_update_sheet_range("sheet-1", "A1", [["a"]])
        assert seen == [
            ("drive_append_sheet_rows", "sheet-1"),
            ("drive_update_sheet_range", "sheet-1"),
        ]

    async def test_default_folder_is_not_created_under_a_shared_parent(self) -> None:
        """folder_id=None must refuse before it creates butlers/<name> anywhere."""
        fake = _FakeGoogle()
        fake.files["root"] = {
            "id": "root",
            "mimeType": _FOLDER_MIME,
            "ownedByMe": True,
            "permissions": [_OWN_GRANT, {"type": "user", "role": "writer", "emailAddress": "b@x"}],
        }
        module = _native_module(fake)
        for call in (
            lambda: module._drive_create_doc("T", "b"),
            lambda: module._drive_create_sheet("T", [["a"]]),
        ):
            result = await call()
            assert result["status"] == "target_shared"
        assert fake.writes == []

    async def test_unreadable_permission_list_fails_closed(self) -> None:
        fake = _FakeGoogle()
        fake.add("sheet-1", _SHEET_MIME)
        del fake.files["sheet-1"]["permissions"]
        module = _native_module(fake)
        result = await module._drive_append_sheet_rows("sheet-1", [["a"]])
        assert result == {"status": "target_shared", "reach": {"permissions_unreadable": 1}}
        assert fake.writes == []

    async def test_default_subfolder_is_not_created_inside_a_shared_butlers_root(self) -> None:
        """The realistic case: butlers/ exists and is shared, butlers/<name> is missing."""
        fake = _FakeGoogle()
        fake.folders_by_name["butlers"] = "butlers-root"
        fake.files["butlers-root"] = {
            "id": "butlers-root",
            "mimeType": _FOLDER_MIME,
            "ownedByMe": True,
            "permissions": [_OWN_GRANT, {"type": "anyone", "role": "reader"}],
        }
        fake.files["root"] = {
            "id": "root",
            "mimeType": _FOLDER_MIME,
            "ownedByMe": True,
            "permissions": [_OWN_GRANT],
        }
        module = _native_module(fake)
        result = await module._drive_create_doc("T", "b")
        assert result["status"] == "target_shared"
        assert result["reach"] == {"anyone:reader": 1}
        assert fake.writes == []


# ---------------------------------------------------------------------------
# PDF export of Butlers-made files, delivered to the owner on Telegram [bu-jv1moa.5 S1]
# ---------------------------------------------------------------------------

_MARK = {"butlers_created_by": "home"}


class _MemoryBlobStore:
    """An in-memory blob store that mints refs under the butler prefix, like S3BlobStore."""

    scheme = "s3"
    bucket = "butlers"

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.types: dict[str, str] = {}

    async def put(self, data: bytes, *, content_type: str, filename: str | None = None) -> str:
        ref = f"s3://butlers/home/2026/11/22/{uuid.uuid4().hex}.pdf"
        self.blobs[ref] = data
        self.types[ref] = content_type
        return ref

    async def get(self, storage_ref: str) -> bytes:
        return self.blobs[storage_ref]


class TestExportFile:
    async def test_exports_a_butlers_made_doc_to_one_blob(self) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("doc-1", _DOC_MIME, name="Weekly brief", appProperties=_MARK)
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_export_file("doc-1")
        assert result["status"] == "ok"
        assert result["media_type"] == "application/pdf"
        assert result["filename"] == "Weekly brief.pdf"
        assert result["size_bytes"] == len(fake.export_body)
        assert store.blobs == {result["storage_ref"]: fake.export_body}
        assert fake.exports[0].url.params["mimeType"] == "application/pdf"
        assert result["sha256"] == hashlib.sha256(fake.export_body).hexdigest()

    async def test_a_sheet_exports_too(self) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("sheet-1", _SHEET_MIME, name="Money 2026", appProperties=_MARK)
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            assert (await module._drive_export_file("sheet-1"))["status"] == "ok"

    async def test_a_file_butlers_did_not_create_is_refused_without_exporting(self) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("doc-1", _DOC_MIME, name="Owner's own")
        fake.add("doc-2", _DOC_MIME, name="Other butler", appProperties={"butlers_created_by": "x"})
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            first = await module._drive_export_file("doc-1")
            second = await module._drive_export_file("doc-2")
        assert first["status"] == second["status"] == "not_created_by_butlers"
        assert fake.exports == [] and store.blobs == {}

    async def test_a_shared_file_is_refused(self) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add(
            "doc-1",
            _DOC_MIME,
            appProperties=_MARK,
            permissions=[_OWN_GRANT, {"type": "anyone", "role": "reader"}],
        )
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_export_file("doc-1")
        assert result["status"] == "target_shared"
        assert fake.exports == [] and store.blobs == {}

    async def test_only_docs_and_sheets_and_only_pdf(self) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("folder-2", _FOLDER_MIME, appProperties=_MARK)
        fake.add("doc-1", _DOC_MIME, appProperties=_MARK)
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            wrong = await module._drive_export_file("folder-2")
            fmt = await module._drive_export_file("doc-1", format="docx")
        assert wrong["status"] == "wrong_type" and fmt["status"] == "invalid"
        assert fake.exports == [] and store.blobs == {}

    @pytest.mark.parametrize("how", ["body", "drive_refusal"])
    async def test_an_export_over_10_mb_is_too_large_and_leaves_no_blob(self, how) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("doc-1", _DOC_MIME, appProperties=_MARK)
        if how == "body":
            fake.export_body = b"x" * (10 * 1024 * 1024 + 1)
        else:
            fake.export_status, fake.export_error = 403, "exportSizeLimitExceeded"
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_export_file("doc-1")
        assert result["status"] == "too_large"
        assert store.blobs == {}

    async def test_a_failed_export_leaves_no_blob(self) -> None:
        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("doc-1", _DOC_MIME, appProperties=_MARK)
        fake.export_status, fake.export_error = 500, "backendError"
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_export_file("doc-1")
        assert result["status"] == "error" and store.blobs == {}

    async def test_a_replayed_effect_returns_the_first_ref_without_a_second_drive_call(
        self,
    ) -> None:
        fake, store, pool = _FakeGoogle(), _MemoryBlobStore(), _FakeReceiptPool()
        fake.add("doc-1", _DOC_MIME, appProperties=_MARK)
        module = _native_module(fake, pool=pool, blob_store=store)
        effect = uuid.uuid4()
        with lineage_scope(_lineage(exposed=False, effect=effect)):
            first = await module._drive_export_file("doc-1")
            requests_after_first = len(fake.requests)
            second = await module._drive_export_file("doc-1")
        assert second["storage_ref"] == first["storage_ref"] and second["replayed"] is True
        assert len(fake.requests) == requests_after_first
        assert len(fake.exports) == 1 and len(store.blobs) == 1

    async def test_without_blob_storage_nothing_is_exported(self) -> None:
        fake = _FakeGoogle()
        fake.add("doc-1", _DOC_MIME, appProperties=_MARK)
        module = _native_module(fake, blob_store=None)
        with lineage_scope(_lineage(exposed=False)):
            result = await module._drive_export_file("doc-1")
        assert result["status"] == "error"
        assert fake.requests == []

    async def test_the_ref_is_delivered_to_the_owner_as_a_telegram_document(self) -> None:
        """Real seam: the storage_ref the tool returns goes through notify's Telegram path."""
        from butlers.modules.telegram import TelegramModule

        fake, store = _FakeGoogle(), _MemoryBlobStore()
        fake.add("doc-1", _DOC_MIME, name="Weekly brief", appProperties=_MARK)
        module = _native_module(fake, blob_store=store)
        with lineage_scope(_lineage(exposed=False)):
            exported = await module._drive_export_file("doc-1")

        telegram = TelegramModule()
        telegram._blob_store = store
        calls: list[dict] = []

        async def post(url, *, json=None, data=None, files=None):
            calls.append({"method": url.rsplit("/", 1)[1], "files": files, "data": data})
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"ok": True, "result": {"message_id": 1}},
            )

        with (
            patch.object(telegram, "_get_client", return_value=MagicMock(post=post)),
            patch.object(telegram, "_base_url", return_value="https://telegram.test"),
            patch("butlers.modules.telegram.write_audit_entry", new_callable=AsyncMock),
            patch("butlers.modules.telegram.require_permission", AsyncMock(return_value=None)),
            patch("butlers.modules.telegram.require_custody_clear", AsyncMock(return_value=None)),
        ):
            await telegram._send_files(
                "123",
                "",
                [
                    {
                        "filename": exported["filename"],
                        "media_type": exported["media_type"],
                        "storage_ref": exported["storage_ref"],
                        "sha256": exported["sha256"],
                        "size_bytes": exported["size_bytes"],
                    }
                ],
                origin_butler="home",
            )
        assert [c["method"] for c in calls] == ["sendDocument"]
        assert calls[0]["files"] == {
            "document": ("Weekly brief.pdf", fake.export_body, "application/pdf")
        }
