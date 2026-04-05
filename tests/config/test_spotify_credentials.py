"""Unit tests for Spotify credential key constants and CredentialStore integration."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import CredentialStore
from butlers.spotify_credentials import (
    SPOTIFY_ACCESS_TOKEN,
    SPOTIFY_CATEGORY,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_REFRESH_TOKEN,
    SPOTIFY_TOKEN_EXPIRES_AT,
)

pytestmark = pytest.mark.unit


def _make_pool(*, fetchrow_return=None, execute_return: str = "DELETE 0") -> MagicMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = fetchrow_return
    conn.fetch.return_value = []
    conn.execute.return_value = execute_return
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = cm
    pool._conn = conn
    return pool


def _make_row(**kwargs) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: kwargs[key]
    return row


async def test_spotify_credentials_store_resolve_delete() -> None:
    """Key constants unique; store with category=spotify; resolve from DB; no env fallback."""
    keys = [
        SPOTIFY_CLIENT_ID,
        SPOTIFY_ACCESS_TOKEN,
        SPOTIFY_REFRESH_TOKEN,
        SPOTIFY_TOKEN_EXPIRES_AT,
    ]
    assert all(isinstance(k, str) and k for k in keys) and len(keys) == len(set(keys))
    assert SPOTIFY_CATEGORY == "spotify"

    # store with category
    pool = _make_pool(execute_return="INSERT 0 1")
    store = CredentialStore(pool)
    for key, value in {
        SPOTIFY_CLIENT_ID: "abc123",
        SPOTIFY_ACCESS_TOKEN: "BQD",
        SPOTIFY_REFRESH_TOKEN: "AQA",
        SPOTIFY_TOKEN_EXPIRES_AT: "2026-03-25",
    }.items():
        await store.store(key, value, category=SPOTIFY_CATEGORY)
    assert pool._conn.execute.call_args[0][3] == "spotify"

    # resolve from DB
    row = _make_row(secret_value="BQD_access")
    assert (
        await CredentialStore(_make_pool(fetchrow_return=row)).resolve(SPOTIFY_ACCESS_TOKEN)
        == "BQD_access"
    )

    # no env fallback
    with patch.dict(os.environ, {SPOTIFY_ACCESS_TOKEN: "env-token"}):
        assert (
            await CredentialStore(_make_pool(fetchrow_return=None)).resolve(SPOTIFY_ACCESS_TOKEN)
            is None
        )

    # delete all
    pool2 = _make_pool(execute_return="DELETE 1")
    store2 = CredentialStore(pool2)
    results = [await store2.delete(k) for k in keys]
    assert all(results)
