"""Tests for shared Google credential storage (butlers.google_credentials).

Covers:
- GoogleCredentials model validation
- store_google_credentials() DB persistence
- load_google_credentials() DB lookup
- resolve_google_credentials() DB-only resolution
- Security: no secret material in repr/logs
- Error messages for missing/invalid credentials
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.google_credentials import (
    GoogleCredentials,
    InvalidGoogleCredentialsError,
    MissingGoogleCredentialsError,
    load_google_credentials,
    resolve_google_credentials,
    store_google_credentials,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_creds() -> GoogleCredentials:
    return GoogleCredentials(
        client_id="client-id-123.apps.googleusercontent.com",
        client_secret="super-secret-xyz",
        refresh_token="1//refresh-token-abc",
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )


def _make_conn(row: dict | None = None) -> AsyncMock:
    """Build a fake asyncpg connection mock."""
    conn = AsyncMock()
    if row is None:
        conn.fetchrow.return_value = None
    else:
        record = MagicMock()
        record.__getitem__ = lambda self, key: row[key]
        conn.fetchrow.return_value = record
    conn.execute.return_value = None
    return conn


# ---------------------------------------------------------------------------
# GoogleCredentials model
# ---------------------------------------------------------------------------


class TestGoogleCredentialsModel:
    def test_valid_credentials(self, fake_creds: GoogleCredentials) -> None:
        assert fake_creds.client_id == "client-id-123.apps.googleusercontent.com"
        assert fake_creds.client_secret == "super-secret-xyz"
        assert fake_creds.refresh_token == "1//refresh-token-abc"
        assert fake_creds.scope == "https://www.googleapis.com/auth/gmail.readonly"

    def test_scope_is_optional(self) -> None:
        creds = GoogleCredentials(client_id="id", client_secret="secret", refresh_token="token")
        assert creds.scope is None

    def test_strips_whitespace_from_required_fields(self) -> None:
        creds = GoogleCredentials(
            client_id="  id  ", client_secret="  secret  ", refresh_token="  token  "
        )
        assert creds.client_id == "id"
        assert creds.client_secret == "secret"
        assert creds.refresh_token == "token"

    def test_empty_client_id_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="", client_secret="s", refresh_token="r")

    def test_whitespace_only_client_id_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="   ", client_secret="s", refresh_token="r")

    def test_empty_client_secret_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="id", client_secret="", refresh_token="r")

    def test_empty_refresh_token_raises(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="id", client_secret="s", refresh_token="")

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):
            GoogleCredentials(client_id="id", client_secret="s", refresh_token="r", unknown="x")

    def test_repr_does_not_leak_secret(self, fake_creds: GoogleCredentials) -> None:
        """client_secret and refresh_token must never appear in repr()."""
        r = repr(fake_creds)
        assert "super-secret-xyz" not in r
        assert "1//refresh-token-abc" not in r
        assert "REDACTED" in r


# ---------------------------------------------------------------------------
# store_google_credentials
# ---------------------------------------------------------------------------


class TestStoreGoogleCredentials:
    async def test_store_executes_upsert(self) -> None:
        conn = _make_conn()
        await store_google_credentials(
            conn,
            client_id="cid",
            client_secret="csecret",
            refresh_token="rtoken",
            scope="scope1",
        )
        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert "INSERT INTO google_oauth_credentials" in args[0]
        assert "ON CONFLICT" in args[0]
        # Credential key must be "google"
        assert args[1] == "google"
        # The JSON payload
        payload = json.loads(args[2])
        assert payload["client_id"] == "cid"
        # Secrets are in payload (stored securely in DB, never logged)
        assert payload["client_secret"] == "csecret"
        assert payload["refresh_token"] == "rtoken"
        assert payload["scope"] == "scope1"

    async def test_store_without_scope(self) -> None:
        conn = _make_conn()
        await store_google_credentials(
            conn,
            client_id="cid",
            client_secret="csecret",
            refresh_token="rtoken",
        )
        args = conn.execute.call_args[0]
        payload = json.loads(args[2])
        assert payload["scope"] is None

    async def test_store_empty_client_id_raises(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="client_id"):
            await store_google_credentials(conn, client_id="", client_secret="s", refresh_token="r")

    async def test_store_empty_client_secret_raises(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="client_secret"):
            await store_google_credentials(
                conn, client_id="id", client_secret="", refresh_token="r"
            )

    async def test_store_empty_refresh_token_raises(self) -> None:
        conn = _make_conn()
        with pytest.raises(ValueError, match="refresh_token"):
            await store_google_credentials(
                conn, client_id="id", client_secret="s", refresh_token=""
            )

    async def test_store_does_not_log_secrets(self, caplog: pytest.LogCaptureFixture) -> None:
        """Secret material must never appear in log output."""
        conn = _make_conn()
        import logging

        with caplog.at_level(logging.DEBUG, logger="butlers.google_credentials"):
            await store_google_credentials(
                conn,
                client_id="public-id",
                client_secret="TOP-SECRET-123",
                refresh_token="1//SUPER-SECRET-TOKEN",
            )
        log_text = caplog.text
        assert "TOP-SECRET-123" not in log_text
        assert "1//SUPER-SECRET-TOKEN" not in log_text
        # client_id IS safe to log
        assert "public-id" in log_text


# ---------------------------------------------------------------------------
# load_google_credentials
# ---------------------------------------------------------------------------


class TestLoadGoogleCredentials:
    async def test_load_returns_none_when_no_row(self) -> None:
        conn = _make_conn(row=None)
        result = await load_google_credentials(conn)
        assert result is None

    async def test_load_returns_credentials_from_dict(self) -> None:
        payload = {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtoken",
            "scope": "scope1",
        }
        conn = _make_conn(row={"credentials": payload})
        result = await load_google_credentials(conn)
        assert result is not None
        assert result.client_id == "cid"
        assert result.client_secret == "csec"
        assert result.refresh_token == "rtoken"
        assert result.scope == "scope1"

    async def test_load_returns_credentials_from_json_string(self) -> None:
        payload = json.dumps(
            {"client_id": "cid", "client_secret": "csec", "refresh_token": "rtoken"}
        )
        conn = _make_conn(row={"credentials": payload})
        result = await load_google_credentials(conn)
        assert result is not None
        assert result.client_id == "cid"

    async def test_load_raises_on_malformed_json_string(self) -> None:
        conn = _make_conn(row={"credentials": "not-json{{"})
        with pytest.raises(InvalidGoogleCredentialsError, match="malformed"):
            await load_google_credentials(conn)

    async def test_load_raises_on_unexpected_type(self) -> None:
        conn = _make_conn(row={"credentials": 12345})
        with pytest.raises(InvalidGoogleCredentialsError, match="unexpected type"):
            await load_google_credentials(conn)

    async def test_load_raises_on_missing_required_fields(self) -> None:
        payload = {"client_id": "cid"}  # missing client_secret, refresh_token
        conn = _make_conn(row={"credentials": payload})
        with pytest.raises(InvalidGoogleCredentialsError) as exc_info:
            await load_google_credentials(conn)
        msg = str(exc_info.value)
        assert "client_secret" in msg or "refresh_token" in msg


# ---------------------------------------------------------------------------
# resolve_google_credentials
# ---------------------------------------------------------------------------


class TestResolveGoogleCredentials:
    async def test_resolve_uses_db_when_available(self) -> None:
        payload = {
            "client_id": "db-id",
            "client_secret": "db-secret",
            "refresh_token": "db-token",
            "scope": "db-scope",
        }
        conn = _make_conn(row={"credentials": payload})
        result = await resolve_google_credentials(conn, caller="test")
        assert result.client_id == "db-id"

    async def test_resolve_raises_when_db_empty(self) -> None:
        conn = _make_conn(row=None)
        with pytest.raises(MissingGoogleCredentialsError) as exc_info:
            await resolve_google_credentials(conn, caller="test")
        assert "butler_secrets" in str(exc_info.value)

    async def test_resolve_raises_when_db_invalid(self) -> None:
        # DB has invalid credentials (missing fields)
        payload = {"client_id": "cid"}  # missing secret and token
        conn = _make_conn(row={"credentials": payload})
        with pytest.raises(MissingGoogleCredentialsError) as exc_info:
            await resolve_google_credentials(conn, caller="test")
        msg = str(exc_info.value)
        assert "invalid" in msg.lower()

    async def test_resolve_error_message_is_actionable(self) -> None:
        conn = _make_conn(row=None)
        with pytest.raises(MissingGoogleCredentialsError) as exc_info:
            await resolve_google_credentials(conn, caller="gmail")
        msg = str(exc_info.value)
        # Must describe how to fix the problem
        assert "bootstrap" in msg.lower()

    async def test_resolve_does_not_log_secrets(self, caplog: pytest.LogCaptureFixture) -> None:
        payload = {
            "client_id": "db-id",
            "client_secret": "NEVER-LOG-THIS",
            "refresh_token": "NEVER-LOG-THIS-EITHER",
        }
        conn = _make_conn(row={"credentials": payload})
        import logging

        with caplog.at_level(logging.DEBUG, logger="butlers.google_credentials"):
            await resolve_google_credentials(conn, caller="test")
        assert "NEVER-LOG-THIS" not in caplog.text
