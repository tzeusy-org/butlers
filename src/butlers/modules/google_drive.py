"""Google Drive module — MCP tools for reading, writing, and managing Drive files.

Provides 7 MCP tools for butler interactions with Google Drive:
- drive_list_files: List files in a folder or matching a query
- drive_get_file_metadata: Get detailed metadata for a single file
- drive_read_file: Download text-representable file content
- drive_write_file: Create or upload a file to Drive
- drive_create_folder: Create a folder in Drive
- drive_move_file: Move a file to a different folder
- drive_search_files: Full-text search across Drive

Butler outputs are organized under a `butlers/` root folder with per-butler
subfolders. Write tools auto-ensure the hierarchy.

Credentials are resolved via ``resolve_google_credentials`` at startup. The
module requires the full ``drive`` scope (not ``drive.readonly``) since it
writes files.

Configured via ``[modules.google_drive]`` in ``butler.toml``.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from butlers.google_credentials import (
    MissingGoogleCredentialsError,
    resolve_google_credentials,
)
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_DEFAULT_TIMEOUT_S = 30.0

# Drive scope required for full read/write access
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_DRIVE_SCOPE_SHORT = "drive"

# Rate-limit retry config (spec §3.5)
_RATE_LIMIT_STATUS_CODES = {403, 429, 503}
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY_S = 1.0

# Google Workspace MIME type -> export MIME type mapping
_GDOC_MIME = "application/vnd.google-apps.document"
_GSHEET_MIME = "application/vnd.google-apps.spreadsheet"
_GSLIDES_MIME = "application/vnd.google-apps.presentation"
_GOOGLE_EXPORT_MAP: dict[str, str] = {
    _GDOC_MIME: "text/plain",
    _GSHEET_MIME: "text/csv",
    _GSLIDES_MIME: "text/plain",
}

# Extension to MIME type inference (spec §5.4)
_EXT_MIME_MAP: dict[str, str] = {
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}

# File metadata fields for list/search
_LIST_FIELDS = "files(id,name,mimeType,modifiedTime,size,parents,shared,owners)"
_SEARCH_FIELDS = "files(id,name,mimeType,modifiedTime,size,parents,shared,owners,webViewLink)"
_META_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,parents,"
    "shared,sharingUser,owners,webViewLink,description"
)

# Credential redaction pattern
_CRED_REDACT_RE = re.compile(r"(client_secret|refresh_token|access_token)=[^\s&]+", re.IGNORECASE)


def _redact_creds(msg: str) -> str:
    """Redact OAuth credential values from error messages."""
    return _CRED_REDACT_RE.sub(r"\1=<REDACTED>", msg)


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------


class GoogleDriveConfig(BaseModel):
    """Configuration for the Google Drive module.

    Fields:
        account: Optional Google account email. If omitted, the primary
            Google account is used.
        max_read_size_bytes: Maximum file size for ``drive_read_file``
            (default 10 MB).
        butler_folder_name: Root folder name for butler outputs
            (default ``"butlers"``).
    """

    model_config = ConfigDict(extra="forbid")

    account: str | None = Field(default=None)
    max_read_size_bytes: int = Field(default=10_485_760)
    butler_folder_name: str = Field(default="butlers")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class GoogleDriveStartupError(RuntimeError):
    """Raised when the Google Drive module fails to start."""


class GoogleDriveAPIError(RuntimeError):
    """Raised when a Drive API call fails after retries."""

    def __init__(self, *, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"Drive API error {status_code}: {message}")


# ---------------------------------------------------------------------------
# HTTP client with token refresh and retry
# ---------------------------------------------------------------------------


class _DriveHTTPClient:
    """HTTP client for Google Drive API with token refresh and rate-limit retry.

    Wraps an httpx.AsyncClient. Tokens are refreshed lazily when the
    cached token is missing or within the early-expiry margin.
    """

    _EARLY_EXPIRY_MARGIN_S = 60

    def __init__(
        self,
        credentials: Any,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credentials = credentials
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._http_client = http_client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S)

    async def close(self) -> None:
        await self._http_client.aclose()

    async def _refresh_token(self) -> None:
        """Exchange refresh token for a new access token."""
        import time

        payload = {
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
            "refresh_token": self._credentials.refresh_token,
            "grant_type": "refresh_token",
        }
        try:
            resp = await self._http_client.post(_GOOGLE_TOKEN_URL, data=payload)
        except httpx.HTTPError as exc:
            raise GoogleDriveAPIError(status_code=0, message=_redact_creds(str(exc))) from exc

        if resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=_redact_creds(f"Token refresh failed: {resp.text[:200]}"),
            )

        data = resp.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.monotonic() + expires_in - self._EARLY_EXPIRY_MARGIN_S
        logger.debug("Drive token refreshed, expires in %ds", expires_in)

    async def _get_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        import time

        if not self._access_token or time.monotonic() >= self._token_expires_at:
            await self._refresh_token()
        return self._access_token  # type: ignore[return-value]

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make an authenticated request with rate-limit retry."""
        import asyncio

        token = await self._get_token()
        auth_headers = self._auth_headers(token)
        if headers:
            auth_headers.update(headers)

        last_exc: Exception | None = None
        delay = _RATE_LIMIT_BASE_DELAY_S
        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            try:
                resp = await self._http_client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    content=content,
                    headers=auth_headers,
                )
            except httpx.HTTPError as exc:
                raise GoogleDriveAPIError(status_code=0, message=_redact_creds(str(exc))) from exc

            if resp.status_code not in _RATE_LIMIT_STATUS_CODES:
                return resp

            last_exc = GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"Rate limited (attempt {attempt + 1})",
            )
            if attempt < _RATE_LIMIT_MAX_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                sleep_for = float(retry_after) if retry_after else delay
                logger.warning(
                    "Drive rate-limited (%d), retrying in %.1fs (attempt %d/%d)",
                    resp.status_code,
                    sleep_for,
                    attempt + 1,
                    _RATE_LIMIT_MAX_RETRIES,
                )
                await asyncio.sleep(sleep_for)
                delay *= 2

        raise last_exc  # type: ignore[misc]

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("GET", f"{_DRIVE_API_BASE}/{path}", **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("POST", f"{_DRIVE_API_BASE}/{path}", **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("PATCH", f"{_DRIVE_API_BASE}/{path}", **kwargs)


# ---------------------------------------------------------------------------
# Butler folder helpers
# ---------------------------------------------------------------------------


def _infer_mime_type(filename: str) -> str:
    """Infer MIME type from file extension."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _EXT_MIME_MAP.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


# ---------------------------------------------------------------------------
# Module implementation
# ---------------------------------------------------------------------------


class GoogleDriveModule(Module):
    """Google Drive module providing 7 MCP tools for file management.

    Credentials are resolved from DB at startup via resolve_google_credentials.
    All tools that write files auto-ensure the butlers/{butler_name}/ folder
    hierarchy in the user's Drive.
    """

    def __init__(self) -> None:
        self._config: GoogleDriveConfig = GoogleDriveConfig()
        self._client: _DriveHTTPClient | None = None
        self._butler_name: str = "butler"
        # Cache: (butler_name, account_email) -> folder_id
        self._folder_cache: dict[tuple[str, str], str] = {}
        self._account_email: str | None = None
        self._db: Any = None

    # ------------------------------------------------------------------
    # Module ABC properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "google_drive"

    @property
    def config_schema(self) -> type[BaseModel]:
        return GoogleDriveConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return "google_drive"

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {
            "drive_write_file": ToolMeta(arg_sensitivities={"content": True}),
            "drive_move_file": ToolMeta(arg_sensitivities={"file_id": True, "new_parent_id": True}),
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Resolve credentials and initialise Drive HTTP client."""
        if isinstance(config, dict):
            config = GoogleDriveConfig(**config)
        self._config = config
        self._db = db

        # Derive butler name from the DB schema (same pattern as MetricsModule).
        schema: str | None = getattr(db, "schema", None)
        if schema:
            self._butler_name = schema

        if credential_store is None:
            raise GoogleDriveStartupError(
                "Google Drive module requires a credential_store. "
                "Ensure the butler daemon is configured with a CredentialStore."
            )

        pool = getattr(db, "pool", None)
        try:
            creds = await resolve_google_credentials(
                credential_store,
                pool=pool,
                caller="google_drive",
                account=config.account,
            )
        except MissingGoogleCredentialsError as exc:
            raise GoogleDriveStartupError(
                f"Google Drive startup failed: {exc}. "
                "Re-authorize at /api/oauth/google/start with the 'drive' scope."
            ) from exc

        # Validate that 'drive' scope is present (not just drive.readonly)
        granted = creds.scope or ""
        scope_words = set(granted.replace(",", " ").split())
        has_drive = _DRIVE_SCOPE in scope_words or _DRIVE_SCOPE_SHORT in scope_words
        has_readonly_only = not has_drive and (
            "drive.readonly" in scope_words
            or "https://www.googleapis.com/auth/drive.readonly" in scope_words
        )
        if has_readonly_only and not has_drive:
            raise GoogleDriveStartupError(
                "Google Drive module requires the 'drive' scope for write access. "
                "Re-authorize at /api/oauth/google/start?force_consent=true with 'drive' scope."
            )

        self._credentials = creds
        self._client = _DriveHTTPClient(creds)
        logger.info("Google Drive module started (account=%s)", config.account or "primary")

    async def on_shutdown(self) -> None:
        """Close HTTP client."""
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Butler folder hierarchy (spec §4)
    # ------------------------------------------------------------------

    async def _ensure_butler_folder(self, butler_name: str) -> str:
        """Ensure butlers/{butler_name}/ folder exists and return its ID.

        Uses a two-level hierarchy:
          1. Root folder named by ``config.butler_folder_name`` (default "butlers")
          2. Per-butler subfolder named ``butler_name``

        Results are cached in ``google_drive_butler_folders`` table.
        Falls back to creating when cached folder is gone (deleted).
        """
        assert self._client is not None, "Module not started"

        account_email = self._account_email or (self._config.account or "primary")
        cache_key = (butler_name, account_email)

        # Check DB cache first
        cached_id = await self._get_cached_folder(butler_name, account_email)
        if cached_id:
            # Verify folder still exists
            resp = await self._client.get(
                f"files/{cached_id}",
                params={"fields": "id,trashed"},
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("trashed", False):
                    self._folder_cache[cache_key] = cached_id
                    return cached_id
            # Folder gone — fall through to re-creation
            logger.info("Cached Drive folder %s not found, recreating", cached_id)

        # In-memory cache check
        if cache_key in self._folder_cache:
            folder_id = self._folder_cache[cache_key]
            resp = await self._client.get(
                f"files/{folder_id}",
                params={"fields": "id,trashed"},
            )
            if resp.status_code == 200 and not resp.json().get("trashed", False):
                return folder_id
            del self._folder_cache[cache_key]

        # Create root butler folder at Drive root
        root_id = await self._find_or_create_folder(
            name=self._config.butler_folder_name,
            parent_id=None,
        )
        # Create per-butler subfolder
        folder_id = await self._find_or_create_folder(
            name=butler_name,
            parent_id=root_id,
        )
        folder_path = f"{self._config.butler_folder_name}/{butler_name}"

        # Cache in DB
        await self._cache_folder(butler_name, account_email, folder_id, folder_path)
        self._folder_cache[cache_key] = folder_id
        return folder_id

    async def _find_or_create_folder(
        self,
        name: str,
        parent_id: str | None,
    ) -> str:
        """Find an existing folder by name+parent or create it."""
        assert self._client is not None

        # Search for existing folder
        escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
        parent_filter = f"'{parent_id}' in parents" if parent_id else "'root' in parents"
        q = (
            f"name='{escaped_name}' and mimeType='{_FOLDER_MIME_TYPE}' "
            f"and {parent_filter} and trashed=false"
        )

        resp = await self._client.get("files", params={"q": q, "fields": "files(id,name)"})
        if resp.status_code == 200:
            files = resp.json().get("files", [])
            if files:
                return files[0]["id"]

        # Create folder
        body: dict[str, Any] = {
            "name": name,
            "mimeType": _FOLDER_MIME_TYPE,
        }
        if parent_id:
            body["parents"] = [parent_id]

        resp = await self._client.post(
            "files",
            json=body,
            params={"fields": "id"},
        )
        if resp.status_code not in (200, 201):
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"Failed to create folder '{name}': {resp.text[:200]}",
            )
        return resp.json()["id"]

    async def _get_cached_folder(self, butler_name: str, account_email: str) -> str | None:
        """Look up a cached folder ID from the DB."""
        if self._db is None:
            return None
        try:
            pool = getattr(self._db, "pool", None)
            if pool is None:
                return None
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT folder_id FROM google_drive_butler_folders "
                    "WHERE butler_name = $1 AND account_email = $2",
                    butler_name,
                    account_email,
                )
                return row["folder_id"] if row else None
        except Exception:
            return None

    async def _cache_folder(
        self,
        butler_name: str,
        account_email: str,
        folder_id: str,
        folder_path: str,
    ) -> None:
        """Persist a folder ID to the DB cache."""
        if self._db is None:
            return
        try:
            pool = getattr(self._db, "pool", None)
            if pool is None:
                return
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO google_drive_butler_folders "
                    "(butler_name, account_email, folder_id, folder_path) "
                    "VALUES ($1, $2, $3, $4) "
                    "ON CONFLICT (butler_name, account_email) DO UPDATE "
                    "SET folder_id = EXCLUDED.folder_id, folder_path = EXCLUDED.folder_path",
                    butler_name,
                    account_email,
                    folder_id,
                    folder_path,
                )
        except Exception:
            logger.warning("Failed to cache butler folder in DB", exc_info=True)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all 7 Google Drive MCP tools."""
        if isinstance(config, dict):
            config = GoogleDriveConfig(**config) if config else GoogleDriveConfig()
        self._config = config
        self._db = db

        module = self

        @mcp.tool(name="drive_list_files")
        async def drive_list_files(
            folder_id: str | None = None,
            query: str | None = None,
        ) -> dict[str, Any]:
            """List files in a Drive folder or matching a query.

            Args:
                folder_id: Folder to list. Defaults to My Drive root.
                query: Additional Drive query filter (e.g. ``name contains 'report'``).

            Returns:
                ``{"files": [...], "total": N, "truncated": bool}``
            """
            return await module._drive_list_files(folder_id=folder_id, query=query)

        @mcp.tool(name="drive_get_file_metadata")
        async def drive_get_file_metadata(file_id: str) -> dict[str, Any]:
            """Get detailed metadata for a single Drive file.

            Args:
                file_id: The Drive file ID.

            Returns:
                Metadata dict, or ``{"status": "not_found", "file": null}``.
            """
            return await module._drive_get_file_metadata(file_id=file_id)

        @mcp.tool(name="drive_read_file")
        async def drive_read_file(file_id: str) -> dict[str, Any]:
            """Download and return content of a text-representable file.

            Args:
                file_id: The Drive file ID.

            Returns:
                ``{"content": ..., "mime_type": ..., "name": ..., "size_bytes": N}``
                or an error dict for binary/oversized files.
            """
            return await module._drive_read_file(file_id=file_id)

        @mcp.tool(name="drive_write_file")
        async def drive_write_file(
            name: str,
            content: str,
            folder_id: str | None = None,
            mime_type: str | None = None,
        ) -> dict[str, Any]:
            """Create or upload a text file to Google Drive.

            Args:
                name: File name.
                content: File content (text).
                folder_id: Target folder ID. Defaults to butler's subfolder.
                mime_type: MIME type. Inferred from extension if omitted.

            Returns:
                ``{"file_id": ..., "name": ..., "folder": ..., "web_view_link": ...}``
            """
            return await module._drive_write_file(
                name=name, content=content, folder_id=folder_id, mime_type=mime_type
            )

        @mcp.tool(name="drive_create_folder")
        async def drive_create_folder(
            name: str,
            parent_id: str | None = None,
        ) -> dict[str, Any]:
            """Create a folder in Google Drive.

            Args:
                name: Folder name.
                parent_id: Parent folder ID. Defaults to butler's subfolder.

            Returns:
                ``{"folder_id": ..., "name": ..., "parent_path": ...}``
            """
            return await module._drive_create_folder(name=name, parent_id=parent_id)

        @mcp.tool(name="drive_move_file")
        async def drive_move_file(file_id: str, new_parent_id: str) -> dict[str, Any]:
            """Move a file to a different folder.

            Args:
                file_id: The Drive file ID to move.
                new_parent_id: Destination folder ID.

            Returns:
                ``{"file_id": ..., "name": ..., "new_parent_id": ...}``
                or ``{"status": "not_found", "error": "File not found"}``.
            """
            return await module._drive_move_file(file_id=file_id, new_parent_id=new_parent_id)

        @mcp.tool(name="drive_search_files")
        async def drive_search_files(
            query: str,
            limit: int | None = None,
        ) -> dict[str, Any]:
            """Full-text search across Google Drive.

            Args:
                query: Search terms (e.g. ``"tax return 2025"``).
                limit: Maximum number of results to return.

            Returns:
                ``{"files": [...], "total": N}``
            """
            return await module._drive_search_files(query=query, limit=limit)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _drive_list_files(
        self,
        folder_id: str | None,
        query: str | None,
    ) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        parent = folder_id or "root"
        base_q = f"'{parent}' in parents and trashed=false"
        full_q = f"{base_q} and {query}" if query else base_q

        files: list[dict[str, Any]] = []
        page_token: str | None = None
        truncated = False

        while True:
            params: dict[str, Any] = {
                "q": full_q,
                "fields": f"nextPageToken,{_LIST_FIELDS}",
                "pageSize": 100,
            }
            if page_token:
                params["pageToken"] = page_token

            resp = await self._client.get("files", params=params)
            if resp.status_code != 200:
                raise GoogleDriveAPIError(
                    status_code=resp.status_code,
                    message=f"files.list failed: {resp.text[:200]}",
                )

            data = resp.json()
            files.extend(data.get("files", []))
            page_token = data.get("nextPageToken")

            if len(files) >= 1000:
                truncated = bool(page_token)
                files = files[:1000]
                break

            if not page_token:
                break

        return {"files": files, "total": len(files), "truncated": truncated}

    async def _drive_get_file_metadata(self, file_id: str) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        resp = await self._client.get(
            f"files/{file_id}",
            params={"fields": _META_FIELDS},
        )
        if resp.status_code == 404:
            return {"status": "not_found", "file": None}
        if resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"files.get failed: {resp.text[:200]}",
            )
        return resp.json()

    async def _drive_read_file(self, file_id: str) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        # Fetch metadata first
        meta_resp = await self._client.get(
            f"files/{file_id}",
            params={"fields": "id,name,mimeType,size,webViewLink"},
        )
        if meta_resp.status_code == 404:
            return {"status": "not_found", "file": None}
        if meta_resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=meta_resp.status_code,
                message=f"files.get failed: {meta_resp.text[:200]}",
            )

        meta = meta_resp.json()
        name = meta.get("name", "")
        mime_type = meta.get("mimeType", "")
        size_bytes = int(meta.get("size", 0) or 0)
        web_view_link = meta.get("webViewLink", "")

        # Check for Google Workspace docs (no size field — exported)
        is_google_doc = mime_type in _GOOGLE_EXPORT_MAP

        # Size limit check (only for non-Google-Doc files)
        if not is_google_doc and size_bytes > self._config.max_read_size_bytes:
            return {
                "status": "too_large",
                "size_bytes": size_bytes,
                "max_bytes": self._config.max_read_size_bytes,
                "name": name,
            }

        # Binary file check (not text/* and not an exportable Google Doc)
        if not is_google_doc and not mime_type.startswith("text/"):
            return {
                "status": "binary_file",
                "mime_type": mime_type,
                "name": name,
                "size_bytes": size_bytes,
                "web_view_link": web_view_link,
            }

        # Download content
        if is_google_doc:
            export_mime = _GOOGLE_EXPORT_MAP[mime_type]
            content_resp = await self._client.get(
                f"files/{file_id}/export",
                params={"mimeType": export_mime},
            )
            result_mime = export_mime
        else:
            content_resp = await self._client.get(
                f"files/{file_id}",
                params={"alt": "media"},
            )
            result_mime = mime_type

        if content_resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=content_resp.status_code,
                message=f"File download failed: {content_resp.text[:200]}",
            )

        text_content = content_resp.text
        return {
            "content": text_content,
            "mime_type": result_mime,
            "name": name,
            "size_bytes": len(text_content.encode()),
        }

    async def _drive_write_file(
        self,
        name: str,
        content: str,
        folder_id: str | None,
        mime_type: str | None,
    ) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        # Resolve target folder
        if folder_id is None:
            folder_id = await self._ensure_butler_folder(self._butler_name)
            folder_path = f"{self._config.butler_folder_name}/{self._butler_name}"
        else:
            folder_path = folder_id

        # Infer MIME type from extension
        resolved_mime = mime_type or _infer_mime_type(name)

        # Multipart upload: metadata + content
        body = {
            "name": name,
            "parents": [folder_id],
            "mimeType": resolved_mime,
        }
        content_bytes = content.encode("utf-8")

        # Build multipart request manually
        boundary = "batch_boundary_gdrive_upload"
        multipart = (
            (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                + _json_dumps(body)
                + f"\r\n--{boundary}\r\n"
                f"Content-Type: {resolved_mime}\r\n\r\n"
            ).encode()
            + content_bytes
            + f"\r\n--{boundary}--".encode()
        )

        resp = await self._client.post(
            "files",
            params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
            content=multipart,
            headers={"Content-Type": f"multipart/related; boundary={boundary}"},
        )
        if resp.status_code not in (200, 201):
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"files.create failed: {resp.text[:200]}",
            )

        data = resp.json()
        return {
            "file_id": data.get("id"),
            "name": data.get("name", name),
            "folder": folder_path,
            "web_view_link": data.get("webViewLink", ""),
        }

    async def _drive_create_folder(
        self,
        name: str,
        parent_id: str | None,
    ) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        if parent_id is None:
            parent_id = await self._ensure_butler_folder(self._butler_name)
            parent_path = f"{self._config.butler_folder_name}/{self._butler_name}"
        else:
            parent_path = parent_id

        body = {
            "name": name,
            "mimeType": _FOLDER_MIME_TYPE,
            "parents": [parent_id],
        }
        resp = await self._client.post(
            "files",
            json=body,
            params={"fields": "id,name"},
        )
        if resp.status_code not in (200, 201):
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"Folder creation failed: {resp.text[:200]}",
            )

        data = resp.json()
        return {
            "folder_id": data.get("id"),
            "name": data.get("name", name),
            "parent_path": parent_path,
        }

    async def _drive_move_file(
        self,
        file_id: str,
        new_parent_id: str,
    ) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        # Fetch current parents
        meta_resp = await self._client.get(
            f"files/{file_id}",
            params={"fields": "id,name,parents"},
        )
        if meta_resp.status_code == 404:
            return {"status": "not_found", "error": "File not found"}
        if meta_resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=meta_resp.status_code,
                message=f"files.get failed: {meta_resp.text[:200]}",
            )

        meta = meta_resp.json()
        name = meta.get("name", "")
        old_parents = ",".join(meta.get("parents", []))

        resp = await self._client.patch(
            f"files/{file_id}",
            params={
                "addParents": new_parent_id,
                "removeParents": old_parents,
                "fields": "id,name,parents",
            },
            json={},
        )
        if resp.status_code == 404:
            return {"status": "not_found", "error": "File not found"}
        if resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"files.update failed: {resp.text[:200]}",
            )

        return {
            "file_id": file_id,
            "name": name,
            "new_parent_id": new_parent_id,
        }

    async def _drive_search_files(
        self,
        query: str,
        limit: int | None,
    ) -> dict[str, Any]:
        assert self._client is not None, "Module not started"

        escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")
        q = f"fullText contains '{escaped_query}' and trashed=false"
        params: dict[str, Any] = {
            "q": q,
            "fields": f"nextPageToken,{_SEARCH_FIELDS}",
            "pageSize": min(limit, 100) if limit else 100,
        }

        resp = await self._client.get("files", params=params)
        if resp.status_code != 200:
            raise GoogleDriveAPIError(
                status_code=resp.status_code,
                message=f"files.list (search) failed: {resp.text[:200]}",
            )

        data = resp.json()
        files = data.get("files", [])
        if limit is not None:
            files = files[:limit]

        return {"files": files, "total": len(files)}


# ---------------------------------------------------------------------------
# JSON helper
# ---------------------------------------------------------------------------


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj)
