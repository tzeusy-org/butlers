"""Unit tests for Spotify credential key constants and CredentialStore integration.

Covers tasks 2.1-2.2 from openspec/changes/connector-spotify/tasks.md:
- Key constants are correctly defined (SPOTIFY_CLIENT_ID, SPOTIFY_ACCESS_TOKEN,
  SPOTIFY_REFRESH_TOKEN, SPOTIFY_TOKEN_EXPIRES_AT)
- Storing, resolving, and deleting Spotify credentials via CredentialStore
  with category="spotify" and is_sensitive=True
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import CredentialStore
from butlers.spotify_credentials import (
    _SPOTIFY_CATEGORY,
    SPOTIFY_ACCESS_TOKEN,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_REFRESH_TOKEN,
    SPOTIFY_TOKEN_EXPIRES_AT,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_credential_store.py)
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    fetchrow_return=None,
    fetch_return=None,
    execute_return: str = "DELETE 0",
) -> MagicMock:
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.fetchrow.return_value = fetchrow_return
    conn.fetch.return_value = fetch_return or []
    conn.execute.return_value = execute_return

    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire.return_value = cm
    pool._conn = conn
    return pool


def _make_row(**kwargs) -> MagicMock:
    """Build a mock asyncpg Record-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: kwargs[key]
    return row


# ---------------------------------------------------------------------------
# Key constant definitions
# ---------------------------------------------------------------------------


class TestKeyConstants:
    """Verify Spotify credential key constants are defined with correct values."""

    def test_client_id_key(self) -> None:
        assert SPOTIFY_CLIENT_ID == "SPOTIFY_CLIENT_ID"

    def test_access_token_key(self) -> None:
        assert SPOTIFY_ACCESS_TOKEN == "SPOTIFY_ACCESS_TOKEN"

    def test_refresh_token_key(self) -> None:
        assert SPOTIFY_REFRESH_TOKEN == "SPOTIFY_REFRESH_TOKEN"

    def test_token_expires_at_key(self) -> None:
        assert SPOTIFY_TOKEN_EXPIRES_AT == "SPOTIFY_TOKEN_EXPIRES_AT"

    def test_spotify_category(self) -> None:
        assert _SPOTIFY_CATEGORY == "spotify"

    def test_keys_are_distinct(self) -> None:
        keys = [
            SPOTIFY_CLIENT_ID,
            SPOTIFY_ACCESS_TOKEN,
            SPOTIFY_REFRESH_TOKEN,
            SPOTIFY_TOKEN_EXPIRES_AT,
        ]
        assert len(keys) == len(set(keys)), "All Spotify credential keys must be unique"

    def test_keys_are_strings(self) -> None:
        for key in [
            SPOTIFY_CLIENT_ID,
            SPOTIFY_ACCESS_TOKEN,
            SPOTIFY_REFRESH_TOKEN,
            SPOTIFY_TOKEN_EXPIRES_AT,
        ]:
            assert isinstance(key, str), f"Key {key!r} must be a str"


# ---------------------------------------------------------------------------
# store() with category="spotify"
# ---------------------------------------------------------------------------


class TestSpotifyStore:
    """Tests for storing Spotify credentials via CredentialStore."""

    async def test_store_client_id_with_spotify_category(self) -> None:
        pool = _make_pool(execute_return="INSERT 0 1")
        store = CredentialStore(pool)
        await store.store(
            SPOTIFY_CLIENT_ID,
            "abc123def456abc123def456abc12345",
            category=_SPOTIFY_CATEGORY,
        )
        sql, *args = pool._conn.execute.call_args[0]
        assert args[0] == SPOTIFY_CLIENT_ID
        assert args[1] == "abc123def456abc123def456abc12345"
        assert args[2] == "spotify"

    async def test_store_access_token_is_sensitive_by_default(self) -> None:
        pool = _make_pool(execute_return="INSERT 0 1")
        store = CredentialStore(pool)
        await store.store(
            SPOTIFY_ACCESS_TOKEN,
            "BQDsomething",
            category=_SPOTIFY_CATEGORY,
        )
        _, *args = pool._conn.execute.call_args[0]
        # is_sensitive is the 5th positional arg (index 4)
        assert args[4] is True

    async def test_store_refresh_token_with_spotify_category(self) -> None:
        pool = _make_pool(execute_return="INSERT 0 1")
        store = CredentialStore(pool)
        await store.store(
            SPOTIFY_REFRESH_TOKEN,
            "AQAxyz789",
            category=_SPOTIFY_CATEGORY,
            is_sensitive=True,
        )
        _, *args = pool._conn.execute.call_args[0]
        assert args[0] == SPOTIFY_REFRESH_TOKEN
        assert args[2] == "spotify"
        assert args[4] is True

    async def test_store_token_expires_at_with_spotify_category(self) -> None:
        pool = _make_pool(execute_return="INSERT 0 1")
        store = CredentialStore(pool)
        await store.store(
            SPOTIFY_TOKEN_EXPIRES_AT,
            "2026-03-25T14:30:00Z",
            category=_SPOTIFY_CATEGORY,
            is_sensitive=False,
        )
        _, *args = pool._conn.execute.call_args[0]
        assert args[0] == SPOTIFY_TOKEN_EXPIRES_AT
        assert args[2] == "spotify"
        # is_sensitive passed explicitly as False
        assert args[4] is False

    async def test_store_does_not_log_access_token(self, caplog: pytest.LogCaptureFixture) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        with caplog.at_level("DEBUG"):
            await store.store(
                SPOTIFY_ACCESS_TOKEN,
                "SUPER_SECRET_SPOTIFY_ACCESS_TOKEN",
                category=_SPOTIFY_CATEGORY,
            )
        for record in caplog.records:
            assert "SUPER_SECRET_SPOTIFY_ACCESS_TOKEN" not in record.getMessage()

    async def test_store_does_not_log_refresh_token(self, caplog: pytest.LogCaptureFixture) -> None:
        pool = _make_pool()
        store = CredentialStore(pool)
        with caplog.at_level("DEBUG"):
            await store.store(
                SPOTIFY_REFRESH_TOKEN,
                "SUPER_SECRET_SPOTIFY_REFRESH_TOKEN",
                category=_SPOTIFY_CATEGORY,
            )
        for record in caplog.records:
            assert "SUPER_SECRET_SPOTIFY_REFRESH_TOKEN" not in record.getMessage()

    async def test_store_all_spotify_keys_upsert(self) -> None:
        """Verify that all four Spotify keys can be stored (insert+upsert)."""
        pool = _make_pool(execute_return="INSERT 0 1")
        store = CredentialStore(pool)
        spotify_secrets = {
            SPOTIFY_CLIENT_ID: "abc123def456abc123def456abc12345",
            SPOTIFY_ACCESS_TOKEN: "BQD_access",
            SPOTIFY_REFRESH_TOKEN: "AQA_refresh",
            SPOTIFY_TOKEN_EXPIRES_AT: "2026-03-25T14:30:00Z",
        }
        for key, value in spotify_secrets.items():
            await store.store(key, value, category=_SPOTIFY_CATEGORY)
        assert pool._conn.execute.call_count == 4


# ---------------------------------------------------------------------------
# resolve() with Spotify keys
# ---------------------------------------------------------------------------


class TestSpotifyResolve:
    """Tests for resolving Spotify credentials via CredentialStore."""

    async def test_resolve_access_token_from_db(self) -> None:
        row = _make_row(secret_value="BQD_access_token")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.resolve(SPOTIFY_ACCESS_TOKEN)
        assert result == "BQD_access_token"

    async def test_resolve_refresh_token_from_db(self) -> None:
        row = _make_row(secret_value="AQA_refresh_token")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.resolve(SPOTIFY_REFRESH_TOKEN)
        assert result == "AQA_refresh_token"

    async def test_resolve_client_id_from_db(self) -> None:
        row = _make_row(secret_value="abc123def456abc123def456abc12345")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.resolve(SPOTIFY_CLIENT_ID)
        assert result == "abc123def456abc123def456abc12345"

    async def test_resolve_token_expires_at_from_db(self) -> None:
        row = _make_row(secret_value="2026-03-25T14:30:00Z")
        pool = _make_pool(fetchrow_return=row)
        store = CredentialStore(pool)
        result = await store.resolve(SPOTIFY_TOKEN_EXPIRES_AT)
        assert result == "2026-03-25T14:30:00Z"

    async def test_resolve_returns_none_when_not_stored(self) -> None:
        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        result = await store.resolve(SPOTIFY_ACCESS_TOKEN)
        assert result is None

    async def test_resolve_no_env_fallback_by_default(self) -> None:
        """Spotify tokens must never fall back to environment variables by default."""
        import os
        from unittest.mock import patch

        pool = _make_pool(fetchrow_return=None)
        store = CredentialStore(pool)
        with patch.dict(os.environ, {SPOTIFY_ACCESS_TOKEN: "env-access-token"}):
            result = await store.resolve(SPOTIFY_ACCESS_TOKEN)
        assert result is None

    async def test_resolve_uses_fallback_pool_when_local_missing(self) -> None:
        local_pool = _make_pool(fetchrow_return=None)
        fallback_row = _make_row(secret_value="shared-spotify-token")
        fallback_pool = _make_pool(fetchrow_return=fallback_row)
        store = CredentialStore(local_pool, fallback_pools=[fallback_pool])

        result = await store.resolve(SPOTIFY_REFRESH_TOKEN)

        assert result == "shared-spotify-token"
        assert local_pool.acquire.call_count == 1
        assert fallback_pool.acquire.call_count == 1


# ---------------------------------------------------------------------------
# delete() with Spotify keys
# ---------------------------------------------------------------------------


class TestSpotifyDelete:
    """Tests for deleting Spotify credentials via CredentialStore."""

    async def test_delete_access_token_returns_true_when_deleted(self) -> None:
        pool = _make_pool(execute_return="DELETE 1")
        store = CredentialStore(pool)
        result = await store.delete(SPOTIFY_ACCESS_TOKEN)
        assert result is True

    async def test_delete_refresh_token_returns_true_when_deleted(self) -> None:
        pool = _make_pool(execute_return="DELETE 1")
        store = CredentialStore(pool)
        result = await store.delete(SPOTIFY_REFRESH_TOKEN)
        assert result is True

    async def test_delete_client_id_returns_false_when_not_found(self) -> None:
        pool = _make_pool(execute_return="DELETE 0")
        store = CredentialStore(pool)
        result = await store.delete(SPOTIFY_CLIENT_ID)
        assert result is False

    async def test_delete_token_expires_at_executes_correct_sql(self) -> None:
        pool = _make_pool(execute_return="DELETE 1")
        store = CredentialStore(pool)
        await store.delete(SPOTIFY_TOKEN_EXPIRES_AT)
        sql, *args = pool._conn.execute.call_args[0]
        assert "DELETE FROM" in sql
        assert "butler_secrets" in sql
        assert args[0] == SPOTIFY_TOKEN_EXPIRES_AT

    async def test_delete_all_spotify_keys(self) -> None:
        """Verify all four Spotify credential keys can be deleted (disconnect flow)."""
        pool = _make_pool(execute_return="DELETE 1")
        store = CredentialStore(pool)
        keys = [
            SPOTIFY_CLIENT_ID,
            SPOTIFY_ACCESS_TOKEN,
            SPOTIFY_REFRESH_TOKEN,
            SPOTIFY_TOKEN_EXPIRES_AT,
        ]
        results = [await store.delete(key) for key in keys]
        assert all(results), "All Spotify keys should report as deleted"
        assert pool._conn.execute.call_count == 4


# ---------------------------------------------------------------------------
# list_secrets() filtered by category="spotify"
# ---------------------------------------------------------------------------


class TestSpotifyListSecrets:
    """Tests for listing Spotify credentials by category."""

    async def test_list_secrets_filters_by_spotify_category(self) -> None:
        pool = _make_pool(fetch_return=[])
        store = CredentialStore(pool)
        await store.list_secrets(category=_SPOTIFY_CATEGORY)
        sql, *args = pool._conn.fetch.call_args[0]
        assert "category" in sql
        assert args[0] == "spotify"
