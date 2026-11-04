"""Focused tests for the Spotify Web API client."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.spotify_client import (
    SpotifyAuthError,
    SpotifyClient,
    SpotifyTokenRefreshUnavailableError,
)
from butlers.testing.fake_pool import AsyncCtx

pytestmark = pytest.mark.unit


def _credential_store(*, expires_at: str | datetime | None = None) -> AsyncMock:
    store = AsyncMock()
    store.pool = MagicMock()
    store.pool.spotify_values = {
        "spotify_oauth_access": "access-token",
        "spotify_oauth_refresh": "refresh-token",
        "spotify_oauth_expires_at": expires_at,
    }

    async def _resolve(key: str) -> str | None:
        values = {
            "SPOTIFY_CLIENT_ID": "client-id",
        }
        return values.get(key)

    store.resolve = AsyncMock(side_effect=_resolve)
    store.store = AsyncMock()
    return store


@pytest.fixture(autouse=True)
def _owner_entity_info_resolver(monkeypatch):
    async def _resolve(pool: object, info_type: str) -> str | None:
        return pool.spotify_values.get(info_type)

    monkeypatch.setattr("butlers.spotify_client.resolve_owner_entity_info", _resolve)


def _response(
    status_code: int,
    json_data: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.headers = httpx.Headers(headers or {})
    response.json = MagicMock(return_value=json_data)
    response.text = json.dumps(json_data)
    return response


async def test_token_refresh_temporarily_unavailable_is_retryable() -> None:
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            503,
            {"error": "temporarily_unavailable", "error_description": ""},
            headers={"Retry-After": "42"},
        )
    )

    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyTokenRefreshUnavailableError) as exc_info:
        await client.get_me()

    assert exc_info.value.retry_after_s == 42.0
    store.store.assert_not_awaited()


async def test_token_refresh_oauth_temporarily_unavailable_is_retryable_on_400() -> None:
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            400,
            {"error": "temporarily_unavailable", "error_description": ""},
            headers={"Retry-After": "30"},
        )
    )

    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyTokenRefreshUnavailableError) as exc_info:
        await client.get_me()

    assert exc_info.value.retry_after_s == 30.0
    store.store.assert_not_awaited()


async def test_token_refresh_invalid_grant_remains_auth_error() -> None:
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            400,
            {"error": "invalid_grant", "error_description": "Refresh token revoked"},
        )
    )

    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyAuthError):
        await client.get_me()

    store.store.assert_not_awaited()


async def test_token_refresh_provider_text_is_absent_from_exception_and_logs(caplog) -> None:
    marker = "PROVIDER_REFRESH_RESPONSE_MARKER"
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            400,
            {"error": marker, "error_description": marker},
        )
    )
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyAuthError) as exc_info:
        await client.get_me()

    assert marker not in str(exc_info.value)
    assert marker not in caplog.text


async def test_malformed_token_refresh_body_is_absent_from_exception_and_logs(caplog) -> None:
    marker = "MALFORMED_REFRESH_RESPONSE_MARKER"
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    store = _credential_store(expires_at=expires_at)
    response = _response(200, {})
    response.json.side_effect = ValueError("invalid json")
    response.text = marker
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(return_value=response)
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with pytest.raises(SpotifyAuthError) as exc_info:
        await client.get_me()

    assert marker not in str(exc_info.value)
    assert marker not in caplog.text


@pytest.mark.parametrize(
    ("payload", "marker"),
    [
        pytest.param(
            {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": "MALFORMED_REFRESH_EXPIRY_MARKER",
            },
            "MALFORMED_REFRESH_EXPIRY_MARKER",
            id="expiry",
        ),
        pytest.param(
            {
                "access_token": "rotated-access",
                "refresh_token": ["MALFORMED_ROTATED_REFRESH_MARKER"],
                "expires_in": 3600,
            },
            "MALFORMED_ROTATED_REFRESH_MARKER",
            id="rotated-refresh",
        ),
    ],
)
async def test_token_refresh_rejects_malformed_success_payload_before_persisting(
    payload: dict[str, Any], marker: str, caplog
) -> None:
    store = _credential_store()
    conn = MagicMock()

    conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
    store.pool.acquire = lambda: AsyncCtx(conn)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(return_value=_response(200, payload))
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with patch(
        "butlers.spotify_client.upsert_owner_entity_info_on_connection",
        new_callable=AsyncMock,
        return_value=True,
    ) as upsert:
        with pytest.raises(SpotifyAuthError) as exc_info:
            await client._refresh_access_token()

    assert str(exc_info.value) == (
        "Spotify token refresh returned an unexpected response. "
        "Re-connect Spotify via dashboard settings."
    )
    assert marker not in str(exc_info.value)
    assert marker not in caplog.text
    upsert.assert_not_awaited()


async def test_owner_expiry_accepts_decoded_datetime() -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    store = _credential_store(expires_at=expires_at)
    client = SpotifyClient(credential_store=store, http_client=AsyncMock(spec=httpx.AsyncClient))

    await client.open()

    assert client._expires_at == expires_at


async def test_successful_refresh_persists_owner_rows_in_one_transaction() -> None:
    store = _credential_store()
    conn = MagicMock()

    conn.transaction = MagicMock(side_effect=lambda: AsyncCtx(None))
    store.pool.acquire = lambda: AsyncCtx(conn)
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.post = AsyncMock(
        return_value=_response(
            200,
            {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            },
        )
    )
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    with patch(
        "butlers.spotify_client.upsert_owner_entity_info_on_connection",
        new_callable=AsyncMock,
        return_value=True,
    ) as upsert:
        await client._refresh_access_token()

    assert [call.args[1] for call in upsert.await_args_list] == [
        "spotify_oauth_access",
        "spotify_oauth_refresh",
        "spotify_oauth_expires_at",
    ]
    assert all(call.args[0] is conn for call in upsert.await_args_list)


async def test_current_playback_requests_track_and_episode_types() -> None:
    store = _credential_store()
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.request = AsyncMock(
        return_value=_response(
            200,
            {"is_playing": True, "item": {"type": "episode", "id": "episode-1"}},
        )
    )
    client = SpotifyClient(credential_store=store, http_client=http_client)
    await client.open()

    await client.get_currently_playing()

    assert http_client.request.await_args.kwargs["params"] == {"additional_types": "track,episode"}


async def test_oauth_tokens_resolve_only_from_owner_entity_info() -> None:
    store = _credential_store()
    values = {
        "spotify_oauth_access": "owner-access",
        "spotify_oauth_refresh": "owner-refresh",
        "spotify_oauth_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }

    async def _resolve(_pool: object, info_type: str) -> str | None:
        return values.get(info_type)

    with patch(
        "butlers.spotify_client.resolve_owner_entity_info",
        side_effect=_resolve,
    ) as resolve_owner:
        client = SpotifyClient(credential_store=store, http_client=AsyncMock())
        await client.open()

    assert resolve_owner.await_count == 3
    store.resolve.assert_awaited_once_with("SPOTIFY_CLIENT_ID")


@pytest.mark.parametrize(
    ("status", "code", "expected_type"),
    [
        (400, "invalid_grant", "SpotifyTokenRejectedError"),
        (401, "invalid_client", "SpotifyTokenRejectedError"),
        (403, "UNTRUSTED_PROVIDER_CODE", "SpotifyTokenRejectedError"),
        (503, "server_error", "SpotifyTokenRefreshUnavailableError"),
    ],
)
async def test_token_failure_keeps_only_typed_status_and_allowlisted_code(
    status,
    code,
    expected_type,
) -> None:
    store = _credential_store(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post.return_value = _response(status, {"error": code, "error_description": "PRIVATE_BODY"})
    client = SpotifyClient(credential_store=store, http_client=http)
    await client.open()
    with pytest.raises((SpotifyAuthError, SpotifyTokenRefreshUnavailableError)) as error:
        await client.get_me()
    assert type(error.value).__name__ == expected_type
    assert getattr(error.value, "status_code", None) == status
    assert getattr(error.value, "oauth_error", None) == (
        code if code != "UNTRUSTED_PROVIDER_CODE" else None
    )
    assert "PRIVATE_BODY" not in str(error.value)
    assert "UNTRUSTED_PROVIDER_CODE" not in str(error.value)
    http.request.assert_not_awaited()


async def test_resource_rejection_after_refresh_is_distinct_from_token_revocation() -> None:
    store = _credential_store(expires_at=datetime.now(UTC) + timedelta(hours=1))
    conn = MagicMock()
    conn.transaction = lambda: AsyncCtx(None)
    store.pool.acquire = lambda: AsyncCtx(conn)
    http = AsyncMock(spec=httpx.AsyncClient)
    http.request.side_effect = [_response(401, {}), _response(401, {})]
    http.post.return_value = _response(200, {"access_token": "next", "expires_in": 3600})
    client = SpotifyClient(credential_store=store, http_client=http)
    await client.open()
    with patch(
        "butlers.spotify_client.upsert_owner_entity_info_on_connection",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with pytest.raises(SpotifyAuthError) as error:
            await client.get_me()
    assert type(error.value).__name__ == "SpotifyResourceUnauthorizedError"
    assert http.request.await_count == 2
    http.post.assert_awaited_once()


@pytest.mark.parametrize(
    "value",
    [
        "2026-01-02T03:04:05Z",
        "2026-01-02T03:04:05",
        datetime(2026, 1, 2, 3, 4, 5),
        "malformed-expiry",
    ],
)
async def test_owner_expiry_representations_keep_existing_normalization(value) -> None:
    store = _credential_store(expires_at=value)
    client = SpotifyClient(credential_store=store, http_client=AsyncMock(spec=httpx.AsyncClient))
    await client.open()
    assert client._expires_at.tzinfo is not None
    if value == "malformed-expiry":
        assert client._expires_at < datetime.now(UTC)
    else:
        assert client._expires_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.mark.parametrize("refresh_unknown", [False, True])
async def test_unknown_expiry_default_and_connector_override(refresh_unknown) -> None:
    http = AsyncMock(spec=httpx.AsyncClient)
    http.request.return_value = _response(200, {"id": "owner"})
    http.post.return_value = _response(400, {"error": "invalid_grant"})
    client = SpotifyClient(
        credential_store=_credential_store(),
        http_client=http,
        refresh_when_expiry_unknown=refresh_unknown,
    )
    await client.open()
    if refresh_unknown:
        with pytest.raises(SpotifyAuthError):
            await client.get_me()
        http.post.assert_awaited_once()
        http.request.assert_not_awaited()
    else:
        assert await client.get_me() == {"id": "owner"}
        http.post.assert_not_awaited()


@pytest.mark.parametrize(("header", "expected"), [("7", 7.0), (None, 43.0), ("bad", 43.0)])
async def test_resource_rate_limit_uses_canonical_delay_without_request_retry(
    monkeypatch, header, expected
) -> None:
    import butlers.spotify_client as shared

    monkeypatch.setattr(shared, "_jittered_backoff", lambda *a, **k: 43.0)
    http = AsyncMock(spec=httpx.AsyncClient)
    http.request.return_value = _response(
        429, {}, headers={} if header is None else {"Retry-After": header}
    )
    calls = []
    client = SpotifyClient(
        credential_store=_credential_store(),
        http_client=http,
        on_api_call=lambda method, status: calls.append((method, status)),
    )
    await client.open()
    with pytest.raises(shared.SpotifyRateLimitError) as error:
        await client.get_currently_playing()
    assert error.value.retry_after_s == expected
    assert calls == [("currently_playing", "429")]
    http.request.assert_awaited_once()
    http.post.assert_not_awaited()


@pytest.mark.parametrize("stage", [1, 2, 3, "commit"])
async def test_refresh_persistence_failure_retains_prior_tokens_and_transaction_authority(
    stage,
) -> None:
    from contextlib import asynccontextmanager

    store = _credential_store()
    conn = MagicMock()
    transaction_errors = []

    @asynccontextmanager
    async def transaction():
        try:
            yield
            if stage == "commit":
                raise RuntimeError("PRIVATE_DB_DETAIL")
        except Exception as error:
            transaction_errors.append(type(error).__name__)
            raise

    conn.transaction = transaction
    store.pool.acquire = lambda: AsyncCtx(conn)
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post.return_value = _response(
        200, {"access_token": "next", "refresh_token": "rotated", "expires_in": 3600}
    )
    events = []
    client = SpotifyClient(credential_store=store, http_client=http, on_token_refresh=events.append)
    await client.open()
    before = (client._access_token, client._refresh_token, client._expires_at)
    rows = []

    async def upsert(connection, kind, value, *, secured):
        assert connection is conn and secured is True
        rows.append(kind)
        return len(rows) != stage

    with patch("butlers.spotify_client.upsert_owner_entity_info_on_connection", side_effect=upsert):
        with pytest.raises(SpotifyAuthError) as error:
            await client._refresh_access_token()
    assert type(error.value).__name__ == "SpotifyCredentialPersistError"
    assert (client._access_token, client._refresh_token, client._expires_at) == before
    assert rows == ["spotify_oauth_access", "spotify_oauth_refresh", "spotify_oauth_expires_at"]
    assert transaction_errors
    assert (
        events == []
    )  # The original connector counted neither malformed nor persistence failures.
    assert "PRIVATE_DB_DETAIL" not in str(error.value)
    http.request.assert_not_awaited()


@pytest.mark.parametrize("token_timeout", [None, 15.0])
async def test_optional_token_timeout_and_refresh_metric_preserve_module_defaults(
    token_timeout,
) -> None:
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post.return_value = _response(503, {"error": "server_error"}, headers={"Retry-After": "2"})
    events = []
    client = SpotifyClient(
        credential_store=_credential_store(),
        http_client=http,
        token_timeout_s=token_timeout,
        on_token_refresh=events.append,
    )
    await client.open()
    with pytest.raises(SpotifyTokenRefreshUnavailableError):
        await client._refresh_access_token()
    assert events == ["error"]
    if token_timeout is None:
        assert "timeout" not in http.post.await_args.kwargs
    else:
        assert http.post.await_args.kwargs["timeout"] == 15.0


@pytest.mark.parametrize("kind", ["album", "artist", "playlist"])
async def test_context_helpers_keep_resource_identity_and_empty_response_contract(kind) -> None:
    from butlers.spotify_client import SpotifyAPIError

    http = AsyncMock(spec=httpx.AsyncClient)
    http.request.side_effect = [_response(200, {"name": "context"}), _response(204, {})]
    client = SpotifyClient(credential_store=_credential_store(), http_client=http)
    await client.open()
    method = getattr(client, "get_" + kind)
    assert await method("id") == {"name": "context"}
    assert http.request.await_args.args[1] == "https://api.spotify.com/v1/" + kind + "s/id"
    with pytest.raises(SpotifyAPIError) as error:
        await method("id")
    assert error.value.status_code == 204


async def test_cancelled_request_has_no_retry_refresh_or_failure_hook() -> None:
    import asyncio

    http = AsyncMock(spec=httpx.AsyncClient)
    http.request.side_effect = asyncio.CancelledError()
    events = []
    client = SpotifyClient(
        credential_store=_credential_store(),
        http_client=http,
        on_api_call=lambda *args: events.append(args),
        on_token_refresh=events.append,
    )
    await client.open()
    with pytest.raises(asyncio.CancelledError):
        await client.get_me()
    assert events == []
    http.request.assert_awaited_once()
    http.post.assert_not_awaited()
    await client.close()
    http.aclose.assert_not_awaited()  # Borrowed transport lifetime remains with its caller.


async def test_owned_transport_keeps_default_timeout_and_closes_once(monkeypatch) -> None:
    import butlers.spotify_client as shared

    http = AsyncMock(spec=httpx.AsyncClient)
    factory = MagicMock(return_value=http)
    monkeypatch.setattr(shared.httpx, "AsyncClient", factory)
    client = SpotifyClient(credential_store=_credential_store())
    await client.open()
    factory.assert_called_once_with(timeout=30.0)
    await client.close()
    await client.close()
    http.aclose.assert_awaited_once()


@pytest.mark.parametrize("missing", ["_refresh_token", "_client_id"])
async def test_missing_refresh_inputs_never_call_transport_or_write(missing) -> None:
    http = AsyncMock(spec=httpx.AsyncClient)
    client = SpotifyClient(credential_store=_credential_store(), http_client=http)
    await client.open()
    setattr(client, missing, None)
    with pytest.raises(SpotifyAuthError) as error:
        await client._refresh_access_token()
    assert type(error.value).__name__ == "SpotifyCredentialsMissingError"
    http.post.assert_not_awaited()
    http.request.assert_not_awaited()
