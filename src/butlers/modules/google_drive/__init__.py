"""Google Drive module — MCP tools for reading, writing, and organizing Drive files.

Provides 7 MCP tools for butler interaction with Google Drive:
- drive_list_files: List files in a folder or by query
- drive_get_file_metadata: Retrieve detailed metadata for a file
- drive_read_file: Download and return text-representable file content
- drive_write_file: Upload a file to Drive (defaults to butler folder)
- drive_create_folder: Create a folder in Drive
- drive_move_file: Move a file to a different folder
- drive_search_files: Full-text search across Drive

Butler outputs are centralized under a ``butlers/{butler_name}/`` folder hierarchy.
Credentials are resolved via the existing Google OAuth infrastructure.

Configured via ``[modules.google_drive]`` in butler.toml.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import mimetypes
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from butlers.google_credentials import (
    MissingGoogleCredentialsError,
    _pool_acquire,
    resolve_google_credentials,
)
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"

_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_GDOC_MIME = "application/vnd.google-apps.document"
_GSHEET_MIME = "application/vnd.google-apps.spreadsheet"
_GSLIDES_MIME = "application/vnd.google-apps.presentation"

# Google Workspace MIME types and their text export formats
_GOOGLE_EXPORT_MAP: dict[str, str] = {
    _GDOC_MIME: "text/plain",
    _GSHEET_MIME: "text/csv",
    _GSLIDES_MIME: "text/plain",
}

# Rate-limit retry config (spec §3.5): retry on rate-limited 403, 429, and 503.
# NOTE: 403 can mean either rate-limited or permission-denied. Only retry when
# the error body contains a quota/rate-limit reason; permission denials must not
# be retried as they will never succeed.
_RATE_LIMIT_RETRY_STATUS_CODES = {429, 503}
_RATE_LIMIT_403_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BASE_DELAY_S = 1.0

# Token expiry safety margin (spec §3.4): refresh 60s before expiry
_TOKEN_EXPIRY_MARGIN_S = 60

# Extension to MIME type map for MIME inference (spec §5.4)
_EXT_MIME_MAP: dict[str, str] = {
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".md": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}

# File metadata fields for list/search/get
_LIST_FIELDS = "files(id,name,mimeType,modifiedTime,size,parents,shared,owners)"
_SEARCH_FIELDS = "files(id,name,mimeType,modifiedTime,size,parents,shared,owners,webViewLink)"
_META_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,parents,"
    "shared,sharingUser,owners,webViewLink,description"
)
_MAX_PAGE_RESULTS = 1000

# Credential redaction for safe error messages (spec §3.4)
_CRED_REDACT_RE = re.compile(
    r"(client_secret|refresh_token|access_token)=[^\s&]+",
    re.IGNORECASE,
)


def _redact_creds(msg: str) -> str:
    """Redact OAuth credential values from error messages."""
    return _CRED_REDACT_RE.sub(r"\1=<REDACTED>", msg)


# ---------------------------------------------------------------------------
# Config schema (spec §2.1)
# ---------------------------------------------------------------------------


class GoogleDriveConfig(BaseModel):
    """Configuration for the Google Drive module.

    Declared under ``[modules.google_drive]`` in butler.toml.
    """

    model_config = ConfigDict(extra="forbid")

    account: str | None = Field(default=None)
    """Google account email to use. Defaults to the primary account."""

    max_read_size_bytes: int = Field(default=10_485_760)
    """Maximum file size in bytes for drive_read_file (default 10 MB)."""

    butler_folder_name: str = Field(default="butlers")
    """Root folder name for butler outputs in Drive (default 'butlers')."""

    @field_validator("account")
    @classmethod
    def _strip_account(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


# ---------------------------------------------------------------------------
# Token cache with early-expiry refresh (spec §3.4)
# ---------------------------------------------------------------------------


class _DriveTokenCache:
    """OAuth token cache with early-expiry refresh and last_token_refresh_at tracking.

    Thread-safe via asyncio.Lock. Refreshes tokens 60 seconds before expiry
    to avoid clock skew issues. After each successful refresh, calls the
    optional ``on_refreshed`` callback (e.g. to update ``google_accounts``).
    """

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    def _is_fresh(self) -> bool:
        return (
            self._access_token is not None
            and time.monotonic() < self._expires_at - _TOKEN_EXPIRY_MARGIN_S
        )

    async def get_token(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        http_client: httpx.AsyncClient,
        on_refreshed: Any | None = None,
    ) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._is_fresh():
            assert self._access_token is not None
            return self._access_token

        async with self._lock:
            # Double-checked locking: another coroutine may have refreshed
            if self._is_fresh():
                assert self._access_token is not None
                return self._access_token

            await self._refresh(
                client_id=client_id,
                client_secret=client_secret,
                refresh_token=refresh_token,
                http_client=http_client,
                on_refreshed=on_refreshed,
            )
            assert self._access_token is not None
            return self._access_token

    async def _refresh(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        http_client: httpx.AsyncClient,
        on_refreshed: Any | None,
    ) -> None:
        try:
            response = await http_client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Google Drive token refresh failed: {_redact_creds(str(exc))}"
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise RuntimeError(
                f"Google Drive token refresh failed (HTTP {response.status_code}): "
                f"{_redact_creds(response.text[:200])}"
            )

        data = response.json()
        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))
        self._expires_at = time.monotonic() + expires_in

        logger.debug("Google Drive token refreshed (expires_in=%ds)", expires_in)

        # Update last_token_refresh_at in google_accounts (spec §3.4)
        if on_refreshed is not None:
            try:
                await on_refreshed()
            except Exception:
                logger.debug(
                    "GoogleDriveModule: failed to invoke on_refreshed callback",
                    exc_info=True,
                )


# ---------------------------------------------------------------------------
# HTTP helper with rate-limit retry (spec §3.5)
# ---------------------------------------------------------------------------


def _is_rate_limit_response(response: httpx.Response) -> bool:
    """Return True only for genuine rate-limit responses that should be retried.

    429 and 503 are always retried. 403 is retried only when the error body
    contains a rate-limit reason (``rateLimitExceeded`` or
    ``userRateLimitExceeded``). Permission-denied 403s (``forbidden``,
    ``accessNotConfigured``, etc.) must not be retried.
    """
    if response.status_code in _RATE_LIMIT_RETRY_STATUS_CODES:
        return True
    if response.status_code == 403:
        try:
            body = response.json()
            errors = body.get("error", {}).get("errors", [])
            return any(e.get("reason") in _RATE_LIMIT_403_REASONS for e in errors)
        except Exception:
            return False
    return False


async def _drive_request(
    http_client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    token: str,
    **kwargs: Any,
) -> httpx.Response:
    """Make a Drive API request with rate-limit retry (exponential backoff).

    Retries on 429, 503, and rate-limited 403 (``rateLimitExceeded`` /
    ``userRateLimitExceeded``) up to _RATE_LIMIT_MAX_RETRIES times with
    base-1s exponential backoff (1s, 2s, 4s).

    Permission-denied 403s (``forbidden``, ``accessNotConfigured``, etc.)
    are returned immediately without retrying.
    """
    headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
    last_exc: Exception | None = None

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        try:
            response = await http_client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Google Drive API network error: {_redact_creds(str(exc))}"
            ) from exc

        if not _is_rate_limit_response(response):
            return response

        delay = _RATE_LIMIT_BASE_DELAY_S * (2**attempt)
        if attempt < _RATE_LIMIT_MAX_RETRIES:
            logger.warning(
                "GoogleDrive: rate limited (HTTP %d), retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                delay,
                attempt + 1,
                _RATE_LIMIT_MAX_RETRIES,
            )
            await asyncio.sleep(delay)
            last_exc = RuntimeError(
                f"Google Drive rate limited (HTTP {response.status_code}) "
                f"after attempt {attempt + 1}"
            )
        else:
            raise RuntimeError(
                f"Google Drive rate limit exceeded after {_RATE_LIMIT_MAX_RETRIES} retries "
                f"(last status={response.status_code})"
            )

    raise last_exc or RuntimeError("Google Drive: unexpected retry loop exit")


# ---------------------------------------------------------------------------
# MIME type inference helper
# ---------------------------------------------------------------------------


def _infer_mime_type(filename: str) -> str:
    """Infer MIME type from file extension."""
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    return _EXT_MIME_MAP.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


# ---------------------------------------------------------------------------
# Module implementation (spec §3.1)
# ---------------------------------------------------------------------------


class GoogleDriveModule(Module):
    """Google Drive module providing 7 MCP tools for Drive file management.

    Implements the Module ABC with:
    - on_startup: credential resolution + scope validation + HTTP client init
    - on_shutdown: HTTP client teardown
    - OAuth token refresh with early-expiry margin and last_token_refresh_at update
    - Rate-limit retry (403/429/503, 3 retries, exponential backoff from 1.0s)
    - Butler folder hierarchy cached in google_drive_butler_folders table
    """

    def __init__(self) -> None:
        self._config: GoogleDriveConfig = GoogleDriveConfig()
        self._http_client: httpx.AsyncClient | None = None
        self._token_cache: _DriveTokenCache = _DriveTokenCache()
        self._credentials: Any | None = None
        self._credentials_ok: bool = False
        self._butler_name: str = "butler"
        self._db: Any | None = None
        # In-memory folder cache: (butler_name, account_email) -> folder_id
        self._folder_cache: dict[tuple[str, str], str] = {}

    # ------------------------------------------------------------------
    # Module ABC properties (spec §3.1)
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
        """Return Alembic branch label for this module's migrations."""
        return "google_drive"

    def tool_metadata(self) -> dict[str, ToolMeta]:
        """Declare sensitivity for write tools (spec §6.2)."""
        return {
            "drive_write_file": ToolMeta(arg_sensitivities={"content": True}),
            "drive_move_file": ToolMeta(arg_sensitivities={"file_id": True, "new_parent_id": True}),
        }

    # ------------------------------------------------------------------
    # Lifecycle (spec §3.2, §3.3)
    # ------------------------------------------------------------------

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Resolve Google credentials and create the HTTP client.

        Implements spec §3.2:
        - Resolve credentials via resolve_google_credentials()
        - Validate that the ``drive`` scope is present in granted_scopes
        - Create an httpx.AsyncClient for Drive API calls

        Parameters
        ----------
        config:
            Module config (GoogleDriveConfig or dict).
        db:
            Butler database instance. Used for butler folder table lookups
            and last_token_refresh_at updates.
        credential_store:
            CredentialStore for OAuth credential resolution.
        blob_store:
            Unused by this module.
        """
        self._config = (
            config if isinstance(config, GoogleDriveConfig) else GoogleDriveConfig(**(config or {}))
        )
        self._credentials_ok = False
        self._credentials = None
        self._db = db

        # Derive butler name from DB schema (same pattern as other modules)
        schema: str | None = getattr(db, "schema", None)
        if schema:
            self._butler_name = schema

        if credential_store is None:
            logger.warning(
                "GoogleDriveModule: no credential_store provided — tools will return errors"
            )
            return

        pool = getattr(db, "pool", None) if db is not None else None

        try:
            creds = await resolve_google_credentials(
                credential_store,
                pool=pool,
                caller="google_drive",
                account=self._config.account,
            )
        except MissingGoogleCredentialsError as exc:
            logger.warning(
                "GoogleDriveModule: credentials unavailable — %s. "
                "Re-authorize at /api/oauth/google/start with the 'drive' scope.",
                exc,
            )
            return

        # Validate drive scope is present (not just drive.readonly).
        # Split on whitespace to avoid substring match: "drive" must not match
        # "drive.readonly" (https://www.googleapis.com/auth/drive vs drive.readonly).
        granted = creds.scope or ""
        if _DRIVE_SCOPE not in granted.split():
            account_hint = self._config.account or ""
            qs = (
                f"?account_hint={account_hint}&force_consent=true"
                if account_hint
                else "?force_consent=true"
            )
            logger.warning(
                "GoogleDriveModule: account does not have the 'drive' scope. "
                "Re-authorize at /api/oauth/google/start%s",
                qs,
            )
            return

        self._credentials = creds
        self._credentials_ok = True

        # Create persistent HTTP client (spec §3.2)
        if self._http_client is not None:
            await self._http_client.aclose()
        self._http_client = httpx.AsyncClient(timeout=30.0)

        logger.info(
            "GoogleDriveModule: started (account=%s)",
            self._config.account or "primary",
        )

    async def on_shutdown(self) -> None:
        """Close HTTP client (spec §3.3)."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Token management (spec §3.4)
    # ------------------------------------------------------------------

    async def _get_token(self) -> str:
        """Return a fresh access token, refreshing if needed.

        Also updates ``public.google_accounts.last_token_refresh_at`` after
        each token refresh (spec §3.4).
        """
        assert self._credentials is not None
        assert self._http_client is not None

        on_refreshed = None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        account_email = self._config.account

        if pool is not None and account_email is not None:

            async def _update_refresh_at() -> None:
                try:
                    async with _pool_acquire(pool) as conn:
                        await conn.execute(
                            "UPDATE public.google_accounts "
                            "SET last_token_refresh_at = now() "
                            "WHERE email = $1",
                            account_email,
                        )
                except Exception:
                    logger.debug(
                        "GoogleDriveModule: failed to update last_token_refresh_at",
                        exc_info=True,
                    )

            on_refreshed = _update_refresh_at

        return await self._token_cache.get_token(
            client_id=self._credentials.client_id,
            client_secret=self._credentials.client_secret,
            refresh_token=self._credentials.refresh_token,
            http_client=self._http_client,
            on_refreshed=on_refreshed,
        )

    def _not_configured_error(self) -> dict[str, Any]:
        """Standard error dict when Drive is not connected."""
        return {
            "status": "error",
            "error": (
                "Google Drive not connected. "
                "Connect a Google account with the 'drive' scope via "
                "/api/oauth/google/start?force_consent=true"
            ),
        }

    # ------------------------------------------------------------------
    # Drive API helpers (spec §3.5 rate-limit retry)
    # ------------------------------------------------------------------

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        assert self._http_client is not None
        token = await self._get_token()
        return await _drive_request(
            self._http_client,
            "GET",
            f"{_DRIVE_API_BASE}/{path}",
            token=token,
            **kwargs,
        )

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        assert self._http_client is not None
        token = await self._get_token()
        return await _drive_request(
            self._http_client,
            "POST",
            url,
            token=token,
            **kwargs,
        )

    async def _patch(self, path: str, **kwargs: Any) -> httpx.Response:
        assert self._http_client is not None
        token = await self._get_token()
        return await _drive_request(
            self._http_client,
            "PATCH",
            f"{_DRIVE_API_BASE}/{path}",
            token=token,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Butler folder hierarchy (spec §4)
    # ------------------------------------------------------------------

    async def _ensure_butler_folder(self, butler_name: str) -> str:
        """Ensure butlers/{butler_name}/ folder exists and return its Drive ID.

        Uses a two-level hierarchy:
        1. Root folder named by ``config.butler_folder_name`` (default "butlers")
        2. Per-butler subfolder named ``butler_name``

        Results are cached in ``google_drive_butler_folders`` table and in
        memory. Re-creates the folder if the cached ID refers to a deleted
        folder (spec §4.4).
        """
        assert self._http_client is not None

        account_email = self._config.account or "primary"
        cache_key = (butler_name, account_email)

        # --- DB cache check ---
        cached_id = await self._get_cached_folder_id(butler_name, account_email)
        if cached_id:
            resp = await self._get(f"files/{cached_id}", params={"fields": "id,trashed"})
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("trashed", False):
                    self._folder_cache[cache_key] = cached_id
                    return cached_id
            # Evict stale in-memory entry to avoid a redundant second API call below.
            self._folder_cache.pop(cache_key, None)
            logger.info(
                "GoogleDriveModule: cached folder %s missing/trashed, recreating",
                cached_id,
            )

        # --- In-memory cache check ---
        if cache_key in self._folder_cache:
            folder_id = self._folder_cache[cache_key]
            resp = await self._get(f"files/{folder_id}", params={"fields": "id,trashed"})
            if resp.status_code == 200 and not resp.json().get("trashed", False):
                return folder_id
            del self._folder_cache[cache_key]

        # --- Create root "butlers/" folder at Drive root ---
        root_id = await self._find_or_create_folder(
            name=self._config.butler_folder_name,
            parent_id=None,
        )

        # --- Create per-butler subfolder ---
        folder_id = await self._find_or_create_folder(
            name=butler_name,
            parent_id=root_id,
        )
        folder_path = f"{self._config.butler_folder_name}/{butler_name}"

        # Cache in DB and memory
        await self._cache_folder_id(butler_name, account_email, folder_id, folder_path)
        self._folder_cache[cache_key] = folder_id
        return folder_id

    async def _find_or_create_folder(
        self,
        name: str,
        parent_id: str | None,
    ) -> str:
        """Find an existing folder by name+parent, or create it."""
        assert self._http_client is not None

        escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
        parent_filter = f"'{parent_id}' in parents" if parent_id else "'root' in parents"
        q = (
            f"name='{escaped_name}' and mimeType='{_FOLDER_MIME_TYPE}' "
            f"and {parent_filter} and trashed=false"
        )

        resp = await self._get("files", params={"q": q, "fields": "files(id,name)"})
        if resp.status_code == 200:
            files = resp.json().get("files", [])
            if files:
                return files[0]["id"]

        # Not found — create it
        body: dict[str, Any] = {"name": name, "mimeType": _FOLDER_MIME_TYPE}
        if parent_id:
            body["parents"] = [parent_id]

        resp = await self._post(
            f"{_DRIVE_API_BASE}/files",
            json=body,
            params={"fields": "id"},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Google Drive: failed to create folder '{name}' "
                f"(HTTP {resp.status_code}): {resp.text[:200]}"
            )
        return resp.json()["id"]

    async def _get_cached_folder_id(
        self,
        butler_name: str,
        account_email: str,
    ) -> str | None:
        """Look up cached folder ID from google_drive_butler_folders table."""
        if self._db is None:
            return None
        pool = getattr(self._db, "pool", None)
        if pool is None:
            return None
        try:
            async with _pool_acquire(pool) as conn:
                row = await conn.fetchrow(
                    "SELECT folder_id FROM google_drive_butler_folders "
                    "WHERE butler_name = $1 AND account_email = $2",
                    butler_name,
                    account_email,
                )
                return row["folder_id"] if row else None
        except Exception:
            logger.debug(
                "GoogleDriveModule: failed to query butler folder cache",
                exc_info=True,
            )
            return None

    async def _cache_folder_id(
        self,
        butler_name: str,
        account_email: str,
        folder_id: str,
        folder_path: str,
    ) -> None:
        """Persist folder ID to google_drive_butler_folders table."""
        if self._db is None:
            return
        pool = getattr(self._db, "pool", None)
        if pool is None:
            return
        try:
            async with _pool_acquire(pool) as conn:
                await conn.execute(
                    "INSERT INTO google_drive_butler_folders "
                    "(butler_name, account_email, folder_id, folder_path) "
                    "VALUES ($1, $2, $3, $4) "
                    "ON CONFLICT (butler_name, account_email) DO UPDATE "
                    "SET folder_id = EXCLUDED.folder_id, "
                    "folder_path = EXCLUDED.folder_path",
                    butler_name,
                    account_email,
                    folder_id,
                    folder_path,
                )
        except Exception:
            logger.warning(
                "GoogleDriveModule: failed to cache butler folder ID",
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # MCP tool implementations (spec §5)
    # ------------------------------------------------------------------

    async def _drive_list_files(
        self,
        folder_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            q_parts = []
            if folder_id:
                q_parts.append(f"'{folder_id}' in parents")
            if query:
                q_parts.append(query)
            q_parts.append("trashed=false")
            q = " and ".join(q_parts)

            all_files: list[dict[str, Any]] = []
            next_page_token: str | None = None

            while True:
                params: dict[str, Any] = {
                    "q": q,
                    "fields": f"nextPageToken,{_LIST_FIELDS}",
                    "pageSize": 100,
                }
                if next_page_token:
                    params["pageToken"] = next_page_token

                resp = await self._get("files", params=params)
                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "error": f"Drive API error (HTTP {resp.status_code})",
                    }

                data = resp.json()
                all_files.extend(data.get("files", []))
                next_page_token = data.get("nextPageToken")

                if not next_page_token or len(all_files) >= _MAX_PAGE_RESULTS:
                    break

            truncated = len(all_files) >= _MAX_PAGE_RESULTS
            return {
                "files": all_files[:_MAX_PAGE_RESULTS],
                "total": len(all_files[:_MAX_PAGE_RESULTS]),
                "truncated": truncated,
            }
        except Exception as exc:
            logger.error("drive_list_files failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _drive_get_file_metadata(self, file_id: str) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            resp = await self._get(f"files/{file_id}", params={"fields": _META_FIELDS})
            if resp.status_code == 404:
                return {"status": "not_found", "file": None}
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "error": f"Drive API error (HTTP {resp.status_code})",
                }
            return resp.json()
        except Exception as exc:
            logger.error("drive_get_file_metadata failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _drive_read_file(self, file_id: str) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            # Get metadata first (include webViewLink for binary_file response)
            meta_resp = await self._get(
                f"files/{file_id}",
                params={"fields": "id,name,mimeType,size,webViewLink"},
            )
            if meta_resp.status_code == 404:
                return {"status": "not_found", "file": None}
            if meta_resp.status_code != 200:
                return {
                    "status": "error",
                    "error": f"Metadata fetch failed (HTTP {meta_resp.status_code})",
                }

            meta = meta_resp.json()
            mime_type = meta.get("mimeType", "")
            name = meta.get("name", "")
            size_bytes = int(meta.get("size", 0) or 0)
            web_view_link = meta.get("webViewLink", "")

            # Size check for non-Google-Workspace files
            if mime_type not in _GOOGLE_EXPORT_MAP:
                if size_bytes > self._config.max_read_size_bytes:
                    return {
                        "status": "too_large",
                        "size_bytes": size_bytes,
                        "max_bytes": self._config.max_read_size_bytes,
                        "name": name,
                    }

            # Fetch content
            if mime_type in _GOOGLE_EXPORT_MAP:
                export_mime = _GOOGLE_EXPORT_MAP[mime_type]
                resp = await self._get(
                    f"files/{file_id}/export",
                    params={"mimeType": export_mime},
                )
                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "error": f"Export failed (HTTP {resp.status_code})",
                    }
                return {
                    "content": resp.text,
                    "mime_type": export_mime,
                    "name": name,
                    "size_bytes": len(resp.content),
                }
            elif mime_type.startswith("text/") or mime_type in {
                "application/json",
                "application/xml",
            }:
                resp = await self._get(f"files/{file_id}", params={"alt": "media"})
                if resp.status_code != 200:
                    return {
                        "status": "error",
                        "error": f"Download failed (HTTP {resp.status_code})",
                    }
                return {
                    "content": resp.text,
                    "mime_type": mime_type,
                    "name": name,
                    "size_bytes": len(resp.content),
                }
            else:
                return {
                    "status": "binary_file",
                    "mime_type": mime_type,
                    "name": name,
                    "size_bytes": size_bytes,
                    "web_view_link": web_view_link,
                }
        except Exception as exc:
            logger.error("drive_read_file failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _drive_write_file(
        self,
        name: str,
        content: str,
        folder_id: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            folder_path: str
            if folder_id is None:
                folder_id = await self._ensure_butler_folder(self._butler_name)
                folder_path = f"{self._config.butler_folder_name}/{self._butler_name}"
            else:
                folder_path = folder_id

            inferred_mime = mime_type or _infer_mime_type(name)
            content_bytes = content.encode()

            # Multipart upload
            metadata: dict[str, Any] = {
                "name": name,
                "parents": [folder_id],
            }

            boundary = "butlers_drive_boundary"
            body = (
                (
                    f"--{boundary}\r\n"
                    f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                    f"{_json.dumps(metadata)}\r\n"
                    f"--{boundary}\r\n"
                    f"Content-Type: {inferred_mime}\r\n\r\n"
                ).encode()
                + content_bytes
                + f"\r\n--{boundary}--".encode()
            )

            resp = await self._post(
                "https://www.googleapis.com/upload/drive/v3/files",
                content=body,
                params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                headers={
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
            )

            if resp.status_code not in (200, 201):
                return {
                    "status": "error",
                    "error": f"Upload failed (HTTP {resp.status_code}): {resp.text[:200]}",
                }

            data = resp.json()
            return {
                "file_id": data["id"],
                "name": data.get("name", name),
                "folder": folder_path,
                "web_view_link": data.get("webViewLink"),
            }
        except Exception as exc:
            logger.error("drive_write_file failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _drive_create_folder(
        self,
        name: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            if parent_id is None:
                parent_id = await self._ensure_butler_folder(self._butler_name)

            body: dict[str, Any] = {
                "name": name,
                "mimeType": _FOLDER_MIME_TYPE,
                "parents": [parent_id],
            }

            resp = await self._post(
                f"{_DRIVE_API_BASE}/files",
                json=body,
                params={"fields": "id,name,webViewLink"},
            )

            if resp.status_code not in (200, 201):
                return {
                    "status": "error",
                    "error": f"Folder creation failed (HTTP {resp.status_code}): {resp.text[:200]}",
                }

            data = resp.json()
            return {
                "folder_id": data["id"],
                "name": data["name"],
                "web_view_link": data.get("webViewLink"),
            }
        except Exception as exc:
            logger.error("drive_create_folder failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _drive_move_file(
        self,
        file_id: str,
        new_parent_id: str,
    ) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            # Get current parents and name
            meta_resp = await self._get(
                f"files/{file_id}",
                params={"fields": "id,name,parents"},
            )
            if meta_resp.status_code == 404:
                return {"status": "not_found", "error": "File not found"}
            if meta_resp.status_code != 200:
                return {
                    "status": "error",
                    "error": f"Failed to fetch file parents (HTTP {meta_resp.status_code})",
                }

            meta = meta_resp.json()
            name = meta.get("name", "")
            old_parents = ",".join(meta.get("parents", []))

            resp = await self._patch(
                f"files/{file_id}",
                params={
                    "addParents": new_parent_id,
                    "removeParents": old_parents,
                    "fields": "id,name,parents",
                },
            )

            if resp.status_code == 404:
                return {"status": "not_found", "error": "File not found"}
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "error": f"Move failed (HTTP {resp.status_code}): {resp.text[:200]}",
                }

            return {"file_id": file_id, "name": name, "new_parent_id": new_parent_id}
        except Exception as exc:
            logger.error("drive_move_file failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _drive_search_files(
        self,
        query: str,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if not self._credentials_ok or self._http_client is None:
            return self._not_configured_error()

        try:
            escaped_query = query.replace("\\", "\\\\").replace("'", "\\'")
            q = f"fullText contains '{escaped_query}' and trashed=false"

            params: dict[str, Any] = {
                "q": q,
                "fields": f"nextPageToken,{_SEARCH_FIELDS}",
                "pageSize": min(limit or 100, 100),
            }

            resp = await self._get("files", params=params)
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "error": f"Drive API error (HTTP {resp.status_code})",
                }

            files = resp.json().get("files", [])
            if limit:
                files = files[:limit]

            return {"files": files, "total": len(files)}
        except Exception as exc:
            logger.error("drive_search_files failed: %s", exc, exc_info=True)
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # Tool registration (spec §6.1)
    # ------------------------------------------------------------------

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all 7 Google Drive MCP tools on the butler's FastMCP server."""
        if isinstance(config, dict):
            config = GoogleDriveConfig(**(config or {}))
        if isinstance(config, GoogleDriveConfig):
            self._config = config
        self._db = db

        # Derive butler name from DB schema when available (same pattern as on_startup).
        # This ensures _butler_name is consistent whether the module is started via
        # on_startup or only registered via register_tools.
        schema: str | None = getattr(db, "schema", None)
        if schema:
            self._butler_name = schema

        module = self

        @mcp.tool(name="drive_list_files")
        async def drive_list_files(
            folder_id: str | None = None,
            query: str | None = None,
        ) -> dict[str, Any]:
            """List files in a Drive folder or matching a query.

            Args:
                folder_id: Folder to list. Defaults to My Drive root.
                query: Additional Drive query filter.

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
            """
            return await module._drive_read_file(file_id=file_id)

        @mcp.tool(name="drive_write_file")
        async def drive_write_file(
            name: str,
            content: str,
            folder_id: str | None = None,
            mime_type: str | None = None,
        ) -> dict[str, Any]:
            """Upload a file to Google Drive.

            Args:
                name: File name (used for MIME type inference if mime_type omitted).
                content: Text content to upload.
                folder_id: Target folder. Defaults to butler's folder.
                mime_type: MIME type override. Inferred from name if omitted.

            Returns:
                ``{"file_id": ..., "web_view_link": ..., "name": ..., "mime_type": ...}``
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
                ``{"folder_id": ..., "name": ..., "web_view_link": ...}``
            """
            return await module._drive_create_folder(name=name, parent_id=parent_id)

        @mcp.tool(name="drive_move_file")
        async def drive_move_file(
            file_id: str,
            new_parent_id: str,
        ) -> dict[str, Any]:
            """Move a file to a different folder.

            Args:
                file_id: The Drive file ID to move.
                new_parent_id: Target folder ID.

            Returns:
                ``{"status": "ok", "file_id": ..., "new_parent_id": ...}``
            """
            return await module._drive_move_file(file_id=file_id, new_parent_id=new_parent_id)

        @mcp.tool(name="drive_search_files")
        async def drive_search_files(
            query: str,
            limit: int | None = None,
        ) -> dict[str, Any]:
            """Full-text search across Google Drive.

            Args:
                query: Search query string.
                limit: Maximum number of results to return.

            Returns:
                ``{"files": [...], "total": N}``
            """
            return await module._drive_search_files(query=query, limit=limit)
