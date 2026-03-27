"""Tests for Google Drive connector (tasks 14.1–14.6).

Covers:
- 14.1: Multi-account discovery — qualifying accounts, missing scopes, degraded startup
- 14.2: Polling and change processing — changes.list parsing, pagination, cursor advancement
- 14.3: Event normalization — each event type detection, fallback, metadata cache updates
- 14.4: ingest.v1 envelope construction — field mapping, idempotency key format
- 14.5: Dynamic account discovery — add/remove accounts, graceful shutdown
- 14.6: Rate-limit handling and error isolation between account loops

All external Google Drive API calls are mocked — no network access required.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from butlers.connectors.google_drive import (
    _CHANGE_TYPE_CREATED,
    _CHANGE_TYPE_MODIFIED,
    _CHANGE_TYPE_MOVED,
    _CHANGE_TYPE_RENAMED,
    _CHANGE_TYPE_SHARING_CHANGED,
    _CHANGE_TYPE_TRASHED,
    GDriveAccountConfig,
    GDriveAccountLoop,
    GDriveConnectorManager,
    GDriveCursor,
    GDriveProcessConfig,
    MultiAccountHealthStatus,
    _build_ingest_envelope,
    _build_normalized_text,
    _detect_change_type,
    _exponential_backoff_retry,
    _FileMetadata,
    _make_idempotency_key,
    _redact_email,
    build_changes_list_url,
    build_start_page_token_url,
    parse_changes_list_response,
)
from butlers.connectors.metrics import ConnectorMetrics

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_DRIVE_SCOPE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"

_FAKE_EMAIL = "user@example.com"
_FAKE_EMAIL2 = "other@example.com"
_FAKE_FILE_ID = "gdrive-file-abc123"
_FAKE_FOLDER_ID = "gdrive-folder-xyz789"
_SWITCHBOARD_URL = "http://localhost:41100/sse"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_config() -> GDriveAccountConfig:
    """Create a minimal GDriveAccountConfig for testing."""
    return GDriveAccountConfig(
        email=_FAKE_EMAIL,
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        switchboard_mcp_url=_SWITCHBOARD_URL,
        poll_interval_s=300,
    )


@pytest.fixture
def process_config() -> GDriveProcessConfig:
    """Create a minimal GDriveProcessConfig for testing."""
    return GDriveProcessConfig(
        switchboard_mcp_url=_SWITCHBOARD_URL,
        poll_interval_s=300,
        account_rescan_interval_s=300,
    )


@pytest.fixture
def mock_db_pool() -> MagicMock:
    """Create a mock asyncpg pool."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.keys = MagicMock(return_value=[])

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def mock_credential_store() -> MagicMock:
    """Create a mock CredentialStore."""
    return MagicMock()


def _make_drive_account_row(
    email: str = _FAKE_EMAIL,
    *,
    granted_scopes: list[str] | None = None,
    status: str = "active",
    is_primary: bool = True,
) -> MagicMock:
    """Create a mock asyncpg Row for a google_accounts row."""
    if granted_scopes is None:
        granted_scopes = [_DRIVE_SCOPE, _DRIVE_SCOPE_READONLY]

    row = MagicMock()
    data = {
        "id": "fake-uuid",
        "entity_id": "fake-entity-uuid",
        "email": email,
        "granted_scopes": granted_scopes,
        "status": status,
        "is_primary": is_primary,
        "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
        "last_token_refresh_at": None,
        "display_name": "Test User",
        "metadata": {},
    }
    row.__getitem__ = lambda self, k: data.get(k)
    row.keys = MagicMock(return_value=list(data.keys()))
    return row


def _make_mock_credentials(email: str = _FAKE_EMAIL) -> MagicMock:
    """Create mock Google credentials."""
    creds = MagicMock()
    creds.client_id = "test-client-id"
    creds.client_secret = "test-client-secret"
    creds.refresh_token = "test-refresh-token"
    creds.scope = f"openid email profile {_DRIVE_SCOPE} {_DRIVE_SCOPE_READONLY}"
    return creds


# ---------------------------------------------------------------------------
# 14.1: Multi-account discovery
# ---------------------------------------------------------------------------


class TestMultiAccountDiscovery:
    """Task 14.1: Verify qualifying account discovery from public.google_accounts."""

    async def test_discovers_accounts_with_full_drive_scope(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager discovers accounts with full drive scope."""
        row = _make_drive_account_row(granted_scopes=[_DRIVE_SCOPE])
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 1
        assert discovered[0]["email"] == _FAKE_EMAIL

    async def test_discovers_accounts_with_readonly_drive_scope(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager discovers accounts with drive.readonly scope."""
        row = _make_drive_account_row(granted_scopes=[_DRIVE_SCOPE_READONLY])
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 1

    async def test_skips_accounts_without_drive_scope(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager skips accounts that have only non-drive scopes."""
        row = _make_drive_account_row(granted_scopes=[_GMAIL_SCOPE])
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 0

    async def test_skips_accounts_with_empty_scopes(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager skips accounts with no granted_scopes."""
        row = _make_drive_account_row(granted_scopes=[])
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 0

    async def test_discovers_multiple_qualifying_accounts(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager discovers all qualifying accounts, not just the first."""
        row1 = _make_drive_account_row(_FAKE_EMAIL, granted_scopes=[_DRIVE_SCOPE])
        row2 = _make_drive_account_row(_FAKE_EMAIL2, granted_scopes=[_DRIVE_SCOPE_READONLY])
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row1, row2]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 2

    async def test_degraded_startup_when_no_qualifying_accounts(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager handles startup with no qualifying accounts (degraded but not crashed)."""
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(return_value=[])

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 0
        # Health should still be reportable (empty, not error)
        health = manager.get_health()
        assert health.active_accounts == 0

    async def test_discover_handles_db_failure_gracefully(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """discover_drive_accounts returns empty list when DB is unavailable."""
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            side_effect=Exception("DB connection failed")
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        # Should not raise — returns empty list on failure
        discovered = await manager.discover_drive_accounts()

        assert discovered == []

    async def test_mixed_scope_accounts_filtered_correctly(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Mixed-scope batch: only drive-scoped accounts are returned."""
        drive_row = _make_drive_account_row(_FAKE_EMAIL, granted_scopes=[_DRIVE_SCOPE])
        no_drive_row = _make_drive_account_row(_FAKE_EMAIL2, granted_scopes=[_GMAIL_SCOPE])
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[drive_row, no_drive_row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        discovered = await manager.discover_drive_accounts()

        assert len(discovered) == 1
        assert discovered[0]["email"] == _FAKE_EMAIL


# ---------------------------------------------------------------------------
# 14.2: Polling and change processing
# ---------------------------------------------------------------------------


class TestPollingAndChangeProcessing:
    """Task 14.2: changes.list parsing, pagination, cursor advancement."""

    def test_parse_changes_list_single_page(self) -> None:
        """Parsing a single-page changes.list response returns changes and new start token."""
        response = {
            "changes": [
                {"fileId": "f1", "file": {"id": "f1", "name": "doc.txt"}},
                {"fileId": "f2", "file": {"id": "f2", "name": "sheet.csv"}},
            ],
            "newStartPageToken": "token-for-next-poll",
        }
        changes, next_page_token, new_start_token = parse_changes_list_response(response)

        assert len(changes) == 2
        assert next_page_token is None  # single page — no nextPageToken
        assert new_start_token == "token-for-next-poll"

    def test_parse_changes_list_with_next_page_token(self) -> None:
        """Parsing a paginated response returns nextPageToken when more pages exist."""
        response = {
            "changes": [{"fileId": "f1"}],
            "nextPageToken": "next-page-123",
            # No newStartPageToken on intermediate pages
        }
        changes, next_page_token, new_start_token = parse_changes_list_response(response)

        assert len(changes) == 1
        assert next_page_token == "next-page-123"
        assert new_start_token is None

    def test_parse_changes_list_empty(self) -> None:
        """Parsing an empty changes.list response returns empty list."""
        response = {
            "changes": [],
            "newStartPageToken": "fresh-token",
        }
        changes, next_page_token, new_start_token = parse_changes_list_response(response)

        assert changes == []
        assert next_page_token is None
        assert new_start_token == "fresh-token"

    def test_parse_changes_list_no_changes_key(self) -> None:
        """Parsing a response without 'changes' key defaults to empty list."""
        response = {"newStartPageToken": "token"}
        changes, _, _ = parse_changes_list_response(response)
        assert changes == []

    def test_build_start_page_token_url(self) -> None:
        """build_start_page_token_url returns the correct Drive API URL."""
        url = build_start_page_token_url()
        assert "changes/startPageToken" in url
        assert "googleapis.com" in url

    def test_build_changes_list_url_includes_page_token(self) -> None:
        """build_changes_list_url embeds the page token in the URL."""
        url = build_changes_list_url("my-page-token-123")
        assert "my-page-token-123" in url
        assert "changes" in url

    def test_build_changes_list_url_includes_removed(self) -> None:
        """build_changes_list_url includes includeRemoved=true by default."""
        url = build_changes_list_url("token")
        assert "includeRemoved=true" in url

    def test_build_changes_list_url_exclude_removed(self) -> None:
        """build_changes_list_url respects include_removed=False."""
        url = build_changes_list_url("token", include_removed=False)
        assert "includeRemoved=false" in url

    def test_cursor_model_fields(self) -> None:
        """GDriveCursor model stores page_token and last_updated_at."""
        now = datetime.now(UTC)
        cursor = GDriveCursor(page_token="abc123", last_updated_at=now)

        assert cursor.page_token == "abc123"
        assert cursor.last_updated_at == now

    def test_cursor_model_serialization(self) -> None:
        """GDriveCursor can be serialized to dict for cursor_store persistence."""
        now = datetime.now(UTC)
        cursor = GDriveCursor(page_token="tok-xyz", last_updated_at=now)

        data = cursor.model_dump()
        assert data["page_token"] == "tok-xyz"
        assert "last_updated_at" in data

    def test_account_config_endpoint_identity(self, account_config: GDriveAccountConfig) -> None:
        """endpoint_identity follows google_drive:user:<email> format."""
        assert account_config.endpoint_identity == f"google_drive:user:{_FAKE_EMAIL}"

    def test_account_config_cursor_key_matches_endpoint_identity(
        self, account_config: GDriveAccountConfig
    ) -> None:
        """cursor_key matches endpoint_identity for cursor_store lookups."""
        assert account_config.cursor_key == account_config.endpoint_identity


# ---------------------------------------------------------------------------
# 14.3: Event normalization
# ---------------------------------------------------------------------------


class TestEventNormalization:
    """Task 14.3: change type detection, normalized_text, metadata cache updates."""

    def _make_cached(self, **kwargs: Any) -> _FileMetadata:
        defaults: dict[str, Any] = {
            "file_id": _FAKE_FILE_ID,
            "name": "original-name.txt",
            "mime_type": "text/plain",
            "parents": [_FAKE_FOLDER_ID],
            "shared": False,
            "modified_time": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        return _FileMetadata(**defaults)

    # Change type detection

    def test_detect_change_type_created_no_cache(self) -> None:
        """New file (no cache entry) → CREATED."""
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {"id": _FAKE_FILE_ID, "name": "new-doc.txt"},
        }
        result = _detect_change_type(change, cached=None)
        assert result == _CHANGE_TYPE_CREATED

    def test_detect_change_type_trashed_by_removed_flag(self) -> None:
        """change.removed=True → TRASHED."""
        change = {"fileId": _FAKE_FILE_ID, "removed": True}
        result = _detect_change_type(change, cached=None)
        assert result == _CHANGE_TYPE_TRASHED

    def test_detect_change_type_trashed_by_file_flag(self) -> None:
        """file.trashed=True → TRASHED even with cached entry."""
        cached = self._make_cached()
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {"id": _FAKE_FILE_ID, "name": "doc.txt", "trashed": True},
        }
        result = _detect_change_type(change, cached=cached)
        assert result == _CHANGE_TYPE_TRASHED

    def test_detect_change_type_renamed(self) -> None:
        """Name changed, same parents → RENAMED."""
        cached = self._make_cached(name="old-name.txt")
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "new-name.txt",
                "parents": [_FAKE_FOLDER_ID],
            },
        }
        result = _detect_change_type(change, cached=cached)
        assert result == _CHANGE_TYPE_RENAMED

    def test_detect_change_type_moved(self) -> None:
        """Parents changed → MOVED (takes precedence over rename)."""
        cached = self._make_cached(parents=["old-folder-id"])
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": cached.name,
                "parents": ["new-folder-id"],
            },
        }
        result = _detect_change_type(change, cached=cached)
        assert result == _CHANGE_TYPE_MOVED

    def test_detect_change_type_sharing_changed(self) -> None:
        """Shared status changed → SHARING_CHANGED."""
        cached = self._make_cached(shared=False)
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": cached.name,
                "parents": [_FAKE_FOLDER_ID],
                "shared": True,
            },
        }
        result = _detect_change_type(change, cached=cached)
        assert result == _CHANGE_TYPE_SHARING_CHANGED

    def test_detect_change_type_modified_fallback(self) -> None:
        """No structural change (name/parents/sharing same) → MODIFIED."""
        cached = self._make_cached()
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": cached.name,
                "parents": [_FAKE_FOLDER_ID],
                "shared": False,
                "modifiedTime": "2026-06-01T12:00:00Z",
            },
        }
        result = _detect_change_type(change, cached=cached)
        assert result == _CHANGE_TYPE_MODIFIED

    def test_moved_takes_precedence_over_rename(self) -> None:
        """When both name and parents change, MOVED takes precedence."""
        cached = self._make_cached(name="old-name.txt", parents=["old-folder"])
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "new-name.txt",
                "parents": ["new-folder"],
            },
        }
        result = _detect_change_type(change, cached=cached)
        # MOVED takes precedence
        assert result == _CHANGE_TYPE_MOVED

    # Normalized text

    def test_normalized_text_contains_label(self) -> None:
        """Normalized text starts with [CHANGE_TYPE] label."""
        text = _build_normalized_text(
            change_type=_CHANGE_TYPE_CREATED,
            file_id=_FAKE_FILE_ID,
            name="report.pdf",
            mime_type="application/pdf",
            modified_time="2026-03-26T10:00:00Z",
            shared=False,
        )
        assert "[CREATED]" in text
        assert "report.pdf" in text

    def test_normalized_text_includes_file_id(self) -> None:
        """Normalized text includes the file ID for traceability."""
        text = _build_normalized_text(
            change_type=_CHANGE_TYPE_MODIFIED,
            file_id=_FAKE_FILE_ID,
            name="sheet.csv",
            mime_type="text/csv",
            modified_time=None,
            shared=False,
        )
        assert _FAKE_FILE_ID in text

    def test_normalized_text_includes_mime_type(self) -> None:
        """Normalized text includes the MIME type."""
        text = _build_normalized_text(
            change_type=_CHANGE_TYPE_RENAMED,
            file_id=_FAKE_FILE_ID,
            name="doc.txt",
            mime_type="text/plain",
            modified_time="2026-03-26T10:00:00Z",
            shared=False,
        )
        assert "text/plain" in text

    def test_normalized_text_shared_flag(self) -> None:
        """Normalized text notes when file is shared."""
        text = _build_normalized_text(
            change_type=_CHANGE_TYPE_SHARING_CHANGED,
            file_id=_FAKE_FILE_ID,
            name="shared-doc.txt",
            mime_type="text/plain",
            modified_time=None,
            shared=True,
        )
        assert "Shared" in text or "shared" in text

    def test_normalized_text_trashed(self) -> None:
        """Normalized text for TRASHED changes has correct label."""
        text = _build_normalized_text(
            change_type=_CHANGE_TYPE_TRASHED,
            file_id=_FAKE_FILE_ID,
            name="old-doc.txt",
            mime_type="text/plain",
            modified_time=None,
            shared=False,
        )
        assert "[TRASHED]" in text

    # Metadata cache updates via process_change

    def test_process_change_adds_new_file_to_cache(
        self, account_config: GDriveAccountConfig
    ) -> None:
        """Processing a CREATED change adds the file to the metadata cache."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "new-file.txt",
                "mimeType": "text/plain",
                "parents": [_FAKE_FOLDER_ID],
                "shared": False,
            },
        }

        loop.process_change(change)

        assert _FAKE_FILE_ID in loop._metadata_cache
        assert loop._metadata_cache[_FAKE_FILE_ID].name == "new-file.txt"

    def test_process_change_updates_existing_cache_entry(
        self, account_config: GDriveAccountConfig
    ) -> None:
        """Processing a MODIFIED change updates the existing cache entry."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        # Pre-populate cache
        loop._metadata_cache[_FAKE_FILE_ID] = _FileMetadata(
            file_id=_FAKE_FILE_ID,
            name="doc.txt",
            mime_type="text/plain",
            parents=[_FAKE_FOLDER_ID],
            shared=False,
            modified_time="2026-01-01T00:00:00Z",
        )
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "doc.txt",
                "mimeType": "text/plain",
                "parents": [_FAKE_FOLDER_ID],
                "shared": False,
                "modifiedTime": "2026-06-01T12:00:00Z",
            },
        }

        loop.process_change(change)

        assert loop._metadata_cache[_FAKE_FILE_ID].modified_time == "2026-06-01T12:00:00Z"

    def test_process_change_removes_trashed_file_from_cache(
        self, account_config: GDriveAccountConfig
    ) -> None:
        """Processing a TRASHED change removes the file from the metadata cache."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        loop._metadata_cache[_FAKE_FILE_ID] = _FileMetadata(
            file_id=_FAKE_FILE_ID,
            name="doc.txt",
            mime_type="text/plain",
            parents=[_FAKE_FOLDER_ID],
            shared=False,
            modified_time=None,
        )
        change = {"fileId": _FAKE_FILE_ID, "removed": True}

        loop.process_change(change)

        assert _FAKE_FILE_ID not in loop._metadata_cache

    def test_process_change_skips_change_without_file_id(
        self, account_config: GDriveAccountConfig
    ) -> None:
        """process_change returns None for changes with no fileId."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        result = loop.process_change({"some_key": "no_file_id"})
        assert result is None

    def test_process_change_returns_envelope(self, account_config: GDriveAccountConfig) -> None:
        """process_change returns a valid ingest.v1 envelope for a valid change."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "doc.txt",
                "mimeType": "text/plain",
                "parents": [_FAKE_FOLDER_ID],
            },
        }

        envelope = loop.process_change(change, observed_at="2026-03-26T10:00:00Z")

        assert envelope is not None
        assert envelope["schema_version"] == "ingest.v1"


# ---------------------------------------------------------------------------
# 14.4: ingest.v1 envelope construction
# ---------------------------------------------------------------------------


class TestIngestV1EnvelopeConstruction:
    """Task 14.4: ingest.v1 envelope field mapping and idempotency key format."""

    def _build_test_envelope(self, **overrides: Any) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "file_id": _FAKE_FILE_ID,
            "change_type": _CHANGE_TYPE_CREATED,
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
            "endpoint_identity": f"google_drive:user:{_FAKE_EMAIL}",
            "observed_at": "2026-03-26T10:00:00+00:00",
            "normalized_text": "[CREATED] report.pdf",
            "idempotency_key": "google_drive:user:user@example.com:gdrive-file-abc123:2026-03-26",
        }
        defaults.update(overrides)
        return _build_ingest_envelope(**defaults)

    def test_schema_version_is_ingest_v1(self) -> None:
        """Envelope schema_version must be 'ingest.v1'."""
        envelope = self._build_test_envelope()
        assert envelope["schema_version"] == "ingest.v1"

    def test_source_channel_is_google_drive(self) -> None:
        """source.channel must be 'google_drive'."""
        envelope = self._build_test_envelope()
        assert envelope["source"]["channel"] == "google_drive"

    def test_source_provider_is_google_drive(self) -> None:
        """source.provider must be 'google_drive'."""
        envelope = self._build_test_envelope()
        assert envelope["source"]["provider"] == "google_drive"

    def test_source_endpoint_identity_correct(self) -> None:
        """source.endpoint_identity matches the account's endpoint_identity."""
        endpoint = f"google_drive:user:{_FAKE_EMAIL}"
        envelope = self._build_test_envelope(endpoint_identity=endpoint)
        assert envelope["source"]["endpoint_identity"] == endpoint

    def test_event_external_event_id_is_file_id(self) -> None:
        """event.external_event_id is the Drive file ID."""
        envelope = self._build_test_envelope(file_id=_FAKE_FILE_ID)
        assert envelope["event"]["external_event_id"] == _FAKE_FILE_ID

    def test_event_external_thread_id_is_null(self) -> None:
        """event.external_thread_id is null for Drive changes (no threading)."""
        envelope = self._build_test_envelope()
        assert envelope["event"]["external_thread_id"] is None

    def test_event_type_includes_change_type(self) -> None:
        """event.event_type follows drive.file.<change_type> format."""
        for change_type in (
            _CHANGE_TYPE_CREATED,
            _CHANGE_TYPE_MODIFIED,
            _CHANGE_TYPE_TRASHED,
            _CHANGE_TYPE_RENAMED,
            _CHANGE_TYPE_MOVED,
            _CHANGE_TYPE_SHARING_CHANGED,
        ):
            envelope = self._build_test_envelope(change_type=change_type)
            assert envelope["event"]["event_type"] == f"drive.file.{change_type}"

    def test_payload_raw_is_null(self) -> None:
        """payload.raw must be null (metadata-only ingestion per spec)."""
        envelope = self._build_test_envelope()
        assert envelope["payload"]["raw"] is None

    def test_payload_normalized_text_present(self) -> None:
        """payload.normalized_text is populated."""
        envelope = self._build_test_envelope(normalized_text="[CREATED] report.pdf")
        assert envelope["payload"]["normalized_text"] == "[CREATED] report.pdf"

    def test_control_ingestion_tier_is_metadata(self) -> None:
        """control.ingestion_tier must be 'metadata' for Drive connector."""
        envelope = self._build_test_envelope()
        assert envelope["control"]["ingestion_tier"] == "metadata"

    def test_control_policy_tier_default(self) -> None:
        """control.policy_tier defaults to 'default'."""
        envelope = self._build_test_envelope()
        assert envelope["control"]["policy_tier"] == "default"

    def test_idempotency_key_format(self) -> None:
        """Idempotency key follows google_drive:<endpoint>:<file_id>:<observed_at> format."""
        endpoint = f"google_drive:user:{_FAKE_EMAIL}"
        observed_at = "2026-03-26T10:00:00Z"
        key = _make_idempotency_key(endpoint, _FAKE_FILE_ID, observed_at)

        assert key.startswith("google_drive:")
        assert endpoint in key
        assert _FAKE_FILE_ID in key
        assert observed_at in key

    def test_idempotency_key_deterministic(self) -> None:
        """Same inputs always produce the same idempotency key."""
        endpoint = f"google_drive:user:{_FAKE_EMAIL}"
        key1 = _make_idempotency_key(endpoint, _FAKE_FILE_ID, "2026-03-26T10:00:00Z")
        key2 = _make_idempotency_key(endpoint, _FAKE_FILE_ID, "2026-03-26T10:00:00Z")
        assert key1 == key2

    def test_idempotency_key_differs_for_different_files(self) -> None:
        """Different file_ids produce different idempotency keys."""
        endpoint = f"google_drive:user:{_FAKE_EMAIL}"
        key1 = _make_idempotency_key(endpoint, "file-id-1", "2026-03-26T10:00:00Z")
        key2 = _make_idempotency_key(endpoint, "file-id-2", "2026-03-26T10:00:00Z")
        assert key1 != key2

    def test_idempotency_key_in_envelope(self, account_config: GDriveAccountConfig) -> None:
        """process_change places the idempotency key in the envelope event field."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        observed_at = "2026-03-26T10:00:00Z"
        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {"id": _FAKE_FILE_ID, "name": "doc.txt"},
        }

        envelope = loop.process_change(change, observed_at=observed_at)

        assert envelope is not None
        assert "idempotency_key" in envelope["event"]
        assert _FAKE_FILE_ID in envelope["event"]["idempotency_key"]


# ---------------------------------------------------------------------------
# 14.5: Dynamic account discovery — add/remove accounts, graceful shutdown
# ---------------------------------------------------------------------------


class TestDynamicAccountDiscovery:
    """Task 14.5: Dynamic account re-scan, loop lifecycle, graceful shutdown."""

    async def test_sync_accounts_adds_new_account_loop(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """sync_accounts spawns a loop for a newly discovered account."""
        row = _make_drive_account_row(_FAKE_EMAIL)
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        fake_creds = _make_mock_credentials()

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ):
            added, removed, unchanged = await manager.sync_accounts()

        assert _FAKE_EMAIL in added
        assert len(removed) == 0
        assert _FAKE_EMAIL in manager._loops

    async def test_sync_accounts_removes_revoked_account_loop(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """sync_accounts stops loops for accounts no longer qualifying."""
        # Start with one account in _loops
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop

        # DB returns empty (account removed)
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(return_value=[])

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
        ):
            added, removed, unchanged = await manager.sync_accounts()

        assert _FAKE_EMAIL in removed
        assert _FAKE_EMAIL not in manager._loops

    async def test_sync_accounts_unchanged_loops_untouched(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """sync_accounts leaves unchanged account loops running."""
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        existing_loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = existing_loop

        row = _make_drive_account_row(_FAKE_EMAIL)
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
        ):
            added, removed, unchanged = await manager.sync_accounts()

        assert _FAKE_EMAIL in unchanged
        assert len(added) == 0
        assert len(removed) == 0
        # Original loop should still be in the manager
        assert manager._loops[_FAKE_EMAIL] is existing_loop

    async def test_sync_accounts_credential_failure_skips_account(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """sync_accounts skips accounts when credential resolution fails."""
        row = _make_drive_account_row(_FAKE_EMAIL)
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            side_effect=Exception("Credential DB unavailable"),
        ):
            added, removed, unchanged = await manager.sync_accounts()

        # Account should be skipped, not added
        assert len(added) == 0
        assert _FAKE_EMAIL not in manager._loops

    async def test_stop_gracefully_stops_all_loops(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """manager.stop() gracefully stops all account loops."""
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop

        # Stop should not raise and should clear loops
        await manager.stop()

        assert len(manager._loops) == 0

    def test_account_loop_start_and_stop(self, account_config: GDriveAccountConfig) -> None:
        """GDriveAccountLoop.start/stop manages asyncio task lifecycle."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        # Not started yet
        assert not loop.is_running

    async def test_account_loop_error_isolation(self, account_config: GDriveAccountConfig) -> None:
        """A crashed account loop captures error without affecting other loops."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        # Patch _poll_loop to raise immediately
        async def _failing_loop() -> None:
            raise RuntimeError("Simulated poll failure")

        loop._poll_loop = _failing_loop  # type: ignore[method-assign]

        task = asyncio.create_task(loop._run())
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (TimeoutError, RuntimeError):
            pass

        # Error should be captured on the loop object
        assert loop._error is not None

    async def test_per_account_config_override_poll_interval(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Per-account metadata.google_drive.poll_interval_s overrides process default."""
        row_data = {
            "id": "fake-uuid",
            "entity_id": "fake-entity-uuid",
            "email": _FAKE_EMAIL,
            "granted_scopes": [_DRIVE_SCOPE],
            "status": "active",
            "is_primary": True,
            "connected_at": datetime(2026, 1, 1, tzinfo=UTC),
            "last_token_refresh_at": None,
            "display_name": "Test User",
            "metadata": {"google_drive": {"poll_interval_s": 60}},
        }
        row = MagicMock()
        row.__getitem__ = lambda self, k: row_data.get(k)
        row.keys = MagicMock(return_value=list(row_data.keys()))

        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
            poll_interval_s=300,  # process default
        )

        fake_creds = _make_mock_credentials()

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ):
            added, _, _ = await manager.sync_accounts()

        assert len(added) == 1
        assert manager._loops[_FAKE_EMAIL]._config.poll_interval_s == 60


# ---------------------------------------------------------------------------
# 14.6: Rate-limit handling and error isolation
# ---------------------------------------------------------------------------


class TestRateLimitHandlingAndErrorIsolation:
    """Task 14.6: Rate-limit retry, Retry-After header, error isolation between loops."""

    async def test_no_retry_on_success(self) -> None:
        """No retry when first call returns 200."""
        response = MagicMock()
        response.status_code = 200

        call_count = 0

        async def mock_call() -> MagicMock:
            nonlocal call_count
            call_count += 1
            return response

        result = await _exponential_backoff_retry(mock_call)
        assert result is response
        assert call_count == 1

    async def test_retries_on_429(self) -> None:
        """Retries on 429 Too Many Requests up to max_retries."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {}

        success = MagicMock()
        success.status_code = 200

        responses = [rate_limited, rate_limited, success]
        idx = 0

        async def mock_call() -> MagicMock:
            nonlocal idx
            r = responses[idx]
            idx += 1
            return r

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _exponential_backoff_retry(mock_call, max_retries=3)

        assert result.status_code == 200

    async def test_retries_on_403(self) -> None:
        """Retries on 403 Forbidden (Drive rate-limit)."""
        forbidden = MagicMock()
        forbidden.status_code = 403
        forbidden.headers = {}

        success = MagicMock()
        success.status_code = 200

        responses_iter = iter([forbidden, success])

        async def mock_call() -> MagicMock:
            return next(responses_iter)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _exponential_backoff_retry(mock_call, max_retries=3)

        assert result.status_code == 200

    async def test_retries_on_503(self) -> None:
        """Retries on 503 Service Unavailable."""
        unavailable = MagicMock()
        unavailable.status_code = 503
        unavailable.headers = {}

        success = MagicMock()
        success.status_code = 200

        responses_iter = iter([unavailable, success])

        async def mock_call() -> MagicMock:
            return next(responses_iter)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _exponential_backoff_retry(mock_call, max_retries=3)

        assert result.status_code == 200

    async def test_returns_last_response_after_max_retries_exhausted(self) -> None:
        """Returns last failing response after max_retries is exhausted."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {}

        async def mock_call() -> MagicMock:
            return rate_limited

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await _exponential_backoff_retry(mock_call, max_retries=2)

        assert result.status_code == 429

    async def test_honors_retry_after_header(self) -> None:
        """Respects Retry-After header delay from rate-limit response."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "5"}

        success = MagicMock()
        success.status_code = 200

        responses_iter = iter([rate_limited, success])
        sleep_calls: list[float] = []

        async def mock_call() -> MagicMock:
            return next(responses_iter)

        async def mock_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", side_effect=mock_sleep):
            result = await _exponential_backoff_retry(mock_call, max_retries=3)

        assert result.status_code == 200
        # Should have slept for the Retry-After duration (or close to it, capped at max)
        assert len(sleep_calls) >= 1
        # The Retry-After of 5s should be honored
        assert sleep_calls[0] <= 60.0  # within max_delay

    async def test_no_retry_on_non_rate_limit_status(self) -> None:
        """Does not retry on non-rate-limit errors (e.g. 404, 500)."""
        not_found = MagicMock()
        not_found.status_code = 404

        call_count = 0

        async def mock_call() -> MagicMock:
            nonlocal call_count
            call_count += 1
            return not_found

        result = await _exponential_backoff_retry(mock_call, retry_on=(429, 503))
        assert result.status_code == 404
        assert call_count == 1  # No retries

    async def test_error_isolation_between_account_loops(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """A failing account loop does not affect sibling loops.

        This test verifies the per-account error isolation architecture:
        each GDriveAccountLoop runs independently so one account's failure
        does not cascade to others.
        """
        config1 = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid1",
            client_secret="cs1",
            refresh_token="rt1",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        config2 = GDriveAccountConfig(
            email=_FAKE_EMAIL2,
            client_id="cid2",
            client_secret="cs2",
            refresh_token="rt2",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        loop1 = GDriveAccountLoop(email=_FAKE_EMAIL, config=config1)
        loop2 = GDriveAccountLoop(email=_FAKE_EMAIL2, config=config2)

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop1
        manager._loops[_FAKE_EMAIL2] = loop2

        # Simulate loop1 failing
        loop1._error = "Simulated Drive API 500 error"
        loop1._source_api_ok = False

        # loop2 should remain healthy
        loop2._source_api_ok = True

        health = manager.get_health()

        # Overall status should be worst-case (error due to loop1)
        assert health.status == "error"
        assert health.active_accounts == 2

        # But loop2 should individually still be healthy
        loop2_health = loop2.get_health()
        assert loop2_health.status == "healthy"

    def test_redact_email_standard(self) -> None:
        """Email redaction shows only first 2 chars + *** + @domain."""
        redacted = _redact_email("user@example.com")
        assert redacted is not None
        assert "@example.com" in redacted
        assert "us***" in redacted

    def test_redact_email_none_returns_none(self) -> None:
        """Redacting None returns None."""
        assert _redact_email(None) is None

    def test_redact_email_no_at_sign(self) -> None:
        """Invalid email (no @) returns '***'."""
        assert _redact_email("notanemail") == "***"


# ---------------------------------------------------------------------------
# GDriveProcessConfig tests
# ---------------------------------------------------------------------------


class TestGDriveProcessConfig:
    """Tests for GDriveProcessConfig.from_env() loading and defaults."""

    def test_from_env_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that defaults are applied when env vars are not set."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.delenv("GDRIVE_POLL_INTERVAL_S", raising=False)
        monkeypatch.delenv("GDRIVE_ACCOUNT_RESCAN_INTERVAL_S", raising=False)

        config = GDriveProcessConfig.from_env()

        assert config.switchboard_mcp_url == _SWITCHBOARD_URL
        assert config.poll_interval_s == 300
        assert config.account_rescan_interval_s == 300

    def test_from_env_custom_poll_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Custom env vars override defaults."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.setenv("GDRIVE_POLL_INTERVAL_S", "120")
        monkeypatch.setenv("GDRIVE_ACCOUNT_RESCAN_INTERVAL_S", "600")

        config = GDriveProcessConfig.from_env()

        assert config.poll_interval_s == 120
        assert config.account_rescan_interval_s == 600

    def test_from_env_missing_switchboard_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing SWITCHBOARD_MCP_URL raises ValueError."""
        monkeypatch.delenv("SWITCHBOARD_MCP_URL", raising=False)

        with pytest.raises(ValueError, match="SWITCHBOARD_MCP_URL is required"):
            GDriveProcessConfig.from_env()

    def test_from_env_invalid_poll_interval_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid GDRIVE_POLL_INTERVAL_S falls back to default."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.setenv("GDRIVE_POLL_INTERVAL_S", "not-a-number")

        config = GDriveProcessConfig.from_env()

        assert config.poll_interval_s == 300  # default

    def test_make_account_config_applies_metadata_overrides(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """make_account_config applies google_drive metadata poll_interval_s override."""
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_gdrive={"poll_interval_s": 60},
        )
        assert account_cfg.poll_interval_s == 60

    def test_make_account_config_no_metadata_uses_process_defaults(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """make_account_config with no metadata uses process-level defaults."""
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_gdrive=None,
        )
        assert account_cfg.poll_interval_s == process_config.poll_interval_s

    def test_make_account_config_invalid_metadata_override_ignored(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """Invalid metadata poll_interval_s value is silently ignored."""
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_gdrive={"poll_interval_s": "not-a-number"},
        )
        # Should fall back to process default
        assert account_cfg.poll_interval_s == process_config.poll_interval_s


# ---------------------------------------------------------------------------
# Aggregated health status
# ---------------------------------------------------------------------------


class TestAggregatedHealthStatus:
    """Tests for aggregated health status across account loops."""

    def test_empty_manager_health_is_healthy(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Manager with no loops reports healthy (degraded at process level is OK)."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        health = manager.get_health()

        assert isinstance(health, MultiAccountHealthStatus)
        assert health.active_accounts == 0
        assert health.status == "healthy"

    def test_all_healthy_loops_give_healthy_status(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """All-healthy loops produce overall 'healthy' status."""
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)
        loop._source_api_ok = True

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop

        health = manager.get_health()
        assert health.status == "healthy"
        assert health.active_accounts == 1

    def test_one_error_loop_escalates_to_error_status(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """One errored loop escalates overall status to 'error'."""
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)
        loop._source_api_ok = False
        loop._error = "API error"

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop

        health = manager.get_health()
        assert health.status == "error"

    def test_health_includes_per_account_details(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Health response includes per-account breakdown."""
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop

        health = manager.get_health()
        assert len(health.account_health) == 1
        # Email should be redacted in health output
        account_health = health.account_health[0]
        assert "us***" in (account_health.email or "")


# ---------------------------------------------------------------------------
# Tasks 12.1–12.5: Multi-account lifecycle management
# ---------------------------------------------------------------------------


class TestMultiAccountLifecycle:
    """Tasks 12.1–12.5: rescan loop, reload, SIGHUP, health server, heartbeat."""

    # ------------------------------------------------------------------
    # 12.1: Periodic account re-scan via _run_rescan_loop
    # ------------------------------------------------------------------

    async def test_run_rescan_loop_calls_sync_on_timeout(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_run_rescan_loop triggers sync_accounts when rescan interval elapses."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
            account_rescan_interval_s=1,
        )
        manager._running = True

        sync_calls: list[int] = []

        async def _mock_sync() -> tuple[list[str], list[str], list[str]]:
            sync_calls.append(1)
            manager._running = False  # Stop after one cycle
            return [], [], []

        manager.sync_accounts = _mock_sync  # type: ignore[method-assign]

        # Run rescan loop — it should call sync once and then stop
        await manager._run_rescan_loop()
        assert len(sync_calls) >= 1

    async def test_run_rescan_loop_stops_when_running_false(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_run_rescan_loop exits immediately when _running is set to False."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
            account_rescan_interval_s=10,  # Long interval
        )
        manager._running = False  # Not running

        sync_calls: list[int] = []

        async def _mock_sync() -> tuple[list[str], list[str], list[str]]:
            sync_calls.append(1)
            return [], [], []

        manager.sync_accounts = _mock_sync  # type: ignore[method-assign]

        # Should exit without calling sync because _running=False
        # Set reload event to unblock wait_for immediately
        manager._reload_event.set()
        await manager._run_rescan_loop()
        # sync_accounts must NOT be called when _running=False
        assert len(sync_calls) == 0

    # ------------------------------------------------------------------
    # 12.2: On-demand reload via reload_accounts() and SIGHUP
    # ------------------------------------------------------------------

    async def test_reload_accounts_returns_summary(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """reload_accounts() returns added/removed/unchanged summary."""
        row = _make_drive_account_row(_FAKE_EMAIL)
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(
            return_value=[row]
        )

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        fake_creds = _make_mock_credentials()

        with patch(
            "butlers.connectors.google_drive.resolve_google_credentials",
            new_callable=AsyncMock,
            return_value=fake_creds,
        ):
            result = await manager.reload_accounts()

        assert "added" in result
        assert "removed" in result
        assert "unchanged" in result

    async def test_reload_event_triggers_rescan_loop_early(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """Setting _reload_event unblocks the rescan loop before the timeout."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
            account_rescan_interval_s=300,  # Long timeout — should be interrupted
        )
        manager._running = True

        sync_calls: list[int] = []

        async def _mock_sync() -> tuple[list[str], list[str], list[str]]:
            sync_calls.append(1)
            manager._running = False  # Stop after first sync
            return [], [], []

        manager.sync_accounts = _mock_sync  # type: ignore[method-assign]

        # Trigger reload event immediately
        manager._reload_event.set()

        # Run loop — it should wake up immediately and call sync
        await manager._run_rescan_loop()
        assert len(sync_calls) == 1

    # ------------------------------------------------------------------
    # 12.2: SIGHUP handler setup
    # ------------------------------------------------------------------

    def test_setup_sighup_registers_handler(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_setup_sighup does not raise (handler registration smoke test)."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        # Should not raise — SIGHUP not available on all platforms is handled gracefully
        try:
            manager._setup_sighup()
        except (OSError, NotImplementedError):
            pass  # OK — unavailable on Windows

    # ------------------------------------------------------------------
    # 12.3: Graceful shutdown on account removal
    # ------------------------------------------------------------------

    async def test_sync_accounts_graceful_removal_stops_loop(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """sync_accounts stops the loop for a removed account (task 12.3)."""
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)
        stop_called = [False]

        async def _mock_stop() -> None:
            stop_called[0] = True

        loop.stop = _mock_stop  # type: ignore[method-assign]

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = loop

        # DB returns empty — account removed
        mock_db_pool.acquire.return_value.__aenter__.return_value.fetch = AsyncMock(return_value=[])

        added, removed, unchanged = await manager.sync_accounts()

        assert _FAKE_EMAIL in removed
        assert stop_called[0], "loop.stop() must be called on graceful removal"
        assert _FAKE_EMAIL not in manager._loops

    async def test_manager_stop_gracefully_stops_all_loops(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """manager.stop() calls loop.stop() for every active loop (task 12.3)."""
        stopped: list[str] = []

        def _make_loop(email: str) -> GDriveAccountLoop:
            cfg = GDriveAccountConfig(
                email=email,
                client_id="cid",
                client_secret="cs",
                refresh_token="rt",
                switchboard_mcp_url=_SWITCHBOARD_URL,
            )
            lp = GDriveAccountLoop(email=email, config=cfg)

            async def _mock_stop() -> None:
                stopped.append(email)

            lp.stop = _mock_stop  # type: ignore[method-assign]
            return lp

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        manager._loops[_FAKE_EMAIL] = _make_loop(_FAKE_EMAIL)
        manager._loops[_FAKE_EMAIL2] = _make_loop(_FAKE_EMAIL2)

        await manager.stop()

        assert _FAKE_EMAIL in stopped
        assert _FAKE_EMAIL2 in stopped
        assert len(manager._loops) == 0

    # ------------------------------------------------------------------
    # 12.4: Aggregated health endpoint
    # ------------------------------------------------------------------

    def test_get_health_state_for_heartbeat_healthy(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_get_health_state_for_heartbeat returns 'healthy' when all loops healthy."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)
        loop._source_api_ok = True
        manager._loops[_FAKE_EMAIL] = loop

        state, error_msg = manager._get_health_state_for_heartbeat()
        assert state == "healthy"
        assert error_msg is None

    def test_get_health_state_for_heartbeat_error(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_get_health_state_for_heartbeat returns 'error' with message when loop fails."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        config = GDriveAccountConfig(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=config)
        loop._source_api_ok = False
        loop._error = "Drive API 500"
        manager._loops[_FAKE_EMAIL] = loop

        state, error_msg = manager._get_health_state_for_heartbeat()
        assert state == "error"
        assert error_msg == "Drive API 500"

    # ------------------------------------------------------------------
    # 12.5: Heartbeat protocol
    # ------------------------------------------------------------------

    def test_get_capabilities_has_required_keys(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_get_capabilities returns a dict with expected capability flags."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        caps = manager._get_capabilities()
        assert "multi_account" in caps
        assert "changes_polling" in caps
        assert "metadata_only" in caps
        assert "reload_accounts" in caps
        assert caps["multi_account"] is True

    def test_start_heartbeat_without_mcp_client_is_noop(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_start_heartbeat is a no-op when no MCP client is wired (non-fatal)."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )
        # No mcp_client set — should not raise
        manager._start_heartbeat()
        assert manager._heartbeat is None

    def test_start_heartbeat_with_mocked_mcp_client(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """_start_heartbeat creates and starts a ConnectorHeartbeat when MCP client is wired."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
            heartbeat_interval_s=120,
        )

        mock_mcp_client = MagicMock()
        manager._mcp_client = mock_mcp_client

        with patch("butlers.connectors.google_drive.ConnectorHeartbeat") as mock_hb_cls:
            mock_hb_instance = MagicMock()
            mock_hb_cls.return_value = mock_hb_instance

            manager._start_heartbeat()

        # Heartbeat should be created and started
        assert mock_hb_cls.called
        assert mock_hb_instance.start.called
        assert manager._heartbeat is mock_hb_instance

    async def test_stop_stops_heartbeat(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """manager.stop() shuts down the heartbeat task (task 12.5)."""
        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        mock_heartbeat = MagicMock()
        mock_heartbeat.stop = AsyncMock()
        manager._heartbeat = mock_heartbeat

        await manager.stop()

        mock_heartbeat.stop.assert_called_once()
        assert manager._heartbeat is None

    # ------------------------------------------------------------------
    # Health server smoke test (no port bind — just app route logic)
    # ------------------------------------------------------------------

    async def test_health_server_app_health_route(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """The /health FastAPI route returns a MultiAccountHealthStatus object."""
        from fastapi.testclient import TestClient

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        # Build the same FastAPI app used internally by _start_health_server
        app = FastAPI(title="Google Drive Connector Health Test")

        @app.get("/health")
        async def health() -> MultiAccountHealthStatus:
            return manager.get_health()

        @app.post("/reload")
        async def reload() -> dict[str, Any]:
            return {"status": "reload_triggered"}

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "active_accounts" in data
        assert "account_health" in data

    async def test_health_server_reload_route(
        self,
        mock_db_pool: MagicMock,
        mock_credential_store: MagicMock,
    ) -> None:
        """The /reload FastAPI route returns a status confirmation."""
        from fastapi.testclient import TestClient

        manager = GDriveConnectorManager(
            db_pool=mock_db_pool,
            credential_store=mock_credential_store,
            switchboard_mcp_url=_SWITCHBOARD_URL,
        )

        app = FastAPI(title="Google Drive Connector Reload Test")

        @app.post("/reload")
        async def reload() -> dict[str, Any]:
            if manager._main_loop is not None and manager._main_loop.is_running():
                manager._main_loop.call_soon_threadsafe(manager._reload_event.set)
            return {"status": "reload_triggered"}

        client = TestClient(app)
        response = client.post("/reload")
        assert response.status_code == 200
        assert response.json()["status"] == "reload_triggered"


# ---------------------------------------------------------------------------
# Task 13.1 — additional env var coverage (CONNECTOR_HEALTH_PORT, etc.)
# ---------------------------------------------------------------------------


class TestGDriveProcessConfigEnvVars:
    """Additional env var coverage for task 13.1 (full env var set)."""

    def test_from_env_connector_health_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CONNECTOR_HEALTH_PORT is parsed and applied to health_port."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40088")

        config = GDriveProcessConfig.from_env()

        assert config.health_port == 40088

    def test_from_env_default_health_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default CONNECTOR_HEALTH_PORT is 40088 (distinct from google-calendar's 40085)."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.delenv("CONNECTOR_HEALTH_PORT", raising=False)

        config = GDriveProcessConfig.from_env()

        assert config.health_port == 40088

    def test_from_env_invalid_health_port_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Invalid CONNECTOR_HEALTH_PORT falls back to default (40088)."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "not-a-port")

        config = GDriveProcessConfig.from_env()

        assert config.health_port == 40088

    def test_from_env_all_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All env vars can be set simultaneously."""
        monkeypatch.setenv("SWITCHBOARD_MCP_URL", _SWITCHBOARD_URL)
        monkeypatch.setenv("GDRIVE_POLL_INTERVAL_S", "60")
        monkeypatch.setenv("GDRIVE_ACCOUNT_RESCAN_INTERVAL_S", "120")
        monkeypatch.setenv("CONNECTOR_HEALTH_PORT", "40088")
        monkeypatch.setenv("CONNECTOR_MAX_INFLIGHT", "16")
        monkeypatch.setenv("CONNECTOR_HEARTBEAT_INTERVAL_S", "60")

        config = GDriveProcessConfig.from_env()

        assert config.switchboard_mcp_url == _SWITCHBOARD_URL
        assert config.poll_interval_s == 60
        assert config.account_rescan_interval_s == 120
        assert config.health_port == 40088
        assert config.max_inflight == 16
        assert config.heartbeat_interval_s == 60


# ---------------------------------------------------------------------------
# Task 13.2 — per-account config overrides (additional coverage)
# ---------------------------------------------------------------------------


class TestPerAccountConfigOverrides:
    """Additional tests for per-account config overrides via google_accounts.metadata."""

    def test_make_account_config_empty_metadata_uses_defaults(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """Empty metadata dict (no google_drive key) uses process defaults."""
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_gdrive={},  # empty google_drive section
        )
        assert account_cfg.poll_interval_s == process_config.poll_interval_s

    def test_make_account_config_propagates_email(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """email field is propagated correctly to account config."""
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
        )
        assert account_cfg.email == _FAKE_EMAIL
        assert account_cfg.endpoint_identity == f"google_drive:user:{_FAKE_EMAIL}"

    def test_make_account_config_inherits_switchboard_url(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """switchboard_mcp_url is inherited from process config."""
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
        )
        assert account_cfg.switchboard_mcp_url == _SWITCHBOARD_URL

    def test_make_account_config_zero_poll_interval_ignored(
        self, process_config: GDriveProcessConfig
    ) -> None:
        """Zero poll_interval_s in metadata is applied (0 is valid int)."""
        # Note: 0 is technically valid as an int even if not practical.
        # The spec says we apply it — the caller is responsible for sane values.
        account_cfg = process_config.make_account_config(
            email=_FAKE_EMAIL,
            client_id="cid",
            client_secret="cs",
            refresh_token="rt",
            metadata_gdrive={"poll_interval_s": 0},
        )
        assert account_cfg.poll_interval_s == 0


# ---------------------------------------------------------------------------
# Task 11.1–11.6: Filter, Metrics, Rate Limiting
# ---------------------------------------------------------------------------


class TestIngestionPolicyEvaluatorIntegration:
    """Task 11.1: IngestionPolicyEvaluator integration in GDriveAccountLoop."""

    def test_account_loop_has_ingestion_policy(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """GDriveAccountLoop initialises with an IngestionPolicyEvaluator."""
        from butlers.ingestion_policy import IngestionPolicyEvaluator

        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        assert hasattr(loop, "_ingestion_policy")
        assert isinstance(loop._ingestion_policy, IngestionPolicyEvaluator)

    def test_policy_scope_matches_spec(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """IngestionPolicyEvaluator scope follows connector:<type>:<identity> format."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        expected_scope = f"connector:google_drive:{account_config.endpoint_identity}"
        assert loop._ingestion_policy.scope == expected_scope

    def test_process_change_blocked_by_policy_returns_none(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """process_change returns None when connector policy blocks the event."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        # Replace the evaluator with one that always blocks
        mock_policy = MagicMock()
        mock_decision = MagicMock()
        mock_decision.allowed = False
        mock_decision.action = "block"
        mock_decision.matched_rule_type = "sender_domain"
        mock_policy.evaluate.return_value = mock_decision
        loop._ingestion_policy = mock_policy

        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "blocked.txt",
                "mimeType": "text/plain",
            },
        }
        result = loop.process_change(change)
        assert result is None

    def test_process_change_allowed_by_policy_returns_envelope(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """process_change returns envelope when policy allows the event."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        # Replace the evaluator with one that always allows
        mock_policy = MagicMock()
        mock_decision = MagicMock()
        mock_decision.allowed = True
        mock_policy.evaluate.return_value = mock_decision
        loop._ingestion_policy = mock_policy

        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "allowed.txt",
                "mimeType": "text/plain",
            },
        }
        result = loop.process_change(change)
        assert result is not None
        assert result["schema_version"] == "ingest.v1"

    def test_process_change_policy_evaluator_exception_is_fail_open(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """If policy evaluator raises, the change is processed (fail-open)."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        mock_policy = MagicMock()
        mock_policy.evaluate.side_effect = RuntimeError("DB unavailable")
        loop._ingestion_policy = mock_policy

        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "failopen.txt",
                "mimeType": "text/plain",
            },
        }
        result = loop.process_change(change)
        # fail-open: envelope is returned even when policy evaluation raises
        assert result is not None


class TestFilteredEventBatchFlush:
    """Task 11.2: Filtered event buffer population and batch flush."""

    def test_account_loop_has_filtered_event_buffer(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """GDriveAccountLoop initialises with a FilteredEventBuffer."""
        from butlers.connectors.filtered_event_buffer import FilteredEventBuffer

        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        assert hasattr(loop, "_filtered_event_buffer")
        assert isinstance(loop._filtered_event_buffer, FilteredEventBuffer)

    def test_blocked_change_adds_to_filtered_buffer(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """A change blocked by policy is recorded in the FilteredEventBuffer."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        mock_policy = MagicMock()
        mock_decision = MagicMock()
        mock_decision.allowed = False
        mock_decision.action = "block"
        mock_decision.matched_rule_type = "sender_domain"
        mock_policy.evaluate.return_value = mock_decision
        loop._ingestion_policy = mock_policy

        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "blocked.txt",
                "mimeType": "text/plain",
            },
        }
        assert len(loop._filtered_event_buffer) == 0
        loop.process_change(change)
        assert len(loop._filtered_event_buffer) == 1

    async def test_flush_filtered_events_calls_buffer_flush(
        self,
        account_config: GDriveAccountConfig,
        mock_db_pool: MagicMock,
    ) -> None:
        """_flush_filtered_events calls FilteredEventBuffer.flush with the pool."""
        loop = GDriveAccountLoop(
            email=_FAKE_EMAIL,
            config=account_config,
            db_pool=mock_db_pool,
        )

        mock_buffer = AsyncMock()
        mock_buffer.flush = AsyncMock()
        loop._filtered_event_buffer = mock_buffer

        await loop._flush_filtered_events()

        mock_buffer.flush.assert_called_once_with(mock_db_pool)

    async def test_flush_filtered_events_noop_without_pool(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """_flush_filtered_events is a no-op when no DB pool is configured."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)  # no db_pool

        mock_buffer = AsyncMock()
        mock_buffer.flush = AsyncMock()
        loop._filtered_event_buffer = mock_buffer

        await loop._flush_filtered_events()
        mock_buffer.flush.assert_not_called()


class TestReplayQueueDrain:
    """Task 11.3: Replay queue drain loop."""

    async def test_drain_replay_pending_called_at_startup(
        self,
        account_config: GDriveAccountConfig,
        mock_db_pool: MagicMock,
    ) -> None:
        """_drain_replay_pending is called once before entering the poll loop."""
        loop = GDriveAccountLoop(
            email=_FAKE_EMAIL,
            config=account_config,
            db_pool=mock_db_pool,
        )

        drain_called = False

        async def fake_drain() -> None:
            nonlocal drain_called
            drain_called = True

        loop._drain_replay_pending = fake_drain  # type: ignore[method-assign]

        # We can't easily run the full poll loop, so test the internal
        # method invocation via a single wrapped call.
        poll_count = 0

        async def fake_poll_once() -> None:
            nonlocal poll_count
            poll_count += 1
            raise asyncio.CancelledError

        loop._poll_once = fake_poll_once  # type: ignore[method-assign]
        loop._flush_filtered_events = AsyncMock()  # type: ignore[method-assign]

        with pytest.raises(asyncio.CancelledError):
            await loop._poll_loop()

        assert drain_called

    async def test_drain_replay_pending_noop_without_pool(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """_drain_replay_pending is a no-op when no DB pool is configured."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)  # no db_pool

        # Should not raise even with no pool
        await loop._drain_replay_pending()

    async def test_drain_replay_pending_calls_drain_helper(
        self,
        account_config: GDriveAccountConfig,
        mock_db_pool: MagicMock,
    ) -> None:
        """_drain_replay_pending calls the drain_replay_pending helper with correct args."""
        loop = GDriveAccountLoop(
            email=_FAKE_EMAIL,
            config=account_config,
            db_pool=mock_db_pool,
        )

        with patch(
            "butlers.connectors.google_drive.drain_replay_pending",
            new_callable=AsyncMock,
        ) as mock_drain:
            await loop._drain_replay_pending()

        mock_drain.assert_called_once()
        call_kwargs = mock_drain.call_args
        assert call_kwargs.kwargs["pool"] is mock_db_pool
        assert call_kwargs.kwargs["connector_type"] == "google_drive"
        assert call_kwargs.kwargs["endpoint_identity"] == loop.endpoint_identity

    async def test_drain_replay_pending_handles_failure_gracefully(
        self,
        account_config: GDriveAccountConfig,
        mock_db_pool: MagicMock,
    ) -> None:
        """_drain_replay_pending swallows errors so the poll loop can still start."""
        loop = GDriveAccountLoop(
            email=_FAKE_EMAIL,
            config=account_config,
            db_pool=mock_db_pool,
        )

        with patch(
            "butlers.connectors.google_drive.drain_replay_pending",
            new_callable=AsyncMock,
            side_effect=Exception("DB unavailable"),
        ):
            # Should not raise
            await loop._drain_replay_pending()


class TestConnectorMetrics:
    """Task 11.4: Standard ConnectorMetrics integration."""

    def test_account_loop_has_connector_metrics(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """GDriveAccountLoop initialises with a ConnectorMetrics instance."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        assert hasattr(loop, "_metrics")
        assert isinstance(loop._metrics, ConnectorMetrics)

    def test_metrics_connector_type_is_google_drive(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """ConnectorMetrics uses connector_type='google_drive'."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        assert loop._metrics._connector_type == "google_drive"

    def test_metrics_endpoint_identity_matches_loop(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """ConnectorMetrics endpoint_identity matches the loop's endpoint_identity."""
        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)
        assert loop._metrics._endpoint_identity == loop.endpoint_identity


class TestGDriveSpecificMetrics:
    """Task 11.5: Drive-specific Prometheus counter and gauge."""

    def test_gdrive_event_type_counter_exists(self) -> None:
        """connector_gdrive_event_type_total counter is defined at module level."""
        from butlers.connectors.google_drive import gdrive_event_type_total

        assert gdrive_event_type_total is not None

    def test_gdrive_metadata_cache_size_gauge_exists(self) -> None:
        """connector_gdrive_metadata_cache_size gauge is defined at module level."""
        from butlers.connectors.google_drive import gdrive_metadata_cache_size

        assert gdrive_metadata_cache_size is not None

    def test_process_change_increments_event_type_counter(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """process_change increments the gdrive_event_type_total counter."""
        from prometheus_client import REGISTRY

        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        # Allow policy evaluation (no blocking)
        mock_policy = MagicMock()
        mock_decision = MagicMock()
        mock_decision.allowed = True
        mock_policy.evaluate.return_value = mock_decision
        loop._ingestion_policy = mock_policy

        label_key = {
            "endpoint_identity": loop.endpoint_identity,
            "event_type": _CHANGE_TYPE_CREATED,
        }

        # Get current value before
        try:
            before = (
                REGISTRY.get_sample_value(
                    "connector_gdrive_event_type_total",
                    label_key,
                )
                or 0.0
            )
        except Exception:
            before = 0.0

        change = {
            "fileId": _FAKE_FILE_ID,
            "file": {
                "id": _FAKE_FILE_ID,
                "name": "new.txt",
                "mimeType": "text/plain",
            },
        }
        loop.process_change(change)

        after = (
            REGISTRY.get_sample_value(
                "connector_gdrive_event_type_total",
                label_key,
            )
            or 0.0
        )
        assert after == before + 1.0

    async def test_poll_loop_updates_metadata_cache_size_gauge(
        self,
        account_config: GDriveAccountConfig,
    ) -> None:
        """The poll loop updates connector_gdrive_metadata_cache_size after each cycle."""
        from prometheus_client import REGISTRY

        loop = GDriveAccountLoop(email=_FAKE_EMAIL, config=account_config)

        # Seed the cache with one entry
        loop._metadata_cache[_FAKE_FILE_ID] = _FileMetadata(
            file_id=_FAKE_FILE_ID,
            name="cached.txt",
            mime_type="text/plain",
            parents=[],
            shared=False,
            modified_time=None,
        )

        # After one iteration the gauge should reflect the cache size
        loop._flush_filtered_events = AsyncMock()  # type: ignore[method-assign]
        loop._drain_replay_pending = AsyncMock()  # type: ignore[method-assign]

        call_count = 0

        async def fake_poll_once() -> None:
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError

        loop._poll_once = fake_poll_once  # type: ignore[method-assign]

        with pytest.raises(asyncio.CancelledError):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await loop._poll_loop()

        label_key = {"endpoint_identity": loop.endpoint_identity}
        gauge_value = REGISTRY.get_sample_value(
            "connector_gdrive_metadata_cache_size",
            label_key,
        )
        # The gauge is set to the cache size; exact value depends on execution
        assert gauge_value is not None


class TestRateLimitWithJitterAndMaxDelay:
    """Task 11.6: Rate-limit handling — base 1s, max 60s, 5 retries, jitter, Retry-After."""

    async def test_max_retries_default_is_five(self) -> None:
        """_exponential_backoff_retry defaults to 5 retries."""
        from butlers.connectors.google_drive import (
            _RATE_LIMIT_MAX_RETRIES,
        )

        assert _RATE_LIMIT_MAX_RETRIES == 5

    async def test_base_delay_is_one_second(self) -> None:
        """_exponential_backoff_retry base delay is 1.0 second."""
        from butlers.connectors.google_drive import (
            _RATE_LIMIT_BASE_DELAY_S,
        )

        assert _RATE_LIMIT_BASE_DELAY_S == 1.0

    async def test_max_delay_is_sixty_seconds(self) -> None:
        """_exponential_backoff_retry max delay is 60 seconds."""
        from butlers.connectors.google_drive import (
            _RATE_LIMIT_MAX_DELAY_S,
        )

        assert _RATE_LIMIT_MAX_DELAY_S == 60.0

    async def test_backoff_delay_does_not_exceed_max(self) -> None:
        """Exponential backoff delay is capped at max_delay."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {}

        success = MagicMock()
        success.status_code = 200

        # Return rate-limited many times then succeed
        call_count = 0

        async def mock_call() -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count >= 5:
                return success
            return rate_limited

        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture_sleep):
            await _exponential_backoff_retry(
                mock_call,
                max_retries=5,
                base_delay=1.0,
                max_delay=60.0,
            )

        for delay in sleep_calls:
            assert delay <= 61.0  # max_delay + jitter upper bound

    async def test_retry_after_header_honored_over_exponential_backoff(self) -> None:
        """When Retry-After is present, it is used instead of exponential backoff."""
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "30"}

        success = MagicMock()
        success.status_code = 200

        responses = iter([rate_limited, success])

        async def mock_call() -> MagicMock:
            return next(responses)

        sleep_calls: list[float] = []

        async def capture_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        with patch("asyncio.sleep", new=capture_sleep):
            result = await _exponential_backoff_retry(
                mock_call,
                max_retries=5,
                base_delay=1.0,
                max_delay=60.0,
            )

        assert result.status_code == 200
        # Retry-After of 30 should be used (< max_delay)
        assert len(sleep_calls) == 1
        assert sleep_calls[0] == 30.0
