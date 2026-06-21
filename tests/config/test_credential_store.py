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
    EntityInfoRow,
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
            "last_verified": None,
            "last_test_ok": None,
            "last_test_code": None,
            "last_test_message": None,
        }[k]
        return r

    rows = [_db_row("K1"), _db_row("K2", "telegram")]
    pool = _make_pool(fetch_return=rows)
    result = await CredentialStore(pool).list_secrets()
    assert len(result) == 2 and all(isinstance(m, SecretMetadata) for m in result)
    assert result[0].is_set is True and result[0].source == "database"
    assert not hasattr(result[0], "secret_value")
    # Test-state columns default to None when never probed
    assert result[0].last_verified is None
    assert result[0].last_test_ok is None
    assert result[0].last_test_code is None
    assert result[0].last_test_message is None


async def test_list_secrets_test_state_columns() -> None:
    """list_secrets populates test-state columns from DB rows when present."""
    verified_at = datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC)

    def _db_row_probed(key: str) -> MagicMock:
        r = MagicMock()
        r.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": "general",
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
            "last_verified": verified_at,
            "last_test_ok": True,
            "last_test_code": 200,
            "last_test_message": None,
        }[k]
        return r

    def _db_row_failing(key: str) -> MagicMock:
        r = MagicMock()
        r.__getitem__ = lambda self, k: {
            "secret_key": key,
            "category": "general",
            "description": None,
            "is_sensitive": True,
            "created_at": _NOW,
            "updated_at": _NOW,
            "expires_at": None,
            "last_verified": None,
            "last_test_ok": False,
            "last_test_code": 401,
            "last_test_message": "Unauthorized",
        }[k]
        return r

    rows = [_db_row_probed("OK_KEY"), _db_row_failing("FAIL_KEY")]
    pool = _make_pool(fetch_return=rows)
    result = await CredentialStore(pool).list_secrets()

    assert len(result) == 2

    ok_meta = result[0]
    assert ok_meta.last_verified == verified_at
    assert ok_meta.last_test_ok is True
    assert ok_meta.last_test_code == 200
    assert ok_meta.last_test_message is None

    fail_meta = result[1]
    assert fail_meta.last_verified is None
    assert fail_meta.last_test_ok is False
    assert fail_meta.last_test_code == 401
    assert fail_meta.last_test_message == "Unauthorized"


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


# ---------------------------------------------------------------------------
# SecretMetadata test-state columns
# ---------------------------------------------------------------------------


def test_secret_metadata_test_state_columns() -> None:
    """SecretMetadata test-state columns default to None and roundtrip unchanged."""
    common = dict(
        key="K",
        category="general",
        description=None,
        is_sensitive=True,
        is_set=True,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
        source="database",
    )
    # Default (never probed).
    defaults = SecretMetadata(**common)
    assert defaults.last_verified is None
    assert defaults.last_test_ok is None
    assert defaults.last_test_code is None
    assert defaults.last_test_message is None

    # Roundtrip of explicit values.
    verified_at = datetime(2026, 5, 15, 10, 30, 0, tzinfo=UTC)
    meta = SecretMetadata(
        **common,
        last_verified=verified_at,
        last_test_ok=False,
        last_test_code=403,
        last_test_message="Permission denied",
    )
    assert meta.last_verified == verified_at
    assert meta.last_test_ok is False
    assert meta.last_test_code == 403
    assert meta.last_test_message == "Permission denied"


# ---------------------------------------------------------------------------
# EntityInfoRow dataclass
# ---------------------------------------------------------------------------


def test_entity_info_row_columns_and_repr_no_leak() -> None:
    """EntityInfoRow defaults/roundtrips test-state + nullable fields; repr hides value."""
    # Defaults (never probed) with no test-state args supplied.
    defaults = EntityInfoRow(
        id="00000000-0000-0000-0000-000000000001",
        entity_id="00000000-0000-0000-0000-000000000002",
        type="google_oauth_refresh",
        value="tok3n",
        label=None,
        is_primary=True,
        secured=True,
        created_at=_NOW,
    )
    assert defaults.last_verified is None
    assert defaults.last_test_ok is None
    assert defaults.last_test_code is None
    assert defaults.last_test_message is None
    # Security: repr must not expose the secret value.
    r = repr(defaults)
    assert "EntityInfoRow" in r
    assert "google_oauth_refresh" in r
    assert "secured=True" in r
    assert "tok3n" not in r

    # Roundtrip of explicit test-state values.
    verified_at = datetime(2026, 5, 20, 14, 0, 0, tzinfo=UTC)
    row = EntityInfoRow(
        id="00000000-0000-0000-0000-000000000001",
        entity_id="00000000-0000-0000-0000-000000000002",
        type="google_oauth_refresh",
        value=None,
        label="Personal account",
        is_primary=True,
        secured=True,
        created_at=_NOW,
        last_verified=verified_at,
        last_test_ok=True,
        last_test_code=200,
        last_test_message=None,
    )
    assert row.last_verified == verified_at
    assert row.last_test_ok is True
    assert row.last_test_code == 200

    # is_primary is nullable (DB column has no NOT NULL constraint).
    nullable = EntityInfoRow(
        id="abc",
        entity_id="def",
        type="email",
        value="user@example.com",
        label=None,
        is_primary=None,
        secured=False,
        created_at=_NOW,
    )
    assert nullable.is_primary is None
