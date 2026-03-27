"""Tests for GoogleDriveModule core implementation (spec §3, tasks 3.1–3.5).

Covers:
  3.1  Module ABC compliance: name, config_schema, dependencies, migration_revisions
  3.2  on_startup: credential resolution, scope validation, HTTP client creation
  3.3  on_shutdown: HTTP client teardown
  3.4  OAuth token refresh with early-expiry margin and last_token_refresh_at update
  3.5  Rate-limit retry: 403/429/503, 3 retries, exponential backoff base 1s

Also covers task 2.1 (GoogleDriveConfig) and task 6.2 (tool_metadata).
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from butlers.modules.base import Module, ToolMeta
from butlers.modules.google_drive import (
    GoogleDriveConfig,
    GoogleDriveModule,
    _drive_request,
    _DriveTokenCache,
    _infer_mime_type,
    _redact_creds,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int,
    json_body: Any = None,
    text: str = "",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Build a fake httpx.Response for testing."""
    if json_body is not None:
        content = json.dumps(json_body).encode()
        content_type = "application/json"
    else:
        content = text.encode()
        content_type = "text/plain"
    resp_headers = {"content-type": content_type}
    if headers:
        resp_headers.update(headers)
    return httpx.Response(status_code, content=content, headers=resp_headers)


def _make_creds(scope: str = "https://www.googleapis.com/auth/drive") -> MagicMock:
    """Return a minimal fake GoogleCredentials object."""
    creds = MagicMock()
    creds.client_id = "test_client_id"
    creds.client_secret = "test_client_secret"
    creds.refresh_token = "test_refresh_token"
    creds.scope = scope
    return creds


def _make_module() -> GoogleDriveModule:
    """Return a GoogleDriveModule instance in the default (un-started) state."""
    return GoogleDriveModule()


def _make_started_module(creds: Any = None) -> GoogleDriveModule:
    """Return a GoogleDriveModule in the started state with a mock HTTP client."""
    module = _make_module()
    module._credentials = creds or _make_creds()
    module._credentials_ok = True
    module._http_client = MagicMock(spec=httpx.AsyncClient)
    module._config = GoogleDriveConfig()
    module._butler_name = "test-butler"
    return module


# ---------------------------------------------------------------------------
# Task 3.1 — Module ABC compliance
# ---------------------------------------------------------------------------


class TestModuleABCCompliance:
    """GoogleDriveModule must satisfy the Module ABC (spec §3.1)."""

    def test_is_module_subclass(self):
        assert issubclass(GoogleDriveModule, Module)

    def test_instantiates_without_arguments(self):
        mod = _make_module()
        assert mod is not None

    def test_name_property(self):
        assert _make_module().name == "google_drive"

    def test_config_schema_is_google_drive_config(self):
        assert _make_module().config_schema is GoogleDriveConfig

    def test_dependencies_is_empty_list(self):
        assert _make_module().dependencies == []

    def test_migration_revisions_returns_google_drive(self):
        assert _make_module().migration_revisions() == "google_drive"

    def test_has_register_tools(self):
        assert callable(getattr(_make_module(), "register_tools", None))

    def test_has_on_startup(self):
        assert callable(getattr(_make_module(), "on_startup", None))

    def test_has_on_shutdown(self):
        assert callable(getattr(_make_module(), "on_shutdown", None))

    def test_tool_metadata_declared(self):
        """Task 6.2 — drive_write_file and drive_move_file must be declared sensitive."""
        meta = _make_module().tool_metadata()
        assert isinstance(meta, dict)
        assert "drive_write_file" in meta
        assert "drive_move_file" in meta

    def test_drive_write_file_content_sensitive(self):
        meta = _make_module().tool_metadata()
        assert meta["drive_write_file"].arg_sensitivities.get("content") is True

    def test_drive_move_file_file_id_sensitive(self):
        meta = _make_module().tool_metadata()
        assert meta["drive_move_file"].arg_sensitivities.get("file_id") is True

    def test_drive_move_file_new_parent_id_sensitive(self):
        meta = _make_module().tool_metadata()
        assert meta["drive_move_file"].arg_sensitivities.get("new_parent_id") is True

    def test_tool_metadata_returns_tool_meta_instances(self):
        meta = _make_module().tool_metadata()
        for v in meta.values():
            assert isinstance(v, ToolMeta)


# ---------------------------------------------------------------------------
# Task 2.1 — GoogleDriveConfig validation
# ---------------------------------------------------------------------------


class TestGoogleDriveConfig:
    """GoogleDriveConfig validation and defaults (spec §2.1)."""

    def test_defaults(self):
        config = GoogleDriveConfig()
        assert config.account is None
        assert config.max_read_size_bytes == 10_485_760
        assert config.butler_folder_name == "butlers"

    def test_explicit_values(self):
        config = GoogleDriveConfig(
            account="user@example.com",
            max_read_size_bytes=5_000_000,
            butler_folder_name="agents",
        )
        assert config.account == "user@example.com"
        assert config.max_read_size_bytes == 5_000_000
        assert config.butler_folder_name == "agents"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            GoogleDriveConfig(unknown="bad")
        assert any(e["type"] == "extra_forbidden" for e in exc_info.value.errors())

    def test_account_strip_whitespace(self):
        config = GoogleDriveConfig(account="  user@example.com  ")
        assert config.account == "user@example.com"

    def test_account_empty_string_becomes_none(self):
        config = GoogleDriveConfig(account="   ")
        assert config.account is None

    def test_account_none_stays_none(self):
        config = GoogleDriveConfig(account=None)
        assert config.account is None

    def test_from_empty_dict(self):
        config = GoogleDriveConfig(**{})
        assert config.account is None

    def test_from_partial_dict(self):
        config = GoogleDriveConfig(**{"max_read_size_bytes": 1024})
        assert config.max_read_size_bytes == 1024
        assert config.account is None


# ---------------------------------------------------------------------------
# Task 3.2 — on_startup
# ---------------------------------------------------------------------------


class TestOnStartup:
    """on_startup: credential resolution and scope validation (spec §3.2)."""

    async def test_startup_no_credential_store_warns(self):
        """No credential_store → logs warning, module stays in degraded state."""
        module = _make_module()
        db = MagicMock()
        await module.on_startup(config=GoogleDriveConfig(), db=db, credential_store=None)
        assert module._credentials_ok is False
        assert module._credentials is None
        assert module._http_client is None

    async def test_startup_resolves_credentials(self):
        """Valid credentials with drive scope → module starts successfully."""
        module = _make_module()
        creds = _make_creds(scope="https://www.googleapis.com/auth/drive")
        credential_store = MagicMock()
        db = MagicMock()
        db.pool = None
        db.schema = "finance"

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(return_value=creds),
        ):
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=db,
                credential_store=credential_store,
            )

        assert module._credentials_ok is True
        assert module._credentials is creds
        assert module._http_client is not None
        assert module._butler_name == "finance"

        # Cleanup
        await module.on_shutdown()

    async def test_startup_missing_credentials_warns(self):
        """MissingGoogleCredentialsError → module stays degraded."""
        from butlers.google_credentials import MissingGoogleCredentialsError

        module = _make_module()
        credential_store = MagicMock()
        db = MagicMock()
        db.pool = None

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(side_effect=MissingGoogleCredentialsError("no creds")),
        ):
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=db,
                credential_store=credential_store,
            )

        assert module._credentials_ok is False
        assert module._http_client is None

    async def test_startup_missing_drive_scope_warns(self):
        """Account has only calendar scope → module stays degraded (missing drive)."""
        module = _make_module()
        creds = _make_creds(scope="https://www.googleapis.com/auth/calendar")
        credential_store = MagicMock()
        db = MagicMock()
        db.pool = None

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(return_value=creds),
        ):
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=db,
                credential_store=credential_store,
            )

        assert module._credentials_ok is False

    async def test_startup_with_account_config(self):
        """account config is passed through to resolve_google_credentials."""
        module = _make_module()
        creds = _make_creds()
        credential_store = MagicMock()
        db = MagicMock()
        db.pool = None

        captured: dict[str, Any] = {}

        async def fake_resolve(store, *, pool=None, caller=None, account=None):
            captured["caller"] = caller
            captured["account"] = account
            return creds

        with patch("butlers.modules.google_drive.resolve_google_credentials", fake_resolve):
            await module.on_startup(
                config=GoogleDriveConfig(account="work@gmail.com"),
                db=db,
                credential_store=credential_store,
            )

        assert captured["caller"] == "google_drive"
        assert captured["account"] == "work@gmail.com"
        assert module._credentials_ok is True

        await module.on_shutdown()

    async def test_startup_drive_scope_with_additional_scopes(self):
        """drive scope present among other scopes → startup succeeds."""
        module = _make_module()
        creds = _make_creds(
            scope=(
                "https://www.googleapis.com/auth/calendar "
                "https://www.googleapis.com/auth/drive "
                "https://www.googleapis.com/auth/gmail.readonly"
            )
        )
        credential_store = MagicMock()
        db = MagicMock()
        db.pool = None

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(return_value=creds),
        ):
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=db,
                credential_store=credential_store,
            )

        assert module._credentials_ok is True
        await module.on_shutdown()

    async def test_startup_db_none_still_works(self):
        """on_startup with db=None completes without error."""
        module = _make_module()
        creds = _make_creds()
        credential_store = MagicMock()

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(return_value=creds),
        ):
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=None,
                credential_store=credential_store,
            )

        assert module._credentials_ok is True
        await module.on_shutdown()

    async def test_startup_idempotent_closes_old_client(self):
        """Calling on_startup twice closes the first HTTP client."""
        module = _make_module()
        creds = _make_creds()
        db = MagicMock()
        db.pool = None

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(return_value=creds),
        ):
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=db,
                credential_store=MagicMock(),
            )
            first_client = module._http_client
            assert first_client is not None

            # Second call should close first client and create a new one
            await module.on_startup(
                config=GoogleDriveConfig(),
                db=db,
                credential_store=MagicMock(),
            )

        assert module._http_client is not None
        assert module._http_client is not first_client
        await module.on_shutdown()

    async def test_startup_config_dict_input(self):
        """on_startup accepts a raw dict for config."""
        module = _make_module()
        creds = _make_creds()
        db = MagicMock()
        db.pool = None

        with patch(
            "butlers.modules.google_drive.resolve_google_credentials",
            AsyncMock(return_value=creds),
        ):
            await module.on_startup(
                config={"max_read_size_bytes": 1024},
                db=db,
                credential_store=MagicMock(),
            )

        assert module._config.max_read_size_bytes == 1024
        assert module._credentials_ok is True
        await module.on_shutdown()


# ---------------------------------------------------------------------------
# Task 3.3 — on_shutdown
# ---------------------------------------------------------------------------


class TestOnShutdown:
    """on_shutdown: HTTP client teardown (spec §3.3)."""

    async def test_shutdown_closes_http_client(self):
        """on_shutdown calls aclose() on the HTTP client."""
        module = _make_module()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        module._http_client = mock_client
        module._credentials_ok = True

        await module.on_shutdown()

        mock_client.aclose.assert_awaited_once()
        assert module._http_client is None

    async def test_shutdown_when_no_client_is_noop(self):
        """on_shutdown with no client does not raise."""
        module = _make_module()
        assert module._http_client is None

        await module.on_shutdown()  # should not raise

    async def test_shutdown_idempotent(self):
        """Calling on_shutdown twice does not raise."""
        module = _make_module()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        module._http_client = mock_client

        await module.on_shutdown()
        await module.on_shutdown()  # second call should be a no-op

        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Task 3.4 — OAuth token refresh
# ---------------------------------------------------------------------------


class TestTokenRefresh:
    """OAuth token refresh with early-expiry margin (spec §3.4)."""

    async def test_token_cache_returns_fresh_token(self):
        """Token cache returns existing token when still fresh."""
        cache = _DriveTokenCache()
        cache._access_token = "fresh_token"
        cache._expires_at = time.monotonic() + 300  # valid for 5 more minutes

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        token = await cache.get_token(
            client_id="cid",
            client_secret="csec",
            refresh_token="rt",
            http_client=mock_client,
        )

        assert token == "fresh_token"
        mock_client.post.assert_not_awaited()

    async def test_token_cache_refreshes_expired_token(self):
        """Token cache calls token endpoint when token is expired."""
        cache = _DriveTokenCache()
        cache._access_token = "old_token"
        cache._expires_at = time.monotonic() - 100  # already expired

        mock_response = _make_response(
            200, json_body={"access_token": "new_token", "expires_in": 3600}
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        token = await cache.get_token(
            client_id="cid",
            client_secret="csec",
            refresh_token="rt",
            http_client=mock_client,
        )

        assert token == "new_token"
        mock_client.post.assert_awaited_once()

    async def test_token_cache_respects_early_expiry_margin(self):
        """Token is refreshed when within 60s of expiry (early-expiry margin)."""
        cache = _DriveTokenCache()
        cache._access_token = "almost_expired"
        # Within the 60s margin — should trigger refresh
        cache._expires_at = time.monotonic() + 30

        mock_response = _make_response(
            200, json_body={"access_token": "refreshed_token", "expires_in": 3600}
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        token = await cache.get_token(
            client_id="cid",
            client_secret="csec",
            refresh_token="rt",
            http_client=mock_client,
        )

        assert token == "refreshed_token"

    async def test_token_cache_calls_on_refreshed_callback(self):
        """on_refreshed callback is invoked after a successful token refresh."""
        cache = _DriveTokenCache()
        # Force a refresh
        cache._expires_at = 0.0

        mock_response = _make_response(
            200, json_body={"access_token": "new_token", "expires_in": 3600}
        )
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        refreshed_called = []

        async def on_refreshed():
            refreshed_called.append(True)

        await cache.get_token(
            client_id="cid",
            client_secret="csec",
            refresh_token="rt",
            http_client=mock_client,
            on_refreshed=on_refreshed,
        )

        assert refreshed_called == [True]

    async def test_token_cache_refresh_failure_raises(self):
        """Token refresh HTTP failure raises RuntimeError with redacted message."""
        cache = _DriveTokenCache()
        cache._expires_at = 0.0

        mock_response = _make_response(401, text="invalid_client")
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(RuntimeError, match="token refresh"):
            await cache.get_token(
                client_id="cid",
                client_secret="csec",
                refresh_token="rt",
                http_client=mock_client,
            )

    async def test_on_refreshed_callback_failure_does_not_propagate(self):
        """A failing on_refreshed callback does not propagate the error."""
        cache = _DriveTokenCache()
        cache._expires_at = 0.0

        mock_response = _make_response(200, json_body={"access_token": "token", "expires_in": 3600})
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = AsyncMock(return_value=mock_response)

        async def failing_callback():
            raise RuntimeError("DB down!")

        # Should not raise despite failing callback
        token = await cache.get_token(
            client_id="cid",
            client_secret="csec",
            refresh_token="rt",
            http_client=mock_client,
            on_refreshed=failing_callback,
        )
        assert token == "token"

    async def test_get_token_updates_last_token_refresh_at(self):
        """_get_token() wires on_refreshed to update google_accounts."""
        module = _make_started_module()
        module._config = GoogleDriveConfig(account="user@example.com")

        mock_pool = AsyncMock()
        mock_conn = AsyncMock()
        mock_pool.acquire = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_conn),
                __aexit__=AsyncMock(return_value=False),
            )
        )
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        module._db = mock_db

        # Pre-inject a fresh token so we force a cache miss
        module._token_cache._expires_at = 0.0

        mock_response = _make_response(
            200, json_body={"access_token": "token_abc", "expires_in": 3600}
        )
        module._http_client.post = AsyncMock(return_value=mock_response)

        token = await module._get_token()
        assert token == "token_abc"


# ---------------------------------------------------------------------------
# Task 3.5 — Rate-limit retry
# ---------------------------------------------------------------------------


class TestRateLimitRetry:
    """Drive API rate-limit retry: 403/429/503, 3 retries, exponential backoff (spec §3.5)."""

    async def test_success_on_first_attempt(self):
        """Non-rate-limit response returned immediately."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_make_response(200, json_body={"files": []}))

        resp = await _drive_request(
            mock_client,
            "GET",
            "https://www.googleapis.com/drive/v3/files",
            token="fake_token",
        )
        assert resp.status_code == 200
        assert mock_client.request.await_count == 1

    async def test_retries_on_429(self):
        """Retries on 429 (Too Many Requests) up to max retries."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        responses = [
            _make_response(429, text="rate limited"),
            _make_response(429, text="rate limited"),
            _make_response(200, json_body={"files": []}),
        ]
        mock_client.request = AsyncMock(side_effect=responses)

        with patch("butlers.modules.google_drive.asyncio.sleep", AsyncMock()):
            resp = await _drive_request(
                mock_client,
                "GET",
                "https://example.com",
                token="t",
            )

        assert resp.status_code == 200
        assert mock_client.request.await_count == 3

    async def test_retries_on_403(self):
        """Retries on 403 (quota exceeded) up to max retries."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        responses = [
            _make_response(403, text="quota"),
            _make_response(200, json_body={"ok": True}),
        ]
        mock_client.request = AsyncMock(side_effect=responses)

        with patch("butlers.modules.google_drive.asyncio.sleep", AsyncMock()):
            resp = await _drive_request(
                mock_client,
                "GET",
                "https://example.com",
                token="t",
            )

        assert resp.status_code == 200

    async def test_retries_on_503(self):
        """Retries on 503 (Service Unavailable)."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        responses = [
            _make_response(503, text="service unavailable"),
            _make_response(200, json_body={}),
        ]
        mock_client.request = AsyncMock(side_effect=responses)

        with patch("butlers.modules.google_drive.asyncio.sleep", AsyncMock()):
            resp = await _drive_request(
                mock_client,
                "GET",
                "https://example.com",
                token="t",
            )

        assert resp.status_code == 200

    async def test_raises_after_max_retries_exceeded(self):
        """Raises RuntimeError after all 3 retries are exhausted."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        # 4 responses: attempt 0, 1, 2, 3 (max_retries=3 → 4 total attempts)
        mock_client.request = AsyncMock(side_effect=[_make_response(429, text="rate limited")] * 10)

        with patch("butlers.modules.google_drive.asyncio.sleep", AsyncMock()):
            with pytest.raises(RuntimeError, match="rate limit exceeded"):
                await _drive_request(
                    mock_client,
                    "GET",
                    "https://example.com",
                    token="t",
                )

        # Should have tried _RATE_LIMIT_MAX_RETRIES + 1 = 4 times
        assert mock_client.request.await_count == 4

    async def test_exponential_backoff_delays(self):
        """Backoff delays are 1.0s, 2.0s, 4.0s (base=1.0, exponential)."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(
            side_effect=[
                _make_response(429),
                _make_response(429),
                _make_response(429),
                _make_response(200, json_body={}),
            ]
        )

        sleep_calls: list[float] = []

        async def capture_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        with patch("butlers.modules.google_drive.asyncio.sleep", capture_sleep):
            await _drive_request(mock_client, "GET", "https://example.com", token="t")

        assert sleep_calls == [1.0, 2.0, 4.0]

    async def test_non_rate_limit_status_codes_not_retried(self):
        """4xx/5xx that are not rate-limit codes are returned immediately."""
        for status in [400, 401, 404, 500, 502]:
            mock_client = AsyncMock(spec=httpx.AsyncClient)
            mock_client.request = AsyncMock(return_value=_make_response(status, text="error"))

            resp = await _drive_request(
                mock_client,
                "GET",
                "https://example.com",
                token="t",
            )
            assert resp.status_code == status
            assert mock_client.request.await_count == 1

    async def test_network_error_raises_runtime_error(self):
        """httpx.HTTPError → RuntimeError with redacted message."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("connect failed"))

        with pytest.raises(RuntimeError, match="network error"):
            await _drive_request(
                mock_client,
                "GET",
                "https://example.com",
                token="t",
            )

    async def test_authorization_header_injected(self):
        """Bearer token is added to the Authorization header."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.request = AsyncMock(return_value=_make_response(200, json_body={}))

        await _drive_request(
            mock_client,
            "GET",
            "https://example.com",
            token="my_bearer_token",
        )

        call_kwargs = mock_client.request.call_args
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer my_bearer_token"


# ---------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------


class TestCredentialRedaction:
    """Sensitive values must be redacted from error messages (spec §3.4)."""

    def test_redacts_client_secret(self):
        msg = "client_secret=super_secret_value&other=stuff"
        assert "super_secret_value" not in _redact_creds(msg)
        assert "<REDACTED>" in _redact_creds(msg)

    def test_redacts_refresh_token(self):
        msg = "refresh_token=my_refresh_token_12345"
        assert "my_refresh_token_12345" not in _redact_creds(msg)

    def test_redacts_access_token(self):
        msg = "access_token=live_access_token_xyz"
        assert "live_access_token_xyz" not in _redact_creds(msg)

    def test_does_not_redact_other_values(self):
        msg = "user_id=12345&email=user@example.com"
        assert _redact_creds(msg) == msg

    def test_case_insensitive_redaction(self):
        msg = "CLIENT_SECRET=hidden_value"
        assert "hidden_value" not in _redact_creds(msg)


# ---------------------------------------------------------------------------
# _infer_mime_type helper
# ---------------------------------------------------------------------------


class TestInferMimeType:
    """MIME type inference from file extension."""

    def test_txt_extension(self):
        assert _infer_mime_type("report.txt") == "text/plain"

    def test_csv_extension(self):
        assert _infer_mime_type("data.csv") == "text/csv"

    def test_json_extension(self):
        assert _infer_mime_type("config.json") == "application/json"

    def test_md_extension(self):
        assert _infer_mime_type("readme.md") == "text/markdown"

    def test_html_extension(self):
        assert _infer_mime_type("page.html") == "text/html"

    def test_htm_extension(self):
        assert _infer_mime_type("page.htm") == "text/html"

    def test_unknown_extension_falls_back_to_octet_stream(self):
        assert _infer_mime_type("file.xyz123unknown") == "application/octet-stream"

    def test_no_extension_falls_back_to_octet_stream(self):
        assert _infer_mime_type("Makefile") == "application/octet-stream"


# ---------------------------------------------------------------------------
# Tools return not-configured error when credentials are missing
# ---------------------------------------------------------------------------


class TestNotConfiguredError:
    """All tools return a meaningful error dict when credentials are not configured."""

    async def _test_tool_returns_error(self, module: GoogleDriveModule, method_name: str, **kwargs):
        method = getattr(module, f"_{method_name}")
        result = await method(**kwargs)
        assert result.get("status") == "error"
        assert "Google Drive not connected" in result.get("error", "")

    async def test_list_files_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(module, "drive_list_files")

    async def test_get_metadata_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(module, "drive_get_file_metadata", file_id="abc")

    async def test_read_file_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(module, "drive_read_file", file_id="abc")

    async def test_write_file_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(
            module, "drive_write_file", name="test.txt", content="hello"
        )

    async def test_create_folder_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(module, "drive_create_folder", name="myfolder")

    async def test_move_file_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(
            module, "drive_move_file", file_id="fid", new_parent_id="pid"
        )

    async def test_search_files_not_configured(self):
        module = _make_module()
        await self._test_tool_returns_error(module, "drive_search_files", query="test")
