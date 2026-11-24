"""Spotify credential-tier boundary tests."""

from __future__ import annotations

import pytest

from butlers.credential_store import CredentialStore
from butlers.spotify_credentials import (
    SPOTIFY_CATEGORY,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_MANAGED_ENTITY_INFO_TYPES,
)
from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import row as _make_row

pytestmark = pytest.mark.unit


async def test_credential_store_owns_only_spotify_client_configuration() -> None:
    """CredentialStore is Tier 1 client configuration, never OAuth token authority."""
    assert SPOTIFY_CATEGORY == "spotify"
    assert SPOTIFY_MANAGED_ENTITY_INFO_TYPES == {
        "spotify_oauth_access",
        "spotify_oauth_refresh",
        "spotify_oauth_expires_at",
    }

    pool = fake_pool(execute="INSERT 0 1")
    store = CredentialStore(pool)
    await store.store(SPOTIFY_CLIENT_ID, "client-config", category=SPOTIFY_CATEGORY)
    assert len(pool.connection.execute.call_args_list) == 1
    assert pool.connection.execute.call_args.args[3] == "spotify"

    row = _make_row(secret_value="client-config")
    assert (
        await CredentialStore(fake_pool(fetchrow=row)).resolve(SPOTIFY_CLIENT_ID) == "client-config"
    )

    pool2 = fake_pool(execute="DELETE 1")
    assert await CredentialStore(pool2).delete(SPOTIFY_CLIENT_ID)
