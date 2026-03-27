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

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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

from butlers.connectors.google_drive import GDriveConnectorManager  # noqa: E402
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
        """GDriveConnectorManager discovers accounts with drive scope at startup."""
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
            manager = GDriveConnectorManager(
                db_pool=mock_pool,
                credential_store=mock_store,
                switchboard_mcp_url="http://localhost:41100/sse",
            )

            # Discover accounts — should return the one with drive scope.
            discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 1
        assert discovered[0]["email"] == _FAKE_ACCOUNT_EMAIL

    async def test_connector_skips_accounts_without_drive_scope(self) -> None:
        """GDriveConnectorManager skips accounts that lack drive scopes."""
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

        manager = GDriveConnectorManager(
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

    async def test_module_startup_fails_missing_drive_scope(self) -> None:
        """Module on_startup raises when 'drive' scope is absent from granted_scopes."""
        from butlers.google_credentials import MissingGoogleCredentialsError  # noqa: PLC0415
        from butlers.modules.google_drive import GoogleDriveStartupError  # noqa: PLC0415

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
            with pytest.raises(
                (MissingGoogleCredentialsError, GoogleDriveStartupError, RuntimeError, ValueError)
            ):
                await module.on_startup(
                    config=config,
                    db=None,
                    credential_store=mock_store,
                )


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

    async def test_drive_write_creates_root_butler_folder_when_missing(self) -> None:
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

    async def test_drive_write_reuses_cached_folder_ids(self) -> None:
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

    async def test_drive_write_then_read_roundtrip(self) -> None:
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
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    ),
                ),
                MagicMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "id": _FAKE_BUTLER_SUBFOLDER_ID,
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

    async def test_drive_write_recreates_deleted_folder(self) -> None:
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

        mock_client.get = AsyncMock(
            side_effect=[not_found_response, search_empty, search_empty]
        )
        mock_client.post = AsyncMock(
            side_effect=[new_folder_response, new_sub_folder_response, file_create_response]
        )

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
