"""Spotify credential-tier boundary tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.credential_store import CredentialStore
from butlers.spotify_credentials import (
    SPOTIFY_CATEGORY,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_MANAGED_ENTITY_INFO_TYPES,
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


async def test_credential_store_owns_only_spotify_client_configuration() -> None:
    """CredentialStore is Tier 1 client configuration, never OAuth token authority."""
    assert SPOTIFY_CATEGORY == "spotify"
    assert SPOTIFY_MANAGED_ENTITY_INFO_TYPES == {
        "spotify_oauth_access",
        "spotify_oauth_refresh",
        "spotify_oauth_expires_at",
    }

    pool = _make_pool(execute_return="INSERT 0 1")
    store = CredentialStore(pool)
    await store.store(SPOTIFY_CLIENT_ID, "client-config", category=SPOTIFY_CATEGORY)
    assert len(pool._conn.execute.call_args_list) == 1
    assert pool._conn.execute.call_args.args[3] == "spotify"

    row = _make_row(secret_value="client-config")
    assert (
        await CredentialStore(_make_pool(fetchrow_return=row)).resolve(SPOTIFY_CLIENT_ID)
        == "client-config"
    )

    pool2 = _make_pool(execute_return="DELETE 1")
    assert await CredentialStore(pool2).delete(SPOTIFY_CLIENT_ID)
