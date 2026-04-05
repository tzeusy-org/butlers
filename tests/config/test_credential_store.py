"""Unit tests for butlers.credential_store.CredentialStore.

All tests mock the asyncpg pool — no real database required.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.credential_store import (
    CredentialStore,
    SecretMetadata,
    _ensure_utc,
    _is_missing_table_error,
    shared_db_name_from_env,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_pool(
    *,
    fetchrow_return=None,
    fetch_return=None,
    execute_return: str = "DELETE 0",
) -> MagicMock:
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
    row = MagicMock()
    row.__getitem__ = lambda self, key: kwargs[key]
    return row


# ---------------------------------------------------------------------------
# store / load / has / delete
# ---------------------------------------------------------------------------


async def test_store_validates_and_upserts(caplog: pytest.LogCaptureFixture) -> None:
    """store() upserts correctly; rejects empty keys/values; doesn't log secret values."""
    pool = _make_pool(execute_return="INSERT 0 1")
    store = CredentialStore(pool)
    await store.store("MY_KEY", "my-value")
    sql, *args = pool._conn.execute.call_args[0]
    assert "INSERT INTO" in sql and "butler_secrets" in sql and "ON CONFLICT" in sql
    assert args[0] == "MY_KEY" and args[1] == "my-value"

    # Category default and custom
    await store.store("KEY", "value")
    assert pool._conn.execute.call_args[0][3] == "general"
    await store.store("KEY", "value", category="telegram")
    assert pool._conn.execute.call_args[0][3] == "telegram"

    # Rejects empty / whitespace
    for key, value in [("", "val"), ("   ", "val"), ("KEY", "")]:
        with pytest.raises(ValueError):
            await store.store(key, value)

    # Secret value not logged
    with caplog.at_level("DEBUG"):
        await store.store("K", "SUPER_SECRET_XYZ")
    assert not any("SUPER_SECRET_XYZ" in r.getMessage() for r in caplog.records)


async def test_load_has_delete() -> None:
    """load/has/delete return correct values based on row presence."""
    row = _make_row(secret_value="tok_abc123")
    assert await CredentialStore(_make_pool(fetchrow_return=row)).load("K") == "tok_abc123"
    assert await CredentialStore(_make_pool(fetchrow_return=None)).load("K") is None
    assert await CredentialStore(_make_pool(fetchrow_return=row)).has("K") is True
    assert await CredentialStore(_make_pool(fetchrow_return=None)).has("K") is False
    assert await CredentialStore(_make_pool(execute_return="DELETE 1")).delete("K") is True
    assert await CredentialStore(_make_pool(execute_return="DELETE 0")).delete("K") is False


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


async def test_resolve_db_wins_env_fallback_optional_and_fallback_pool() -> None:
    """resolve(): DB wins over env; no env fallback by default; fallback pool used on local miss."""
    row = _make_row(secret_value="db-value")
    with patch.dict(os.environ, {"MY_KEY": "env-value"}):
        assert (
            await CredentialStore(_make_pool(fetchrow_return=row)).resolve("MY_KEY") == "db-value"
        )
        assert await CredentialStore(_make_pool(fetchrow_return=None)).resolve("MY_KEY") is None
        assert (
            await CredentialStore(_make_pool(fetchrow_return=None)).resolve(
                "MY_KEY", env_fallback=True
            )
            == "env-value"
        )

    fallback_pool = _make_pool(fetchrow_return=_make_row(secret_value="shared"))
    store = CredentialStore(_make_pool(fetchrow_return=None), fallback_pools=[fallback_pool])
    assert await store.resolve("K", env_fallback=False) == "shared"
    assert fallback_pool.acquire.call_count == 1

    local_pool = _make_pool(fetchrow_return=row)
    store2 = CredentialStore(local_pool, fallback_pools=[fallback_pool])
    assert await store2.resolve("K") == "db-value"
    assert fallback_pool.acquire.call_count == 1  # not called again


# ---------------------------------------------------------------------------
# list_secrets
# ---------------------------------------------------------------------------


async def test_list_secrets() -> None:
    """list_secrets returns SecretMetadata; filters by category; never exposes values."""

    def _db_row(key, category="general"):
        r = MagicMock()
        r.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": category,
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
        }[k]
        return r

    rows = [_db_row("K1"), _db_row("K2", "telegram")]
    pool = _make_pool(fetch_return=rows)
    result = await CredentialStore(pool).list_secrets()
    assert len(result) == 2 and all(isinstance(m, SecretMetadata) for m in result)
    assert result[0].is_set is True and result[0].source == "database"
    assert not hasattr(result[0], "secret_value")


# ---------------------------------------------------------------------------
# Helpers + repr
# ---------------------------------------------------------------------------


def test_helpers_and_repr() -> None:
    """Utilities and repr are correct."""
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert _ensure_utc(naive).tzinfo == UTC
    assert _is_missing_table_error(RuntimeError('relation "butler_secrets" does not exist')) is True

    with patch.dict(os.environ, {}, clear=True):
        assert shared_db_name_from_env() == "butlers"
    with patch.dict(os.environ, {"BUTLER_SHARED_DB_NAME": "my_shared"}):
        assert shared_db_name_from_env() == "my_shared"

    store = CredentialStore(_make_pool(), fallback_pools=[_make_pool()])
    r = repr(store)
    assert "CredentialStore" in r and "fallback_pools=1" in r

    meta = SecretMetadata(
        key="MY_KEY",
        category="general",
        description=None,
        is_sensitive=True,
        is_set=True,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
        source="database",
    )
    assert "MY_KEY" in repr(meta) and "general" in repr(meta)
